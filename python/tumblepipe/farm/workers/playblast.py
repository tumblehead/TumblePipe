"""Render a shot's collapsed stage as a preview, with our viewport delegate or
Karma XPU, and encode it into the versioned playblast mp4 and the rolling
daily."""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import json
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.app import CommandFailed
from tumblepipe.apps.houdini import HOUDINI_VERSION_ENV, Husk
from tumblepipe.apps import mp4
from tumblepipe.apps.viewport_renderer import VIEWPORT_DELEGATE, viewport_args, viewport_env
from tumblepipe.pipe import review_events
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_base_env, print_env, job_data_dir
from tumblepipe.farm.frames import frame_file, HOUDINI_FRAME_TOKEN
from tumblepipe.farm.specs import playblast as playblast_spec
from tumblepipe.farm.worker import ensure_dir, error, headline
from tumblepipe.pipe.paths.review import release_playblast

# Karma XPU as a playblast, by default a clay look instead of the scene's
# shading. It draws through CUDA/OptiX, so it needs a GPU but no GL context
# or display.
#
# There is deliberately no Storm (GL) option. It was the default until 1.57,
# and on real shots it never finished: HideAndReek/010 on maria-2060 and 030
# on judas both sat on frame 1 for over ten minutes, then husk died with
# 0xE06D7363. XPU rendered 030's first frame in 72 s, most of it scene load.
# Houdini's own GL look stays with the in-session th::playblast (Renderer:
# Houdini GL); the farm draws the viewport look with our delegate.
#
# --timelimit-image stops a runaway frame at XPU_FRAME_SECONDS and keeps the
# partial image, so one heavy frame cannot hold the whole shot.
XPU_FRAME_SECONDS = 120


# husk checks a licence out whatever draws the frames, our viewport delegate
# included: with no --check-licenses it asks for Houdini Engine, Core or FX
# first (husk --list-license-checks), seats shared with artists, and
# "Karma Renderer" only after them. QA 2026-11-10: a TumbleView farm
# playblast failed "No licenses could be found" on a worker without a seat.
LICENCE_ARGS = ['--check-licenses', 'Karma Renderer']


def husk_renderer_args(look: dict) -> list[str]:
    """husk's renderer arguments for ``look``: our viewport delegate or
    Karma XPU, either way on the Karma Renderer licence, never a seat."""
    if playblast_spec.look_renderer(look) == 'viewport':
        return [*LICENCE_ARGS, *viewport_args()]
    return [*LICENCE_ARGS, '--allowed-procedurals', 'all', *xpu_args(look)]


def xpu_args(look: dict) -> list[str]:
    """husk's arguments for ``look`` (``specs.playblast.valid_look``).

    The light itself is authored by the collapse for 'dome'
    (``render_settings.apply_playblast_dome``): the scene's lights are already
    off in the input, so husk must not disable scene lights, which would take
    the dome with them. For 'headlight', --autoheadlight is the load-bearing
    flag: --disable-scene-lights alone leaves no light at all and every frame
    renders black; with it husk puts a light on the camera, and --ao-* shades
    it with occlusion so form reads. For 'scene' it only fills in for a stage
    with no lights.
    """
    args = [
        '--engine', 'xpu',
        '--threads', '-1',
        '--pixel-samples', str(look['samples']),
    ]
    if not look['materials']:
        args.append('--disable-scene-materials')
    if look['light'] == 'headlight':
        args += [
            '--disable-scene-lights',
            '--autoheadlight',
            '--headlight', 'distant',
            '--ao-samples', '4',
            '--ao-distance', '2',
        ]
    elif look['light'] == 'scene':
        args.append('--autoheadlight')
    return args + [
        '--disable-motionblur',
        '--timelimit', str(XPU_FRAME_SECONDS),
        '--timelimit-image',
    ]


# What a failed render's husk log usually says, and what each means.
HUSK_LOG_HINTS = (
    'Read the husk log above before blaming the worker: "All AOVs bypassed '
    'or missing" means the stage asked for a render var the preview cannot '
    'fill, which is a submission problem, not a machine one. "No licenses '
    'could be found" is licensing, not the GPU. "Unable to load render '
    'plugin: BRAY_HdKarmaXPU" or a CUDA/OptiX error means this worker has no '
    'usable NVIDIA GPU for Karma XPU. '
    f'"Unable to load render plugin: {VIEWPORT_DELEGATE}" means this '
    'TumblePipe install has no plugins/houdini<major>/ delegate build for '
    "the job's Houdini."
)


def render_problem(
    husk_returncode: int, render_range: BlockRange, jpg_path: Path,
) -> str | None:
    """Why the husk render of ``render_range`` to ``jpg_path`` (with
    ``HOUDINI_FRAME_TOKEN``) cannot become the playblast, or None when it
    can: husk exited 0 and wrote every frame of the range.

    A playblast missing frames is refused, not encoded: the encoder would
    hold the frame before each hole, and a shot's playblast that freezes
    where husk died reads as the animation, not as a failure.
    """
    missing = [
        frame for frame in render_range
        if not frame_file(jpg_path, frame, HOUDINI_FRAME_TOKEN).exists()
    ]
    if husk_returncode == 0 and not missing:
        return None
    rendered = len(render_range) - len(missing)
    shown = ', '.join(map(str, missing[:10])) + (', ...' if len(missing) > 10 else '')
    detail = f'; missing frames {shown}' if missing else ''
    return (
        f'Playblast failed: husk exited {husk_returncode} having rendered {rendered}/'
        f'{len(render_range)} frames{detail}. {HUSK_LOG_HINTS}'
    )


