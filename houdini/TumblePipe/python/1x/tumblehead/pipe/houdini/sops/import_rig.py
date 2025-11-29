import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.sops import rename_packed
from tumblehead.pipe.paths import list_version_paths

api = default_client()

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

class ImportRig(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return list(asset_entities)

    def list_version_names(self):
        asset_uri = self.get_asset_uri()
        if asset_uri is None: return []
        export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / 'rig'
        asset_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(asset_path)
        version_names = [version_path.name for version_path in version_paths]
        return version_names

    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('asset').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[-1]
        if version_name not in version_names: return None
        return version_name

    def get_instances(self):
        return self.parm('instances').eval()

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))

    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_instances(self, instances):
        self.parm('instances').set(instances)
    
    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])
    
    def execute(self):

        # Nodes
        context = self.native()
        import_node = context.node('import')
        bypass_node = context.node('bypass')
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Clear scene
        _clear_scene(dive_node, output_node)

        # Parameters
        asset_uri = self.get_asset_uri()
        if asset_uri is None:
            bypass_node.parm('input').set(0)
            return
        version_name = self.get_version_name()
        instances = self.get_instances()

        # Create filename from URI segments
        uri_name = '_'.join(asset_uri.segments[1:])

        # Paths
        export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / 'rig' / version_name
        version_path = api.storage.resolve(export_uri)
        file_path = version_path / f'{uri_name}_rig_{version_name}.bgeo.sc'
        if not file_path.exists():
            bypass_node.parm('input').set(0)
            return
        
        # Load rig
        import_node.parm('file').set(path_str(file_path))
        import_node.parm('reload').pressButton()
        
        # Create instances
        prev_node = dive_node.indirectInputs()[0]
        source_node = dive_node.indirectInputs()[1]
        base_name = asset_uri.segments[-1]  # Last segment is the asset name
        prim_base_path = '/'.join(asset_uri.segments[1:])  # e.g., "char/hero"
        prim_parent_path = '/'.join(asset_uri.segments[1:-1])  # e.g., "char"
        for instance_index in range(instances):
            anchor_node = source_node
            instance_name = base_name if instances == 1 else f'{base_name}{instance_index}'

            # Rename the packed primitives if necessary
            if instances > 1:
                rename_node = rename_packed.create(dive_node, f'{uri_name}_{instance_name}_rename')
                rename_node.set_from_path(f'/{prim_base_path}/*')
                rename_node.set_to_path(f'/{prim_parent_path}/{instance_name}/*')
                rename_node.native().setInput(0, anchor_node)
                anchor_node = rename_node.native()

            # Create the add character to scene node
            add_node = dive_node.createNode('apex::sceneaddcharacter', f'{uri_name}_{instance_name}_add')
            add_node.parm('charactername').deleteAllKeyframes()
            add_node.parm('charactername').set(instance_name)

            # Connect the nodes
            add_node.setInput(0, prev_node)
            add_node.setInput(1, anchor_node)
            prev_node = add_node
        
        # Connect to output
        output_node.setInput(0, prev_node)

        # Layout the dive nodes
        dive_node.layoutChildren()
        
        # Disable bypass
        bypass_node.parm('input').set(1)

def create(scene, name):
    node_type = ns.find_node_type('import_rig', 'Sop')
    assert node_type is not None, 'Could not find import_rig node type'
    native = scene.node(name)
    if native is not None: return ImportRig(native)
    return ImportRig(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DIVE)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def latest():
    raw_node = hou.pwd()
    node = ImportRig(raw_node)
    node.latest()

def execute():
    raw_node = hou.pwd()
    node = ImportRig(raw_node)
    node.execute()