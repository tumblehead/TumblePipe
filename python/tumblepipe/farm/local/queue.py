"""The local farm's queue on disk: batches, their task state, and control requests.

Everything lives under :func:`root`, a per-user folder on this machine (the
renders run here, so the queue is the machine's, not the project's)::

    runner.lock                 held by the running runner (see ``runner``)
    runner.log                  the runner's own output
    hpm/                        the hpm executable the runner drives
    batches/<batch id>/
        batch.json              written once, at submit: what to run
        state.json              written only by the runner: how far it got
        control/<n>.json        requests from the manager window, applied
                                and removed by the runner
        logs/<job>_<task>.log   one log per task, as Deadline keeps one per task

One writer per file: ``batch.json`` is the submitter's, ``state.json`` the
runner's, each control file its requester's. A reader may meet a file
mid-replace on Windows, so reads retry briefly.

A batch is the Deadline batch it replaces: jobs with dependencies between
them, each split into tasks of ``chunk_size`` frames, the way Deadline splits
a job's frame list. Task states are Deadline's; a job's state is derived
from its tasks' and its dependencies' (:func:`job_status`).
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

ROOT_ENV = 'TH_LOCAL_FARM_DIR'
BATCH_VERSION = 1

BATCH_NAME = 'batch.json'
STATE_NAME = 'state.json'
CONTROL_DIR = 'control'
LOG_DIR = 'logs'

# Task states.
QUEUED = 'queued'
RENDERING = 'rendering'
COMPLETED = 'completed'
FAILED = 'failed'
SUSPENDED = 'suspended'
CANCELLED = 'cancelled'
TASK_STATES = (QUEUED, RENDERING, COMPLETED, FAILED, SUSPENDED, CANCELLED)

# Job states: the task states, plus waiting on a dependency.
PENDING = 'pending'

# Control requests the manager window can make, on a job or a whole batch.
SUSPEND = 'suspend'
RESUME = 'resume'
RETRY = 'retry'
CANCEL = 'cancel'
REMOVE = 'remove'
COMMANDS = (SUSPEND, RESUME, RETRY, CANCEL, REMOVE)

# Which task states each command moves, and to what.
_TRANSITIONS = {
    SUSPEND: ({QUEUED, RENDERING}, SUSPENDED),
    RESUME: ({SUSPENDED}, QUEUED),
    RETRY: ({FAILED, CANCELLED}, QUEUED),
    CANCEL: ({QUEUED, RENDERING, SUSPENDED}, CANCELLED),
}


def root(environ=None) -> Path:
    """The local farm's folder on this machine (``$TH_LOCAL_FARM_DIR`` wins)."""
    environ = os.environ if environ is None else environ
    if environ.get(ROOT_ENV):
        return Path(environ[ROOT_ENV])
    if sys.platform == 'win32':
        base = environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'TumblePipe' / 'local_farm'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'TumblePipe' / 'local_farm'
    base = environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    return Path(base) / 'tumblepipe' / 'local_farm'


def batches_dir(root_path: Path) -> Path:
    return Path(root_path) / 'batches'


def now() -> str:
    return dt.datetime.now().isoformat(timespec='seconds')


# ── files ─────────────────────────────────────────────────

