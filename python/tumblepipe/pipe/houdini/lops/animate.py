import tumblepipe.pipe.houdini.nodes as ns

class Animate(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def create(scene, name):
    return ns.create_node(scene, name, Animate, 'animate')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_DIVE)

def on_created(raw_node):
    set_style(raw_node)
