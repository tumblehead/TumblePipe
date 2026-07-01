import hou

import tumblepipe.pipe.houdini.nodes as ns

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
    return ns.create_node(scene, name, Duplicate, 'duplicate')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = Duplicate(raw_node)
    node.execute()