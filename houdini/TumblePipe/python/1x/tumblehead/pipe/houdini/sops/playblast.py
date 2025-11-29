from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import os

import hou

from tumblehead.api import path_str, fix_path, default_client
from tumblehead.apps import mp4
from tumblehead.config.timeline import get_frame_range, get_fps
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.houdini.lops import animate
from tumblehead.pipe.paths import (
    get_next_playblast_path,
    get_latest_playblast_path,
    get_daily_path,
    get_workfile_context
)
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/sops/playblast')

class Playblast(ns.Node):
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
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_camera_name(self, camera_name):
        camera_names = self.list_camera_names()
        if camera_name not in camera_names: return
        self.parm('camera').set(camera_name)
    
    def playblast(self):

        # Find nodes
        context = self.native()
        objects_node = context.node('objects')
        camera_node = objects_node.node('camera')
        playblast_node = context.node('playblast')
        render_node = playblast_node.node('render')
        raw_animate_node = context.node('../../../../')
        assert raw_animate_node.type().name().startswith('th::animate'), 'Parent node is not an animate node'
        animate_node = animate.Animate(raw_animate_node)

        # Parameters and paths
        shot_uri = animate_node.get_shot_uri()
        department_name = self.get_department_names()
        camera_path = self.get_camera_path()
        frame_range = get_frame_range(shot_uri)
        render_range = frame_range.full_range()
        output_playblast_path = get_next_playblast_path(shot_uri, department_name)
        output_daily_path = get_daily_path(shot_uri, department_name)

        # Set the camera path
        camera_node.parm('primpath').set(camera_path)

        # Work in a temporary directory
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
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

        # Find nodes
        context = self.native()
        raw_animate_node = context.node('../../../../')
        assert raw_animate_node.type().name().startswith('th::animate'), 'Parent node is not an animate node'
        animate_node = animate.Animate(raw_animate_node)

        # Parameters and paths
        shot_uri = animate_node.get_shot_uri()
        department_name = self.get_department_names()
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

        # Find nodes
        context = self.native()
        raw_animate_node = context.node('../../../../')
        assert raw_animate_node.type().name().startswith('th::animate'), 'Parent node is not an animate node'
        animate_node = animate.Animate(raw_animate_node)

        # Parameters and paths
        shot_uri = animate_node.get_shot_uri()
        department_name = self.get_department_names()
        output_playblast_path = get_latest_playblast_path(shot_uri, department_name)
        output_path = output_playblast_path.parent

        # Create and open the directory containing the playblast
        output_path.mkdir(parents=True, exist_ok=True)
        hou.ui.showInFileBrowser(f'{path_str(output_path)}')

def create(scene, name):
    node_type = ns.find_node_type('playblast', 'Sop')
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
    node_type = ns.find_node_type('playblast', 'Sop')
    if raw_node_type != node_type: return
    node = Playblast(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return
    
    # Set the default values from context
    node.set_department_name(context.department_name)

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