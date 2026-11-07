"""Give older playblasts the start frame newer ones record.

A playblast written since TumblePipe records its first frame (the shot frame
its first picture is) in the movie: a ``start_frame`` tag and a timecode
track (:func:`tumblepipe.apps.mp4.start_frame_args`). The editor addresses a
shot's movie by shot frame, so a movie without one cannot be trimmed or
slipped. This finds the ones without, and writes it into those it can place:

- a movie that records one already is left alone;
- one whose frame count is its shot's full range (the shot's frames plus
  roll, which is what a playblast covers) starts at that range's first
  frame, and is rewritten with it (a remux: the pictures are copied, not
  re-encoded);
- any other is listed as one it cannot place: its shot's range changed since
  it was made, so only a new playblast can say where it starts.

Nothing is guessed when the editor runs: a movie is placed here once, or not
at all. ``scripts/fix_playblast_start_frames.py`` is the command line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from tumblepipe.api import api, path_str
from tumblepipe.apps.mp4 import START_FRAME_KEY, start_frame_args
from tumblepipe.config.timeline import FrameRange, get_frame_range, get_fps
from tumblepipe.util.io import replace_file
from tumblepipe.util.uri import Uri

#: The movies a shot's playblast folder holds: its playblasts (.mp4) and
#: its render layers' alpha movies (.mov).
MOVIE_SUFFIXES = ('.mp4', '.mov')

#: Where a rewrite is written before it replaces the movie. Discovery never
#: reads it as a version (``paths.review``: its stem is no version name).
TEMP_SUFFIX = '.start_frame_tmp'


@dataclass(frozen=True)
class Probe:
    """What a movie says of itself: its video frames and its start frame
    tag, as written (None when it has none)."""
    frames: int
    start_frame: Optional[str]


@dataclass(frozen=True)
class Verdict:
    """What to do with one movie: ``has`` (it records a start frame), ``fix``
    (write ``start_frame`` into it) or ``unplaceable`` (``reason`` says why)."""
    path: Path
    shot: Uri
    action: str
    start_frame: Optional[int] = None
    reason: str = ''


def decide(path: Path, shot: Uri, probe: Probe, frame_range: Optional[FrameRange]) -> Verdict:
    """The verdict on ``path``, a playblast of ``shot`` whose current range is
    ``frame_range``. Pure: everything it needs is passed in."""
    if probe.start_frame is not None:
        try:
            int(probe.start_frame.strip())
        except ValueError:
            return Verdict(path, shot, 'unplaceable', reason = (
                f'its {START_FRAME_KEY} tag is {probe.start_frame!r}, not a frame number'))
        return Verdict(path, shot, 'has', start_frame = int(probe.start_frame.strip()))
    if frame_range is None:
        return Verdict(path, shot, 'unplaceable', reason = 'the shot has no frame range')
    full = frame_range.full_range()
    span = full.last_frame - full.first_frame + 1
    if probe.frames != span:
        return Verdict(path, shot, 'unplaceable', reason = (
            f'{probe.frames} frames, but the shot\'s range '
            f'{full.first_frame}-{full.last_frame} has {span}: '
            'the range changed since it was made'))
    return Verdict(path, shot, 'fix', start_frame = full.first_frame)


def probe(path: Path, ffprobe) -> Probe:
    """``path``'s video frame count and start frame tag, read by ``ffprobe``
    (:class:`tumblepipe.apps.houdini.FFProbe`)."""
    output = ffprobe.call([
        '-v', 'error',
        '-select_streams', 'v:0',
        '-count_packets',
        '-show_entries', f'stream=nb_read_packets:format_tags={START_FRAME_KEY}',
        '-of', 'json',
        path_str(path),
    ])
    data = json.loads(output)
    streams = data.get('streams') or []
    if not streams:
        raise ValueError(f'{path}: no video stream')
    tags = (data.get('format') or {}).get('tags') or {}
    return Probe(
        frames = int(streams[0]['nb_read_packets']),
        start_frame = tags.get(START_FRAME_KEY),
    )


def shot_uris() -> list[Uri]:
    """Every shot of the active project."""
    from tumblepipe.config.entities import is_terminal_entity
    return [
        entity.uri
        for entity in api.config.list_entities(Uri.parse('entity:/shots'), closure = True)
        if is_terminal_entity(api.config, entity.uri)
    ]


def playblast_movies(shot: Uri) -> list[Path]:
    """Every movie under ``shot``'s playblast folder (its departments, their
    render layers), in path order. Leftover rewrites are not movies."""
    root = api.storage.resolve(Uri.parse('render:/playblast') / shot.segments)
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob('*')
        if path.is_file()
        and path.suffix.lower() in MOVIE_SUFFIXES
        and not path.stem.endswith(TEMP_SUFFIX)
    )


def survey(shots: list[Uri], ffprobe) -> Iterator[Verdict]:
    """The verdict on every playblast movie of ``shots``. A movie the probe
    cannot read is unplaceable, with the reason."""
    for shot in shots:
        frame_range = get_frame_range(shot)
        for path in playblast_movies(shot):
            try:
                found = probe(path, ffprobe)
            except Exception as error:
                yield Verdict(path, shot, 'unplaceable', reason = f'cannot read it: {error}')
                continue
            yield decide(path, shot, found, frame_range)


def rewrite(verdict: Verdict, ffmpeg, ffprobe, framerate: int,
    replace: Callable[[Path, Path], None] = replace_file) -> None:
    """Write ``verdict``'s start frame into its movie: remux it beside
    itself, check the copy (same frames, the tag reads back), then replace
    the movie with it. On any failure the movie is untouched and the copy is
    gone."""
    assert verdict.action == 'fix' and verdict.start_frame is not None
    path = verdict.path
    temp = path.with_name(f'{path.stem}{TEMP_SUFFIX}{path.suffix}')
    try:
        before = probe(path, ffprobe)
        ffmpeg.run([
            '-v', 'error', '-y',
            '-i', path_str(path),
            '-map', '0:v:0', '-c', 'copy',
            *start_frame_args(verdict.start_frame, framerate),
            path_str(temp),
        ])
        after = probe(temp, ffprobe)
        if after.frames != before.frames:
            raise RuntimeError(f'the rewrite has {after.frames} frames, the movie {before.frames}')
        if after.start_frame is None or int(after.start_frame) != verdict.start_frame:
            raise RuntimeError(f'the rewrite\'s {START_FRAME_KEY} reads {after.start_frame!r}')
        replace(temp, path)
    finally:
        temp.unlink(missing_ok = True)


def framerate_of(shot: Uri) -> int:
    """The rate a shot's timecode counts at; refused when it has none."""
    fps = get_fps(shot)
    if fps is None:
        raise ValueError(f'{shot} has no fps')
    return fps
