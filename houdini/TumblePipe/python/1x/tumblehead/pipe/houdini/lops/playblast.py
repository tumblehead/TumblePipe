from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import os

import hou

from tumblehead.api import path_str, fix_path, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.apps import mp4
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.apps.deadline import log_progress
from tumblehead.pipe.paths import (
    get_next_playblast_path,
    get_latest_playblast_path,
    get_daily_path,
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/playblast')

class Playblast(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def _get_stage_root(self):
        context = self.native()
        stage_node = context.node('stage_IN')
        if stage_node is None: return None
        stage = stage_node.stage()
        if stage is None: return None
        return stage.GetPseudoRoot()

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return list(shot_entities)

    def list_department_names(self):
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]

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

    def get_shot_uri(self) -> Uri | None:
        shot_uris = self.list_shot_uris()
        if len(shot_uris) == 0: return None
        shot_uri_raw = self.parm('shot').eval()
        if len(shot_uri_raw) == 0: return shot_uris[0]
        shot_uri = Uri.parse_unsafe(shot_uri_raw)
        if shot_uri not in shot_uris: return None
        return shot_uri

    def get_department_name(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name

    def get_camera_path(self):
        camera_paths = self.list_camera_paths()
        if len(camera_paths) == 0: return None
        camera_names = self.list_camera_names()
        camera_name = self.parm('camera').eval()
        if len(camera_name) == 0: return camera_paths[0]
        if camera_name not in camera_names: return None
        camera_index = camera_names.index(camera_name)
        if camera_index < 0: return None
        if len(camera_paths) <= camera_index: return None
        return camera_paths[camera_index]

    def get_frame_range_source(self):
        return self.parm('frame_range').eval()

    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
                shot_uri = self.get_shot_uri()
                if shot_uri is None: return None
                frame_range = get_frame_range(shot_uri)
                return frame_range
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval(),
                    1  # step_size
                )
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_camera_name(self, camera_name):
        camera_names = self.list_camera_names()
        if camera_name not in camera_names: return
        self.parm('camera').set(camera_name)
    
    def export(self):
        
        # Find nodes
        context = self.native()
        metadata_node = context.node('metadata')
        cache_node = context.node('cache')
        ropnet_node = context.node('ropnet')
        render_node = ropnet_node.node('render')

        # Parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        camera_path = self.get_camera_path()
        frame_range = self.get_frame_range()
        print(f"[DEBUG] FrameRange: start={frame_range.start_frame}, end={frame_range.end_frame}, start_roll={frame_range.start_roll}, end_roll={frame_range.end_roll}, step={frame_range.step_size}")
        render_range = frame_range.full_range()
        print(f"[DEBUG] BlockRange: first={render_range.first_frame}, last={render_range.last_frame}, step={render_range.step_size}")
        fps = get_fps()

        # Check camera path
        assert camera_path is not None, 'No camera path found'

        # Paths
        output_playblast_path = get_next_playblast_path(shot_uri, department_name)
        output_daily_path = get_daily_path(shot_uri, department_name)

        # Set the camera path
        render_node.parm('loppath').set(cache_node.path())
        render_node.parm('cameraprim').set(camera_path)

        # Work in a temporary directory
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_dir_path = Path(temp_dir)

            # Temp paths
            temp_cache_path = temp_dir_path / 'cache.usd'
            temp_jpg_path = temp_dir_path / 'jpg' / 'playblast.$F4.jpg'
            temp_playblast_path = temp_dir_path / 'playblast.mp4'

            # Set metadata
            metadata_node.parm('starttime').set(render_range.first_frame)
            metadata_node.parm('endtime').set(render_range.last_frame)
            metadata_node.parm('timepersecond').set(fps)
            metadata_node.parm('framespersecond').set(fps)

            # Export the cache
            cache_node.parm('file').set(path_str(temp_cache_path))
            cache_node.parm('f1').set(render_range.first_frame)
            cache_node.parm('f2').set(render_range.last_frame)
            cache_node.parm('execute').pressButton()
            cache_node.parm('loadfromdisk').set(1)

            # Render the frames
            temp_jpg_path.parent.mkdir(exist_ok=True, parents=True)
            render_node.parm('picture').set(path_str(temp_jpg_path))
            for frame_index in log_progress(render_range):
                render_node.parm('f1').set(frame_index)
                render_node.parm('f2').set(frame_index)
                render_node.parm('execute').pressButton()

            # Encode mp4
            mp4.from_jpg(
                temp_jpg_path,
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
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        output_playblast_path = get_latest_playblast_path(shot_uri, department_name)

        # Open playblast
        if not output_playblast_path.exists():
            return hou.ui.displayMessage(
                (
                    f'No playblast found for {shot_uri}.\n'
                    f'{output_playblast_path}'
                ),
                title='Playblast',
                severity=hou.severityType.Message
            )
        os.startfile(path_str(output_playblast_path))
    
    def open_location(self):

        # Parameters and paths
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        output_playblast_path = get_latest_playblast_path(shot_uri, department_name)
        output_path = output_playblast_path.parent

        # Create and open the directory containing the playblast
        output_path.mkdir(parents=True, exist_ok=True)
        hou.ui.showInFileBrowser(f'{path_str(output_path)}')

def create(scene, name):
    node_type = ns.find_node_type('playblast', 'Lop')
    assert node_type is not None, 'Could not find playblast node type'
    native = scene.node(name)
    if native is not None: return Playblast(native)
    return Playblast(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('playblast', 'Lop')
    if raw_node_type != node_type: return
    node = Playblast(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return

    # Set the default values from context
    node.set_shot_uri(context.entity_uri)
    node.set_department_name(context.department_name)

def export():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.export()

def view_latest():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.view_latest()

def open_location():
    raw_node = hou.pwd()
    node = Playblast(raw_node)
    node.open_location()