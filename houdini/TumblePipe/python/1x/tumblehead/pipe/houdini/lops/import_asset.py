import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import import_asset_layer

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_asset')

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

class ImportAsset(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return [entity.uri for entity in asset_entities]

    def list_department_names(self):
        asset_departments = list_departments('assets')
        if len(asset_departments) == 0: return []
        asset_department_names = [dept.name for dept in asset_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
        ]

    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('asset').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri
    
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
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))
    
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
        asset_uri = self.get_asset_uri()
        department_names = self.get_department_names()
        include_layerbreak = self.get_include_layerbreak()

        # Check parameters
        if asset_uri is None: return result.Value(None)

        # Create node name from URI segments
        uri_name = '_'.join(asset_uri.segments[1:])

        # Build asset layer nodes
        prev_node = None
        for department_name in department_names:
            layer_node = import_asset_layer.create(dive_node, f'{uri_name}_{department_name}')
            layer_node.set_asset_uri(asset_uri)
            layer_node.set_department_name(department_name)
            layer_node.set_include_layerbreak(False)
            layer_node.latest()
            layer_node.execute()
            if prev_node is not None:
                _connect(prev_node, layer_node.native())
            prev_node = layer_node.native()

        # Connect to output
        _connect(prev_node, output_node)

        # Enable or disable layerbreak
        switch_node.parm('input').set(1 if include_layerbreak else 0)

        # Layout the nodes
        dive_node.layoutChildren()

        # Done
        return result.Value(None)

def create(scene, name):
    node_type = ns.find_node_type('import_asset', 'Lop')
    assert node_type is not None, 'Could not find import_asset node type'
    native = scene.node(name)
    if native is not None: return ImportAsset(native)
    return ImportAsset(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportAsset(raw_node)
    node.execute()