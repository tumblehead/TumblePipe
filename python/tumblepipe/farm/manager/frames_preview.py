"""Which frames the Farm Manager's "Preview in Cinedex" opens (#85).

Right-click a job ▸ *Preview in Cinedex* shows a job's rendered frames in
Cinedex as stills (``cinedex --frames <folder>``, through
``review_playlist.open_preview_frames``). It is offered for the jobs that
write frames, render, denoise, composite and slapcomp (partial or full),
once their output folder holds a frame; otherwise it is disabled and says
why.

Before the first frame lands (Remy, 2026-11-20) it is offered too, for a
job that has not ended and whose frame list Cinedex reads: it opens the
folder the frames will land in (:func:`waiting_folder`, which need not be
there yet) with the job's frame list, and Cinedex shows every frame as a
gap ("waiting for the first frame") until they land, then fills them in as
it does for a render part-way through (``frames_view``). Such a target is
``waiting``; :attr:`Target.ready` is one with frames on disk.

Where a job's frames are starts where *Open output folder* goes
(:func:`output_folder` of the job's first output, which the window uses for
both). A render's or a denoise's first output is its receipt pattern, in
the version folder (``.../render/<channel>/v0003/<stem>.####.json``), and
its frames are in the AOV folders under it: the ``beauty`` AOV is the one
shown, as the review mp4 encodes it. A slapcomp writes its EXRs into the
version folder itself. A composite is either: its receipts are in the
version folder of ``composite/<layer>``, a merged comp writes its EXRs there
too, a channel's comp into that version's ``beauty`` AOV
(``pipe.comp.layer_output_path``). Cinedex reads the frames of the one folder it is
given (``previews.rs``, ``open_frames``), so the folder handed over is the
one holding the frames.

Cinedex plays the frames (the editor's transport and timeline) at the
shot's fps and shows the job's frames not on disk yet as gaps that fill in
as they land: :func:`launch_options` gives it the rate (``--fps``, the job's
entity's, as the project config says) and the frame list the job renders
(``--expect``, the job's ``frames``). Either is left out when it is not
known, or the frame list is not one Cinedex reads (:func:`expected_frames`).

A playblast job is offered while it renders: its worker copies each
finished frame beside the movie it will write (``<movie>.mp4.frames``,
``farm.workers.playblast.FrameMirror``) and removes them when it ends, so
a finished playblast is played as its movie.

An mp4 job is not offered: its output is a movie, and the frames it encodes
are another job's output, a row of their own in the same batch. A batch row
(or several jobs) opens the best output among its jobs: a full job before
any partial one (the preview frames only), then a slapcomp over a composite
over a denoise over a render, then the newest version. So a full render
beats a partial slapcomp.

Qt-free: the window asks, the tests pin it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tumblepipe import naming
from tumblepipe.farm.frames import playblast_frames_dir
from tumblepipe.farm.manager.rows import JobRow, ended, row_entity

#: The menu item.
LABEL = 'Preview in Cinedex'

#: The farm workers whose jobs write frames, best first for a batch.
SLAPCOMP = 'slapcomp'
COMPOSITE = 'composite'
DENOISE = 'denoise'
RENDER = 'render'
FRAME_KINDS = (SLAPCOMP, COMPOSITE, DENOISE, RENDER)
#: The farm worker that encodes frames to a movie.
MOVIE_KIND = 'mp4'
#: The farm worker that renders a playblast: frames beside its movie to
#: come while it renders (``frames.playblast_frames_dir``), then the movie.
PLAYBLAST_KIND = 'playblast'

#: The file kinds Cinedex shows as frames (``th_pipe::preview_place::FRAME_EXTENSIONS``).
FRAME_EXTENSIONS = ('exr', 'jpg', 'jpeg')

#: The AOV a render's, a denoise's or a channel comp's frames are shown from.
BEAUTY_AOV = 'beauty'

#: The folder of a composite that writes the merged comp (its receipts and
#: frames in the version folder itself): ``pipe.comp.MERGED_FOLDER``.
MERGED_COMP_FOLDER = 'slapcomp'


@dataclass(frozen=True)
class Target:
    """What *Preview in Cinedex* does for a selection: open ``folder`` (the
    frames of ``row``, or with ``waiting`` the folder they will land in), or
    nothing, for ``reason``."""
    row: JobRow | None = None
    folder: Path | None = None
    reason: str = ''
    waiting: bool = False

    @property
    def enabled(self) -> bool:
        return self.folder is not None

    @property
    def ready(self) -> bool:
        """Whether there are frames on disk to show (not only to wait for)."""
        return self.enabled and not self.waiting


# ── where the frames are ──────────────────────────────────

def output_folder(path: str) -> Path:
    """The folder an output is written to, which *Open output folder* opens:
    the path itself when it is a folder, else the folder of the file (an
    image sequence's ``####`` pattern included)."""
    target = Path(path)
    return target if target.is_dir() else target.parent


def row_output_folder(row: JobRow) -> Path | None:
    """The folder *Open output folder* opens for ``row``; None when the job
    names no output."""
    return output_folder(row.outputs[0]) if row.outputs else None


def is_frame_file(name: str) -> bool:
    """Whether Cinedex shows the file ``name`` as a frame."""
    _, dot, extension = name.rpartition('.')
    return bool(dot) and extension.lower() in FRAME_EXTENSIONS


def has_frames(folder: Path) -> bool:
    """Whether ``folder`` holds a frame file (not in a folder under it).
    Stops at the first one: a render folder on the share holds thousands."""
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if is_frame_file(entry.name) and entry.is_file():
                    return True
    except OSError:
        return False
    return False


def frames_folder(folder: Path) -> Path | None:
    """The folder of ``folder``'s frames: ``folder`` when it holds frames (a
    slapcomp, any folder of frames), else its ``beauty`` AOV folder when that
    does (a render's or a denoise's version folder); None when neither."""
    if has_frames(folder):
        return folder
    beauty = folder / BEAUTY_AOV
    if has_frames(beauty):
        return beauty
    return None


# ── which jobs ────────────────────────────────────────────

def is_partial(row: JobRow) -> bool:
    """Whether ``row`` renders the preview frames only: the pipelines name
    those ``partial render ...`` / ``partial denoise ...``
    (``pipelines.render``), the whole range ``full ...``."""
    return row.name.split(' ', 1)[0].lower() == 'partial'


def version_number(folder: Path | None) -> int:
    """The version a job writes, from its version folder's name (``v0003``
    is 3); -1 when the folder is not a version."""
    if folder is None or not naming.is_valid_version_name(folder.name):
        return -1
    return naming.get_version_code(folder.name)


def waiting_folder(row: JobRow) -> Path | None:
    """The folder ``row``'s frames will land in, before any has (it need
    not be there yet): a playblast's frames folder beside its movie, a
    slapcomp's and a merged comp's version folder, else (a render, a
    denoise, a channel's comp) its version folder's ``beauty`` AOV folder;
    None for a job that writes no frames or names no output."""
    if not row.outputs:
        return None
    if row.kind == PLAYBLAST_KIND:
        return playblast_frames_dir(Path(row.outputs[0]))
    if row.kind not in FRAME_KINDS:
        return None
    folder = row_output_folder(row)
    if row.kind == SLAPCOMP or (row.kind == COMPOSITE and folder.parent.name == MERGED_COMP_FOLDER):
        return folder
    return folder / BEAUTY_AOV


def can_wait(row: JobRow) -> bool:
    """Whether ``row``'s preview may open before its first frame: it has
    not ended (frames are still to come) and its frame list is one Cinedex
    reads, so Cinedex knows which frames to wait for."""
    return not ended(row) and bool(expected_frames(row.frames))


def job_target(row: JobRow) -> Target:
    """What *Preview in Cinedex* opens for the one job ``row``: its frames,
    or before the first one, while it can wait for them (:func:`can_wait`),
    the folder they will land in."""
    target = _frames_target(row)
    if target.enabled or not can_wait(row):
        return target
    folder = waiting_folder(row)
    if folder is None:
        return target
    return Target(row, folder, waiting=True)


def _frames_target(row: JobRow) -> Target:
    """What *Preview in Cinedex* opens for ``row`` from the frames on disk."""
    if row.kind == MOVIE_KIND:
        return Target(row, reason=(
            'An mp4 job writes a movie, not frames: preview the job whose '
            'frames it encodes (or the batch row)'
        ))
    if row.kind == PLAYBLAST_KIND:
        if not row.outputs:
            return Target(row, reason='The job names no output')
        frames = playblast_frames_dir(Path(row.outputs[0]))
        if has_frames(frames):
            return Target(row, frames)
        return Target(row, reason=(
            "A playblast's frames are there only while it renders, from its "
            'second frame on; when it is done, play its movie'
        ))
    if row.kind not in FRAME_KINDS:
        what = f'a {row.kind} job' if row.kind else 'a job of an unknown kind'
        return Target(row, reason=(
            f'Only render, denoise, composite and slapcomp jobs write frames to preview; this is {what}'
        ))
    folder = row_output_folder(row)
    if folder is None:
        return Target(row, reason='The job names no output folder')
    if not folder.is_dir():
        return Target(row, reason=f'The output folder is not there yet: {folder}')
    frames = frames_folder(folder)
    if frames is None:
        return Target(row, reason=f'No frames in the output folder yet: {folder}')
    return Target(row, frames)


def rank(row: JobRow) -> tuple[bool, int, int]:
    """How good ``row``'s frames are for a batch: a full job over a partial
    one, then slapcomp over composite over denoise over render, then the
    newer version."""
    kinds = tuple(reversed(FRAME_KINDS))
    category = kinds.index(row.kind) if row.kind in kinds else -1
    return not is_partial(row), category, version_number(row_output_folder(row))


def best_target(selected: Iterable[JobRow]) -> Target:
    """What *Preview in Cinedex* opens for the selected jobs (a batch row
    stands for its jobs): the one job's frames, or the best frames on disk
    among several (:func:`rank`)."""
    selected = list(selected)
    if not selected:
        return Target(reason='Nothing is selected')
    if len(selected) == 1:
        return job_target(selected[0])
    targets = list(map(job_target, selected))
    ready = [target for target in targets if target.ready]
    # Frames on disk beat frames still to come; with none on disk, the best
    # job still rendering is waited for.
    pool = ready or [target for target in targets if target.enabled]
    if not pool:
        return Target(reason='No render, denoise or slapcomp job here has frames on disk yet')
    return max(pool, key=lambda target: rank(target.row))


# ── how Cinedex plays them ────────────────────────────────

#: One word of a frame list Cinedex's ``--expect`` reads
#: (``th_playlist::frame_list::parse_frame_list``): a frame, or a range,
#: with a step (``x``, ``:`` or ``by``, as Deadline writes it). ASCII digits
#: only: Rust reads no others (``\d`` alone would take ``١`` or ``１``).
_FRAME_WORD = re.compile(r'(-?\d+)(?:-(-?\d+))?(?:(?:x|:|by)([1-9]\d*))?', re.ASCII)

#: Frame numbers and steps Cinedex reads: 64-bit signed integers.
_I64 = range(-2**63, 2**63)

#: The longest frame list handed over: Cinedex refuses more than a million
#: frames, and a list that long is no render's.
MAX_EXPECTED = 100_000


def expected_frames(frames: str) -> str:
    """``frames`` (a job's frame list: ``1001-1100``, ``1001,1050,1100``,
    ``1-99x2``) normalised for ``cinedex --expect``: its words joined by
    commas; '' when it is empty, has a word Cinedex does not read (a frame
    or step past 64 bits among them), or spans more than
    :data:`MAX_EXPECTED` frames. Cinedex refuses its whole command line on a
    list it cannot read, so one is left out rather than handed over
    (``tests/test_expect_parity.py``)."""
    words = [word for word in re.split(r'[,\s]+', frames or '') if word]
    matches = [_FRAME_WORD.fullmatch(word) for word in words]
    if not words or not all(matches):
        return ''
    total = 0
    for match in matches:
        numbers = [int(group) for group in match.groups() if group is not None]
        if not all(number in _I64 for number in numbers):
            return ''
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        total += abs(last - first) + 1
    return ','.join(words) if total <= MAX_EXPECTED else ''


def row_fps(row: JobRow) -> int | None:
    """The fps ``row``'s frames play at: its entity's (a shot's own, else
    the project's), None when the job names no entity or the config says
    none (Cinedex then plays at 24)."""
    entity = row_entity(row)
    if not entity:
        return None
    try:
        from tumblepipe.config.timeline import get_fps
        from tumblepipe.util.uri import Uri
        fps = get_fps(Uri.parse(entity))
    except Exception:
        return None
    return fps if fps and 1 <= fps <= 240 else None


def launch_options(row: JobRow | None, fps=None) -> tuple[int | None, str]:
    """What Cinedex is told about ``row``'s frames: the rate they play at
    (``fps(row)``, :func:`row_fps` by default) and the frames the job
    renders (:func:`expected_frames`); None / '' for what is not known."""
    if row is None:
        return None, ''
    return (fps or row_fps)(row), expected_frames(row.frames)
