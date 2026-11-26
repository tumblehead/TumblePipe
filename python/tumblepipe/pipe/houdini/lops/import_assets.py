from pathlib import Path

import hou


from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import (
    Department,
    list_departments,
    list_entity_departments,
)
from tumblepipe.pipe.paths import get_latest_staged_path, get_staged_base_path
from tumblepipe.pipe.usd.instances import copies_are_animated
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
from tumblepipe.pipe.houdini import import_metadata
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.asset_rows import AssetRows
from tumblepipe.pipe.houdini.entity_node import ImportOptions
from tumblepipe.pipe.houdini.lops import import_asset
from tumblepipe.pipe.houdini.nodes import button

def _instance_prim_paths(asset_prim_path: str, instances: int) -> list[str]:
    """The prims the Duplicate LOP makes of ``asset_prim_path``, in order.

    With its defaults (0-based ``@copy``, source deactivated) the copies of
    ``/CHAR/Chair`` are ``/CHAR/Chair0`` .. ``/CHAR/Chair<N-1>``; the source
    itself is not one of them.
    """
    return [f'{asset_prim_path}{index}' for index in range(instances)]


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

class ImportAssets(ImportOptions, AssetRows):
    """One asset import per multiparm row (see AssetRows): the row's
    asset's staged build, in its channel, at its version; with one
    department exclusion, layerbreak and import mode for every row."""

    COUNT_PARM = 'asset_imports'
    VERSION_TOKENS = ('latest', 'current')

    def _offers(self, asset_uri: Uri) -> bool:
        """Every listed asset, the template's empty categories included."""
        return True

    def _version_folder(self, asset_uri: Uri, channel_name: str) -> Path:
        """The row's channel's staged builds (_staged/<channel>/v####,
        mirroring import_shot)."""
        return get_staged_base_path(asset_uri, channel_name)

    def _latest_version(self, asset_uri: Uri, channel_name: str) -> str | None:
        """What the resolver's latest mode loads: the newest finished build."""
        latest = get_latest_staged_path(asset_uri, channel_name)
        return None if latest is None else latest.name

    def scoped_departments(
        self, context: str, include_generated: bool = True,
        ) -> list[Department]:
        """The pool narrowed to the union of the rows' assets.

        The base resolves one entity and narrows to it. This node addresses
        N assets at once and its exclusion is node-level, so there is no
        single entity to narrow to — and narrowing to any one row would hide
        departments that another row's asset has. Union instead, ordered by
        the pool, because pool order is pipeline order and is load-bearing.

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

    def add_picked_assets(self, picked) -> str:
        """Append a row for each of ``picked`` (asset URIs, the picker's
        order) that no row holds yet; return the status line saying how
        many were added and skipped. Rows already there are not touched."""
        from tumblepipe.pipe import entity_picker as ep

        count = self.parm('asset_imports').eval()
        existing = [self.parm(f'entity{i}').eval() for i in range(1, count + 1)]
        added, skipped = ep.plan_rows(existing, picked)
        new_rows = [self.add_asset_entry(Uri.parse(uri)) for uri in added]
        # The rows are written; their labels (the version 'latest' is now)
        # read every row once, after all of them exist.
        with self._rows_read_once():
            for index in new_rows:
                self._update_labels(index)
        message = ep.rows_added_text(len(added), len(skipped))
        if added:
            message += ' Press Import to load them.'
        return f"{self.path().rsplit('/', 1)[-1]}: {message}"

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
        with resolver.deferred_refresh(), self._rows_read_once():
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
        asset_imports = self.get_row_imports()
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
            else import_metadata.copies_script(script_args)
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
            metadata_script = import_metadata.inline_script(all_prim_paths)
        else:
            metadata_script = import_metadata.assets_script(
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

@button('Import Assets')
def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportAssets(raw_node)
    node.execute()

@button('Import Assets')
def browse(kwargs):
    """The top Browse... button: pick several assets in the picker and add
    a row for each. Assets already in a row are skipped; the status line
    says how many were added and skipped."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    node = ImportAssets(kwargs['node'])
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        title="Add Assets",
        parent=hou.qt.mainWindow(),
        multi_select=True,
    )
    if not dialog.exec_():
        return
    # One undo step for all the rows added.
    with hou.undos.group(f'Add assets to {node.path()}'):
        message = node.add_picked_assets(dialog.get_selected_uris())
    hou.ui.setStatusMessage(message, severity=hou.severityType.Message)


@button('Import Assets')
def select(kwargs):
    """A row's Browse... button: pick that row's asset."""
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
    # One read of the rows for the whole loop: each get_entity_uri reads
    # every row otherwise, O(rows^2) on every cook (32 s at 1,000 rows).
    with wrapper._rows_read_once():
        for i in range(1, count + 1):
            entity_uri = wrapper.get_entity_uri(i)
            if entity_uri is None:
                continue
            paths.append(uri_to_prim_path(entity_uri))
    return ' '.join(paths)