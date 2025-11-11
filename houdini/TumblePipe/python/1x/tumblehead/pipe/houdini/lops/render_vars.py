import tumblehead.pipe.houdini.nodes as ns

class RenderVars(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def create(scene, name):
    node_type = ns.find_node_type('render_vars', 'Lop')
    assert node_type is not None, 'Could not find render_vars node type'
    native = scene.node(name)
    if native is not None: RenderVars(native)
    return RenderVars(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)