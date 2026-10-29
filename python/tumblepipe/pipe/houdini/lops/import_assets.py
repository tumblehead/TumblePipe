import hou


from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import (
    Department,
    list_departments,
    list_entity_departments,
)
from tumblepipe.config.channels import CHANNEL_KEY, list_channels
from tumblepipe.pipe.paths import current_staged_path, list_version_paths
from tumblepipe.pipe.usd.instances import copies_are_animated
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context
from tumblepipe.pipe.houdini.prim_metadata import ANIMATABLE_KEY
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.lops import import_asset

def _instance_prim_paths(asset_prim_path: str, instances: int) -> list[str]:
    """The prims the Duplicate LOP makes of ``asset_prim_path``, in order.

    With its defaults (0-based ``@copy``, source deactivated) the copies of
    ``/CHAR/Chair`` are ``/CHAR/Chair0`` .. ``/CHAR/Chair<N-1>``; the source
    itself is not one of them.
    """
    return [f'{asset_prim_path}{index}' for index in range(instances)]


def _update_script(instances):

    # Prepare script
    script = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import prim_metadata, prim_queries',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        ''
    ]

    # Update metadata instance names. Guard the prim: if the asset
    # composed empty (e.g. a payload that didn't resolve), GetPrimAtPath
    # returns an invalid prim — skip it instead of crashing the import.
    # Mirrors import_layer.py's metadata-update guard.
    #
    # Create the metadata when it's absent rather than only updating it in
    # place. customData does compose through the Duplicate LOP's reference
    # arcs (the historical "duplicates lose metadata" drops were the scrape
    # not traversing instance proxies — see prim_queries.iter_prim_children), but
    # re-authoring it per instance keeps each copy's 'instance' name unique
    # and covers duplicate modes that don't reference the source.
    for prim_path, asset_uri_str, instance_name in instances:
        prim_var = f'prim_{instance_name}'
        metadata_var = f'metadata_{instance_name}'
        script += [
            f'{prim_var} = root.GetPrimAtPath("{prim_path}")',
            f'if {prim_var}.IsValid():',
            f'    {metadata_var} = prim_metadata.get_metadata({prim_var})',
            f'    if {metadata_var} is None:',
            f"        {metadata_var} = {{'uri': '{asset_uri_str}', 'instance': '{instance_name}', 'inputs': []}}",
            f'    {metadata_var}["instance"] = "{instance_name}"',
            f'    prim_metadata.set_metadata({prim_var}, {metadata_var})',
            ''
        ]
    
    # Done
    return script

def _assets_metadata_script(
    asset_imports: list[tuple[Uri, str, str, int]],
    shot_uri: Uri | None = None,
    shot_department: str | None = None,
    animated=copies_are_animated
) -> str:
    """
    Generate Python script for setting asset metadata on scene prims.

    Sets metadata as customData on each asset's root scene prim.
    If shot_uri and shot_department are provided, adds them to the inputs array
    to track which department introduced these assets.

    Args:
        asset_imports: List of (asset_uri, channel, version, instances) tuples
        shot_uri: Optional shot entity URI for provenance tracking
        shot_department: Optional shot department name for provenance tracking
        animated: Each asset's prim_metadata.ANIMATABLE_KEY stamp

    Returns:
        Python script string to execute in the set_metadata node
    """
    # Build inputs list for provenance tracking
    inputs_str = '[]'
    if shot_uri is not None and shot_department is not None:
        inputs_str = f"[{{'uri': '{str(shot_uri)}', 'department': '{shot_department}', 'version': 'initial'}}]"

    script_lines = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import prim_metadata, prim_queries',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
    ]

    for asset_uri, channel, version, instances in asset_imports:
        if instances == 0:
            continue

        base_name = asset_uri.segments[-1]
        prim_path = uri_to_prim_path(asset_uri)

        # Set metadata on the scene prim (single instance only;
        # for multi-instance, metadata is set on base prim and
        # duplicated automatically by the Duplicate LOP)
        script_lines.extend([
            f'# Asset: {asset_uri}',
            f'prim = root.GetPrimAtPath("{prim_path}")',
            'if prim.IsValid():',
            '    prim_metadata.set_metadata(prim, {',
            f"        'uri': '{str(asset_uri)}',",
            f"        'instance': '{base_name}',",
            f"        '{CHANNEL_KEY}': '{channel}',",
            f"        '{ANIMATABLE_KEY}': {bool(animated(asset_uri))},",
            f"        'inputs': {inputs_str}",
            '    })',
            '',
        ])

    return '\n'.join(script_lines)

