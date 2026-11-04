from tempfile import TemporaryDirectory
from pathlib import Path
import subprocess
import shutil
import re
import os

import hou

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_departments
from tumblepipe.config.groups import Group, get_group, is_group_uri
from tumblepipe.config.timeline import get_fps
from tumblepipe.apps import mp4
from tumblepipe.apps.viewport_renderer import VIEWPORT_DELEGATE, viewport_args, viewport_env
from tumblepipe.pipe.usd.render_settings import (
    PLAYBLAST_SETTINGS_PRIM_PATH,
    generate_playblast_wrapper,
)
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
    get_layer_playblast_movie_path,
    playblast_version_of,
)
from tumblepipe.config.channels import CHANNEL_PRIMS_PROPERTY, list_channel_layers

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

        renderer = self.parm('renderer').evalAsString()
        layers = self._render_layers(shot_uri, renderer)
        if renderer == 'gl':
            # Houdini's flipbook: set its camera and its own blurs, from the
            # camera's shutter and lens.
            render_node.parm('loppath').set(cache_node.path())
            render_node.parm('cameraprim').set(camera_path)
            flipbook_blur(context, render_node)

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
            if renderer == 'gl':
                render_node.parm('picture').set(path_str(temp_jpg_path))
                for frame_index in log_progress(render_range):
                    render_node.parm('f1').set(frame_index)
                    render_node.parm('f2').set(frame_index)
                    render_node.parm('execute').pressButton()
            else:
                self._render_viewport(
                    temp_cache_path, temp_jpg_path, camera_path, render_range, fps
                )

            # Encode mp4
            mp4.from_jpg(
                temp_jpg_path,
                render_range,
                fps,
                temp_playblast_path
            )

            # One movie per render layer, rendered and encoded before
            # anything is copied, so a failed layer leaves no half version.
            version_name = playblast_version_of(output_playblast_path)
            layer_movies = []
            for layer_name, hide_name in self._write_hide_layers(
                temp_cache_path, camera_path, layers
            ):
                layer_png_path = temp_dir_path / layer_name / 'playblast.$F4.png'
                layer_png_path.parent.mkdir(parents=True, exist_ok=True)
                self._render_viewport(
                    temp_cache_path, layer_png_path, camera_path, render_range, fps,
                    overrides=(f'./{hide_name}',), alpha=True,
                    wrapper_name=f'playblast_{layer_name}.usda',
                )
                temp_mov_path = temp_dir_path / f'{layer_name}.mov'
                mp4.from_png_alpha(layer_png_path, render_range, fps, temp_mov_path)
                layer_movies.append((
                    temp_mov_path,
                    get_layer_playblast_movie_path(
                        shot_uri, department_name, layer_name, version_name
                    ),
                ))

            # Copy to network
            output_playblast_path.parent.mkdir(exist_ok=True, parents=True)
            output_daily_path.parent.mkdir(exist_ok=True, parents=True)
            for temp_mov_path, output_mov_path in layer_movies:
                output_mov_path.parent.mkdir(exist_ok=True, parents=True)
                shutil.copyfile(temp_mov_path, output_mov_path)
            shutil.copyfile(temp_playblast_path, output_playblast_path)
            shutil.copyfile(temp_playblast_path, output_daily_path)

    def _render_layers(self, shot_uri, renderer) -> list[tuple[str, str | None]]:
        """The render layers to playblast, bottom to top; [] when Render
        Layers is off. Raises when it is on but cannot be done."""
        parm = self.native().parm('render_layers')
        if parm is None or not parm.eval():
            return []
        if renderer != 'viewport':
            raise self._error(
                'Render Layers needs the Viewport renderer: Houdini GL cannot '
                'render a transparent background.'
            )
        layers = list_channel_layers(shot_uri)
        if not layers:
            raise self._error(
                f'Render Layers is on, but {shot_uri} names no prims for any of '
                f'its channels. Add a "{CHANNEL_PRIMS_PROPERTY}" field to the '
                f'shot (or the project) in the Config Editor, mapping a channel '
                f'to a prim pattern, e.g. {{"characters": "/CHAR/*"}}.'
            )
        return layers

    def _write_hide_layers(self, cache_path, camera_path, layers):
        """Write, beside the cache, each render layer's visibility overrides
        (usd.layer_split). Yields (layer name, override file name) for the
        layers that keep something that draws; a layer with nothing to draw
        (a pattern matching nothing, or only a camera; a remainder the other
        layers claimed whole) makes no movie."""
        if not layers:
            return
        from pxr import Sdf, Usd
        from tumblepipe.pipe.usd import layer_split
        from tumblepipe.pipe.usd.render_passes import expand_pattern
        stage = Usd.Stage.Open(path_str(cache_path))
        # The same match the farm render's passes make (render_passes), so a
        # layered playblast and the render agree on what each layer holds.
        matched = {
            name: expand_pattern(stage, pattern)
            for name, pattern in layers if pattern is not None
        }
        camera = {Sdf.Path(camera_path)}
        for name, pattern in layers:
            if pattern is None:
                claimed = set().union(*matched.values()) if matched else set()
                keep = layer_split.remainder_keep(stage, claimed)
            else:
                keep = matched[name]
            if not layer_split.holds_geometry(stage, keep):
                continue
            hide_name = f'hide_{name}.usda'
            layer_split.write_hide_layer(
                layer_split.prims_to_hide(stage, keep | camera),
                cache_path.parent / hide_name,
            )
            yield name, hide_name

    def _render_viewport(
        self, cache_path, image_path, camera_path, render_range, fps,
        overrides=(), alpha=False, wrapper_name='playblast.usda',
        ):
        """Render the exported cache with our viewport renderer: husk and
        hdThPlayblast, as the farm playblast does, through a layer over the
        cache that renders only a colour buffer through ``camera_path``
        (generate_playblast_wrapper). ``overrides`` are layers over the
        cache (a render layer's hidden prims); ``alpha`` renders a
        transparent background. Shows husk's progress; cancelling it stops
        husk. Raises when a frame is missing."""
        wrapper_path = cache_path.parent / wrapper_name
        wrapper_path.write_text(
            generate_playblast_wrapper(
                f'./{cache_path.name}', camera_path, fps, tuple(overrides)
            ),
            encoding='utf-8',
        )
        width, height = self.native().parmTuple('res').eval()
        blur = (
            bool(self.parm('motion_blur').eval()),
            bool(self.parm('depth_of_field').eval()),
            int(self.parm('blur_samples').eval()),
        )
        env = viewport_env(
            dict(os.environ), Path(api.PIPELINE_PATH), hou.applicationVersion()[0],
            self.parm('shading').evalAsString(), blur, fps, alpha=alpha,
        )
        husk = Path(hou.text.expandString('$HFS')) / 'bin' / (
            'husk.exe' if os.name == 'nt' else 'husk'
        )
        command = [
            path_str(husk), *viewport_args(),
            '--settings', PLAYBLAST_SETTINGS_PRIM_PATH,
            '--resolver-context', path_str(wrapper_path),
            '--verbose', 'a1',
            '--make-output-path',
            '--no-mplay',
            '--res', str(width), str(height),
            '--frame', str(render_range.first_frame),
            '--frame-count', str(len(render_range)),
            '--frame-inc', str(render_range.step_size),
            '--output', path_str(image_path),
            path_str(wrapper_path),
        ]
        returncode, log = run_husk(command, env, len(render_range))
        missing = [
            frame for frame in render_range
            if not Path(str(image_path).replace('$F4', f'{frame:04d}')).exists()
        ]
        if returncode == 0 and not missing:
            return
        hint = ''
        if f'Unable to load render plugin: {VIEWPORT_DELEGATE}' in log:
            hint = (
                '\n\nThis TumblePipe install has no viewport renderer for this '
                'Houdini: set Renderer to Houdini GL.'
            )
        tail = '\n'.join(log.splitlines()[-15:])
        raise self._error(
            f'husk exited {returncode} having rendered '
            f'{len(render_range) - len(missing)}/{len(render_range)} frames.'
            f'{hint}\n\n{tail}'
        )

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


