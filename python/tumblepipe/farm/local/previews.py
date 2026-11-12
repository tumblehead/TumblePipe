"""Preview frames rendered on this machine: knowing when they are ready (#75).

A render or comp submitted with Preview Frames renders the preview frames
first (``pipelines.render``: partial render -> partial denoise; or
``pipelines.composite``: partial composite), then a *partial notify* job
posts them to Discord. On the farm that post is the only word the artist
gets; on this machine the runner can say so at once, so a mistake is caught
before the full range renders.

The preview batch is done when every job the partial notify waits on has
completed: its frames are on disk then, whether or not the Discord post
itself works (a project without Discord, an unknown channel). So the signal
is the queue's own task states, read where the runner already reads them,
and nothing polls the file system.

What the runner records, in the batch's ``state.json`` (it is that file's
only writer), under ``previews``::

    "previews": {
        "<notify job index>": {
            "ready": "2026-11-12T10:31:05",   # when its frames were done
            "entity": "entity:/shots/010/0100",
            "department": "denoise",
            "version": "v0003",
            "frame_path": "P:/.../beauty.####.exr",
            "frames": [1001, 1050, 1100],
            "notified": true,                 # the OS notification went out
            "error": ""                       # why it did not, if it did not
        }
    }

A job is recorded once, so it is announced once. The Farm Manager reads the
same entries for its notice (``manager.rows``), and :func:`preview_frame_paths`
hands any other reader (Cinedex's "Open in player", later) the frames.

Stdlib only, like the rest of ``farm.local``: the runner imports it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from tumblepipe.farm.frames import FRAME_TOKEN, frame_file
from tumblepipe.farm.local import queue

#: The ``state.json`` key the runner records ready previews under.
STATE_KEY = 'previews'

#: The OS notification's title.
TITLE = 'Preview frames ready'

#: The farm worker a partial notify runs (``workers/notify.py``).
NOTIFY_WORKER = 'notify'
PARTIAL_MODE = 'partial'

_ENTITY = re.compile(r'entity:/(?:shots|assets|groups|edits)/(.+)', re.DOTALL)


@dataclass(frozen=True)
class Preview:
    """A job's preview frames: whose, which version, and where."""
    entity: str = ''        # the entity URI, '' when the message did not name it
    department: str = ''
    version: str = ''
    frame_path: str = ''    # the frame pattern, ``####`` for the frame
    frames: tuple[int, ...] = field(default_factory=tuple)

    @property
    def folder(self) -> str:
        """The folder the frames are written to ('' without a frame path)."""
        if not self.frame_path:
            return ''
        return str(Path(self.frame_path).parent).replace('\\', '/')


# ── which jobs are preview notifies ───────────────────────

def worker_kind(script: str) -> str:
    """The farm worker a job runs, from its script: ``.../workers/notify.py`` is ``notify``."""
    if not script:
        return ''
    return Path(str(script).replace('\\', '/')).stem


def may_be_preview(job: dict) -> bool:
    """Whether ``job`` (a ``batch.json`` entry) runs the notify worker, so
    its context is worth reading to see whether it posts preview frames."""
    return worker_kind(job.get('script', '')) == NOTIFY_WORKER


def partial_frames(command: dict) -> tuple[int, ...]:
    """A partial command's preview frames, in order: its ``frames`` (since
    #74), else the first, middle and last a command queued before names."""
    if isinstance(command.get('frames'), list):
        return tuple(int(frame) for frame in command['frames'])
    return tuple(
        int(command[key])
        for key in ('first_frame', 'middle_frame', 'last_frame')
        if isinstance(command.get(key), int)
    )


def parse_message(message: str) -> tuple[str, str, str]:
    """``(entity, department, version)`` from a partial notify's message,
    ``'<uri> - <department> - <version>'`` (``post_render.frames_notify_job``);
    '' for whatever it does not hold."""
    parts = [part.strip() for part in str(message or '').split(' - ')]
    if len(parts) != 3:
        return '', '', ''
    return parts[0], parts[1], parts[2]


def preview_of(context) -> Preview | None:
    """The preview frames a notify job's context posts, or None when it
    posts something else (a message, a review mp4) or is not readable."""
    if not isinstance(context, dict):
        return None
    command = context.get('command')
    if not isinstance(command, dict) or command.get('mode') != PARTIAL_MODE:
        return None
    frame_path = command.get('frame_path')
    if not isinstance(frame_path, str) or not frame_path:
        return None
    try:
        frames = partial_frames(command)
    except (TypeError, ValueError):
        return None
    entity, department, version = parse_message(context.get('message', ''))
    return Preview(entity, department, version, frame_path, frames)


def read_context(job: dict) -> dict:
    """The job's ``context.json`` (its first argument, relative to its data
    dir), {} when unreadable."""
    tokens = str(job.get('arguments') or '').split()
    if not tokens or not tokens[0].endswith('.json'):
        return {}
    first = tokens[0]
    is_absolute = bool(re.match(r'^[A-Za-z]:[\\/]', first)) or first.startswith(('/', '\\'))
    if is_absolute:
        path = Path(first)
    elif job.get('data_dir'):
        path = Path(job['data_dir']) / first
    else:
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ── when a preview is ready ───────────────────────────────

def recorded(state: dict) -> dict:
    """The previews the runner recorded in ``state``, by job index (a str)."""
    entries = state.get(STATE_KEY) if isinstance(state, dict) else None
    return entries if isinstance(entries, dict) else {}