def _inline_marker_script(prim_paths: list[str]) -> str:
    """
    Generate the metadata script for 'inline' import mode.

    Marks every imported instance prim (base prims and duplicates) as
    'inlined' instead of authoring pipeline metadata: the assets are
    deliberately baked into the export, so they must not be scraped and
    re-referenced, and the publish guards must not read the missing
    metadata as an accidental drop. Targets only this node's prims —
    assets flowing through from upstream nodes are untouched.
    """
    script_lines = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import prim_metadata, prim_queries',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
    ]

    for prim_path in prim_paths:
        script_lines.extend([
            f'prim = root.GetPrimAtPath("{prim_path}")',
            'if prim.IsValid():',
            '    prim_metadata.mark_inlined(prim)',
            '',
        ])

    return '\n'.join(script_lines)

def _row_node_name(asset_uri: Uri, channel: str) -> str:
    """Name of the per-row import_asset node inside the dive."""
    uri_name = '_'.join(asset_uri.segments[1:])
    if channel == 'default':
        return f'{uri_name}_import'
    return f'{uri_name}_{channel}_import'

def _parse_variant_choice(value: str) -> tuple[str, str] | None:
    """Split a ``usd_variant#`` value (``<variantSet>=<variant>``)."""
    set_name, sep, variant_name = value.partition('=')
    if not sep or not set_name or not variant_name:
        return None
    return set_name, variant_name

def _variant_selection_script(rows: dict[int, list[str]]) -> str:
    """
    Generate the script lines that apply each row's USD variant choice.

    The choice is read live from the ``usd_variant#`` parms when the
    set_metadata node cooks, not baked in here: switching variants then
    recooks the node instead of needing another Import. Only the row ->
    prim path mapping is baked, and that is what Import rebuilds anyway.
    Runs downstream of the HDA's layerbreak so the selection is part of
    the exported layer, exactly as a Set Variant LOP after the node was.
    """
    if not rows:
        return ''
    return '\n'.join([
        '',
        '# USD variant selections (see import_assets.apply_variant_selections)',
        'from tumblepipe.pipe.houdini.lops import import_assets as _import_assets',
        '_import_assets.apply_variant_selections(',
        '    hou.pwd().parent(),',
        '    hou.pwd().editableStage(),',
        f'    {rows!r}',
        ')',
        '',
    ])

def apply_variant_selections(hda_node, stage, rows: dict[int, list[str]]):
    """Author each row's chosen variant selection on its instance prims.

    A choice naming a variantSet or variant the prim does not carry is
    skipped rather than authored: a dangling selection composes to nothing
    (an unselected variant hides its content), and a stale row mapping —
    rows reordered without re-importing — must not land on another asset.
    """
    for index, prim_paths in rows.items():
        choice = _parse_variant_choice(hda_node.parm(f'usd_variant{index}').eval())
        if choice is None:
            continue
        set_name, variant_name = choice
        for prim_path in prim_paths:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                continue
            variant_sets = prim.GetVariantSets()
            if not variant_sets.HasVariantSet(set_name):
                continue
            variant_set = variant_sets.GetVariantSet(set_name)
            if variant_name not in variant_set.GetVariantNames():
                continue
            variant_set.SetVariantSelection(variant_name)

