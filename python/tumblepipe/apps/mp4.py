from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import BlockRange
from tumblepipe.apps import houdini

# Houdini's hffmpeg ships libopenh264 (not libx264); its quality rate-control
# mode keeps the notify "shrink to fit Discord" path shrinking with resolution.
# Output is standard yuv420p H.264, universally playable. -q:v is tunable.
_H264_ENCODE = ['-c:v', 'libopenh264', '-rc_mode', 'quality', '-q:v', '22']
    
def from_jpg(
    framestack_path: Path,
    frame_range: BlockRange,
    framerate: int,
    output_mp4_path: Path,
    repeat_missing_frames: bool = True
    ):
    """Encode a JPG sequence (``name.$F4.jpg``) as an H.264 mp4."""
    _encode_sequence(
        framestack_path, ('jpg', 'jpeg'), frame_range, framerate,
        output_mp4_path, repeat_missing_frames,
        ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', *_H264_ENCODE, '-pix_fmt', 'yuv420p'],
    )


# A render layer's playblast movie (paths.review.get_layer_playblast_movie_path):
# QuickTime Animation (qtrle) in a .mov, so it keeps the frames' alpha
# (H.264 has none). Lossless, straight alpha. Of the alpha codecs Houdini's
# hffmpeg writes (png, qtrle; no prores_ks), qtrle is the one the player's
# own FFmpeg decodes: its PNG decoder needs zlib, which that build leaves out.
# -g 1: every frame a keyframe. qtrle otherwise codes a frame as changes
# to the one before, and the player decodes a stack's layers by seeking
# straight to each frame.
_ALPHA_MOVIE_ENCODE = ['-c:v', 'qtrle', '-g', '1', '-pix_fmt', 'argb']


def from_png_alpha(
    framestack_path: Path,
    frame_range: BlockRange,
    framerate: int,
    output_mov_path: Path,
    repeat_missing_frames: bool = True
    ):
    """Encode a PNG sequence (``name.$F4.png``) with alpha as a QuickTime
    Animation .mov that keeps the alpha."""
    _encode_sequence(
        framestack_path, ('png',), frame_range, framerate,
        output_mov_path, repeat_missing_frames, _ALPHA_MOVIE_ENCODE,
    )


def _encode_sequence(
    framestack_path: Path,
    suffixes: tuple[str, ...],
    frame_range: BlockRange,
    framerate: int,
    output_path: Path,
    repeat_missing_frames: bool,
    encode_args: list[str],
    ):
    """Encode the frames ``framestack_path`` names (``name.*.<suffix>``) as
    one movie spanning first..last frame, each missing frame filled with the
    nearest rendered one before it (the first rendered one before that)."""

    # List frame stack
    framestack_name, _, framestack_suffix = framestack_path.name.split('.')
    assert framestack_suffix in suffixes, (
        'Invalid framestack suffix: '
        f'{framestack_suffix}'
    )
    input_frames = list(sorted(framestack_path.parent.glob(
        f'{framestack_name}.*.{framestack_suffix}'
    )))

    # Find missing frames
    # frame_count is the number of *rendered* frames (step-aware) and is only
    # used for the missing-frame check below. The gap-fill logic further down
    # expands the frames into a contiguous 1-per-frame sequence spanning
    # first_frame..last_frame, so the encoder must be told to emit the full
    # contiguous span, not the stepped count (otherwise a step>1 render yields
    # an mp4 truncated to the front 1/step_size of the range).
    frame_count = len(frame_range)
    span_frame_count = frame_range.last_frame - frame_range.first_frame + 1
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
    base_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)
        
        # Copy over framestack
        for input_frame in input_frames:
            frame_index = int(input_frame.name.split('.')[-2])
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.{framestack_suffix}'
            logging.info(f'Copying to {temp_frame_path}')
            shutil.copyfile(input_frame, temp_frame_path)
        
        # Pre-fill in missing frames using next frame as fill in
        first_available_frame = (
            temp_dir_path /
            f'frame.{first_available_frame_index:04d}.{framestack_suffix}'
        )
        previous_frame = first_available_frame
        for frame_index in range(frame_range.first_frame, first_available_frame_index):
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.{framestack_suffix}'
            logging.info(f'Prefilling in {temp_frame_path}')
            shutil.copyfile(previous_frame, temp_frame_path)
        
        # Post-fill in missing frames using the previous frame as fill in
        for frame_index in range(
            first_available_frame_index + 1,
            frame_range.last_frame + 1):
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.{framestack_suffix}'
            if frame_index in available_frame_indices:
                previous_frame = temp_frame_path
            else:
                logging.info(f'Postfilling in {temp_frame_path}')
                shutil.copyfile(previous_frame, temp_frame_path)
        
        # Verify that all frames are present
        for frame_index in frame_range:
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.{framestack_suffix}'
            if temp_frame_path.exists(): continue
            raise RuntimeError(
                'Failed to fill in missing frame: '
                f'{temp_frame_path}'
            )
        
        # Combine the frames into a movie
        temp_framestack_path = temp_dir_path / f'frame.%04d.{framestack_suffix}'
        temp_output_path = temp_dir_path / f'output{output_path.suffix}'
        houdini.FFmpeg().run([
            '-y',
            '-framerate', str(framerate),
            '-pattern_type', 'sequence',
            '-start_number', str(frame_range.first_frame),
            '-i', path_str(temp_framestack_path),
            '-frames:v', str(span_frame_count),
            *encode_args,
            path_str(temp_output_path)
        ])
        output_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_path, output_path)

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
    base_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)

        # Temporary paths
        temp_output_path = temp_dir_path / 'output.mp4'
        temp_output_path.parent.mkdir(exist_ok=True, parents=True)

        # Scale the video
        scale_expression = (
            f'*{2**scale_factor}'
            if scale_factor >= 0 else
            f'/{2**(-scale_factor)}'
        )
        houdini.FFmpeg().run([
            '-y',
            '-i', path_str(input_mp4_path),
            '-vf', (
                'scale='
                f'trunc(iw{scale_expression}/2)*2:'
                f'trunc(ih{scale_expression}/2)*2'
            ),
            *_H264_ENCODE,
            '-pix_fmt', 'yuv420p',
            path_str(temp_output_path)
        ])
        output_mp4_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_path, output_mp4_path)