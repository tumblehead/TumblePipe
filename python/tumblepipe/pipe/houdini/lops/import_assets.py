import hou

from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.variants import list_variants
from tumblepipe.pipe.paths import list_version_paths, get_workfile_context
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from tumblepipe.util import result
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.lops import import_asset


def _clear_scene(dive_node, output_node, keep_names=()):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs, outputs, and preserved nodes.
    # Preserved nodes (e.g. the persistent edit node) keep their promoted
    # parm links and stored deltas across rebuilds.
    keep = {output_node.name()} | set(keep_names)
    for node in dive_node.children():
        if node.name() in keep: continue
        node.destroy()

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _insert(data, path, value):
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value

def _update_script(instances):

    # Prepare script
    script = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import util',
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
    # not traversing instance proxies — see util._iter_prim_children), but
    # re-authoring it per instance keeps each copy's 'instance' name unique
    # and covers duplicate modes that don't reference the source.
    for prim_path, asset_uri_str, instance_name in instances:
        prim_var = f'prim_{instance_name}'
        metadata_var = f'metadata_{instance_name}'
        script += [
            f'{prim_var} = root.GetPrimAtPath("{prim_path}")',
            f'if {prim_var}.IsValid():',
            f'    {metadata_var} = util.get_metadata({prim_var})',
            f'    if {metadata_var} is None:',
            f"        {metadata_var} = {{'uri': '{asset_uri_str}', 'instance': '{instance_name}', 'inputs': []}}",
            f'    {metadata_var}["instance"] = "{instance_name}"',
            f'    util.set_metadata({prim_var}, {metadata_var})',
            ''
        ]
    
    # Done
    return script