def ready_jobs(batch: dict, state: dict, preview_indices: Iterable[int]) -> list[int]:
    """The preview notify jobs (``preview_indices``) whose preview frames
    are done and that are not recorded yet, in batch order.

    Done means every job it waits on completed: the frames are on disk,
    whatever becomes of the post itself. A preview whose render failed,
    was cancelled or still runs is not done.
    """
    wanted = set(preview_indices)
    if not wanted:
        return []
    statuses = queue.job_statuses(batch, state)
    seen = recorded(state)
    result = []
    for job in batch['jobs']:
        index = job['index']
        if index not in wanted or str(index) in seen:
            continue
        if all(statuses.get(dep) == queue.COMPLETED for dep in job['deps']):
            result.append(index)
    return result


def record(
    state: dict, job_index: int, preview: Preview, ready: str,
    notified: bool, error: str = '',
    ) -> None:
    """Note in ``state`` that ``job_index``'s preview frames were ready at
    ``ready`` and whether the OS notification went out."""
    entries = state.get(STATE_KEY)
    if not isinstance(entries, dict):
        entries = state[STATE_KEY] = {}
    entries[str(job_index)] = {
        'ready': ready,
        'entity': preview.entity,
        'department': preview.department,
        'version': preview.version,
        'frame_path': preview.frame_path,
        'frames': list(preview.frames),
        'notified': bool(notified),
        'error': str(error or ''),
    }


#: The whole numbers a job index or a frame may be: a 64-bit integer, as
#: Cinedex reads them (``th_pipe::previews``).
_I64 = range(-2**63, 2**63)

#: A job index key: ASCII digits after at most one ``-``.
_JOB_KEY = re.compile(r'-?[0-9]+', re.ASCII)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in _I64


def _text(value) -> str:
    """A name field: its text, '' for anything else (a number is not a name)."""
    return value if isinstance(value, str) else ''


def job_key(key) -> int | None:
    """The job index a ``previews`` key names: ASCII digits after at most
    one ``-``, within 64 bits (``3``, ``03``, ``-1``); None for any other
    key (``--3``, ``+3``, ``²``), which is passed over."""
    if not isinstance(key, str) or not _JOB_KEY.fullmatch(key):
        return None
    job = int(key)
    return job if job in _I64 else None


def entry_preview(entry) -> Preview | None:
    """A recorded entry back as a :class:`Preview`; None when it is not one
    (it wants a text ``frame_path``). Names that are not text read as '';
    frames are kept when every one is a whole number within 64 bits, else
    read as none. Cinedex reads it the same (``test_previews_parity``)."""
    if not isinstance(entry, dict) or not isinstance(entry.get('frame_path'), str):
        return None
    frames = entry.get('frames')
    if not isinstance(frames, list) or not all(_whole(f) for f in frames):
        frames = []
    return Preview(
        _text(entry.get('entity')), _text(entry.get('department')),
        _text(entry.get('version')), entry['frame_path'], tuple(frames),
    )


def recorded_previews(state) -> dict[int, Preview]:
    """The previews recorded in ``state``, by job index, in job order.

    Keys that are not job indices (:func:`job_key`) and entries that are
    not previews are passed over. Two keys naming the same job (``3`` and
    ``03``): the one later in the object wins, a key written twice
    counting at its first place with its last value (as ``json`` keeps
    it); an entry that is not a preview displaces nothing. Cinedex reads
    it by the same rules (``th_pipe::previews``, test_previews_parity)."""
    result = {}
    for key, entry in recorded(state).items():
        job = job_key(key)
        preview = entry_preview(entry) if job is not None else None
        if preview is not None:
            result[job] = preview
    return dict(sorted(result.items()))


# ── what the artist is told ───────────────────────────────

def entity_label(uri: str) -> str:
    """The entity as an artist names it: ``entity:/shots/010/0100`` is ``010/0100``."""
    match = _ENTITY.fullmatch(str(uri or ''))
    return match.group(1) if match else str(uri or '')


def summary(preview: Preview) -> str:
    """``010/0100 denoise v0003: 3 frames``."""
    count = len(preview.frames)
    what = ' '.join(
        part for part in (entity_label(preview.entity), preview.department, preview.version)
        if part
    ) or 'Preview'
    return f"{what}: {count} frame{'' if count == 1 else 's'}"


def notice_text(preview: Preview) -> tuple[str, str]:
    """The OS notification's ``(title, body)``: the summary, then the folder."""
    body = summary(preview)
    if preview.folder:
        body += '\n' + preview.folder
    return TITLE, body


def frame_paths(preview: Preview) -> list[Path]:
    """The preview's frame files, in the order they were rendered."""
    if FRAME_TOKEN not in Path(preview.frame_path).name:
        return []
    return [frame_file(Path(preview.frame_path), frame) for frame in preview.frames]


# ── for other readers ─────────────────────────────────────

def batch_previews(folder: Path) -> dict[int, Preview]:
    """The ready previews of the local batch in ``folder``, by notify job index."""
    batch = queue.read_batch(folder)
    return recorded_previews(queue.read_state(folder, batch))


def preview_frame_paths(folder: Path, job_index: int | None = None) -> list[Path]:
    """The preview frame files of a local batch (one of its notify jobs, or
    all of them), once ready; [] while none is.

    The hook for anything that wants to show them (Cinedex's "Open in
    player"): the runner has already decided they are done.
    """
    previews = batch_previews(Path(folder))
    chosen = (
        [previews[job_index]] if job_index is not None and job_index in previews
        else [] if job_index is not None
        else [previews[key] for key in sorted(previews)]
    )
    return [path for preview in chosen for path in frame_paths(preview)]