def write_json(path: Path, data) -> None:
    """Replace ``path`` with ``data`` in one step, so a reader sees old or new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex[:8]}.tmp')
    temp.write_text(json.dumps(data, indent=1), encoding='utf-8')
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            # Windows refuses to replace a file a reader has open.
            if attempt == 19:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def read_json(path: Path, default=None):
    """``path`` parsed, or ``default`` when it does not exist."""
    for attempt in range(20):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default
        except (PermissionError, json.JSONDecodeError):
            # Mid-replace on Windows; our writes are whole-file replaces, so
            # a torn read clears up at once.
            if attempt == 19:
                raise
            time.sleep(0.05)


# ── frames into tasks ─────────────────────────────────────

def job_frames(frames: Iterable[int], start: int | None, end: int | None, step: int | None) -> list[int]:
    """The frames a job renders: its frame list, else ``start..end`` by ``step``.

    Deadline sorts a frame list and drops repeats; so does this.
    """
    frames = list(frames)
    if frames:
        return sorted(set(int(f) for f in frames))
    step = int(step or 1)
    if step < 1:
        raise ValueError(f'Invalid step size: {step}')
    return list(range(int(start), int(end) + 1, step))


def task_spans(frames: list[int], chunk_size: int) -> list[list[int]]:
    """Deadline's tasks for ``frames``: ``chunk_size`` frames each, as ``[first, last]``.

    The worker is handed a task's first and last frame, as the HPM plugin
    hands it ``GetStartFrame()``/``GetEndFrame()``.
    """
    chunk_size = int(chunk_size)
    if chunk_size < 1:
        raise ValueError(f'Invalid chunk size: {chunk_size}')
    return [
        [frames[i], frames[min(i + chunk_size, len(frames)) - 1]]
        for i in range(0, len(frames), chunk_size)
    ]


# ── batches ───────────────────────────────────────────────

def new_batch_id() -> str:
    """Sorts by submission time, and stays unique between machines' users."""
    return dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]


def add_batch(root_path: Path, batch: dict) -> Path:
    """Queue ``batch`` (a ``batch.json`` document); returns its folder."""
    folder = batches_dir(root_path) / batch['id']
    if folder.exists():
        raise FileExistsError(f'Batch folder already exists: {folder}')
    (folder / CONTROL_DIR).mkdir(parents=True)
    (folder / LOG_DIR).mkdir()
    write_json(folder / BATCH_NAME, batch)
    return folder


def list_batch_dirs(root_path: Path) -> list[Path]:
    """Queued batch folders, oldest first (batch ids sort by time)."""
    folder = batches_dir(root_path)
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir()
        if path.is_dir() and (path / BATCH_NAME).is_file()
    )


def read_batch(folder: Path) -> dict:
    batch = read_json(Path(folder) / BATCH_NAME)
    if batch is None:
        raise FileNotFoundError(f'No {BATCH_NAME} in {folder}')
    if batch.get('version') != BATCH_VERSION:
        raise ValueError(
            f"{folder} is a version {batch.get('version')!r} batch; this "
            f'runner reads version {BATCH_VERSION}'
        )
    return batch


def initial_state(batch: dict) -> dict:
    """A batch's state before the runner touched it: every task queued."""
    return {
        'jobs': {
            str(job['index']): {
                'tasks': [new_task_state() for _ in job['tasks']],
            }
            for job in batch['jobs']
        },
    }


def new_task_state() -> dict:
    return {
        'status': QUEUED, 'progress': 0, 'started': None, 'finished': None,
        'exit_code': None, 'error': None, 'attempts': 0,
    }


def read_state(folder: Path, batch: dict) -> dict:
    """The runner's state for the batch, or its initial state."""
    state = read_json(Path(folder) / STATE_NAME)
    return state if state is not None else initial_state(batch)


def write_state(folder: Path, state: dict) -> None:
    write_json(Path(folder) / STATE_NAME, state)


def log_path(folder: Path, job_index: int, task_index: int) -> Path:
    return Path(folder) / LOG_DIR / f'{int(job_index):02d}_{int(task_index):03d}.log'


# ── states ────────────────────────────────────────────────

def task_statuses(state: dict, job_index: int) -> list[str]:
    return [task['status'] for task in state['jobs'][str(job_index)]['tasks']]


