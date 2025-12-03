import tumblehead.pipe.houdini.nodes as ns

class Animate(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def create(scene, name):
    animate_type = ns.find_node_type('animate', 'Lop')
    assert animate_type is not None, 'Could not find animate node type'
    native = scene.node(name)
    if native is not None: return Animate(native)
    return Animate(scene.createNode(animate_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DIVE)

def on_created(raw_node):
    set_style(raw_node)
