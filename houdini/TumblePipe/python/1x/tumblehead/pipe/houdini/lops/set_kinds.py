import hou

from tumblehead.api import default_client
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class SetKinds(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def get_category_name(self):
        return self.parm('category').eval()
    
    def get_item_name(self):
        return self.parm('item').eval()
    
    def set_category_name(self, category_name):
        self.parm('category').set(category_name)
    
    def set_item_name(self, item_name):
        self.parm('item').set(item_name)
    
    def execute(self):

        # Nodes
        context = self.native()
        category_node = context.node('category_kind')
        asset_node = context.node('asset_kind')
        content_node = context.node('content_kind')

        # Parameters
        category_name = self.get_category_name()
        item_name = self.get_item_name()

        # Set parameters
        category_node.parm('primpattern').set(f'/{category_name}')
        asset_node.parm('primpattern').set(f'/{category_name}/{item_name}')
        content_node.parm('primpattern').set(f'/{category_name}/{item_name}/*')

def create(scene, name):
    node_type = ns.find_node_type('set_kinds', 'Lop')
    assert node_type is not None, 'Could not find set_kinds node type'
    native = scene.node(name)
    if native is not None: return SetKinds(native)
    return SetKinds(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = SetKinds(raw_node)
    node.execute()