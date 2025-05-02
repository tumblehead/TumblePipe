import tumblehead.pipe.houdini.nodes as ns

def create(scene, name):
    node_type = ns.find_node_type('render_vars', 'Lop')
    assert node_type is not None, 'Could not find render_vars node type'
    native = scene.node(name)
    if native is not None: native
    return scene.createNode(node_type.name(), name)

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)