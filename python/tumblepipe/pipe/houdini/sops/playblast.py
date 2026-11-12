import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import hou

from tumblepipe.api import api, path_str
from tumblepipe.apps import mp4
from tumblepipe.pipe import review_events
from tumblepipe.util import events
from tumblepipe.config.multi_timeline import shift_block_range
from tumblepipe.config.timeline import BlockRange, get_fps, get_frame_range
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini import prim_queries, render_camera, tumbleview
from tumblepipe.pipe.houdini.entity_node import FROM_CONTEXT, EntityNode
from tumblepipe.pipe.houdini.time_offset import TimeOffsetNode
from tumblepipe.pipe.houdini.lops.playblast import (
    DEFAULT_RES,
    camera_error,
    resolve_camera_path,
)
from tumblepipe.pipe.paths import (
    get_daily_path,
    get_latest_playblast_path,
    get_next_playblast_path,
)
from tumblepipe.pipe.usd.render_settings import live_playblast_keep_paths
from tumblepipe.util.uri import Uri


@dataclass(frozen=True)
class Plan:
    """What a playblast of the node renders: the shot and department, the
    camera, the shot's own frames, its fps and resolution, and how far a
    shared-timeline Multi moves the shot in this session (``time_offset``:
    the animation is cached on the moved frames, ``cache_range``, and
    rendered on the shot's own), and TumbleView's ``lighting`` and ``cull``
    (the node's Lighting and Backface Culling)."""
    entity_uri: Uri
    department_name: str
    camera_path: str
    render_range: BlockRange
    fps: float
    res: tuple[int, int]
    time_offset: int = 0
    lighting: str = 'scene'
    cull: str = 'off'

    @property
    def cache_range(self) -> BlockRange:
        return shift_block_range(self.render_range, self.time_offset)


