"""Render a playblast cache with TumbleView (hdThPlayblast) through husk.

Every in-session playblast renders this way: the LOP and SOP ``th::playblast``
nodes, and the live playblast. Each writes its stage to a ``cache.usd`` first;
this writes a wrapper over it that renders only a colour buffer through one
camera (``render_settings.generate_playblast_wrapper``; the shot's own Karma
render settings make husk refuse our delegate) and runs husk on it.
"""

import itertools
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import hou

from tumblepipe.api import api, path_str
from tumblepipe.apps.viewport_renderer import (
    VIEWPORT_DELEGATE,
    viewport_args,
    viewport_env,
)
from tumblepipe.pipe.usd.render_settings import (
    PLAYBLAST_SETTINGS_PRIM_PATH,
    generate_playblast_wrapper,
)


class TumbleViewError(Exception):
    """husk failed or left frames unrendered; the message says which and why."""


@dataclass(frozen=True)
class Look:
    """How the frames look: ``shading`` (specs.playblast.PLAYBLAST_SHADINGS),
    motion blur, depth of field, blur samples, a transparent background, and
    the GPU (None: the delegate's own default, the CPU under husk)."""
    shading: str = 'lit'
    motion_blur: bool = False
    depth_of_field: bool = False
    blur_samples: int = 16
    alpha: bool = False
    gpu: bool | None = None


def write_wrapper(cache_path: Path, camera_path: str, fps: float,
                  overrides=(), name: str = 'playblast.usda',
                  time_offset: int = 0) -> Path:
    """The wrapper layer beside ``cache_path``: the cache under
    ``overrides`` (layers over it, e.g. a render layer's hidden prims),
    rendering a colour buffer through ``camera_path``. A cache written on a
    shared-timeline Multi's frames is moved back by ``time_offset``, so the
    wrapper's frames are the shot's own."""
    wrapper_path = cache_path.parent / name
    wrapper_path.write_bytes(
        generate_playblast_wrapper(
            f'./{cache_path.name}', camera_path, fps, tuple(overrides),
            time_offset,
        ).encode('utf-8')
    )
    return wrapper_path


def husk_path() -> Path:
    return Path(hou.text.expandString('$HFS')) / 'bin' / (
        'husk.exe' if os.name == 'nt' else 'husk'
    )


def husk_command(wrapper_path: Path, image_path: Path, res: tuple[int, int],
                 frames: list[int]) -> list[str]:
    """husk rendering ``frames`` of ``wrapper_path`` at ``res`` to
    ``image_path`` (a ``$F4`` pattern). Evenly spaced frames go as one range;
    any other list (the live playblast's current-frame-first order) as
    ``--frame-list``."""
    width, height = res
    command = [
        path_str(husk_path()), *viewport_args(),
        '--settings', PLAYBLAST_SETTINGS_PRIM_PATH,
        '--resolver-context', path_str(wrapper_path),
        '--verbose', 'a1',
        '--make-output-path',
        '--no-mplay',
        '--res', str(width), str(height),
    ]
    command += frame_args(frames)
    command += ['--output', path_str(image_path), path_str(wrapper_path)]
    return command


def frame_args(frames: list[int]) -> list[str]:
    """husk's frame arguments for ``frames`` (non-empty), in that order."""
    if not frames:
        raise ValueError('no frames to render')
    steps = {b - a for a, b in itertools.pairwise(frames)}
    if len(frames) == 1 or (len(steps) == 1 and next(iter(steps)) > 0):
        step = next(iter(steps)) if steps else 1
        return ['--frame', str(frames[0]), '--frame-count', str(len(frames)),
                '--frame-inc', str(step)]
    # husk's --frame-list takes the frames as separate arguments, up to the
    # next option (one space-joined argument is refused as invalid).
    return ['--frame-list', *(str(f) for f in frames)]


def husk_env(fps: float, look: Look) -> dict:
    """This process's environment with the delegate registered and ``look``
    set."""
    env = viewport_env(
        dict(os.environ), Path(api.PIPELINE_PATH), hou.applicationVersion()[0],
        look.shading, (look.motion_blur, look.depth_of_field, look.blur_samples),
        fps, alpha=look.alpha,
    )
    if look.gpu is not None:
        env['TH_PLAYBLAST_GPU'] = '1' if look.gpu else '0'
    return env


def frame_path(image_path: Path, frame: int) -> Path:
    return Path(str(image_path).replace('$F4', f'{frame:04d}'))


def render(cache_path: Path, image_path: Path, camera_path: str,
           frames: list[int], fps: float, res: tuple[int, int],
           look: Look | None = None, overrides=(),
           wrapper_name: str = 'playblast.usda', time_offset: int = 0) -> None:
    """Render ``frames`` (the shot's own) of the cache through
    ``camera_path`` to ``image_path``, in this session, with a progress bar
    the artist can cancel (which stops husk and raises
    hou.OperationInterrupted). A cache on a shared-timeline Multi's frames
    is read ``time_offset`` later (write_wrapper). Raises TumbleViewError
    when husk fails or a frame is missing."""
    look = look or Look()
    wrapper_path = write_wrapper(
        cache_path, camera_path, fps, overrides, wrapper_name, time_offset
    )
    command = husk_command(wrapper_path, image_path, res, frames)
    returncode, log = run_husk(command, husk_env(fps, look), len(frames))
    missing = [f for f in frames if not frame_path(image_path, f).exists()]
    if returncode == 0 and not missing:
        return
    raise TumbleViewError(failure_message(returncode, log, len(frames), len(missing)))


def failure_message(returncode: int, log: str, frames: int, missing: int) -> str:
    hint = ''
    if f'Unable to load render plugin: {VIEWPORT_DELEGATE}' in log:
        hint = (
            '\n\nThis TumblePipe install has no TumbleView renderer for this '
            'Houdini version.'
        )
    tail = '\n'.join(log.splitlines()[-15:])
    return (
        f'husk exited {returncode} having rendered {frames - missing}/{frames} '
        f'frames.{hint}\n\n{tail}'
    )


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
