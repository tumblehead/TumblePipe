from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil

from tumblehead.api import (
    path_str,
    fix_path,
    to_wsl_path,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import BlockRange
from tumblehead.apps import wsl

api = default_client()

FFMPEG_PATH = '/usr/bin/ffmpeg'
    
def from_jpg(
    framestack_path: Path,
    frame_range: BlockRange,
    framerate: int,
    output_mp4_path: Path,
    repeat_missing_frames: bool = True
    ):
    
    # List frame stack
    framestack_name, _, framestack_suffix = framestack_path.name.split('.')
    assert framestack_suffix in ['jpg', 'jpeg'], (
        'Invalid framestack suffix: '
        f'{framestack_suffix}'
    )
    input_frames = list(sorted(framestack_path.parent.glob(
        f'{framestack_name}.*.{framestack_suffix}'
    )))

    # Find missing frames
    frame_count = (frame_range.first_frame - frame_range.last_frame) + 1
    available_frame_indices = {
        int(frame.name.split('.')[-2])
        for frame in input_frames
    }
    first_available_frame_index = min(available_frame_indices)
    if (not repeat_missing_frames and
        len(available_frame_indices) != frame_count):
        missing_frame_indices = list(sorted(set(frame_range) - available_frame_indices))
        raise ValueError(f'Missing frames: {missing_frame_indices}')

    # Open temporary workspace
    base_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)
        
        # Copy over framestack
        for input_frame in input_frames:
            frame_index = int(input_frame.name.split('.')[-2])
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.jpg'
            logging.info(f'Copying to {temp_frame_path}')
            shutil.copyfile(input_frame, temp_frame_path)
        
        # Pre-fill in missing frames using next frame as fill in
        first_available_frame = (
            temp_dir_path /
            f'frame.{first_available_frame_index:04d}.jpg'
        )
        previous_frame = first_available_frame
        for frame_index in range(frame_range.first_frame, first_available_frame_index):
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.jpg'
            logging.info(f'Prefilling in {temp_frame_path}')
            shutil.copyfile(previous_frame, temp_frame_path)
        
        # Post-fill in missing frames using the previous frame as fill in
        for frame_index in range(
            first_available_frame_index + 1,
            frame_range.last_frame + 1):
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.jpg'
            if frame_index in available_frame_indices:
                previous_frame = temp_frame_path
            else:
                logging.info(f'Postfilling in {temp_frame_path}')
                shutil.copyfile(previous_frame, temp_frame_path)
        
        # Verify that all frames are present
        for frame_index in frame_range:
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.jpg'
            if temp_frame_path.exists(): continue
            raise RuntimeError(
                'Failed to fill in missing frame: '
                f'{temp_frame_path}'
            )
        
        # Combine JPEGs into video
        temp_framestack_path = temp_dir_path / 'frame.%04d.jpg'
        temp_output_mp4_path = temp_dir_path / 'output.mp4'
        wsl.run([
            FFMPEG_PATH,
            '-framerate', str(framerate),
            '-pattern_type', 'sequence',
            '-start_number', str(frame_range.first_frame),
            '-i', path_str(to_wsl_path(temp_framestack_path)),
            '-frames:v', str(frame_count),
            '-c:v', 'libx264',
            '-crf', '17',
            '-pix_fmt', 'yuv420p',
            path_str(to_wsl_path(temp_output_mp4_path))
        ])
        output_mp4_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_mp4_path, output_mp4_path)

def scale(
    input_mp4_path: Path,
    output_mp4_path: Path,
    scale_factor: int
    ):

    # Check if the input file exists
    if not input_mp4_path.exists():
        raise FileNotFoundError(
            f'Input MP4 file does not exist: {input_mp4_path}'
        )
    
    # Open temporary workspace
    base_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)

        # Temporary paths
        temp_output_mp4_path = temp_dir_path / 'output.mp4'
        temp_output_mp4_path.parent.mkdir(exist_ok=True, parents=True)

        # Scale the video
        scale_expression = (
            f'*{2**scale_factor}'
            if scale_factor >= 0 else
            f'/{2**(-scale_factor)}'
        )
        wsl.run([
            FFMPEG_PATH, 
            '-i', path_str(input_mp4_path), 
            '-vf', (
                'scale='
                f'iw{scale_expression}:'
                f'ih{scale_expression}'
            ),
            path_str(to_wsl_path(temp_output_mp4_path))
        ])
        output_mp4_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_mp4_path, output_mp4_path)