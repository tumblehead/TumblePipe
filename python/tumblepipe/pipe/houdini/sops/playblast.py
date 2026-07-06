from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import os

import hou

from tumblepipe.api import path_str, local_path, api
from tumblepipe.apps import mp4
from tumblepipe.config.timeline import get_frame_range, get_fps
from tumblepipe.pipe.houdini import util
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.paths import (
    get_next_playblast_path,
    get_latest_playblast_path,
    get_daily_path
)
from tumblepipe.util.uri import Uri


class Playblast(EntityNode):

    # Playblasts list the renderable shot departments.
    DEPARTMENT_CONTEXT = 'shots'
    DEPARTMENT_FILTER = 'renderable'

    def __init__(self, native):
        super().__init__(native)

    def _get_stage_root(self):
        context = self.native()
        objects_node = context.node('objects')
        if objects_node is None: return None
        camera_node = objects_node.node('camera')
        if camera_node is None: return None
        stage_node = camera_node.node(camera_node.parm('loppath').eval())
        if stage_node is None: return None
        stage = stage_node.stage()
        if stage is None: return None
        return stage.GetPseudoRoot()

    def list_entity_uris(self) -> list[str]:
        uris = api.config.list_entity_uris(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        return ['from_context'] + [str(uri) for uri in uris]

    def list_camera_paths(self):
        root = self._get_stage_root()
        if root is None: return []
        cameras = root.GetPrimAtPath('/cameras')
        if cameras is None: return []
        if not cameras.IsValid(): return []
        return util.list_cameras(cameras)
    
    def list_camera_names(self):
        camera_paths = self.list_camera_paths()
        return [path.rsplit('/', 1)[-1] for path in camera_paths]
    
    def get_department_names(self):
        department_names = self.list_department_names()
        department_name = self.parm('department').eval()
        if department_name not in department_names: return None
        return department_name

    def get_camera_path(self):
        camera_paths = self.list_camera_paths()
        camera_names = self.list_camera_names()
        camera_name = self.parm('camera').eval()
        if camera_name not in camera_names: return None
        camera_index = camera_names.index(camera_name)
        if camera_index >= len(camera_paths): return None
        return camera_paths[camera_index]
    
    def set_camera_name(self, camera_name):
        camera_names = self.list_camera_names()
        if camera_name not in camera_names: return
        self.parm('camera').set(camera_name)
        self._update_labels()

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            entity_uri = self.get_entity_uri()
            if entity_uri:
                self.parm('entity_label').set(f'from_context: {entity_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            # Specific entity URI selected - no label needed
            self.parm('entity_label').set('')

        # Department doesn't have from_context option, so just clear label
        self.parm('department_label').set('')

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def playblast(self):

        # Config reads are coherent: a shot's frame range written to disk after
        # this session started (shot import, a producer/another session editing
        # config) is picked up on read, so get_frame_range() below is current
        # with no manual refresh.

        # Find nodes
        context = self.native()
        objects_node = context.node('objects')
        camera_node = objects_node.node('camera')
        playblast_node = context.node('playblast')
        render_node = playblast_node.node('render')

        # Parameters and paths
        entity_uri = self.get_entity_uri()
        department_name = self.get_department_names()
        camera_path = self.get_camera_path()
        frame_range = get_frame_range(entity_uri)
        render_range = frame_range.full_range()
        output_playblast_path = get_next_playblast_path(entity_uri, department_name)
        output_daily_path = get_daily_path(entity_uri, department_name)

        # Set the camera path
        camera_node.parm('primpath').set(camera_path)

        # Work in a temporary directory
        root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_dir_path = Path(temp_dir)

            # Temp paths
            temp_framestack_path = temp_dir_path / 'playblast' / 'playblast.$F4.jpeg'
            temp_playblast_path = temp_dir_path / 'playblast.mp4'

            # Render
            render_node.parm('picture').set(path_str(temp_framestack_path))
            render_node.parm('f1').set(render_range.first_frame)
            render_node.parm('f2').set(render_range.last_frame)
            render_node.parm('execute').pressButton()

            # Encode mp4
            mp4.from_jpg(
                temp_framestack_path,
                render_range,
                get_fps(),
                temp_playblast_path
            )

            # Copy to network
            output_playblast_path.parent.mkdir(exist_ok=True, parents=True)
            output_daily_path.parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(temp_playblast_path, output_playblast_path)
            shutil.copyfile(temp_playblast_path, output_daily_path)
    
    def view_latest(self):

        # Parameters and paths
        entity_uri = self.get_entity_uri()
        department_name = self.get_department_names()
        output_playblast_path = get_latest_playblast_path(entity_uri, department_name)

        # Open playblast
        if output_playblast_path is None or not output_playblast_path.exists():
            return hou.ui.displayMessage(
                (
                    f'No playblast found for {entity_uri}.\n'
                    f'{output_playblast_path}'
                ),
                title='Playblast',
                severity=hou.severityType.Message
            )
        os.startfile(path_str(output_playblast_path))

    def open_location(self):

        # Parameters and paths
        entity_uri = self.get_entity_uri()
        department_name = self.get_department_names()
        output_playblast_path = get_latest_playblast_path(entity_uri, department_name)
        # No playblast yet → fall back to the (not-yet-populated) output dir.
        if output_playblast_path is None:
            output_path = get_next_playblast_path(entity_uri, department_name).parent
        else:
            output_path = output_playblast_path.parent

        # Create and open the directory containing the playblast
        output_path.mkdir(parents=True, exist_ok=True)
        hou.ui.showInFileBrowser(f'{path_str(output_path)}')

def create(scene, name):
    return ns.create_node(scene, name, Playblast, 'playblast', 'Sop')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    # Validate node type
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('playblast', 'Sop')
    if raw_node_type != node_type:
        return

    node = Playblast(raw_node)
    node._initialize()

def export():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.playblast()

def view_latest():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.view_latest()

def open_location():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.open_location()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = Playblast(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='shots',  # Only shots for playblast
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Shot",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node._update_labels()