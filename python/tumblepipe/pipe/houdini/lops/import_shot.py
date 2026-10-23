import logging


from tumblepipe.api import api
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import get_frame_range, get_fps
from tumblepipe.config.department import (
    list_departments,
    list_entity_departments,
)
from tumblepipe.config.channels import CHANNEL_KEY, list_channels, read_published_channel
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini import playbar
from tumblepipe.pipe.houdini.entity_node import EntityNode, FROM_CONTEXT
from tumblepipe.pipe.houdini.lops import shot_sublayers
from tumblepipe.pipe.houdini.ui import layer_stack
from tumblepipe.pipe.paths import (
    SHARED_CHANNEL,
    current_staged_file_path,
    get_staged_file_path,
    list_version_paths,
)
from tumblepipe.pipe.context import get_source_department
from tumblepipe.pipe.usd.sublayer_refs import (
    nested_asset_department_refs,
)
from tumblepipe import resolver

logger = logging.getLogger(__name__)

# The 'exclude' parm: which shot department layers the node leaves out.
EXCLUDE_DOWNSTREAM = 'downstream'  # own department and every later one
EXCLUDE_SELF = 'self'              # own department only
EXCLUDE_NOTHING = 'none'           # every department, own included
EXCLUDE_MODES = (EXCLUDE_DOWNSTREAM, EXCLUDE_SELF, EXCLUDE_NOTHING)


def excluded_departments(department_name, downstream_departments, mode):
    """Shot departments an import leaves out, before any render cut.

    The node's own department is out — the workfile authors it — unless
    ``EXCLUDE_NOTHING`` asks for the whole shot (a check import below the
    export, reviewing the publish). Its downstream is out only in
    ``EXCLUDE_DOWNSTREAM`` mode.
    """
    excluded = set()
    if mode == EXCLUDE_NOTHING: return excluded
    if mode == EXCLUDE_DOWNSTREAM:
        excluded.update(downstream_departments)
    if department_name is not None:
        excluded.add(department_name)
    return excluded


def split_excluded_assets(assets, departments_to_exclude, all_shot_departments):
    """Split the build's assets into (kept, excluded asset URIs).

    An asset is excluded when the department that introduced it is. Its
    layer is not sublayered, but later departments' overs still make its
    prim exist; tagging and duplicating that empty prim showed ghosts like
    /SET/cloud0 and /SET/cloud1 (HideAndReek 010, 2026-10-12). Only the
    kept assets get metadata and copies.
    """
    excluded_uris = set()
    for asset_info in assets:
        source_dept = get_source_department(
            asset_info.get('inputs', []), all_shot_departments
        )
        if source_dept is not None and source_dept in departments_to_exclude:
            excluded_uris.add(asset_info['asset'])
    kept = [a for a in assets if a['asset'] not in excluded_uris]
    return kept, excluded_uris


def _indent(lines):
    """Indent lines of code by 4 spaces."""
    return [f"    {line}" for line in lines]


def _set_shot_metadata_script(shot_uri, frame_range, fps, version_name, assets):
    """Generate Python script to set shot and asset metadata in USD stage.

    Shot metadata is stored in stage-level customLayerData.
    Asset metadata is stored as customData on each asset's scene prim.
    """
    from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path

    header = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import prim_metadata',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
        'def update(root):'
    ]

    content = [
        "# Set shot metadata on stage-level customLayerData",
        "shot_metadata = {",
        f"    'uri': '{str(shot_uri)}',",
        f"    'start_frame': {frame_range.start_frame},",
        f"    'end_frame': {frame_range.end_frame},",
        f"    'start_roll': {frame_range.start_roll},",
        f"    'end_roll': {frame_range.end_roll},",
        f"    'fps': {fps},",
        f"    'version': '{version_name}',",
        "    'inputs': []",
        "}",
        "prim_metadata.set_shot_metadata(stage, shot_metadata)",
    ]

    # Set asset metadata on scene prims
    for asset_info in assets:
        asset_uri_str = asset_info['asset']
        asset_uri = Uri.parse(asset_uri_str)
        asset_prim_path = uri_to_prim_path(asset_uri)
        asset_name = asset_uri.segments[-1]
        # Every asset record carries these: the staged build's context.json
        # writes all four, and _get_scene_assets builds them. Inputs keep the
        # composition history.
        asset_channel = read_published_channel(
            asset_info, where=f'asset record {asset_uri_str}', required=True
        )
        asset_inputs = asset_info['inputs']
        instances = asset_info['instances']

        content.extend([
            "",
            f"# Asset: {asset_uri_str}",
            f"asset_prim = root.GetPrimAtPath('{asset_prim_path}')",
            "if asset_prim.IsValid():",
            "    prim_metadata.set_metadata(asset_prim, {",
            f"        'uri': '{asset_uri_str}',",
            f"        'instance': '{asset_name}',",
            f"        '{CHANNEL_KEY}': '{asset_channel}',",
            f"        'instances': {instances},",
            f"        'inputs': {asset_inputs!r}",
            "    })",
        ])

    footer = ["", "update(root)"]

    script = header + _indent(content) + footer
    return '\n'.join(script)


