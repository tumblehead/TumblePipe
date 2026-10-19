
import tumblepipe.pipe.houdini.nodes as ns

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
    return ns.create_node(scene, name, RenamePacked, 'rename_packed', 'Sop')
