import tumblehead.pipe.houdini.nodes as ns

class PuzzleMattes(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def create(scene, name):
    node_type = ns.find_node_type('puzzlemattes', 'Lop')
    assert node_type is not None, 'Could not find puzzlemattes node type'
    native = scene.node(name)
    if native is not None: return PuzzleMattes(native)
    return PuzzleMattes(scene.createNode(node_type.name(), name))