import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import list_variants
from tumblehead.pipe.paths import list_version_paths
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import import_asset

api = default_client()


def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
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
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        ''
    ]

    # Update metadata instance names
    for prim_path, instance_name in instances:
        prim_var = f'prim_{instance_name}'
        metadata_var = f'metadata_{instance_name}'
        script += [
            f'{prim_var} = root.GetPrimAtPath("{prim_path}")',
            f'{metadata_var} = util.get_metadata({prim_var})',
            f'{metadata_var}["instance"] = "{instance_name}"',
            f'util.set_metadata({prim_var}, {metadata_var})',
            ''
        ]
    
    # Done
    return script

class ImportAssets(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self, index) -> list[Uri]:
        all_asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        all_asset_uris = [entity.uri for entity in all_asset_entities]

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

    def list_department_names(self):
        return [d.name for d in list_departments('assets') if d.publishable]

    def get_entity_uri(self, index) -> Uri | None:
        asset_uris = self.list_entity_uris(index)
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm(f'entity{index}').eval()
        if len(asset_uri_raw) == 0 or Uri.parse_unsafe(asset_uri_raw) not in asset_uris:
            asset_uri = asset_uris[0]
            self.parm(f'entity{index}').set(str(asset_uri))
            return asset_uri
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

        staged_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / '_staged'
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

    def get_exclude_department_names(self):
        return list(filter(len, self.parm('departments').eval().split()))

    def get_department_names(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return []
        exclude_department_names = self.get_exclude_department_names()
        return [
            department_name
            for department_name in department_names
            if department_name not in exclude_department_names
        ]

    def get_asset_imports(self) -> list[tuple[Uri, str, str, int]]:
        """Returns list of (asset_uri, variant, version, instances) for all asset imports."""
        asset_imports = []
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.get_entity_uri(index)
            if asset_uri is None: continue
            variant = self.get_variant_name(index)
            version = self.get_version_name(index)
            instances = self.get_instances(index)
            asset_imports.append((asset_uri, variant, version, instances))
        return asset_imports
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

    def set_entity_uri(self, index, asset_uri: Uri):
        asset_uris = self.list_entity_uris(index)
        if asset_uri not in asset_uris: return
        self.parm(f'entity{index}').set(str(asset_uri))
    
    def set_instances(self, index, instances):
        self.parm(f'instances{index}').set(instances)
    
    def set_exclude_department_names(self, exclude_department_names):
        department_names = self.list_department_names()
        self.parm('departments').set(' '.join([
            department_name
            for department_name in exclude_department_names
            if department_name in department_names
        ]))
    
    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))

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

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        asset_imports = self.get_asset_imports()
        exclude_department_names = self.get_exclude_department_names()
        include_layerbreak = self.get_include_layerbreak()

        # Check if any assets to import
        active_imports = [(uri, var, ver, inst) for uri, var, ver, inst in asset_imports if inst > 0]
        if not active_imports:
            ns.set_node_comment(context, "Bypassed: No assets configured")
            context.bypass(True)
            return result.Value(None)

        # Build the merge node
        merge_node = dive_node.createNode('merge', 'merge')
        merge_node.parm('mergestyle').set('separate')

        # Build asset nodes
        script_args = []
        for asset_uri, variant, version, instances in asset_imports:
            if instances == 0: continue

            # Create node name from URI segments (include variant for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = f'{uri_name}_{variant}_import' if variant != 'default' else f'{uri_name}_import'

            # Import the asset
            asset_node = import_asset.create(
                dive_node,
                node_name
            )
            asset_node.set_asset_uri(asset_uri)
            asset_node.set_variant_name(variant)
            asset_node.parm('version').set(version)
            asset_node.set_exclude_department_names(
                exclude_department_names
            )
            asset_node.set_include_layerbreak(include_layerbreak)
            asset_node.execute()

            # In the case of one instance
            if instances == 1:
                _connect(asset_node.native(), merge_node)
                continue

            # Duplicate the asset
            from tumblehead.pipe.houdini.util import uri_to_prim_path
            asset_prim_path = uri_to_prim_path(asset_uri)
            duplicate_node = dive_node.createNode(
                'duplicate',
                f'{uri_name}_duplicate'
            )
            duplicate_node.parm('sourceprims').set(asset_prim_path)
            duplicate_node.parm('ncy').set(instances)
            duplicate_node.parm('duplicatename').set(
                '`@srcname``@copy`'
            )
            _connect(asset_node.native(), duplicate_node)

            # Duplicate the metadata
            from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
            asset_metadata_path = uri_to_metadata_prim_path(asset_uri)
            duplicate_metadata_node = dive_node.createNode(
                'duplicate',
                f'{uri_name}_metadata_duplicate'
            )
            duplicate_metadata_node.parm('sourceprims').set(asset_metadata_path)
            duplicate_metadata_node.parm('ncy').set(instances)
            duplicate_metadata_node.parm('duplicatename').set(
                '`@srcname``@copy`'
            )
            duplicate_metadata_node.parm('parentprimtype').set('')
            _connect(duplicate_node, duplicate_metadata_node)
            _connect(duplicate_metadata_node, merge_node)

            # Update the script arguments
            # Get sanitized entity name from metadata path to match duplicate node output
            sanitized_name = asset_metadata_path.rsplit('/', 1)[1]  # e.g., _Chair
            asset_metadata_base = asset_metadata_path.rsplit('/', 1)[0]  # e.g., /_METADATA/_assets/_CHAR
            base_name = asset_uri.segments[-1]  # Last segment is the asset name
            for index in range(instances):
                # Match duplicate node naming: _Chair, _Chair0, _Chair1, etc.
                if index == 0:
                    instance_prim_name = sanitized_name  # Original keeps name
                else:
                    instance_prim_name = f'{sanitized_name}{index}'  # Copies get number suffix

                instance_name = api.naming.get_instance_name(base_name, index)
                script_args.append((
                    f'{asset_metadata_base}/{instance_prim_name}',
                    instance_name
                ))

        # Update the instances names in the metadata
        python_node = dive_node.createNode(
            'pythonscript',
            'metadata_update'
        )
        python_node.parm('python').set(
            '\n'.join(_update_script(script_args))
        )
        python_node.setInput(0, merge_node)
        output_node.setInput(0, python_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        asset_count = len(active_imports)
        ns.set_node_comment(context, f"Imported: {asset_count} asset{'s' if asset_count != 1 else ''}")

        # Done
        return result.Value(None)

def create(scene, name):
    node_type = ns.find_node_type('import_assets', 'Lop')
    assert node_type is not None, 'Could not find import_assets node type'
    native = scene.node(name)
    if native is not None: return ImportAssets(native)
    return ImportAssets(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

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
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

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