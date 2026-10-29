from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import os

import hou

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_departments
from tumblepipe.config.groups import Group, get_group, is_group_uri
from tumblepipe.config.timeline import get_fps
from tumblepipe.apps import mp4
from tumblepipe.pipe.houdini import prim_queries
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini import render_camera
from tumblepipe.pipe.houdini.entity_node import (
    EntityNode,
    EntityNodeError,
    FROM_CONTEXT,
)
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.apps.deadline import log_progress
from tumblepipe.pipe.paths import (
    get_next_playblast_path,
    get_latest_playblast_path,
    get_daily_path,
)

def resolve_camera_path(camera_name: str, camera_paths: list[str]) -> str | None:
    """The stage camera a playblast's Camera parm names, or None.

    Camera is a menu whose value starts empty, and Houdini shows an empty
    menu parm as its first entry: the artist sees a camera picked and
    presses Playblast (Magnus: "just try to click playblast, there is a
    python error"). Empty is therefore the camera the menu shows, the first
    one listed. The SOP playblast resolves its parm here too.
    """
    camera_names = [path.rsplit('/', 1)[-1] for path in camera_paths]
    if camera_name == '':
        return camera_paths[0] if camera_paths else None
    if camera_name not in camera_names:
        return None
    return camera_paths[camera_names.index(camera_name)]


def camera_error(camera_name: str, camera_names: list[str]) -> str:
    if not camera_names:
        return 'there is no camera on the stage: the shot has no camera published'
    return (
        f"camera '{camera_name}' is not a camera on the stage "
        f"(cameras: {', '.join(camera_names)})"
    )


