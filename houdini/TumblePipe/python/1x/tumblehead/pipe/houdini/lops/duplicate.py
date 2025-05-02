import hou

from tumblehead.api import default_client
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class Duplicate(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def get_prim_path(self):
        return self.parm('prim_path').eval()
    
    def get_instances(self):
        return self.parm('instances').eval()

    def set_prim_path(self, prim_path):
        self.parm('prim_path').set(prim_path)

    def set_instances(self, instances):
        self.parm('instances').set(instances)
    
    def execute(self):

        # Nodes
        context = self.native()
        duplicate_node = context.node('duplicate')
        prefix_node = context.node('prefix')
        rename_node = context.node('rename')

        # Parameters
        prim_path = self.get_prim_path()
        instances = self.get_instances()
        new_name = prim_path.rsplit('/', 1)[-1]

        # Set parameters
        duplicate_node.parm('sourceprims').set(prim_path)
        duplicate_node.parm('ncy').set(instances)
        prefix_node.parm('primpattern').set(prim_path)
        rename_node.parm('primpattern').set(prim_path)
        rename_node.parm('primnewname').set(new_name)

def create(scene, name):
    node_type = ns.find_node_type('duplicate', 'Lop')
    assert node_type is not None, 'Could not find duplicate node type'
    native = scene.node(name)
    if native is not None: return Duplicate(native)
    return Duplicate(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = Duplicate(raw_node)
    node.execute()