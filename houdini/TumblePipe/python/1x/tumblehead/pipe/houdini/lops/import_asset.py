import hou

from tumblehead.api import default_client
from tumblehead.util.cache import Cache
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import import_asset_layer

api = default_client()

CACHE_VERSION_NAMES = Cache()

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
    
    def list_category_names(self):
        return api.config.list_category_names()

    def list_asset_names(self):
        category_name = self.get_category_name()
        if category_name is None: return []
        return api.config.list_asset_names(category_name)
    
    def list_department_names(self):
        asset_department_names = api.config.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/import_asset')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
        ]

    def get_category_name(self):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm('category').eval()
        if len(category_name) == 0: return category_names[0]
        if category_name not in category_names: return None
        return category_name

    def get_asset_name(self):
        asset_names = self.list_asset_names()
        if len(asset_names) == 0: return None
        asset_name = self.parm('asset').eval()
        if len(asset_name) == 0: return asset_names[0]
        if asset_name not in asset_names: return None
        return asset_name
    
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

    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)

    def set_asset_name(self, asset_name):
        asset_names = self.list_asset_names()
        if asset_name not in asset_names: return
        self.parm('asset').set(asset_name)
    
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
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        department_names = self.get_department_names()
        include_layerbreak = self.get_include_layerbreak()

        # Build asset layer nodes
        prev_node = None
        for department_name in department_names:
            layer_node = import_asset_layer.create(dive_node, f'{category_name}_{asset_name}_{department_name}')
            layer_node.set_category_name(category_name)
            layer_node.set_asset_name(asset_name)
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

def clear_cache():
    CACHE_VERSION_NAMES.clear()

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

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportAsset(raw_node)
    node.execute()