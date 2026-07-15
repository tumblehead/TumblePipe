from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil
import json
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    path_str,
    local_path,
    to_windows_path,
    api
)
from tumblepipe.util.io import load_json
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Husk
from tumblepipe.apps import mp4
from tumblepipe.farm.tasks.env import get_base_env, print_env, job_data_dir
from tumblepipe.farm.tasks.playblast import _spec

# The one GL Hydra delegate husk can actually load headless. HD_HoudiniRenderer
# (the delegate the interactive flipbook uses) fails with "Unable to load render
# plugin" under husk, so a farm playblast is Storm-shaded, not a pixel-identical
# copy of the viewport flipbook. Verified with `husk --list-renderers`.
STORM_DELEGATE = 'HdStormRendererPlugin'


def _headline(title):
    print(f' {title} '.center(80, '='))


def _error(msg):
    logging.error(msg)
    return 1


def _get_frame_path(frame_path: Path, frame_index: int) -> Path:
    frame_name = str(frame_index).zfill(4)
    return frame_path.parent / frame_path.name.replace('$F4', frame_name)


def main(
    render_range: BlockRange,
    fps: int,
    resolution: tuple[int, int],
    input_path: Path,
    output_paths: list[Path]
    ) -> int:

    # Storm reads the OCIO config just like the Karma path does.
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    husk = Husk()

    # Open a temporary directory
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths -- husk substitutes $F4 per frame; mp4.from_jpg globs the
        # stack back out of this directory.
        temp_jpg_path = temp_path / 'jpg' / 'playblast.$F4.jpg'
        temp_jpg_path.parent.mkdir(parents=True, exist_ok=True)
        temp_mp4_path = temp_path / 'playblast.mp4'

        # Get and print environment for debugging
        env = get_base_env(api)
        print_env()

        # Render the whole range in one husk invocation with the GL delegate.
        # No --camera: husk resolves the render camera from the staged stage's
        # RenderSettings prim, exactly as the Karma render worker relies on.
        # --resolver-context is required for the custom ArResolver (entity:/
        # storage: URIs in the staged USD) to work under husk.
        _headline('Rendering playblast frames (Hydra Storm)')
        width, height = resolution
        husk.run(
            to_windows_path(input_path),
            [
                '--resolver-context', path_str(to_windows_path(input_path)),
                '--renderer', STORM_DELEGATE,
                '--gpu',
                '--make-output-path',
                '--no-mplay',
                '--res', str(width), str(height),
                '--frame', str(render_range.first_frame),
                '--frame-count', str(len(render_range)),
                '--frame-inc', str(render_range.step_size),
                '--output', path_str(to_windows_path(temp_jpg_path))
            ],
            env=env
        )

        # Check that the frames were generated
        rendered = 0
        for frame_index in render_range:
            frame_path = _get_frame_path(temp_jpg_path, frame_index)
            if local_path(frame_path).exists():
                rendered += 1
        if rendered == 0:
            return _error(
                'Playblast produced no frames -- the GL (Storm) delegate likely '
                'has no usable GPU/GL context on this worker. Confirm the '
                'playblast farm group has GL-capable, non-headless workers.'
            )
        print(f'Rendered {rendered}/{len(render_range)} frames')

        # Encode the mp4 (fills any missing frames by repeat, like the local HDA)
        _headline('Encoding mp4')
        mp4.from_jpg(temp_jpg_path, render_range, fps, temp_mp4_path)
        if not temp_mp4_path.exists():
            return _error(f'Failed to encode mp4: {temp_mp4_path}')

        # Copy to every network destination (versioned playblast + rolling daily)
        _headline('Copying files to network')
        for output_path in output_paths:
            output_path = local_path(output_path)
            print(f'Copying file: {output_path}')
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(temp_mp4_path, output_path)

        # Verify copies landed
        for output_path in output_paths:
            if not local_path(output_path).exists():
                return _error(f'Output not copied: {output_path}')

    print('Success')
    return 0


def _run(config: dict) -> int:

    # Print config
    _headline('Config')
    print(json.dumps(config, indent=4))

    # Frame range
    first_frame = config['first_frame']
    last_frame = config['last_frame']
    step_size = config['step_size']
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame, step_size)

    # Input USD (bundled into the job data dir, addressed relative to it)
    input_path = job_data_dir() / config['input_path']
    if not input_path.exists():
        return _error(f'Input path not found: {input_path}')

    # Print input USD content for debugging (only for ASCII .usda files)
    if input_path.suffix == '.usda':
        _headline('Input USD')
        with open(input_path, 'r') as f:
            print(f.read())

    output_paths = [Path(output_path) for output_path in config['output_paths']]

    return main(
        render_range,
        config['fps'],
        tuple(config['res']),
        input_path,
        output_paths
    )


def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    # A playblast is a single monolithic task, so the frame range Deadline
    # appends is the full range already in the config; parse-and-ignore it to
    # keep the standard worker CLI shape.
    parser.add_argument('start_frame', type=int, nargs='?')
    parser.add_argument('end_frame', type=int, nargs='?')
    args = parser.parse_args()

    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _spec.is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')

    return _run(config)


if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())