def _get_scene_assets(shot_uri: Uri) -> list[dict]:
    """Get current asset data from scene context.json, including inherited assets.

    Reads scene reference from root layer's context.json (same source as USD composition).
    Also includes inherited assets from parent scenes in the hierarchy.

    Args:
        shot_uri: The shot entity URI

    Returns:
        List of asset dicts with keys: asset, instance, channel, instances, inputs
        Inherited assets appear first, direct scene assets can override them.
    """
    from tumblepipe.pipe.paths import latest_export_path, get_current_scene_staged_file_path
    from tumblepipe.config.channels import DEFAULT_CHANNEL
    from tumblepipe.config.scene import get_inherited_assets

    # Get root layer to find scene reference (same approach as graph.py Fix 2)
    root_version_path = latest_export_path(shot_uri, DEFAULT_CHANNEL, 'root')
    if root_version_path is None:
        return []

    # Read scene reference from root's context.json
    root_context_data = load_json(root_version_path / 'context.json')

    scene_ref = root_context_data.get('parameters', {}).get('scene')
    if scene_ref is None:
        return []

    # Follow scene reference to get current assets
    scene_uri = Uri.parse(scene_ref)
    scene_layer_path = get_current_scene_staged_file_path(scene_uri)
    if scene_layer_path is None:
        return []

    scene_context = load_json(scene_layer_path.parent / 'context.json')

    # Get direct scene assets
    scene_assets = scene_context.get('parameters', {}).get('assets', [])

    # Get inherited assets from parent scenes
    inherited = get_inherited_assets(scene_uri)

    # Build result: inherited first, then direct (direct overrides inherited)
    result = []
    seen_assets = set()

    # Add inherited assets (from furthest parent to closest)
    for entry, parent_uri in reversed(inherited):
        if entry.asset not in seen_assets:
            asset_uri = Uri.parse(entry.asset)
            result.append({
                'asset': entry.asset,
                'instance': asset_uri.segments[-1],
                CHANNEL_KEY: entry.channel,
                'instances': entry.instances,
                'inputs': []
            })
            seen_assets.add(entry.asset)

    # Add/override with direct scene assets
    for asset_data in scene_assets:
        asset_uri_str = asset_data['asset']
        asset_uri = Uri.parse(asset_uri_str)
        new_entry = {
            'asset': asset_uri_str,
            'instance': asset_uri.segments[-1],
            CHANNEL_KEY: read_published_channel(asset_data, where=f'scene asset {asset_uri_str}'),
            'instances': asset_data.get('instances', 1),
            'inputs': []
        }
        if asset_uri_str in seen_assets:
            # Override inherited entry
            for i, item in enumerate(result):
                if item['asset'] == asset_uri_str:
                    result[i] = new_entry
                    break
        else:
            result.append(new_entry)
            seen_assets.add(asset_uri_str)

    return result



# The Department token that imports the whole staged stack with no
# department/downstream exclusion. Set by the render stage builder, where the
# composition must not depend on the ambient workfile context (the farm has
# none, a debug session has an arbitrary one).
NO_DEPARTMENT = 'none'