def _assets_metadata_script(
    asset_imports: list[tuple[Uri, str, str, int]],
    shot_uri: Uri | None = None,
    shot_department: str | None = None
) -> str:
    """
    Generate Python script for setting asset metadata on scene prims.

    Sets metadata as customData on each asset's root scene prim.
    If shot_uri and shot_department are provided, adds them to the inputs array
    to track which department introduced these assets.

    Args:
        asset_imports: List of (asset_uri, variant, version, instances) tuples
        shot_uri: Optional shot entity URI for provenance tracking
        shot_department: Optional shot department name for provenance tracking

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
        'from tumblepipe.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
    ]

    for asset_uri, variant, version, instances in asset_imports:
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
            '    util.set_metadata(prim, {',
            f"        'uri': '{str(asset_uri)}',",
            f"        'instance': '{base_name}',",
            f"        'variant': '{variant}',",
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
        'from tumblepipe.pipe.houdini import util',
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
            '    util.mark_inlined(prim)',
            '',
        ])

    return '\n'.join(script_lines)

class ImportAssets(EntityNode):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self, index) -> list[Uri]:
        all_asset_uris = api.config.list_entity_uris(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )

        # Filter out already-selected assets
        count = self.parm('asset_imports').eval()
        other_asset_uris = set()
        for other_index in range(1, count + 1):
            if other_index == index: continue
            other_asset_uri_raw = self.parm(f'entity{other_index}').eval()
            if len(other_asset_uri_raw) == 0: continue
            other_asset_uri = Uri.parse_unsafe(other_asset_uri_raw)
            if other_asset_uri in all_asset_uris:
                other_asset_uris.add(other_asset_uri)

        return [
            asset_uri
            for asset_uri in all_asset_uris
            if asset_uri not in other_asset_uris
        ]

    def get_entity_uri(self, index) -> Uri | None:
        """Resolve the entity for a row, read-only.

        Falls back to the first available asset WITHOUT writing the parm:
        this is called from the variant/version menu scripts, and a parm
        write during menu evaluation dirties the node mid-draw (and can
        re-trigger evaluation). The fallback is materialized on the parm
        only by explicit actions (select/add_asset_entry/execute).
        """
        asset_uris = self.list_entity_uris(index)
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm(f'entity{index}').eval()
        if len(asset_uri_raw) == 0 or Uri.parse_unsafe(asset_uri_raw) not in asset_uris:
            return asset_uris[0]
        return Uri.parse_unsafe(asset_uri_raw)
    
    def get_instances(self, index):
        return self.parm(f'instances{index}').eval()

    def list_variant_names(self, index: int) -> list[str]:
        """List available variant names for the asset at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['default']
        variants = list_variants(asset_uri)
        if not variants:
            return ['default']
        if 'default' in variants:
            variants.remove('default')
            variants.insert(0, 'default')
        return variants

    def get_variant_name(self, index: int) -> str:
        """Get selected variant name for this index, defaults to 'default'."""
        variant_names = self.list_variant_names(index)
        variant_name = self.parm(f'variant{index}').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def set_variant_name(self, index: int, variant_name: str):
        """Set variant name for this index."""
        self.parm(f'variant{index}').set(variant_name)

    def list_version_names(self, index: int) -> list[str]:
        """List available staged versions for asset at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['latest', 'current']

        # Staged directory for the row's variant
        # (_staged/<variant>/v####, mirroring import_shot)
        staged_uri = (
            Uri.parse_unsafe('export:/') /
            asset_uri.segments /
            '_staged' /
            self.get_variant_name(index)
        )
        staged_path = api.storage.resolve(staged_uri)

        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        return ['latest', 'current'] + version_names

    def get_version_name(self, index: int) -> str:
        """Get selected version name for this index. Default is 'latest'."""
        version_name = self.parm(f'version{index}').eval()
        if len(version_name) == 0:
            return 'latest'
        return version_name

    def set_version_name(self, index: int, version_name: str):
        """Set version name for this index."""
        self.parm(f'version{index}').set(version_name)

    def get_asset_imports(self) -> list[tuple[Uri, str, str, int]]:
        """Returns list of (asset_uri, variant, version, instances) for all asset imports.

        Materializes each row's resolved URI back onto its entity parm —
        this runs from execute() (an explicit action), and the saved scene
        must record what the node actually imports (entity-parm sweeps
        read workfiles, and get_entity_uri's fallback is deliberately
        read-only for menu evaluation).
        """
        asset_imports = []
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.get_entity_uri(index)
            if asset_uri is None: continue
            if self.parm(f'entity{index}').eval() != str(asset_uri):
                self.parm(f'entity{index}').set(str(asset_uri))
            variant = self.get_variant_name(index)
            version = self.get_version_name(index)
            instances = self.get_instances(index)
            asset_imports.append((asset_uri, variant, version, instances))
        return asset_imports

    def set_entity_uri(self, index, asset_uri: Uri):
        asset_uris = self.list_entity_uris(index)
        if asset_uri not in asset_uris: return
        self.parm(f'entity{index}').set(str(asset_uri))

    def add_asset_entry(
        self,
        asset_uri: Uri,
        variant: str = 'default',
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
        self.parm(f'variant{new_index}').set(variant)
        self.parm(f'version{new_index}').set(version)
        self.parm(f'instances{new_index}').set(instances)
        return new_index

    def set_instances(self, index, instances):
        self.parm(f'instances{index}').set(instances)

    def _update_labels(self, index: int):
        """Update label parameters for the given index."""
        entity_uri = self.get_entity_uri(index)
        if entity_uri:
            self.parm(f'entity_label{index}').set(str(entity_uri))
        else:
            self.parm(f'entity_label{index}').set('none')

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name(index)
        if version_name == 'latest':
            version_names = self.list_version_names(index)
            actual_versions = [v for v in version_names if v not in ('latest', 'current')]
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm(f'version_label{index}').set(version_name)

    def _initialize(self):
        """Initialize node and update labels for all existing entries."""
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

    def execute(self):

        # Update labels for all entries
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

        # Clear scene. Preserve the persistent edit node (layout_assets):
        # its promoted parms + stored deltas back the multi-asset transform
        # handles, so it must survive the rebuild rather than be recreated.
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        edit_node = dive_node.node('layout_assets')
        keep_names = ('layout_assets',) if edit_node is not None else ()
        _clear_scene(dive_node, output_node, keep_names)

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
            return result.Value(None)

        # Build the merge node
        merge_node = dive_node.createNode('merge', 'merge')
        merge_node.parm('mergestyle').set('separate')

        # Build asset nodes
        script_args = []
        all_prim_paths = []
        for asset_uri, variant, version, instances in asset_imports:
            if instances == 0: continue
            asset_prim_path = uri_to_prim_path(asset_uri)

            # Create node name from URI segments (include variant for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = f'{uri_name}_{variant}_import' if variant != 'default' else f'{uri_name}_import'

            # Import the asset
            asset_node = import_asset.create(
                dive_node,
                node_name
            )
            asset_node.set_entity_uri(asset_uri)
            asset_node.set_variant_name(variant)
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
                _connect(asset_node.native(), merge_node)
                continue

            # Duplicate the asset (metadata in customData travels with the prim)
            duplicate_node = dive_node.createNode(
                'duplicate',
                f'{uri_name}_duplicate'
            )
            duplicate_node.parm('sourceprims').set(asset_prim_path)
            duplicate_node.parm('ncy').set(instances)
            duplicate_node.parm('duplicatename').set(
                '`@srcname``@copy`'
            )
            # Author duplicate transforms as a plain matrix op, not the
            # XformCommonAPI srt/pivot stack — the CommonAPI writer
            # conflicts with the matrix ops already composed on imported
            # prims (edit-node xformOp:transform:* and re-established
            # instance refs), scrambling/blocking the xformOpOrder.
            commonapi_parm = duplicate_node.parm('usexformcommonapi')
            if commonapi_parm is not None:
                commonapi_parm.set(0)
            _connect(asset_node.native(), duplicate_node)
            _connect(duplicate_node, merge_node)

            # Update the script arguments to rename instance in metadata.
            # The Duplicate LOP (defaults: deactivate source, 0-based
            # @copy) names the copies Chair0..ChairN-1 and deactivates
            # Chair. Tag both spellings for the first instance — the
            # 0-based copy and the legacy kept-name form — since the
            # update script skips invalid prim paths; the deactivated
            # base harmlessly keeps its base metadata.
            asset_prim_base = asset_prim_path.rsplit('/', 1)[0]  # e.g., /CHAR
            prim_name = asset_prim_path.rsplit('/', 1)[1]  # e.g., Chair
            base_name = asset_uri.segments[-1]  # Last segment is the asset name
            for index in range(instances):
                instance_name = api.naming.get_instance_name(base_name, index)
                instance_prim_names = [f'{prim_name}{index}']
                if index == 0:
                    instance_prim_names.append(prim_name)
                for instance_prim_name in instance_prim_names:
                    script_args.append((
                        f'{asset_prim_base}/{instance_prim_name}',
                        str(asset_uri),
                        instance_name
                    ))

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

        # Route the merged + metadata-updated assets through the persistent
        # edit node so the HDA's promoted 'edit' state can transform all
        # imported assets at once. Fall back to a direct connection only if
        # the edit node is somehow missing (e.g. a legacy node).
        if edit_node is not None:
            edit_node.setInput(0, python_node)
            output_node.setInput(0, edit_node)
        else:
            output_node.setInput(0, python_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        asset_count = len(active_imports)
        ns.set_node_comment(context, f"Imported: {asset_count} asset{'s' if asset_count != 1 else ''}")

        # Get workfile context for provenance tracking
        shot_uri = None
        shot_department = None
        file_path = Path(hou.hipFile.path())
        workfile_context = get_workfile_context(file_path)
        if workfile_context is not None:
            entity_uri = workfile_context.entity_uri
            # Only track shot context (not asset context)
            if entity_uri.segments and entity_uri.segments[0] == 'shots':
                shot_uri = entity_uri
                shot_department = workfile_context.department_name

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
        self.parm('set_metadata_python').set(metadata_script)

        # Done
        return result.Value(None)

def create(scene, name):
    return ns.create_node(scene, name, ImportAssets, 'import_assets')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    node = ImportAssets(raw_node)
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ImportAssets(raw_node)
    node.execute()

def select(index: int):
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportAssets(raw_node)

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
    """Return the imported asset prim paths, space-separated."""
    count = raw_node.parm('asset_imports').eval()
    paths = []
    for i in range(1, count + 1):
        entity = raw_node.parm(f'entity{i}').eval()
        if not entity:
            continue
        try:
            paths.append(uri_to_prim_path(Uri.parse_unsafe(entity)))
        except ValueError:
            pass
    return ' '.join(paths)