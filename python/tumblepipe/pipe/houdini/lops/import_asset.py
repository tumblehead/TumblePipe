import os
import re
from pathlib import Path


from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json
from tumblepipe.pipe.usd.sublayer_refs import generate_staged_sublayer_uri
from tumblepipe.pipe.houdini import import_metadata
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
from tumblepipe.pipe.paths import (
    get_latest_staged_path,
    get_staged_base_path,
    list_version_paths
)
from tumblepipe.pipe.houdini.nodes import button

def _staged_context_assets(staged_file_path: Path) -> list[dict]:
    """Tracked sub-assets recorded next to a staged file.

    The staged build's context.json lists every asset tracked in the
    department layers it composed ({asset, instances, channel, inputs});
    every staged build writes one, so a missing sidecar raises -- rebuild it.
    """
    context_data = load_json(staged_file_path.parent / 'context.json')
    return context_data['parameters']['assets']

def _expand_staged_layers(
    staged_path: Path,
    excluded: set[str],
    latest: bool,
    _visited: set | None = None,
    _dropped: set[str] | None = None
) -> list[str]:
    """Flatten a staged file into resolved leaf layer paths, dropping
    excluded departments.

    A staged file may sublayer OTHER assets' staged files — the tracked
    assets of a set-style asset. Those refs carry no dept= parameter, so a
    flat filter would load them whole and the excluded department would
    ride back in through every subasset. Recurse into them instead, so an
    exclusion applies to the entire nesting. Returns strongest-first order
    (matching the staged file's sublayer order); revisited staged files
    (cyclic or diamond nesting) are expanded once.

    ``_dropped`` accumulates the departments an exclusion actually removed,
    anywhere in the nesting. It lets the caller tell a real exclusion from
    one naming a department that composes nothing — and it reports what the
    *files* contain rather than what the config now says, so a staged build
    made before a department was flipped non-renderable still counts.
    """
    from tumblepipe import resolver

    if _visited is None:
        _visited = set()
    key = os.path.normcase(os.path.normpath(str(staged_path)))
    if key in _visited:
        return []
    _visited.add(key)

    layers = []
    for department, ref in _list_staged_layers(staged_path):
        if department in excluded:
            if _dropped is not None:
                _dropped.add(department)
            continue
        uri = Uri.parse(ref)
        # Strip version when in 'latest' mode so the resolver
        # picks the actual latest department export.
        if latest and 'version' in uri.query:
            stripped = dict(uri.query)
            del stripped['version']
            uri = Uri(uri.purpose, uri.segments, stripped)
        layer_resolved = resolver.try_resolve_entity_uri(str(uri))
        if not layer_resolved or not Path(layer_resolved).exists():
            continue
        if department is None and '_staged' in Path(layer_resolved).parts:
            layers.extend(_expand_staged_layers(
                Path(layer_resolved), excluded, latest, _visited, _dropped
            ))
        else:
            layers.append(layer_resolved)
    return layers

def _list_staged_layers(staged_file_path: Path) -> list[tuple[str | None, str]]:
    """Parse the staged .usda layer stack into (department, ref) pairs.

    The staged asset file sublayers one layer per exported department,
    strongest first, followed by the staged files of any tracked nested
    assets. Every ref is an entity URI; the department is its dept= query
    param, None for nested-asset staged refs (they carry no dept=). A
    filesystem-path ref (from builds staged before URIs) is refused --
    rebuild the asset.
    """
    layers = []
    content = staged_file_path.read_text()
    for match in re.finditer(r'@([^@]+)@', content):
        ref = match.group(1)
        if not ref.startswith('entity:'):
            raise ValueError(
                f'{staged_file_path} sublayers {ref!r}, a file path: staged '
                'builds sublayer entity URIs. Rebuild the asset.'
            )
        layers.append((Uri.parse(ref).query.get('dept'), ref))
    return layers

