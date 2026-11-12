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
from tumblepipe.farm.specs import frame_ranges as frame_list

# Houdini's hffmpeg ships libopenh264 (not libx264); its quality rate-control
# mode keeps the notify "shrink to fit Discord" path shrinking with resolution.
# Output is standard yuv420p H.264, universally playable. -q:v is tunable.
_H264_ENCODE = ['-c:v', 'libopenh264', '-rc_mode', 'quality', '-q:v', '22']

#: The movie metadata key a playblast records its first frame under (the
#: shot frame its first picture is), so the player can address the movie by
#: shot frame. Read back by th_media (`MovieIndex::start_frame`) and written
#: into older playblasts by scripts/fix_playblast_start_frames.py.
START_FRAME_KEY = 'start_frame'

#: The movie metadata key a back-to-back movie (a multi-range render's,
#: #84) lists the frames it shows under, as Frame List text
#: (``1001-1030 1040-1055 1100``): its pictures are those frames in order,
#: so the listed frame count is the movie's. Read by Cinedex (#86).
FRAME_RANGES_KEY = frame_list.CONFIG_KEY


def frame_timecode(frame: int, framerate: int) -> str:
    """``frame`` as a non-drop SMPTE timecode at ``framerate`` (frame 0 is
    00:00:00:00), for editors that place movies by timecode (Resolve)."""
    if frame < 0 or framerate <= 0:
        raise ValueError(f'No timecode for frame {frame} at {framerate} fps')
    seconds, frames = divmod(frame, framerate)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}'


def start_frame_args(first_frame: int, framerate: int) -> list[str]:
    """The FFmpeg output arguments that record ``first_frame`` in a movie:
    the ``start_frame`` tag (QuickTime keeps a custom tag only with
    ``use_metadata_tags``) and a timecode track starting there."""
    return [
        '-movflags', 'use_metadata_tags',
        '-metadata', f'{START_FRAME_KEY}={first_frame}',
        '-timecode', frame_timecode(first_frame, framerate),
    ]

#: How a playblast's JPG frames are encoded: H.264, sizes padded even.
#: Cinedex's Keep as playblast passes the same (th_pipe::live::keep_args,
#: tests/test_live_parity.py).
JPG_ENCODE_ARGS = ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', *_H264_ENCODE, '-pix_fmt', 'yuv420p']


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
        output_mp4_path, repeat_missing_frames, JPG_ENCODE_ARGS,
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


def edit_movie_args(
    frames_pattern: Path,
    audio_path: Path | None,
    frame_count: int,
    framerate: int,
    start_timecode: str,
    output_path: Path
    ) -> list[str]:
    """hffmpeg's arguments that encode an edit ``cinedex --render`` wrote
    (``farm/workers/edit_render.py``): ``frames_pattern`` (``edit.%04d.png``,
    numbered from 0) as H.264, with ``audio_path`` (``edit.wav``) as AAC
    when the edit has sound, and a timecode track starting at the edit's
    ``start_timecode``, so an editor places the movie by it."""
    if frame_count <= 0 or framerate <= 0:
        raise ValueError(f'No movie of {frame_count} frames at {framerate} fps')
    args = [
        '-y',
        '-framerate', str(framerate),
        '-start_number', '0',
        '-i', path_str(frames_pattern),
    ]
    if audio_path is not None:
        args += ['-i', path_str(audio_path)]
    args += [
        '-frames:v', str(frame_count),
        '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
        *_H264_ENCODE,
        '-pix_fmt', 'yuv420p',
    ]
    if audio_path is not None:
        args += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
    return args + ['-timecode', start_timecode, path_str(output_path)]


def from_edit_frames(
    frames_pattern: Path,
    audio_path: Path | None,
    frame_count: int,
    framerate: int,
    start_timecode: str,
    output_path: Path
    ):
    """Encode an edit's rendered frames and sound as an H.264 + AAC mp4
    (:func:`edit_movie_args`)."""
    houdini.FFmpeg().run(edit_movie_args(
        frames_pattern, audio_path, frame_count, framerate,
        start_timecode, output_path,
    ))


def frame_ranges_args(frames) -> list[str]:
    """The FFmpeg output arguments that list ``frames``, a back-to-back
    movie's, under :data:`FRAME_RANGES_KEY`; none when they run on without a
    gap (the movie is then numbered as its frames are)."""
    ranges = frame_list.of_frames(frames)
    if not frame_list.is_multi(ranges):
        return []
    return ['-metadata', f'{FRAME_RANGES_KEY}={frame_list.format_ranges(ranges)}']


def back_to_back_plan(frames) -> list[tuple[int, int]]:
    """The frames of a movie of only ``frames`` (a multi-range render's,
    #84), played back to back: ``(movie frame, frame it shows)``, the movie
    numbered on from the first listed frame with no gaps."""
    frames = list(frames)
    if not frames:
        return []
    return [(frames[0] + index, frame) for index, frame in enumerate(frames)]