#: husk's progress lines at --verbose 'a': "ALF_PROGRESS 42%".
_PROGRESS = re.compile(r'ALF_PROGRESS\s+(\d+)%')


def run_husk(command: list[str], env: dict, frames: int) -> tuple[int, str]:
    """Run husk with a progress bar the artist can cancel (which stops
    husk and raises hou.OperationInterrupted). Its exit code and log."""
    lines = []
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors='replace', creationflags=flags,
    )
    try:
        with hou.InterruptableOperation(
            'Playblast', long_operation_name=f'Rendering {frames} frames',
            open_interrupt_dialog=True,
        ) as operation:
            for line in process.stdout:
                lines.append(line.rstrip())
                match = _PROGRESS.search(line)
                if match:
                    operation.updateLongProgress(int(match.group(1)) / 100.0)
            process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    return process.returncode, '\n'.join(lines)


def flipbook_blur(node, render_node) -> None:
    """Set the flipbook ROP's blurs from the node's Motion Blur, Depth of
    Field and Blur Samples (the images it averages for motion blur). Off
    unless the node turns them on."""
    render_node.parm('motionblur').set(int(node.parm('motion_blur').eval()))
    render_node.parm('motionblurframes').set(max(2, int(node.parm('blur_samples').eval())))
    render_node.parm('dof').set(int(node.parm('depth_of_field').eval()))


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