def job_status(statuses: list[str], deps_completed: bool) -> str:
    """A job's state from its tasks', as the Deadline Monitor would show it.

    Rendering wins; a failed task marks the job failed (its other tasks
    still run, as on Deadline, but nothing after it starts); a job with
    work left waits (``pending``) until its dependencies complete.
    """
    if RENDERING in statuses:
        return RENDERING
    if FAILED in statuses:
        return FAILED
    if QUEUED in statuses:
        return QUEUED if deps_completed else PENDING
    if SUSPENDED in statuses:
        return SUSPENDED
    if CANCELLED in statuses:
        return CANCELLED
    return COMPLETED


def job_statuses(batch: dict, state: dict) -> dict[int, str]:
    """Every job's state, dependencies resolved (jobs are in submit order)."""
    result: dict[int, str] = {}
    pending = list(batch['jobs'])
    # The batch lists jobs in topological order, but resolve by fixpoint so
    # a hand-edited or older file cannot make a job read its dependency
    # before that dependency has a state.
    while pending:
        progressed = False
        for job in list(pending):
            deps = job['deps']
            if not all(dep in result for dep in deps):
                continue
            completed = all(result[dep] == COMPLETED for dep in deps)
            result[job['index']] = job_status(task_statuses(state, job['index']), completed)
            pending.remove(job)
            progressed = True
        if not progressed:
            raise ValueError(f"Batch {batch['id']} has a dependency cycle or a missing job")
    return result


def next_task(batch: dict, state: dict) -> tuple[int, int] | None:
    """``(job index, task index)`` to run next in this batch, or None.

    The first queued task of the first job whose dependencies are complete
    and that has not failed (a failed job's dependents never start; its own
    remaining tasks do, as on Deadline).
    """
    statuses = job_statuses(batch, state)
    for job in batch['jobs']:
        if statuses[job['index']] not in (QUEUED, FAILED):
            continue
        if not all(statuses[dep] == COMPLETED for dep in job['deps']):
            continue
        for task_index, status in enumerate(task_statuses(state, job['index'])):
            if status == QUEUED:
                return job['index'], task_index
    return None


def apply_command(batch: dict, state: dict, command: str, job: int | None) -> list[tuple[int, int]]:
    """Apply ``command`` to ``job`` (every job when None) in ``state``.

    Returns the tasks it moved out of ``rendering``: the runner must stop
    their processes. ``remove`` changes no state; the runner deletes the
    batch once nothing in it renders.
    """
    if command not in COMMANDS:
        raise ValueError(f'Unknown command: {command!r}')
    if command == REMOVE:
        command = CANCEL
    sources, target = _TRANSITIONS[command]
    stopped = []
    for job_doc in batch['jobs']:
        index = job_doc['index']
        if job is not None and index != job:
            continue
        for task_index, task in enumerate(state['jobs'][str(index)]['tasks']):
            if task['status'] not in sources:
                continue
            if task['status'] == RENDERING:
                stopped.append((index, task_index))
            task['status'] = target
            if target == QUEUED:
                task.update(progress=0, started=None, finished=None, exit_code=None, error=None)
    return stopped


# ── control requests ──────────────────────────────────────

_REQUEST_COUNTER = itertools.count()


def request(folder: Path, command: str, job: int | None = None) -> Path:
    """Ask the runner to apply ``command`` to ``job`` of the batch in ``folder``.

    Requests apply in name order. Windows' clock can give two requests made
    in a row the same time, so a per-process sequence number orders those.
    """
    if command not in COMMANDS:
        raise ValueError(f'Unknown command: {command!r}')
    name = f'{time.time_ns():020d}_{os.getpid():08d}_{next(_REQUEST_COUNTER):08d}.json'
    path = Path(folder) / CONTROL_DIR / name
    write_json(path, {'command': command, 'job': job})
    return path


def pending_requests(folder: Path) -> list[tuple[Path, dict]]:
    """Unapplied control requests, oldest first."""
    control = Path(folder) / CONTROL_DIR
    if not control.is_dir():
        return []
    result = []
    for path in sorted(control.glob('*.json')):
        data = read_json(path)
        if data is not None:
            result.append((path, data))
    return result