class ImportAsset(EntityNode):

    # import_asset only ever addresses assets, so 'from_context' inside a
    # shot workfile resolves to nothing rather than to the shot.
    ENTITY_CONTEXTS = ('assets',)

    def __init__(self, native):
        super().__init__(native)

    def list_version_names(self) -> list[str]:
        """List available staged versions including 'latest' and 'current'."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ['latest', 'current']

        staged_path = get_staged_base_path(asset_uri, self.get_channel_name())

        # Get versioned directories (v0001, v0002, etc.)
        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        # Add special options at the beginning
        return ['latest', 'current'] + version_names


    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        self.parm('entity_label').set(self._entity_label())

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name()
        if version_name == 'latest':
            # What the resolver's latest mode loads: the newest finished build
            asset_uri = self.get_entity_uri()
            if asset_uri is not None:
                latest = get_latest_staged_path(asset_uri, self.get_channel_name())
                if latest is not None:
                    version_name = latest.name
        self.parm('version_label').set(version_name)

    def execute(self):
        self._update_labels()
        native = self.native()
        asset_uri = self.require_entity_uri()

        # Get channel and staged file path based on version selection
        channel_name = self.get_channel_name()
        version_name = self.get_version_name()

        from tumblepipe import resolver

        # Build the entity URI we want to resolve. 'latest' leaves the version
        # unpinned and turns on latest_mode so any nested entity:// URIs inside
        # the loaded layer also cascade. 'current' and specific versions bake
        # the version, freezing both this top-level resolve and nested ones.
        if version_name == 'latest':
            resolver.set_latest_mode(True)
            staged_uri = generate_staged_sublayer_uri(asset_uri, channel_name)
        else:
            resolver.set_latest_mode(False)
            if version_name == 'current':
                current_path = get_latest_staged_path(asset_uri, channel_name)
                if current_path is None:
                    self.parm('import_filepath1').set('')
                    ns.set_node_comment(native, "Bypassed: No staged file found")
                    native.bypass(True)
                    return
                pinned_version = current_path.name
            else:
                pinned_version = version_name
            staged_uri = generate_staged_sublayer_uri(asset_uri, channel_name, pinned_version)

        # Invalidate cached nested resolutions so the new mode takes effect
        resolver.refresh_context()

        # Resolve via the entity resolver and feed the inner sublayer LOP a
        # plain filesystem path. The URI form does not survive Houdini's
        # geometry-parm + chs() pipeline cleanly, so we let the resolver do
        # its work here and stash the resolved path. Nested entity:// URIs
        # inside the loaded layer continue to go through the resolver at
        # USD load time.
        resolved = resolver.try_resolve_entity_uri(staged_uri)
        if not resolved or not Path(resolved).exists():
            self.parm('import_filepath1').set('')
            ns.set_node_comment(native, "Bypassed: No staged file found")
            native.bypass(True)
            return

        # Apply department exclusion by loading the staged file's
        # per-department sublayers individually — the staged layer itself
        # composes every department, so it can only be loaded whole.
        #
        # Without exclusions, load the staged file directly. That is a
        # shortcut, NOT a correctness requirement: this comment used to
        # claim the whole-file load was needed "to keep its header metadata
        # (frame range, metersPerUnit, upAxis)", and it is not. Every
        # department export authors the same header the staged root does,
        # and the Sublayer LOP composes stage metadata from its sublayers,
        # so the flatten preserves it. Verified in hython against paleindia
        # (SET, Arena, KingTower — excluding a composing department, and a
        # non-renderable one that composes nothing either way): every case
        # produced a stage identical to the whole-file load on upAxis,
        # metersPerUnit, start/endTimeCode and fps. See
        # designs/nested-asset-workflow.md §4.1.
        #
        # It stays because it is one layer to open instead of eight, and
        # because it is the common case — not because the paths differ.
        excluded = set(self.get_exclude_department_names())
        layer_paths = [resolved]
        if excluded:
            # Flatten the staged file (recursing into tracked-asset staged
            # refs so the exclusion reaches nested assets too). The result
            # is strongest-first; the Sublayer LOP composes its last file
            # strongest, so load in reverse.
            dropped: set[str] = set()
            flattened = _expand_staged_layers(
                Path(resolved), excluded, version_name == 'latest',
                _dropped=dropped
            )
            # Only take the flatten if it actually removed something. An
            # exclusion naming a department that composes nothing — a
            # non-renderable one (only renderable department exports are
            # ever sublayered into a staged asset), or one this asset does
            # not have — otherwise buys N layers to compose the very same
            # stage: excluding 'rig' took Clash/SET from 1 layer to 8, for
            # a prim-for-prim identical result (1281 prims either way,
            # verified in hython; designs/nested-asset-workflow.md §4.1).
            if dropped:
                layer_paths = list(reversed(flattened))

        import_node = native.node('import')
        if not layer_paths:
            self.parm('import_filepath1').set('')
            import_node.parm('num_files').set(0)
            ns.set_node_comment(native, "Bypassed: All departments excluded")
            native.bypass(True)
            return

        # filepath1 channels to the HDA-level parm; extra slots are set
        # directly on the (editable) internal sublayer node.
        self.parm('import_filepath1').set(layer_paths[0])
        import_node.parm('num_files').set(len(layer_paths))
        for index, layer_path in enumerate(layer_paths[1:], start=2):
            import_node.parm(f'filepath{index}').set(layer_path)
        ns.clear_auto_bypass(native)

        # Update version label with resolved folder name
        resolved_version = Path(resolved).parent.name
        self.parm('version_label').set(resolved_version)

        # Get shot context if we're in a shot workfile
        workfile_context = scene_context()
        shot_uri = entity_in_contexts(workfile_context, contexts=('shots',))
        shot_department = (
            workfile_context.department_name if shot_uri is not None else None
        )

        # Generate and set metadata script. Inline mode swaps the pipeline
        # metadata for a marker so the export bakes the asset in instead of
        # re-referencing it. The staged context.json's tracked sub-assets
        # are covered too: their roots re-tagged and their multi-instance
        # duplicates re-defined (see import_metadata._subasset_lines).
        subassets = _staged_context_assets(Path(resolved))
        if self.get_import_mode() == 'inline':
            script = import_metadata.asset_inline_script(asset_uri, subassets)
        else:
            script = import_metadata.asset_script(
                asset_uri, channel_name, shot_uri, shot_department, subassets
            )
        self.parm('metadata_python').set(script)

        # Set success comment with import metadata
        ns.set_node_comment(native, f"Imported: {resolved_version}")

def create(scene, name):
    return ns.create_node(scene, name, ImportAsset, 'import_asset', force_valid_node_name=True)

def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    node = ImportAsset(raw_node)
    # 'entity' keeps its 'from_context' default. Seeding it with the first
    # asset in the project pinned every fresh node to an arbitrary asset —
    # whichever sorted first — and the artist had no way to tell that from a
    # deliberate choice.
    node._update_labels()

@button('Import Asset')
def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportAsset(raw_node)
    node.execute()

@button('Import Asset')
def select(kwargs):
    """Entity button: pick the asset, then Import (as the Import button
    does). Cancel changes nothing."""
    if ImportAsset(kwargs['node']).pick_entity('Select Asset'):
        execute(kwargs)

def output_modified_prims(raw_node) -> str:
    """Return the prim path this HDA wrote, for the output's modifiedprims.

    Resolve through the wrapper rather than parsing the raw parm: the parm's
    default is the 'from_context' sentinel, which is not a URI, so parsing it
    returned '' for a node in its default state. Here that also emptied the
    internal transform's primpattern -- so the artist's placement of the
    imported asset silently applied to nothing.
    """
    # Evaluated on every cook: an unset 'from_context' modifies nothing.
    entity_uri = ImportAsset(raw_node).get_entity_uri()
    if entity_uri is None:
        return ''
    return uri_to_prim_path(entity_uri)