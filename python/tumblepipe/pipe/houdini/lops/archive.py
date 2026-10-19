from pathlib import Path


from tumblepipe.api import path_str
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode

class Archive(FrameRangeNode, ns.Node):
    FRAME_RANGE_PARM = 'range_type'

    def get_archive_path(self):
        archive_path = self.parm('archive_path').eval()
        if len(archive_path) == 0: return None
        return Path(archive_path)

    def export(self):

        # Nodes
        native = self.native()
        export_node = native.node('export')

        # Get the archive path
        stage_path = self.get_archive_path()
        if stage_path is None:
            raise self._error('Archive Path is not set')

        # Get and set the frame range
        render_range = self.get_frame_range().full_range()
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
    return ns.create_node(scene, name, Archive, 'archive')

def export(kwargs):
    raw_node = kwargs['node']
    node = Archive(raw_node)
    node.export()