class ImportAssets(EntityNode):
    def __init__(self, native):
        super().__init__(native)

    def scoped_departments(
        self, context: str, include_generated: bool = True,
        ) -> list[Department]:
        """The pool narrowed to the union of the rows' assets.

        The base resolves one entity and narrows to it. This node addresses
        N assets at once and its exclusion is node-level, so there is no
        single entity to narrow to — and narrowing to any one row would hide
        departments that another row's asset has. Union instead, ordered by
        the pool, because pool order is pipeline order and is load-bearing.

        Overriding this at all is not cosmetic: the base calls
        ``self.get_entity_uri()`` with no args, which this class's indexed
        override cannot serve. That was an unguarded TypeError, so
        ``list_department_names()`` and ``set_exclude_department_names()``
        raised on every import_assets node — the exclusion menu could not
        populate, and the singular→plural upgrade dropped the exclusion into
        a log.debug.

        Read-only throughout: this runs from the exclusion menu's script,
        and a parm write during menu evaluation dirties the node mid-draw.
        """
        pool = list_departments(context, include_generated=include_generated)
        assigned: set[str] = set()
        for index in range(1, self.parm('asset_imports').eval() + 1):
            entity_uri = self.get_entity_uri(index)
            if entity_uri is None:
                continue
            if not entity_uri.segments or entity_uri.segments[0] != context:
                continue
            assigned.update(
                department.name
                for department in list_entity_departments(
                    entity_uri, include_generated=include_generated,
                )
            )
        if not assigned:
            # No rows, or none drawing from this context. The base's own
            # answer for an entity it cannot narrow to is the whole pool.
            return pool
        return [
            department for department in pool if department.name in assigned
        ]

    def list_entity_uris(self, index) -> list[Uri]:
        all_asset_uris = api.config.list_entity_uris(
            filter = Uri.parse('entity:/assets'),
            closure = True
        )

        # Filter out already-selected assets
        count = self.parm('asset_imports').eval()
        other_asset_uris = set()
        for other_index in range(1, count + 1):
            if other_index == index: continue
            other_asset_uri_raw = self.parm(f'entity{other_index}').eval()
            if len(other_asset_uri_raw) == 0: continue
            other_asset_uri = Uri.parse(other_asset_uri_raw)
            if other_asset_uri in all_asset_uris:
                other_asset_uris.add(other_asset_uri)

        return [
            asset_uri
            for asset_uri in all_asset_uris
            if asset_uri not in other_asset_uris
        ]

    def get_entity_uri(self, index) -> Uri | None:
        """The row's asset; None for a row with no asset picked yet.

        A row's asset is always explicit — an empty row used to resolve to
        the first asset in the project, which imported an arbitrary asset.
        An asset that is not in the project, or that another row already
        imports, raises EntityNodeError.
        """
        asset_uri_raw = self.parm(f'entity{index}').eval()
        if len(asset_uri_raw) == 0: return None
        asset_uri = Uri.parse(asset_uri_raw)
        if asset_uri in self.list_entity_uris(index):
            return asset_uri
        if asset_uri in self.list_asset_uris():
            raise self._error(
                f'row {index}: {asset_uri} is already imported by another '
                'row. Remove one of the rows, or raise its Instances.'
            )
        raise self._error(
            f'row {index}: {asset_uri} is not an asset in the project. '
            "Pick one with the row's Entity button."
        )

    def require_entity_uri(self, index) -> Uri:
        """The row's asset, for the import, which cannot skip a row."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            raise self._error(
                f"row {index} has no asset. Pick one with the row's Entity "
                'button, or remove the row.'
            )
        return asset_uri

    def get_instances(self, index):
        return self.parm(f'instances{index}').eval()

    def list_channel_names(self, index: int) -> list[str]:
        """The row's asset's channels, 'default' first; none while unset."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return []
        return list_channels(asset_uri)

    def get_channel_name(self, index: int) -> str:
        """The row's Channel, which must be a channel of the row's asset."""
        asset_uri = self.require_entity_uri(index)
        channel_names = list_channels(asset_uri)
        channel_name = self.parm(f'channel{index}').eval()
        if channel_name not in channel_names:
            raise self._error(
                f"row {index}: channel '{channel_name}' is not a channel of "
                f"{asset_uri} (listed: {', '.join(channel_names)})"
            )
        return channel_name

    def set_channel_name(self, index: int, channel_name: str):
        """Set channel name for this index."""
        self.parm(f'channel{index}').set(channel_name)

    def list_version_names(self, index: int) -> list[str]:
        """List available staged versions for asset at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['latest', 'current']

        # Staged directory for the row's channel
        # (_staged/<channel>/v####, mirroring import_shot)
        staged_uri = (
            Uri.parse('export:/') /
            asset_uri.segments /
            '_staged' /
            self.get_channel_name(index)
        )
        staged_path = api.storage.resolve(staged_uri)

        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        return ['latest', 'current'] + version_names

    def get_version_name(self, index: int) -> str:
        """The row's version: 'latest', 'current' or a version name."""
        version_name = self.parm(f'version{index}').eval()
        if len(version_name) == 0:
            raise self._error(
                f"row {index}: version is empty. Pick a version, or 'latest'."
            )
        return version_name

    def set_version_name(self, index: int, version_name: str):
        """Set version name for this index."""
        self.parm(f'version{index}').set(version_name)

    def list_usd_variant_menu(self, index: int) -> list[str]:
        """Menu items for the row's USD variant choice, read-only.

        Lists the variantSets authored on the row's asset prim, read from
        the row's import node inside the dive — so the menu fills after the
        first Import, and lists what the asset actually composes rather
        than what the config thinks it should. Empty token = leave the
        published selection alone.
        """
        menu = ['', 'As published']
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return menu
        dive_node = self.native().node('dive')
        row_node = dive_node.node(
            _row_node_name(asset_uri, self.get_channel_name(index))
        )
        if row_node is None:
            return menu
        try:
            stage = row_node.stage()
        except hou.Error:
            # A row whose import fails to cook has no stage to list; the
            # node's own cook error already says why.
            return menu
        prim = stage.GetPrimAtPath(uri_to_prim_path(asset_uri))
        if not prim.IsValid():
            return menu
        variant_sets = prim.GetVariantSets()
        set_names = variant_sets.GetNames()
        for set_name in set_names:
            for variant_name in variant_sets.GetVariantSet(set_name).GetVariantNames():
                menu.append(f'{set_name}={variant_name}')
                menu.append(
                    variant_name if len(set_names) == 1
                    else f'{set_name}: {variant_name}'
                )
        return menu

    def get_asset_imports(self) -> list[tuple[Uri, str, str, int]]:
        """(asset_uri, channel, version, instances) for every row. Every row
        must name its asset; see require_entity_uri."""
        asset_imports = []
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.require_entity_uri(index)
            channel = self.get_channel_name(index)
            version = self.get_version_name(index)
            instances = self.get_instances(index)
            asset_imports.append((asset_uri, channel, version, instances))
        return asset_imports

    def set_entity_uri(self, index, asset_uri: Uri):
        if asset_uri not in self.list_entity_uris(index):
            raise self._error(
                f'row {index}: {asset_uri} is not an asset in the project, '
                'or another row already imports it'
            )
        self.parm(f'entity{index}').set(str(asset_uri))

    def add_asset_entry(
        self,
        asset_uri: Uri,
        channel: str = 'default',
        version: str = 'latest',
        instances: int = 1,
    ) -> int:
        """Append a new multiparm entry for ``asset_uri`` and return its index.

        Unlike :meth:`set_entity_uri`, this writes the URI directly
        without round-tripping through ``list_entity_uris`` — the
        caller has already resolved the URI, and the filter-by-existing
        pass in that method would drop the brand-new entry (since
        entry ``new_index`` temporarily shares the URI with the entry
        being disambiguated against).
        """
        count = self.parm('asset_imports').eval()
        new_index = count + 1
        self.parm('asset_imports').set(new_index)
        self.parm(f'entity{new_index}').set(str(asset_uri))
        self.parm(f'channel{new_index}').set(channel)
        self.parm(f'version{new_index}').set(version)
        self.parm(f'instances{new_index}').set(instances)
        return new_index

    def set_instances(self, index, instances):
        self.parm(f'instances{index}').set(instances)

    def _update_labels(self, index: int):
        """Update label parameters for the given index."""
        self.parm(f'entity_label{index}').set(
            self.parm(f'entity{index}').eval() or 'none'
        )

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name(index)
        if version_name == 'latest':
            # What the resolver's latest mode loads: the newest finished build
            asset_uri = self.get_entity_uri(index)
            if asset_uri is not None:
                latest = current_staged_path(asset_uri, self.get_channel_name(index))
                if latest is not None:
                    version_name = latest.name
        self.parm(f'version_label{index}').set(version_name)

    def _initialize(self):
        """Initialize node and update labels for all existing entries."""
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

    def execute(self):
        # Each inner import_asset.execute() requests a global resolver-cache
        # refresh (it dirties every composed stage in the session); defer
        # them so a multi-asset rebuild costs one refresh at the end.
        from tumblepipe import resolver
        with resolver.deferred_refresh():
            return self._execute()

    def _execute(self):

        # Update labels for all entries
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

        # Clear scene. The persistent edit node (layout_assets) that backs the
        # HDA's transform state lives at the HDA root, downstream of the
        # layerbreak, so nothing in the dive survives a rebuild.
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        ns.clear_network(dive_node, output_node)

        # Parameters
        asset_imports = self.get_asset_imports()
        exclude_department_names = self.get_exclude_department_names()
        import_mode = self.get_import_mode()

        # Check if any assets to import
        active_imports = [(uri, var, ver, inst) for uri, var, ver, inst in asset_imports if inst > 0]
        if not active_imports:
            ns.set_node_comment(context, "Bypassed: No assets configured")
            self.parm('set_metadata_python').set('')  # Clear metadata script
            context.bypass(True)
            return
        # Undo a bypass left by an earlier empty run, or the node reports
        # "Imported" while passing nothing through.
        ns.clear_auto_bypass(context)

        # Build the merge node
        merge_node = dive_node.createNode('merge', 'merge')
        merge_node.parm('mergestyle').set('separate')

        # Build asset nodes
        script_args = []
        all_prim_paths = []
        asset_prim_paths: dict[Uri, list[str]] = {}
        for asset_uri, channel, version, instances in asset_imports:
            if instances == 0: continue
            asset_prim_path = uri_to_prim_path(asset_uri)

            # Create node name from URI segments (include channel for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = _row_node_name(asset_uri, channel)

            # Import the asset
            asset_node = import_asset.create(
                dive_node,
                node_name
            )
            asset_node.set_entity_uri(asset_uri)
            asset_node.set_channel_name(channel)
            asset_node.parm('version').set(version)
            asset_node.set_exclude_department_names(
                exclude_department_names
            )
            # Always disable layerbreak on internal nodes - layerbreaks interfere
            # with merge and cause metadata to be stripped. The parent import_assets
            # keeps its own (now hidden) layerbreak downstream of the metadata step.
            asset_node.set_include_layerbreak(False)
            asset_node.set_import_mode(import_mode)
            asset_node.execute()

            # In the case of one instance
            if instances == 1:
                all_prim_paths.append(asset_prim_path)
                asset_prim_paths[asset_uri] = [asset_prim_path]
                ns.connect_next(asset_node.native(), merge_node)
                continue

            # Duplicate the asset (metadata in customData travels with the prim).
            # Mark the copies instanceable only when the asset is NOT animatable.
            # Instanceable copies share a single prototype and cannot carry
            # per-instance overrides (USD forbids editing descendants beneath an
            # instance), so an animatable character imported this way would lose
            # its individual animation and read as default pose. This mirrors
            # import_shot.py: both read copies_are_animated, which also counts a
            # published rig as animatable.
            animatable = copies_are_animated(asset_uri)
            duplicate_node = dive_node.createNode(
                'duplicate',
                f'{uri_name}_duplicate'
            )
            duplicate_node.parm('sourceprims').set(asset_prim_path)
            duplicate_node.parm('ncy').set(instances)
            duplicate_node.parm('duplicatename').set(
                '`@srcname``@copy`'
            )
            duplicate_node.parm('makeinstances').set(int(not animatable))
            ns.connect_next(asset_node.native(), duplicate_node)
            ns.connect_next(duplicate_node, merge_node)

            # Update the script arguments to rename instance in metadata.
            # The Duplicate LOP (defaults: deactivate source, 0-based
            # @copy) names the copies Chair0..ChairN-1 and deactivates
            # Chair; the deactivated base keeps its base metadata.
            base_name = asset_uri.segments[-1]  # Last segment is the asset name
            for index, instance_prim_path in enumerate(
                _instance_prim_paths(asset_prim_path, instances)
            ):
                instance_name = naming.get_instance_name(base_name, index)
                script_args.append((
                    instance_prim_path,
                    str(asset_uri),
                    instance_name
                ))
                asset_prim_paths.setdefault(asset_uri, []).append(
                    instance_prim_path
                )

        # Update the instances names in the metadata. In inline mode the
        # update script must not run: it would (re)create pipeline metadata
        # on the duplicates — the HDA-level marker script downstream handles
        # every instance prim instead.
        all_prim_paths.extend(prim_path for prim_path, _, _ in script_args)
        python_node = dive_node.createNode(
            'pythonscript',
            'metadata_update'
        )
        python_node.parm('python').set(
            '' if import_mode == 'inline'
            else '\n'.join(_update_script(script_args))
        )
        python_node.setInput(0, merge_node)

        output_node.setInput(0, python_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        asset_count = len(active_imports)
        ns.set_node_comment(context, f"Imported: {asset_count} asset{'s' if asset_count != 1 else ''}")

        # Provenance: only a shot workfile is tracked (not an asset, and not
        # the group a Multi's workfile records — that is not a shot)
        workfile_context = scene_context()
        shot_uri = entity_in_contexts(workfile_context, contexts=('shots',))
        shot_department = (
            workfile_context.department_name if shot_uri is not None else None
        )

        # Generate and set metadata script (runs after merge at HDA level).
        # Inline mode swaps the pipeline metadata for a marker on every
        # instance prim so the export bakes the assets in instead of
        # re-referencing them.
        if import_mode == 'inline':
            metadata_script = _inline_marker_script(all_prim_paths)
        else:
            metadata_script = _assets_metadata_script(
                active_imports,
                shot_uri,
                shot_department
            )

        # USD variant choices, keyed by row. The asset is unique per row
        # (list_entity_uris filters out the other rows' picks).
        variant_rows = {}
        for index in range(1, count + 1):
            asset_uri = self.get_entity_uri(index)
            if asset_uri in asset_prim_paths:
                variant_rows[index] = asset_prim_paths[asset_uri]
        metadata_script += _variant_selection_script(variant_rows)
        self.parm('set_metadata_python').set(metadata_script)

def create(scene, name):
    return ns.create_node(scene, name, ImportAssets, 'import_assets')

def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

    node = ImportAssets(raw_node)
    node._initialize()

def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportAssets(raw_node)
    node.execute()

def select(kwargs):
    """A row's Entity button: pick that row's asset."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    index = kwargs['parm'].multiParmInstanceIndices()[0]
    node = ImportAssets(kwargs['node'])
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm(f'entity{index}').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )
    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm(f'entity{index}').set(selected_uri)
            node._update_labels(index)

def output_modified_prims(raw_node) -> str:
    """Return the imported asset prim paths, space-separated.

    Evaluated on every cook: a row with no asset picked yet modifies
    nothing; a row naming an asset that does not resolve raises.
    """
    wrapper = ImportAssets(raw_node)
    count = raw_node.parm('asset_imports').eval()
    paths = []
    for i in range(1, count + 1):
        entity_uri = wrapper.get_entity_uri(i)
        if entity_uri is None:
            continue
        paths.append(uri_to_prim_path(entity_uri))
    return ' '.join(paths)