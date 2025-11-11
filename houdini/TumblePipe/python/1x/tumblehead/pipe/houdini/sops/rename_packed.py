import hou

from tumblehead.api import default_client
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class RenamePacked(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def get_from_path(self):
        return self.parm('from0').eval()
    
    def get_to_path(self):
        return self.parm('to0').eval()
    
    def set_from_path(self, from_path):
        self.parm('from0').set(from_path)

    def set_to_path(self, to_path):
        self.parm('to0').set(to_path)

def create(scene, name):
    node_type = ns.find_node_type('rename_packed', 'Sop')
    assert node_type is not None, 'Could not find rename_packed node type'
    native = scene.node(name)
    if native is not None: return RenamePacked(native)
    return RenamePacked(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = RenamePacked(raw_node)
    node.execute()