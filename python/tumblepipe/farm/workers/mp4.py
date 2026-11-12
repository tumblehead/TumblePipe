"""Encode a frame sequence into mp4s (the layer and slapcomp reviews)."""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import BlockRange
from tumblepipe.apps import exr, mp4
from tumblepipe.pipe import review_events
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.frames import frame_file
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import mp4 as mp4_spec
from tumblepipe.farm.worker import ensure_dir, error


def main(
    render_range,
    fps: int,
    input_path: Path,
    output_paths: list[Path],
    back_to_back: bool = False
    ) -> int:
    """Encode ``input_path``'s frames over ``render_range`` into every one of
    ``output_paths``, replacing what is there (a daily is overwritten by
    each new version).

    ``back_to_back`` (a multi-range render's frames, #84): ``render_range``
    is the list of frames, encoded one after the other with no gap filled
    in, each frame's number burnt into its picture so the jumps show."""

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Print environment variables for debugging
    print_env()

    # Every input frame must be there
    for frame_index in render_range:
        input_frame_path = frame_file(input_path, frame_index)
        if input_frame_path.exists(): continue
        return error(f'Input frame not found: {input_frame_path}')

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Convert EXRs to JPGs
        temp_input_path = temp_path / 'temp.####.jpg'
        size = None
        if back_to_back:
            info = exr.get_image_info(frame_file(input_path, render_range[0]))[0]
            size = info.width, info.height
        for frame_index in render_range:
            exr.to_jpeg(
                frame_file(input_path, frame_index),
                frame_file(temp_input_path, frame_index),
                None if size is None else exr.burn_in_args(str(frame_index), *size)
            )

        # Encode to mp4
        temp_output_path = temp_path / 'temp.mp4'
        if back_to_back:
            mp4.from_jpg_frames(
                temp_input_path,
                render_range,
                fps,
                temp_output_path
            )
        else:
            mp4.from_jpg(
                temp_input_path,
                render_range,
                fps,
                temp_output_path
            )

        # Check that the temporary output exists
        if not temp_output_path.exists():
            return error(f'Temp output not generated: {temp_output_path}')

        # Copy to output paths
        for output_path in output_paths:
            ensure_dir(output_path.parent)
            shutil.copyfile(temp_output_path, output_path)

    # Check that all the outputs exist
    for output_path in output_paths:
        if output_path.exists(): continue
        return error(f'Output not generated: {output_path}')
    review_events.announce_written(output_paths)

    # Done
    print('Success')
    return 0


def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Encode the configured range (the one Deadline task spans it), or
    a multi-range render's frames back to back."""
    ranges = frame_ranges.from_config(config)
    if ranges is not None:
        return main(
            frame_ranges.frames(ranges, config['step_size']),
            config['fps'],
            Path(config['input_path']),
            list(map(Path, config['output_paths'])),
            back_to_back = True
        )
    return main(
        BlockRange(
            config['first_frame'],
            config['last_frame'],
            config['step_size']
        ),
        config['fps'],
        Path(config['input_path']),
        list(map(Path, config['output_paths']))
    )


if __name__ == '__main__':
    sys.exit(worker.run(mp4_spec.is_valid_context, from_config))