def from_jpg_frames(
    framestack_path: Path,
    frames,
    framerate: int,
    output_mp4_path: Path
    ):
    """Encode only ``frames`` of a JPG sequence (``name.####.jpg``) back
    to back as an H.264 mp4 (:func:`back_to_back_plan`), listing them
    (:func:`frame_ranges_args`). Every frame must exist: nothing is filled
    in."""
    frames = list(frames)
    plan = back_to_back_plan(frames)
    if not plan:
        raise ValueError(f'No frames to encode: {framestack_path}')
    name, _, suffix = framestack_path.name.split('.')
    base_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)
        for movie_frame, frame in plan:
            shutil.copyfile(
                framestack_path.parent / f'{name}.{frame:04d}.{suffix}',
                temp_dir_path / f'frame.{movie_frame:04d}.{suffix}',
            )
        temp_output_path = temp_dir_path / f'output{output_mp4_path.suffix}'
        houdini.FFmpeg().run(sequence_args(
            path_str(temp_dir_path / f'frame.%04d.{suffix}'),
            plan[0][0], plan[-1][0], framerate,
            [*JPG_ENCODE_ARGS, *frame_ranges_args(frames)], path_str(temp_output_path),
        ))
        output_mp4_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_path, output_mp4_path)


def fill_plan(first_frame: int, last_frame: int, available) -> list[tuple[int, int]]:
    """The frames a movie of ``first_frame..last_frame`` is made of, from
    the rendered frames ``available``: for each frame, the one it shows, the
    nearest rendered at or before it, else the first rendered. Empty when
    nothing is rendered."""
    rendered = sorted(set(available))
    if not rendered:
        return []
    plan = []
    at = 0
    for frame in range(first_frame, last_frame + 1):
        while at < len(rendered) and rendered[at] <= frame:
            at += 1
        plan.append((frame, rendered[at - 1] if at else rendered[0]))
    return plan


def sequence_args(pattern: str, first_frame: int, last_frame: int, framerate: int,
                  encode_args: list[str], output: str) -> list[str]:
    """hffmpeg's arguments that encode the frames ``pattern`` names
    (``frame.%04d.<suffix>``), one per frame of ``first_frame..last_frame``,
    at ``framerate`` into ``output``, recording the first frame."""
    return [
        '-y',
        '-framerate', str(framerate),
        '-pattern_type', 'sequence',
        '-start_number', str(first_frame),
        '-i', pattern,
        '-frames:v', str(last_frame - first_frame + 1),
        *encode_args,
        *start_frame_args(first_frame, framerate),
        output,
    ]


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

    # Find missing frames: frame_count is the number of *rendered* frames
    # (step-aware), only for the check; the movie spans every frame of
    # first..last, each missing one filled (fill_plan).
    frame_count = len(frame_range)
    available_frame_indices = {
        int(frame.name.split('.')[-2])
        for frame in input_frames
    }
    if (not repeat_missing_frames and
        len(available_frame_indices) != frame_count):
        missing_frame_indices = list(sorted(set(frame_range) - available_frame_indices))
        raise ValueError(f'Missing frames: {missing_frame_indices}')
    input_by_index = {
        int(frame.name.split('.')[-2]): frame
        for frame in input_frames
    }

    # Open temporary workspace
    base_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    base_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(base_temp_path)) as temp_dir:
        temp_dir_path = Path(temp_dir)

        # Each frame of the movie, a rendered frame or the one filling in
        plan = fill_plan(frame_range.first_frame, frame_range.last_frame, available_frame_indices)
        if not plan:
            raise ValueError(f'No frames to encode: {framestack_path}')
        for frame_index, source_index in plan:
            temp_frame_path = temp_dir_path / f'frame.{frame_index:04d}.{framestack_suffix}'
            if frame_index != source_index:
                logging.info(f'Filling in {temp_frame_path} with frame {source_index}')
            shutil.copyfile(input_by_index[source_index], temp_frame_path)

        # Combine the frames into a movie
        temp_framestack_path = temp_dir_path / f'frame.%04d.{framestack_suffix}'
        temp_output_path = temp_dir_path / f'output{output_path.suffix}'
        houdini.FFmpeg().run(sequence_args(
            path_str(temp_framestack_path),
            frame_range.first_frame, frame_range.last_frame, framerate,
            encode_args, path_str(temp_output_path),
        ))
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
            # Keep the start frame and timecode the movie records.
            '-map_metadata', '0',
            '-movflags', 'use_metadata_tags',
            path_str(temp_output_path)
        ])
        output_mp4_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(temp_output_path, output_mp4_path)