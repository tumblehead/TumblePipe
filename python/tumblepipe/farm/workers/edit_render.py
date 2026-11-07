"""Render a published edit version into a movie (Render Edit).

The editor renders it headless, ``th_player --render``: every frame
composited and shown through the project's OCIO config as a PNG, the sound
mixed into one WAV, and ``render.json`` saying what it wrote. Houdini's
hffmpeg encodes them (``apps.mp4.from_edit_frames``) into
``render:/edits/<name>/<name>_edit_vNNNN.mp4``. Everything the edit plays is
in the project, so a worker needs nothing of its own but the player, which
the package ships in ``bin/<platform>/``.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import subprocess
import sys

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import BlockRange
from tumblepipe.apps import mp4
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.specs import edit_render as edit_render_spec
from tumblepipe.farm.worker import ensure_dir, error, headline
from tumblepipe.pipe.review_playlist import find_player, project_ocio, render_arguments


def render_command(player: Path, edit_uri: Uri, version: str, out_dir: Path) -> list[str]:
    """The player's command line that renders ``edit_uri`` at ``version``
    into ``out_dir``, in the active project and its OCIO config (``$OCIO``)."""
    return render_arguments(
        player, path_str(api.PROJECT_PATH), project_ocio(),
        edit_uri, version, out_dir,
    )


def read_render_info(out_dir: Path) -> dict:
    """The ``render.json`` the player wrote into ``out_dir``; raises when it
    is missing or not what ``specs.edit_render.is_valid_render_info`` reads."""
    info_path = out_dir / edit_render_spec.RENDER_INFO_NAME
    if not info_path.is_file():
        raise ValueError(f'The player wrote no {info_path.name} in {out_dir}')
    info = load_json(info_path)
    if not edit_render_spec.is_valid_render_info(info):
        raise ValueError(f'The player wrote an unreadable {info_path.name}: {info!r}')
    return info


def missing_frames(out_dir: Path, frame_count: int) -> list[Path]:
    """The frames ``0..frame_count-1`` the player should have written and did not."""
    return [
        path for path in (
            out_dir / (edit_render_spec.FRAMES_PATTERN % index)
            for index in range(frame_count)
        )
        if not path.is_file()
    ]


def main(edit_uri: Uri, version: str, output_path: Path) -> int:
    """Render version ``version`` of the edit ``edit_uri`` and encode it to
    ``output_path``, replacing what is there."""

    # The OCIO config the frames are shown through, and the player
    try:
        ocio = project_ocio()
        player = find_player()
    except (ValueError, FileNotFoundError) as reason:
        return error(str(reason))

    # Print environment variables for debugging
    print_env()
    print(f'  player={player}')
    print(f'  ocio={ocio}')

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        render_path = temp_path / 'render'
        render_path.mkdir()

        # Render the frames and the sound. The player prints its progress
        # and, on failure, the reason, into this task's log. It runs from its
        # own folder: the FFmpeg libraries it loads are beside it.
        headline(f'Rendering {edit_uri}@{version}')
        command = render_command(player, edit_uri, version, render_path)
        print(' '.join(command), flush=True)
        sys.stderr.flush()
        completed = subprocess.run(
            command,
            cwd = str(player.parent),
            stdin = subprocess.DEVNULL,
            check = False
        )
        if completed.returncode != 0:
            return error(f'The player failed to render {edit_uri}@{version} (exit code {completed.returncode})')

        # What it wrote
        try:
            info = read_render_info(render_path)
        except ValueError as reason:
            return error(str(reason))
        missing = missing_frames(render_path, info['frames'])
        if missing:
            return error(f'The player left out {len(missing)} frames, first {missing[0]}')
        audio_path = None
        if info['audio'] is not None:
            audio_path = render_path / info['audio']
            if not audio_path.is_file():
                return error(f'The player wrote no sound file: {audio_path}')

        # Encode to mp4
        headline('Encoding')
        temp_output_path = temp_path / 'edit.mp4'
        mp4.from_edit_frames(
            render_path / edit_render_spec.FRAMES_PATTERN,
            audio_path,
            info['frames'],
            info['fps'],
            info['start_timecode'],
            temp_output_path
        )
        if not temp_output_path.exists():
            return error(f'Temp output not generated: {temp_output_path}')

        # Copy to the output path
        ensure_dir(output_path.parent)
        shutil.copyfile(temp_output_path, output_path)

    if not output_path.exists():
        return error(f'Output not generated: {output_path}')

    # Done
    print('Success')
    return 0


def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Render the configured edit version (the one Deadline task stands for it)."""
    return main(
        Uri.parse(config['edit_uri']),
        config['version'],
        Path(config['output_path'])
    )


if __name__ == '__main__':
    sys.exit(worker.run(edit_render_spec.is_valid_context, from_config))