class ImportShot(EntityNode):

    # A shot import only ever addresses a shot: 'from_context' in an asset
    # workfile or a Multi's resolves to nothing, and the node bypasses.
    ENTITY_CONTEXTS = ('shots',)

    def list_asset_department_names(self):
        # The whole pool: these are the departments of the *assets* this shot
        # imports, not of the shot itself, and each asset carries its own
        # scoping.
        return [d.name for d in list_departments('assets') if d.renderable]

    def _shot_departments(self):
        """The shot's own departments — its assignment, or the whole pool."""
        shot_uri = self.get_entity_uri()
        if shot_uri is None:
            return list_departments('shots')
        return list_entity_departments(shot_uri)

    def list_shot_department_names(self):
        return [d.name for d in self._shot_departments() if d.renderable]

    def list_department_names(self) -> list[str]:
        names = [d.name for d in self._shot_departments()]
        return [FROM_CONTEXT, NO_DEPARTMENT] + names

    def list_version_names(self) -> list[str]:
        """List available staged versions including 'latest' and 'current'."""
        shot_uri = self.get_entity_uri()
        if shot_uri is None:
            return ['latest', 'current']

        channel_name = self.get_channel_name()

        # Get staged directory for this channel
        staged_uri = Uri.parse('export:/') / shot_uri.segments / '_staged' / channel_name
        staged_path = api.storage.resolve(staged_uri)

        # Get versioned directories (v0001, v0002, etc.)
        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        # Add special options at the beginning
        return ['latest', 'current'] + version_names

    def get_department_name(self) -> str | None:
        """The department the import is for; None for 'none' (the whole
        staged stack) or an unset 'from_context'. A Multi's workfile
        resolves its own department even though it resolves no shot: the
        node then still excludes its own layer."""
        if self.parm('department').eval() == NO_DEPARTMENT:
            return None
        return super().get_department_name()

    def get_exclude_asset_department_names(self) -> list[str]:
        return list(filter(len, self.parm('asset_departments').eval().split()))

    def set_exclude_asset_department_names(self, department_names):
        asset_department_names = self.list_asset_department_names()
        self.parm('asset_departments').set(' '.join([
            department_name
            for department_name in department_names
            if department_name in asset_department_names
        ]))

    def get_downstream_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        shot_department_index = shot_department_names.index(department_name)
        return shot_department_names[shot_department_index + 1:]

    def get_exclude_mode(self) -> str:
        """``'downstream'`` (own + later departments), ``'self'`` (own only)
        or ``'none'`` (nothing — the whole published shot).

        ``'self'`` lets an upstream department see what comes after it — the
        environment workfile seeing the animation camera. Safe for publishing:
        export_layer layerbreaks everything this node loads.
        """
        mode = self.parm('exclude').eval()
        if mode not in EXCLUDE_MODES:
            raise ValueError(
                f"Unknown exclude mode '{mode}' on the node: "
                f"expected one of {', '.join(EXCLUDE_MODES)}"
            )
        return mode

    def set_exclude_mode(self, mode: str):
        if mode not in EXCLUDE_MODES:
            raise ValueError(
                f"Unknown exclude mode '{mode}': "
                f"expected one of {', '.join(EXCLUDE_MODES)}"
            )
        self.parm('exclude').set(mode)

    def get_exclude_downstream_of(self) -> str | None:
        """Department whose *downstream* alone should be excluded, or None.

        A workfile excludes its own department too (it authors that layer),
        but a render must keep it — 'render up to lighting' includes
        lighting. This parm expresses that second, strictly-downstream cut,
        and is set by the render stage builder.

        It is a parm rather than a transient attribute on purpose: every
        callback and scene refresh wraps the node in a fresh ``ImportShot``,
        so anything not persisted here would be silently lost on the next
        import and the node would quietly go back to composing every
        department.
        """
        department_name = self.parm('exclude_downstream_of').eval()
        if len(department_name) == 0: return None
        return department_name

    def set_exclude_downstream_of(self, department_name: str | None):
        self.parm('exclude_downstream_of').set(department_name or '')

    def get_strictly_downstream_department_names(self):
        """Departments after ``exclude_downstream_of``, itself included.

        Resolved against this node's own department list — the shot's
        assignment, not the whole pool — so a scoped entity cuts where its
        own pipeline actually ends. Raises when the department is not in
        that list: composing everything instead is the renders-too-much
        bug this exists to prevent.
        """
        department_name = self.get_exclude_downstream_of()
        if department_name is None: return []
        shot_department_names = self.list_shot_department_names()
        if department_name not in shot_department_names:
            raise ValueError(
                f"exclude_downstream_of '{department_name}' is not a "
                f"renderable department of {self.get_entity_uri()}: "
                f"[{', '.join(shot_department_names)}]"
            )
        index = shot_department_names.index(department_name)
        return shot_department_names[index + 1:]

    def get_include_procedurals(self):
        return bool(self.parm('include_procedurals').eval())

    def set_channel_name(self, channel_name: str):
        super().set_channel_name(channel_name)
        self._update_labels()

    def set_version_name(self, version_name: str):
        self.parm('version').set(version_name)
        self._update_labels()

    def set_include_procedurals(self, include_procedurals):
        self.parm('include_procedurals').set(int(include_procedurals))

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

        # Update version label when 'latest' or 'current' is selected
        version_raw = self.parm('version').eval()
        if version_raw in ('latest', 'current'):
            shot_uri = self.get_entity_uri()
            if shot_uri is not None:
                # Both 'latest' and 'current' use highest versioned staged file
                # ('latest' strips versions for dynamic resolution at import time)
                channel_name = self.get_channel_name()
                actual_file_path = current_staged_file_path(shot_uri, channel_name)
                if actual_file_path is not None:
                    resolved_version = actual_file_path.parent.name
                    self.parm('version_label').set(resolved_version)
                else:
                    self.parm('version_label').set('')
            else:
                self.parm('version_label').set('')
        else:
            # Specific version selected - no label needed
            self.parm('version_label').set('')


    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def execute(self):
        self._update_labels()

        # Get nodes
        context = self.native()
        import_node = context.node('import')
        metadata_node = context.node('metadata')

        # Get parameters
        shot_uri = self.get_entity_uri()
        if shot_uri is None:
            ns.set_node_comment(context, "Bypassed: No shot selected")
            context.bypass(True)
            return
        # Undo a bypass left by an earlier run with no shot selected.
        ns.clear_auto_bypass(context)

        # Get staged file path based on version and channel selection
        version_name = self.get_version_name()
        channel_name = self.get_channel_name()

        if version_name == 'latest':
            # Enable resolver latest mode for full cascade semantics
            # All nested entity:/ URIs will resolve to their latest versions
            resolver.set_latest_mode(True)
            staged_file_path = current_staged_file_path(shot_uri, channel_name)
        elif version_name == 'current':
            # Disable latest mode - use frozen versions from build
            resolver.set_latest_mode(False)
            staged_file_path = current_staged_file_path(shot_uri, channel_name)
        else:
            # Disable latest mode - use specific version
            resolver.set_latest_mode(False)
            staged_file_path = get_staged_file_path(shot_uri, version_name, channel_name)

        # Refresh resolver cache to ensure fresh resolution with the new mode
        resolver.refresh_context()

        if staged_file_path is None or not staged_file_path.exists():
            # No staged build yet - set empty state and return
            import_node.parm('num_files').set(0)
            ns.set_node_comment(context, "No staged build yet")
            return

        # Get assets based on version type
        if version_name in ('latest', 'current'):
            # For latest/current: union of scene assets + context.json assets
            # - Scene assets: what's currently in live scene (USD composition follows latest)
            # - Context.json: what was built (has provenance inputs from department exports)
            scene_assets = _get_scene_assets(shot_uri)

            # Read context.json for additional assets from department exports
            context_path = staged_file_path.parent / 'context.json'
            context_data = load_json(context_path) if context_path.exists() else None
            context_assets = []
            if context_data is not None:
                context_assets = context_data.get('parameters', {}).get('assets', [])

            # Build lookup for context asset inputs (provenance tracking)
            context_inputs_lookup = {
                ctx_asset.get('asset'): ctx_asset.get('inputs', [])
                for ctx_asset in context_assets
            }

            # Union: use scene assets as base, but merge inputs from context
            # This preserves provenance info (which department introduced each asset)
            assets = []
            scene_asset_uris = set()
            for scene_asset in scene_assets:
                asset_uri = scene_asset['asset']
                scene_asset_uris.add(asset_uri)
                # Merge inputs from context if available
                merged_asset = scene_asset.copy()
                if asset_uri in context_inputs_lookup:
                    merged_asset['inputs'] = context_inputs_lookup[asset_uri]
                assets.append(merged_asset)

            # Add any context assets not in scene
            for ctx_asset in context_assets:
                if ctx_asset.get('asset') not in scene_asset_uris:
                    assets.append(ctx_asset)
        else:
            # For specific versions: only context.json (frozen state at build time)
            # USD dependencies are fixed in versioned builds
            context_path = staged_file_path.parent / 'context.json'
            context_data = load_json(context_path) if context_path.exists() else None
            assets = []
            if context_data is not None:
                assets = context_data.get('parameters', {}).get('assets', [])

        # Parse sublayers from staged file
        sublayers = shot_sublayers.parse_staged_sublayers(staged_file_path)

        # Exclude the current department, and its downstream unless the node
        # asks to see them. Shown downstream layers are tagged for the Layer
        # Stack: their opinions beat this department's in the final shot.
        department_name = self.get_department_name()
        downstream_departments = set(self.get_downstream_shot_department_names())
        departments_to_exclude = excluded_departments(
            department_name, downstream_departments, self.get_exclude_mode()
        )

        # A render cut excludes downstream only — the selected department is
        # what the render is *of*, so it stays. Additive, so it composes with
        # the workfile exclusion above rather than replacing it.
        departments_to_exclude.update(
            self.get_strictly_downstream_department_names()
        )

        # Assets introduced by an excluded department stay out: their layers
        # are not loaded, and they get no metadata or copies below.
        # This prevents import_shot from loading assets that import_assets will handle
        assets, excluded_asset_uris = split_excluded_assets(
            assets, departments_to_exclude, self.list_shot_department_names()
        )

        # Filter out excluded layers - both department layers AND assets from excluded departments
        def should_include_layer(info):
            # Skip _shared channel layers for single-channel entities
            if info[CHANNEL_KEY] == SHARED_CHANNEL:
                if len(list_channels(shot_uri)) <= 1:
                    return False

            if info['type'] == 'shot_department':
                return info['department'] not in departments_to_exclude
            elif info['type'] == 'asset':
                asset_uri = shot_sublayers.asset_uri_of(info['path'])
                return str(asset_uri) not in excluded_asset_uris
            # The root layer
            return True

        layers_to_load = list(reversed([
            info for info in sublayers
            if should_include_layer(info)
        ]))
        for info in layers_to_load:
            if info['type'] == 'shot_department':
                info['downstream'] = info['department'] in downstream_departments

        # Configure Sublayer LOP with filtered layers
        import_node.parm('num_files').set(len(layers_to_load))

        loaded_refs = []
        for i, info in enumerate(layers_to_load):
            # Every staged sublayer is an entity URI
            uri = Uri.parse(info['path'])
            pin_stripped = False
            # Strip version when in 'latest' mode so resolver picks actual latest
            if version_name == 'latest' and 'version' in uri.query:
                stripped = dict(uri.query)
                del stripped['version']
                uri = Uri(uri.purpose, uri.segments, stripped)
                pin_stripped = True
            layer_path = str(uri)
            import_node.parm(f'filepath{i+1}').set(layer_path)
            loaded_refs.append(layer_path)

            # Stamp what the resolver actually lands on, so the Layer Stack
            # reports the loaded version rather than the pin we just stripped.
            # Only when a pin was stripped: otherwise the ref carries its own
            # version and the recorded pin is already the truth — no reason to
            # pay a resolve per layer for a value we have.
            if pin_stripped:
                info['effective_version'] = shot_sublayers.effective_layer_version(layer_path)

        # Update layer stack UI with checkboxes and link to sublayer enables
        layer_stack.update_layer_stack(context, layers_to_load)

        # Excluded asset departments are muted, not filtered: the layers sit
        # several sublayers below anything this node loads (scene assets go
        # root -> scene -> asset staged -> department), so there is no list
        # to take them out of. The refs are read from the same staged files
        # the stage composes, in the resolver mode set above.
        excluded_asset_departments = self.get_exclude_asset_department_names()
        mute_refs = nested_asset_department_refs(
            loaded_refs,
            excluded_asset_departments,
            resolver.try_resolve_entity_uri,
        )

        # Set FPS and frame range. Apply fps *before* the range so the range
        # is stored against the final fps from the start; an fps change after
        # the range is set can otherwise shift the start/end frames. set_fps
        # also pins the range now, so this ordering is belt-and-suspenders.
        frame_range = get_frame_range(shot_uri)
        if frame_range is None:
            raise self._error(f'{shot_uri} has no frame range authored')
        fps = get_fps(shot_uri)
        if fps is None:
            raise self._error(f'no fps configured for {shot_uri} or the project')
        playbar.set_fps(fps)
        # Don't yank the playhead back to the start on every re-import;
        # only reset it when the current frame is outside the shot range.
        playbar.set_frame_range(frame_range, preserve_current_frame=True)

        # Set the shot metadata script on the metadata pythonscript node
        metadata_node.parm('python').set(_set_shot_metadata_script(
            shot_uri,
            frame_range,
            fps,
            staged_file_path.parent.name,
            assets
        ))

        # Handle asset duplication for assets with instances > 1
        self._setup_asset_duplication(context, assets, mute_refs)

        # Set success comment with import metadata
        resolved_version = staged_file_path.parent.name
        comment = f"Imported: {resolved_version}"
        # Try to get timestamp and user from context.json
        if context_data is not None:
            outputs = context_data.get('outputs', [])
            if outputs:
                output = outputs[0]
                timestamp = output.get('timestamp', '')
                user = output.get('user', '')
                if timestamp and user:
                    comment += f"\n{timestamp}\nby {user}"
        if excluded_asset_departments:
            # Say what the exclusion did: a name matching no layer (no asset
            # in the shot exported it) otherwise looks applied.
            comment += (
                f"\nMuted asset {', '.join(excluded_asset_departments)}: "
                f"{len(mute_refs)} layer{'' if len(mute_refs) == 1 else 's'}"
            )
        ns.set_node_comment(context, comment)

    def _setup_asset_duplication(self, context, assets, mute_refs=()):
        """Configure duplication for assets with instance count > 1.

        Creates/updates duplicate nodes for each asset that needs multiple instances.
        The 'duplicates' subnet is part of the HDA template.

        ``mute_refs`` are layer identifiers to mute (excluded asset
        departments). They get a Configure Stage node at the head of the
        same chain: this subnet is the HDA's one editable place rebuilt on
        every import, and a mute only has to come before whatever reads the
        composed stage.
        """
        from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
        from tumblepipe.api import default_client

        api = default_client()

        # Get the duplicates subnet (part of HDA template)
        duplicates_node = context.node('duplicates')

        # Clear existing duplicate nodes inside the subnet
        for child in list(duplicates_node.children()):
            if child.name() not in ('output0',) and not child.name().startswith('input'):
                child.destroy()

        # Get input and output of duplicates subnet
        duplicates_input = duplicates_node.indirectInputs()[0] if duplicates_node.indirectInputs() else None
        duplicates_output = duplicates_node.node('output0')

        prev_node = duplicates_input

        if mute_refs:
            # 'addremove', not 'set': a mute configured upstream of this
            # node must survive it.
            mute_node = duplicates_node.createNode(
                'configurestage', 'mute_asset_departments'
            )
            mute_node.parm('editmute').set('addremove')
            mute_node.parm('mutepaths').set(' '.join(sorted(mute_refs)))
            if prev_node:
                mute_node.setInput(0, prev_node)
            prev_node = mute_node

        # Create duplicate nodes for each asset with instances > 1
        # Metadata in customData travels with the scene prim automatically
        instance_prim_paths = []
        prototype_prim_paths = []
        for asset_info in assets:
            instances = asset_info.get('instances', 1)
            if instances <= 1:
                continue

            asset_uri_str = asset_info.get('asset', '')
            if not asset_uri_str:
                continue

            asset_uri = Uri.parse(asset_uri_str)
            asset_prim_path = uri_to_prim_path(asset_uri)
            asset_name = asset_uri.segments[-1] if asset_uri.segments else 'asset'

            # Check if asset is animatable
            properties = api.config.get_properties(asset_uri)
            animatable = properties.get('animatable', False) if properties else False

            # Create asset duplicate node (metadata in customData travels with the prim)
            dup_node = duplicates_node.createNode('duplicate', f'{asset_name}_dup')
            dup_node.parm('sourceprims').set(asset_prim_path)
            dup_node.parm('ncy').set(instances)
            dup_node.parm('duplicatename').set('`@srcname``@copy`')
            dup_node.parm('makeinstances').set(int(not animatable))

            if prev_node:
                dup_node.setInput(0, prev_node)
            prev_node = dup_node

            # Duplicate names its copies {name}0..{name}N-1 (0-based
            # @copy, source deactivated) — collect for the op-order pass,
            # along with the source it deactivated: that prim is the
            # prototype the copies reference.
            instance_prim_paths.extend(
                f'{asset_prim_path}{index}' for index in range(instances)
            )
            prototype_prim_paths.append(asset_prim_path)

        # Placement op VALUES compose onto the duplicated prims from the
        # authoring department's exported sidecar, but the xformOpOrder
        # that applies them was stripped with the original Duplicate defs
        # — without this pass the farm render graph (which builds this
        # exact node network) stacks every copy at the prototype, just
        # like the GUI import did. Mirrors import_asset's re-established
        # instances via the same util helper.
        if instance_prim_paths:
            script_lines = [
                'import hou',
                '',
                'from tumblepipe.pipe.houdini import prim_queries',
                '',
                'node = hou.pwd()',
                'stage = node.editableStage()',
                'root = stage.GetPseudoRoot()',
                '',
            ]
            # Prototypes first: the Duplicate LOP leaves its deactivated
            # source with whatever transform composed, which for a prim
            # that never passed an import Transform LOP is none at all.
            # Authoring here before the copies below lets the op set reach
            # them through Duplicate's reference arc, matching the GUI
            # import (see prim_queries.author_identity_placement_ops).
            for prototype_prim_path in prototype_prim_paths:
                script_lines += [
                    f'prim = root.GetPrimAtPath("{prototype_prim_path}")',
                    'if prim.IsValid():',
                    '    if not prim_queries.apply_placement_op_order(prim):',
                    '        prim_queries.author_identity_placement_ops(prim)',
                    '',
                ]
            for instance_prim_path in instance_prim_paths:
                script_lines += [
                    f'prim = root.GetPrimAtPath("{instance_prim_path}")',
                    'if prim.IsValid():',
                    '    prim_queries.apply_placement_op_order(prim)',
                    '',
                ]
            order_node = duplicates_node.createNode(
                'pythonscript', 'placement_op_order'
            )
            order_node.parm('python').set('\n'.join(script_lines))
            if prev_node:
                order_node.setInput(0, prev_node)
            prev_node = order_node

        # Connect last node to output
        if duplicates_output and prev_node and prev_node != duplicates_input:
            duplicates_output.setInput(0, prev_node)
        elif duplicates_output and duplicates_input:
            # No duplicates needed, connect input directly to output
            duplicates_output.setInput(0, duplicates_input)

        # Layout the subnet
        duplicates_node.layoutChildren()


