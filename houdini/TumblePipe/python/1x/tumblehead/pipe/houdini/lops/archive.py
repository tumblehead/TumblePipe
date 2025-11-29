from pathlib import Path

import hou

from tumblehead.api import (
    path_str,
    default_client
)
from tumblehead.config.timeline import FrameRange
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class Archive(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def get_archive_path(self):
        archive_path = self.parm('archive_path').eval()
        if len(archive_path) == 0: return None
        return Path(archive_path)
    
    def get_frame_range(self):
        range_type = self.parm('range_type').eval()
        match range_type:
            case 'single_frame':
                frame_index = self.parm('frame_settingsx').eval()
                return FrameRange(
                    frame_index,
                    frame_index,
                    0, 0
                )
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                )
            case _:
                raise ValueError(f'Invalid range type: "{range_type}"')
    
    def export(self):

        # Nodes
        native = self.native()
        export_node = native.node('export')

        # Get the archive path
        stage_path = self.get_archive_path()
        if stage_path is None:
            raise ValueError('Archive path is not set')
        
        # Get and set the frame range
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        self.parm('frame_rangex').set(render_range.first_frame)
        self.parm('frame_rangey').set(render_range.last_frame)

        # Define and set the output paths
        archive_path = stage_path.parent
        extra_path = archive_path / 'extra'
        self.parm('extra_path').set(path_str(extra_path))
        self.parm('stage_path').set(path_str(stage_path))

        # Export the archive
        export_node.parm('execute').pressButton()

def create(scene, name):
    node_type = ns.find_node_type('archive', 'Lop')
    assert node_type is not None, 'Could not find archive node type'
    native = scene.node(name)
    if native is not None: return Archive(native)
    return Archive(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DIVE)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def export():
    raw_node = hou.pwd()
    node = Archive(raw_node)
    node.export()