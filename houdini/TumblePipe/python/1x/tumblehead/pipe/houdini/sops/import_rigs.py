import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.sops import import_rig

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

class ImportRigs(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return [entity.uri for entity in asset_entities]

    def list_available_asset_uris(self, index: int) -> list[Uri]:
        """List asset URIs excluding those already used by other indices."""
        all_uris = self.list_asset_uris()
        if len(all_uris) == 0: return []
        count = self.parm('rig_imports').eval()
        used_uris = set()
        for other_index in range(1, count + 1):
            if other_index == index: continue
            other_uri_raw = self.parm(f'asset{other_index}').eval()
            if len(other_uri_raw) == 0: continue
            other_uri = Uri.parse_unsafe(other_uri_raw)
            if other_uri in all_uris:
                used_uris.add(other_uri)
        return [uri for uri in all_uris if uri not in used_uris]

    def get_asset_uri(self, index: int) -> Uri | None:
        asset_uris = self.list_available_asset_uris(index)
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm(f'asset{index}').eval()
        if len(asset_uri_raw) == 0:
            asset_uri = asset_uris[0]
            self.parm(f'asset{index}').set(str(asset_uri))
            return asset_uri
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in self.list_asset_uris():
            asset_uri = asset_uris[0]
            self.parm(f'asset{index}').set(str(asset_uri))
        return asset_uri

    def get_instances(self, index: int) -> int:
        return self.parm(f'instances{index}').eval()

    def get_rig_imports(self) -> dict[Uri, int]:
        """Returns {asset_uri: instances} for all rig imports."""
        rig_imports = {}
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.get_asset_uri(index)
            if asset_uri is None: continue
            instances = self.get_instances(index)
            rig_imports[asset_uri] = instances
        return rig_imports

    def set_asset_uri(self, index: int, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm(f'asset{index}').set(str(asset_uri))

    def set_instances(self, index: int, instances: int):
        self.parm(f'instances{index}').set(instances)

    def set_rig_imports(self, rig_imports: dict[Uri, int]):
        """Set rig imports from {asset_uri: instances} dict."""
        self.parm('rig_imports').set(0)
        for asset_uri, instances in rig_imports.items():
            if instances == 0: continue
            index = self.parm('rig_imports').eval() + 1
            self.parm('rig_imports').set(index)
            self.set_asset_uri(index, asset_uri)
            self.set_instances(index, instances)
    
    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        rig_imports = self.get_rig_imports()

        # Build asset nodes
        prev_node = None
        for asset_uri, instances in rig_imports.items():
            if instances == 0: continue

            # Create node name from URI segments
            uri_name = '_'.join(asset_uri.segments[1:])

            # Import the rig
            rig_node = import_rig.create(dive_node, f'{uri_name}_import')
            rig_node.set_asset_uri(asset_uri)
            rig_node.set_instances(instances)
            rig_node.latest()
            rig_node.execute()

            # Connect the rig
            if prev_node is not None:
                _connect(prev_node, rig_node.native())
            prev_node = rig_node.native()

        # Connect to output
        if prev_node is not None:
            _connect(prev_node, output_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Done
        return result.Value(None)

def create(scene, name):
    node_type = ns.find_node_type('import_rigs', 'Sop')
    assert node_type is not None, 'Could not find import_rigs node type'
    native = scene.node(name)
    if native is not None: return ImportRigs(native)
    return ImportRigs(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportRigs(raw_node)
    node.execute()