class Playblast(FrameRangeNode, EntityNode):

    # A playblast renders one shot's camera into that shot's folder.
    ENTITY_CONTEXTS = ('shots',)

    def _get_stage_root(self):
        context = self.native()
        stage_node = context.node('stage_IN')
        if stage_node is None: return None
        stage = stage_node.stage()
        if stage is None: return None
        return stage.GetPseudoRoot()

    def _shot_department_names(self) -> list[str]:
        # In a Multi's workfile with Entity left on from_context no shot
        # resolves, and the group URI is not an entity URI, so the Multi's
        # own department list is the pool there.
        if self.get_entity_uri() is not None:
            departments = self.scoped_departments('shots')
        else:
            departments = list_departments('shots')
            multi = self.get_multi()
            if multi is not None:
                covered = set(multi.departments)
                departments = [d for d in departments if d.name in covered]
        return [d.name for d in departments if d.renderable]

    def list_department_names(self) -> list[str]:
        return [FROM_CONTEXT] + self._shot_department_names()

    def list_camera_paths(self):
        root = self._get_stage_root()
        if root is None: return []
        return prim_queries.list_stage_cameras(root)

    def list_camera_names(self):
        camera_paths = self.list_camera_paths()
        return [path.rsplit('/', 1)[-1] for path in camera_paths]

    def get_multi(self) -> Group | None:
        """The Multi whose folder this workfile lives in, if any.

        A member's covered department row saves into its Multi's folder, so
        that workfile's context.json names the group ('groups:/shots/<name>')
        and not one of its shots. A playblast renders one camera into one
        shot's folder, so Entity 'from_context' resolves nothing there and
        the artist picks the member with the Entity button.
        """
        context = self._workfile_context()
        if context is None: return None
        if not is_group_uri(context.entity_uri): return None
        return get_group(context.entity_uri)

    def get_camera_path(self):
        camera_path = resolve_camera_path(
            self.parm('camera').eval(), self.list_camera_paths()
        )
        if camera_path is None:
            raise self._error(camera_error(
                self.parm('camera').eval(), self.list_camera_names()
            ))
        return camera_path

    def set_camera_name(self, camera_name):
        if camera_name not in self.list_camera_names():
            raise self._error(f"camera '{camera_name}' is not on the stage")
        self.parm('camera').set(camera_name)

    def _update_labels(self):
        """Show what 'from_context' resolved to, as import_shot does."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

    def export(self):

        # Config reads are coherent: a shot's frame range written to disk after
        # this session started (shot import, a producer/another session editing
        # config) is picked up on read, so get_frame_range() below is current
        # with no manual refresh.

        # Find nodes
        context = self.native()
        metadata_node = context.node('metadata')
        cache_node = context.node('cache')
        ropnet_node = context.node('ropnet')
        render_node = ropnet_node.node('render')

        # Parameters (each raises when it does not resolve)
        self._update_labels()
        shot_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        camera_path = self.get_camera_path()
        render_range = self.get_frame_range().full_range()
        fps = get_fps(shot_uri)
        if fps is None:
            raise self._error(f'no fps configured for {shot_uri} or the project')

        # Paths
        output_playblast_path = get_next_playblast_path(shot_uri, department_name)
        output_daily_path = get_daily_path(shot_uri, department_name)

        # Set the camera path
        render_node.parm('loppath').set(cache_node.path())
        render_node.parm('cameraprim').set(camera_path)

        # Work in a temporary directory
        root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
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
                fps,
                temp_playblast_path
            )

            # Copy to network
            output_playblast_path.parent.mkdir(exist_ok=True, parents=True)
            output_daily_path.parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(temp_playblast_path, output_playblast_path)
            shutil.copyfile(temp_playblast_path, output_daily_path)
    
    def view_latest(self):

        # Parameters and paths
        shot_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        output_playblast_path = get_latest_playblast_path(shot_uri, department_name)

        # Open playblast
        if output_playblast_path is None or not output_playblast_path.exists():
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
        shot_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        output_playblast_path = get_latest_playblast_path(shot_uri, department_name)
        # No playblast yet: open the (not-yet-populated) output dir.
        if output_playblast_path is None:
            output_path = get_next_playblast_path(shot_uri, department_name).parent
        else:
            output_path = output_playblast_path.parent

        # Create and open the directory containing the playblast
        output_path.mkdir(parents=True, exist_ok=True)
        hou.ui.showInFileBrowser(f'{path_str(output_path)}')

#: The playblast size when the shot configures no render resolution.
DEFAULT_RES = (1280, 720)


def default_res(raw_node, axis: int) -> int:
    """One side of the Resolution parm's default (the HDA's expression):
    the shot's render resolution, so the playblast has the render's frame.
    A fixed 1280x720 squashed a project that is not 16:9. Falls back to
    ``DEFAULT_RES`` while no shot resolves."""
    try:
        entity_uri = Playblast(raw_node).get_entity_uri()
    except EntityNodeError:
        entity_uri = None
    res = render_camera.resolution(str(entity_uri)) if entity_uri else None
    return (res or DEFAULT_RES)[axis]


def create(scene, name):
    return ns.create_node(scene, name, Playblast, 'playblast')

def on_created(raw_node):

    # Set node style. 'entity' and 'department' stay at their 'from_context'
    # default, so shot + department resolve from the workfile at eval time
    # rather than being baked into the parms at creation.
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)
    Playblast(raw_node)._update_labels()

def select(kwargs):
    """Entity button."""
    Playblast(kwargs['node']).pick_entity('Select Shot')

def update_labels(kwargs):
    Playblast(kwargs['node'])._update_labels()

def export(kwargs):
    raw_node = kwargs['node']
    node = Playblast(raw_node)
    with ns.node_errors_as_message('Playblast'):
        node.export()

def view_latest(kwargs):
    raw_node = kwargs['node']
    node = Playblast(raw_node)
    with ns.node_errors_as_message('Playblast'):
        node.view_latest()

def open_location(kwargs):
    raw_node = kwargs['node']
    node = Playblast(raw_node)
    with ns.node_errors_as_message('Playblast'):
        node.open_location()