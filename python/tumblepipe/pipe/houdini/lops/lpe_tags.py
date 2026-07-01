import tumblepipe.pipe.houdini.nodes as ns

class LPETags(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def create(scene, name):
    return ns.create_node(scene, name, LPETags, 'lpe_tags')