def main(
    render_range: BlockRange,
    fps: int,
    resolution: tuple[int, int],
    look: dict,
    input_path: Path,
    output_paths: list[Path]
    ) -> int:

    # XPU reads the OCIO config just like the render worker does.
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    husk = Husk()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths -- husk substitutes $F4 per frame; mp4.from_jpg globs the
        # stack back out of this directory.
        temp_jpg_path = temp_path / 'jpg' / f'playblast.{HOUDINI_FRAME_TOKEN}.jpg'
        temp_jpg_path.parent.mkdir(parents=True, exist_ok=True)
        temp_mp4_path = temp_path / 'playblast.mp4'

        # Get and print environment for debugging
        env = get_base_env()
        print_env()

        # Render the whole range in one husk invocation (see xpu_args).
        # No --camera: husk resolves the render camera from the staged stage's
        # RenderSettings prim, exactly as the Karma render worker relies on.
        # --resolver-context is required for the custom ArResolver (entity:/
        # storage: URIs in the staged USD) to work under husk.
        #
        # -V a2 is what makes a wrong-looking playblast debuggable at all.
        # Without it husk renders silently, and the one line that says which
        # camera the frames were shot through --
        #
        #   Using stage default settings: /scene/Render/rendersettings
        #   Defaulting to use settings found at /Render/rendersettings
        #   No camera in render settings, defaulting to <some camera>
        #
        # -- never reaches the Deadline log. The third of those is the
        # "renders the wrong view" failure: husk found no settings prim and
        # picked the first camera on the stage. 'a' also emits ALF_PROGRESS,
        # which Deadline reads as task progress.
        #
        # --check-licenses 'Karma Renderer' (LICENCE_ARGS) is what the render
        # worker passes, and both playblast renderers need it: without it
        # husk takes a Houdini Core/FX seat (shared with artists) and fails
        # "No licenses could be found" while Karma licences sit free.
        headline('Rendering playblast frames')
        width, height = resolution
        renderer = playblast_spec.look_renderer(look)
        print(f'Renderer: {renderer}')
        if renderer == 'viewport':
            houdini_major = int(env[HOUDINI_VERSION_ENV].split('.')[0])
            shading = playblast_spec.look_shading(look)
            blur = playblast_spec.look_blur(look)
            lighting = playblast_spec.look_lighting(look)
            cull = playblast_spec.look_cull(look)
            transparency = playblast_spec.look_transparency(look)
            print(f'Shading: {shading}')
            print(f'Lighting: {lighting}')
            print(f'Culling: {cull}')
            print(f'Transparency: {transparency}')
            print('Blur: motion {}, depth of field {}, {} samples'.format(*blur))
            env = viewport_env(env, Path(api.PIPELINE_PATH), houdini_major, shading,
                               blur, fps, lighting=lighting, cull=cull,
                               transparency=transparency)
        renderer_args = husk_renderer_args(look)
        # husk can exit 0 having written nothing ("All AOVs bypassed"), and
        # non-zero having written part of the range: render_problem checks
        # both.
        husk_returncode = 0
        try:
            husk.run(
                input_path,
                [
                    '--resolver-context', path_str(input_path),
                    *renderer_args,
                    '--verbose', 'a2',
                    '--make-output-path',
                    '--no-mplay',
                    '--res', str(width), str(height),
                    '--frame', str(render_range.first_frame),
                    '--frame-count', str(len(render_range)),
                    '--frame-inc', str(render_range.step_size),
                    '--output', path_str(temp_jpg_path)
                ],
                env=env
            )
        except CommandFailed as failed:
            husk_returncode = failed.returncode
        problem = render_problem(husk_returncode, render_range, temp_jpg_path)
        if problem is not None:
            return error(problem)
        print(f'Rendered {len(render_range)} frames')

        # Encode the mp4. Every rendered frame is there; a stepped range's
        # in-between frames hold the rendered frame before them.
        headline('Encoding mp4')
        mp4.from_jpg(
            temp_jpg_path, render_range, fps, temp_mp4_path,
            repeat_missing_frames=False,
        )
        if not temp_mp4_path.exists():
            return error(f'Failed to encode mp4: {temp_mp4_path}')

        # Copy to every network destination (versioned playblast + rolling daily)
        headline('Copying files to network')
        for output_path in output_paths:
            print(f'Copying file: {output_path}')
            ensure_dir(output_path.parent)
            shutil.copyfile(temp_mp4_path, output_path)

        # Verify copies landed
        for output_path in output_paths:
            if not output_path.exists():
                return error(f'Output not copied: {output_path}')
        # The versioned movie is there: its submit-time reservation goes.
        for output_path in output_paths:
            release_playblast(output_path)
        # This machine's player resolves the shot again (a farm worker's
        # journal has no reader).
        review_events.announce_written(output_paths)

    print('Success')
    return 0


def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Playblast the configured range (the one Deadline task spans it)."""
    headline('Config')
    print(json.dumps(config, indent=4))

    # Input USD (bundled into the job data dir, addressed relative to it)
    input_path = job_data_dir() / config['input_path']
    if not input_path.exists():
        return error(f'Input path not found: {input_path}')

    # Print input USD content for debugging (only for ASCII .usda files)
    if input_path.suffix == '.usda':
        headline('Input USD')
        print(input_path.read_text())

    return main(
        BlockRange(
            config['first_frame'],
            config['last_frame'],
            config['step_size']
        ),
        config['fps'],
        tuple(config['res']),
        config['look'],
        input_path,
        list(map(Path, config['output_paths']))
    )


if __name__ == '__main__':
    sys.exit(worker.run(playblast_spec.is_valid_context, from_config))