def create(scene, name):
    return ns.create_node(scene, name, ImportShot, 'import_shot', force_valid_node_name=True)


def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

    node = ImportShot(raw_node)
    node._initialize()


def execute(kwargs):
    """The Import button's and the Channel/Version menus' callback.

    execute() rebuilds this node's Layer Stack with setParmTemplateGroup,
    which replaces the HDA instance's parms. Run inside the callback of one
    of those parms, it frees the parm Houdini is still executing: pressing
    Import crashed Houdini (access violation in pressButton), and a cook
    that caught the half-built interface composed with every layer
    disabled, so the viewport showed nothing until something re-cooked it.
    The import runs after the callback returns instead, in Manual update
    mode like a scene-open refresh, so the graph cooks once, complete.
    """
    import hou

    path = kwargs['node'].path()

    def _import():
        native = hou.node(path)
        if native is None:
            return
        try:
            with playbar.update_mode(hou.updateMode.Manual):
                ImportShot(native).execute()
        except Exception as exc:
            from tumblepipe.pipe.houdini.ui.report import report_failure
            report_failure(f"Importing the shot into {path}", exc)

    if not hou.isUIAvailable():
        ImportShot(kwargs['node']).execute()
        return
    import hdefereval
    hdefereval.executeDeferred(_import)


def select(kwargs):
    """Entity button."""
    ImportShot(kwargs['node']).pick_entity('Select Shot')


def inspect_asset_layer(kwargs, index: int):
    """A Layer Stack row's '...' button."""
    native = kwargs['node']
    layer_stack.inspect_asset_layer(
        native, index, pinned=ImportShot(native).get_version_name() != 'latest'
    )