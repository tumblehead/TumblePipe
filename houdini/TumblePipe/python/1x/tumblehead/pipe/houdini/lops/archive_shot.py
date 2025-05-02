from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.config import FrameRange
from tumblehead.pipe.houdini import nodes as ns

api = default_client()

class ArchiveShot(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def get_archive_path(self):
        return Path(self.parm('archive_path').eval())
    
    def get_frame_range(self):
        return FrameRange(
            self.parm('frame_settingsx').eval(),
            self.parm('frame_settingsy').eval(),
            self.parm('roll_settingsx').eval(),
            self.parm('roll_settingsy').eval()
        )
    
    def set_archive_path(self, archive_path):
        self.parm('archive_path').set(path_str(archive_path))
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def export(self):
        
        # Find nodes
        context = self.native()
        export_node = context.node('export')
        import_node = context.node('import')

        # Parameters
        archive_path = self.get_archive_path()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()

        # Export the cache
        export_node.parm('lopoutput').set(path_str(archive_path))
        export_node.parm('f1').set(render_range.first_frame)
        export_node.parm('f2').set(render_range.last_frame)
        export_node.parm('execute').pressButton()

        # Import the cache
        import_node.parm('filepath1').set(path_str(archive_path))

def create(scene, name):
    node_type = ns.find_node_type('archive_shot', 'Lop')
    assert node_type is not None, 'Could not find archive_shot node type'
    native = scene.node(name)
    if native is not None: return ArchiveShot(native)
    return ArchiveShot(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_BLACK)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_ARCHIVE)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def export():
    raw_node = hou.pwd()
    node = ArchiveShot(raw_node)
    node.export()