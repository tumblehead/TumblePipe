import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
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

    def list_asset_uris(self, index) -> list[Uri]:
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
            other_asset_uri_raw = self.parm(f'asset{other_index}').eval()
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
        return [d.name for d in list_departments('assets') if d.renderable]

    def get_asset_uri(self, index) -> Uri | None:
        asset_uris = self.list_asset_uris(index)
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm(f'asset{index}').eval()
        if len(asset_uri_raw) == 0 or Uri.parse_unsafe(asset_uri_raw) not in asset_uris:
            asset_uri = asset_uris[0]
            self.parm(f'asset{index}').set(str(asset_uri))
            return asset_uri
        return Uri.parse_unsafe(asset_uri_raw)
    
    def get_instances(self, index):
        return self.parm(f'instances{index}').eval()

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

    def get_asset_imports(self) -> dict[Uri, int]:
        asset_imports = {}
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.get_asset_uri(index)
            if asset_uri is None: continue
            instances = self.get_instances(index)
            asset_imports[asset_uri] = instances
        return asset_imports
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

    def set_asset_uri(self, index, asset_uri: Uri):
        asset_uris = self.list_asset_uris(index)
        if asset_uri not in asset_uris: return
        self.parm(f'asset{index}').set(str(asset_uri))
    
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
    
    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        switch_node = context.node('switch')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        asset_imports = self.get_asset_imports()
        exclude_department_names = self.get_exclude_department_names()
        include_layerbreak = self.get_include_layerbreak()

        # Build the merge node
        merge_node = dive_node.createNode('merge', 'merge')
        merge_node.parm('mergestyle').set('separate')

        # Build asset nodes
        script_args = []
        for asset_uri, instances in asset_imports.items():
            if instances == 0: continue

            # Create node name from URI segments
            uri_name = '_'.join(asset_uri.segments[1:])

            # Import the asset
            asset_node = import_asset.create(
                dive_node,
                f'{uri_name}_import'
            )
            asset_node.set_asset_uri(asset_uri)
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
            asset_metadata_base = asset_metadata_path.rsplit('/', 1)[0]  # /_METADATA/_assets/_char
            base_name = asset_uri.segments[-1]  # Last segment is the asset name
            for index in range(instances):
                instance_name = api.naming.get_instance_name(base_name, index)
                script_args.append((
                    f'{asset_metadata_base}/{instance_name}',
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

        # Enable or disable layerbreak
        switch_node.parm('input').set(1 if include_layerbreak else 0)

        # Layout the nodes
        dive_node.layoutChildren()

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

def execute():
    raw_node = hou.pwd()
    node = ImportAssets(raw_node)
    node.execute()