class Playblast(TimeOffsetNode, EntityNode):
    """The SOP th::playblast. Its time offset is its shot's on the open
    Multi's shared timeline (the HDA has no Time Offset parm, so
    TimeOffsetNode reads it as from_multi)."""

    # A playblast is always a shot's.
    ENTITY_CONTEXTS = ('shots',)
    # Playblasts list the renderable shot departments.
    DEPARTMENT_CONTEXT = 'shots'
    DEPARTMENT_FILTER = 'renderable'

    def __init__(self, native):
        super().__init__(native)

    def _get_stage_root(self):
        # The shot stage the node sits beside (IN_stage of th::animate, which
        # stage/fetch_stage fetches): its cameras and /SET.
        fetch_node = self.native().node('stage/fetch_stage')
        if fetch_node is None: return None
        stage = fetch_node.stage()
        if stage is None: return None
        return stage.GetPseudoRoot()


    def list_camera_paths(self):
        root = self._get_stage_root()
        if root is None: return []
        return prim_queries.list_stage_cameras(root)
    
    def list_camera_names(self):
        camera_paths = self.list_camera_paths()
        return [path.rsplit('/', 1)[-1] for path in camera_paths]
    
    def list_department_names(self):
        # The Department parm defaults to 'from_context' (the workfile's own
        # department); EntityNode.get_department_name resolves it.
        return [FROM_CONTEXT] + super().list_department_names()

    def get_camera_path(self):
        # An empty Camera is the camera its menu shows (see the LOP's).
        camera_path = resolve_camera_path(
            self.parm('camera').eval(), self.list_camera_paths()
        )
        if camera_path is None:
            raise self._error(camera_error(
                self.parm('camera').eval(), self.list_camera_names()
            ))
        return camera_path

    def set_camera_name(self, camera_name):
        camera_names = self.list_camera_names()
        if camera_name not in camera_names:
            raise self._error(
                f"camera '{camera_name}' is not a camera on the stage "
                f"(cameras: {', '.join(camera_names) or 'none'})"
            )
        self.parm('camera').set(camera_name)
        self._update_labels()

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def plan(self) -> Plan:
        """What a playblast renders now; raises the node's error when its
        shot, department, camera, frame range or fps is missing."""
        # Config reads are coherent: a shot's frame range written to disk after
        # this session started (shot import, a producer/another session editing
        # config) is picked up on read, so get_frame_range() below is current
        # with no manual refresh.
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        camera_path = self.get_camera_path()
        frame_range = get_frame_range(entity_uri)
        if frame_range is None:
            raise self._error(
                f'{entity_uri} has no frame range authored in the database'
            )
        fps = get_fps(entity_uri)
        if fps is None:
            raise self._error(
                f'no fps configured for {entity_uri} or the project'
            )
        return Plan(
            entity_uri=entity_uri,
            department_name=department_name,
            camera_path=camera_path,
            render_range=frame_range.full_range(),
            fps=fps,
            res=render_camera.resolution(str(entity_uri)) or DEFAULT_RES,
            time_offset=self.get_time_offset(),
            **tumbleview.node_lighting_and_cull(self.native()),
        )

    def playblast(self):
        """Render the input (an APEX scene, invoked, or geometry) with /SET
        through the shot camera, with TumbleView, to the next playblast
        version and the daily."""
        plan = self.plan()
        entity_uri, department_name = plan.entity_uri, plan.department_name
        camera_path, render_range = plan.camera_path, plan.render_range
        fps, res = plan.fps, plan.res
        output_playblast_path = get_next_playblast_path(entity_uri, department_name)
        output_daily_path = get_daily_path(entity_uri, department_name)

        root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_dir_path = Path(temp_dir)
            cache_path = temp_dir_path / 'cache.usd'
            jpg_path = temp_dir_path / 'jpg' / 'playblast.$F4.jpg'
            movie_path = temp_dir_path / 'playblast.mp4'

            self.cache_stage(cache_path, camera_path, plan.cache_range, fps)
            jpg_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                tumbleview.render(
                    cache_path, jpg_path, camera_path, list(render_range), fps, res,
                    look=tumbleview.Look(lighting=plan.lighting, cull=plan.cull),
                    time_offset=plan.time_offset,
                )
            except tumbleview.TumbleViewError as error:
                raise self._error(str(error)) from None
            mp4.from_jpg(jpg_path, render_range, fps, movie_path)

            output_playblast_path.parent.mkdir(exist_ok=True, parents=True)
            output_daily_path.parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(movie_path, output_playblast_path)
            shutil.copyfile(movie_path, output_daily_path)
        review_events.announce(events.PLAYBLAST, entity_uri, department_name)

    def cache_stage(self, cache_path, camera_path, render_range, fps):
        """Write what the playblast shows to ``cache_path`` over
        ``render_range`` (this session's frames: a shared-timeline Multi's
        member is cached on its moved frames, Plan.cache_range, and its
        wrapper moves them back): the shot stage with everything but /SET, the
        camera and /Render deactivated (stage/keep), and the input on top at
        /live. A character's last published animation cannot show through.

        Not a graft from the stage: graftbranches flattens its source, and
        everything above the shot import's Layer Break (all of /SET and the
        camera, in a real workfile) is stripped by that flattening. For the
        same reason the cache keeps layer breaks."""
        stage_net = self.native().node('stage')
        keep = stage_net.node('keep')
        metadata = stage_net.node('metadata')
        cache = stage_net.node('cache')
        keep.parm('primpattern1').set(' '.join(live_playblast_keep_paths(camera_path)))
        metadata.parm('starttime').set(render_range.first_frame)
        metadata.parm('endtime').set(render_range.last_frame)
        metadata.parm('timepersecond').set(fps)
        metadata.parm('framespersecond').set(fps)
        cache.parm('file').set(path_str(cache_path))
        # f1-f3 default to $FSTART/$FEND expressions, which set() leaves in
        # place (it would cache the playbar's range); clear them first.
        for name, value in (('f1', render_range.first_frame),
                            ('f2', render_range.last_frame),
                            ('f3', render_range.step_size)):
            cache.parm(name).deleteAllKeyframes()
            cache.parm(name).set(value)
        cache.parm('execute').pressButton()

    def view_latest(self):

        # Parameters and paths
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
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
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
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

def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)

    # Validate node type
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('playblast', 'Sop')
    if raw_node_type != node_type:
        return

    node = Playblast(raw_node)
    node._initialize()

def export(kwargs):
    raw_node = kwargs['node']
    node = Playblast(raw_node)
    with ns.node_errors_as_message('Playblast'):
        node.playblast()

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

def select(kwargs):
    """Entity button."""
    Playblast(kwargs['node']).pick_entity('Select Shot')