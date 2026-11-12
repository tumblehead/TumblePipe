"""Which frames the Farm Manager's "Preview in Cinedex" opens (#85).

Right-click a job ▸ *Preview in Cinedex* shows a job's rendered frames in
Cinedex as stills (``cinedex --frames <folder>``, through
``review_playlist.open_preview_frames``). It is offered for the jobs that
write frames, render, denoise, composite and slapcomp (partial or full),
once their output folder holds a frame; otherwise it is disabled and says
why.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tumblepipe import naming
from tumblepipe.farm.manager.rows import JobRow

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

#: The file kinds Cinedex shows as frames (``previews.rs`` FRAME_EXTENSIONS).
FRAME_EXTENSIONS = ('exr', 'jpg', 'jpeg')

#: The AOV a render's, a denoise's or a channel comp's frames are shown from.
BEAUTY_AOV = 'beauty'


@dataclass(frozen=True)
class Target:
    """What *Preview in Cinedex* does for a selection: open ``folder`` (the
    frames of ``row``), or nothing, for ``reason``."""
    row: JobRow | None = None
    folder: Path | None = None
    reason: str = ''

    @property
    def enabled(self) -> bool:
        return self.folder is not None


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


def job_target(row: JobRow) -> Target:
    """What *Preview in Cinedex* opens for the one job ``row``."""
    if row.kind == MOVIE_KIND:
        return Target(row, reason=(
            'An mp4 job writes a movie, not frames: preview the job whose '
            'frames it encodes (or the batch row)'
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
    ready = [target for target in map(job_target, selected) if target.enabled]
    if not ready:
        return Target(reason='No render, denoise or slapcomp job here has frames on disk yet')
    return max(ready, key=lambda target: rank(target.row))
