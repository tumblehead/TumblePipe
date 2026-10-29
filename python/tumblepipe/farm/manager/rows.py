"""What the Farm Manager lists: jobs and tasks, local or on the farm, as plain rows.

Both sources come out in one vocabulary — the local queue's states, which
are Deadline's (``queued``, ``rendering``, ``pending``, ``suspended``,
``completed``, ``failed``, ``cancelled``; ``unknown`` for anything else) — so
the window draws and filters them the same way. Qt-free.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe.farm.local import queue

LOCAL = 'local'
FARM = 'farm'

UNKNOWN = 'unknown'
FINISHED = (queue.COMPLETED, queue.CANCELLED)
# The actions the window offers, by source (``deadline_jobs.COMMANDS`` maps
# the farm ones onto deadlinecommand verbs).
ACTIONS = {
    LOCAL: (queue.SUSPEND, queue.RESUME, queue.RETRY, queue.CANCEL, queue.REMOVE),
    FARM: ('suspend', 'resume', 'retry', 'requeue', 'delete'),
}


@dataclass(frozen=True)
class JobRow:
    source: str
    id: str                 # Deadline job id, or local:<batch>/<index>
    batch_key: str          # groups the jobs of one batch
    batch: str              # the batch's name
    name: str
    user: str
    status: str
    done: int               # completed tasks
    total: int
    errors: int
    submitted: str          # ISO local time, '' when unknown
    progress: float         # 0..100, the rendering tasks' own progress included
    outputs: tuple[str, ...] = ()
    folder: str = ''        # local: the batch folder
    job_index: int | None = None
    job_dir: str = ''       # local: the shared job folder (export:/other/jobs/<uuid>)


@dataclass(frozen=True)
class TaskRow:
    index: int
    frames: str
    status: str
    progress: int
    worker: str = ''
    started: str = ''
    finished: str = ''
    errors: int = 0
    error: str = ''
    log_path: str = ''      # local: the task's log file
    extra: dict = field(default_factory=dict, compare=False, hash=False)


def frames_text(first: int, last: int) -> str:
    return str(first) if first == last else f'{first}-{last}'


def progress_of(statuses: list[str], progresses: list[int]) -> float:
    """A job's progress: completed tasks whole, rendering ones by their own progress."""
    if not statuses:
        return 0.0
    total = 0.0
    for status, progress in zip(statuses, progresses):
        if status == queue.COMPLETED:
            total += 100
        elif status == queue.RENDERING:
            total += max(0, min(100, progress))
    return round(total / len(statuses), 1)


# ── local ─────────────────────────────────────────────────

def local_job_rows(folder: Path, batch: dict, state: dict) -> list[JobRow]:
    statuses = queue.job_statuses(batch, state)
    rows = []
    for job in batch['jobs']:
        index = job['index']
        tasks = state['jobs'][str(index)]['tasks']
        task_states = [t['status'] for t in tasks]
        rows.append(JobRow(
            source=LOCAL,
            id=f"local:{batch['id']}/{index}",
            batch_key=f"local:{batch['id']}",
            batch=batch['name'],
            name=job['name'],
            user=batch.get('user', ''),
            status=statuses[index],
            done=task_states.count(queue.COMPLETED),
            total=len(tasks),
            errors=sum(1 for t in tasks if t['status'] == queue.FAILED),
            submitted=batch.get('submitted', ''),
            progress=progress_of(task_states, [t.get('progress', 0) for t in tasks]),
            outputs=tuple(job.get('outputs', ())),
            folder=str(folder),
            job_index=index,
            job_dir=batch.get('job_dir', ''),
        ))
    return rows


def local_rows(root_path: Path) -> tuple[list[JobRow], list[str]]:
    """Every queued local job, and why any batch could not be read."""
    rows, problems = [], []
    for folder in queue.list_batch_dirs(root_path):
        try:
            batch = queue.read_batch(folder)
            rows.extend(local_job_rows(folder, batch, queue.read_state(folder, batch)))
        except Exception as error:
            problems.append(f'{folder.name}: {error}')
    return rows, problems


def local_task_rows(folder: Path, job_index: int) -> list[TaskRow]:
    batch = queue.read_batch(folder)
    state = queue.read_state(folder, batch)
    job = next(j for j in batch['jobs'] if j['index'] == job_index)
    rows = []
    for index, (span, task) in enumerate(zip(job['tasks'], state['jobs'][str(job_index)]['tasks'])):
        rows.append(TaskRow(
            index=index,
            frames=frames_text(*span),
            status=task['status'],
            progress=int(task.get('progress') or 0),
            worker=batch.get('machine', ''),
            started=task.get('started') or '',
            finished=task.get('finished') or '',
            errors=1 if task['status'] == queue.FAILED else 0,
            error=task.get('error') or '',
            log_path=str(queue.log_path(folder, job_index, index)),
        ))
    return rows


# ── Deadline ──────────────────────────────────────────────

# Job.Stat and Task.Stat codes (deadline-mcp's model/status.rs).
_JOB_STAT = {2: queue.SUSPENDED, 3: queue.COMPLETED, 4: queue.FAILED, 6: queue.PENDING}
_TASK_STAT = {
    2: queue.QUEUED, 3: queue.SUSPENDED, 4: queue.RENDERING, 5: queue.COMPLETED,
    6: queue.FAILED, 8: queue.PENDING,
}


def deadline_time(value) -> str:
    """A Deadline document time as ISO local time; '' for unset (year 1)."""
    if not value or not isinstance(value, str):
        return ''
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return ''
    if parsed.year <= 1:
        return ''
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.isoformat(timespec='seconds')


def deadline_job_status(stat, rendering_chunks) -> str:
    if stat == 1:
        return queue.RENDERING if (rendering_chunks or 0) > 0 else queue.QUEUED
    return _JOB_STAT.get(stat, UNKNOWN)


def deadline_task_status(stat) -> str:
    return _TASK_STAT.get(stat, UNKNOWN)


def deadline_progress(text) -> int:
    """``Prog`` (``"42 %"``) as an int."""
    try:
        return max(0, min(100, int(float(str(text).replace('%', '').strip() or 0))))
    except ValueError:
        return 0


def farm_job_row(doc: dict) -> JobRow:
    props = doc.get('Props') or {}
    total = int(props.get('Tasks') or 0)
    done = int(doc.get('CompletedChunks') or 0)
    status = deadline_job_status(doc.get('Stat'), doc.get('RenderingChunks'))
    outputs = tuple(
        str(Path(d) / f) for d, f in zip(doc.get('OutDir') or [], doc.get('OutFile') or [])
    )
    batch = props.get('Batch') or ''
    return JobRow(
        source=FARM,
        id=doc.get('_id', ''),
        batch_key=f'farm:{batch}' if batch else f"farm:{doc.get('_id', '')}",
        batch=batch or props.get('Name', ''),
        name=props.get('Name', ''),
        user=props.get('User', ''),
        status=status,
        done=done,
        total=total,
        errors=int(doc.get('Errs') or 0),
        submitted=deadline_time(doc.get('Date')),
        progress=100.0 if status == queue.COMPLETED else (round(done * 100 / total, 1) if total else 0.0),
        outputs=outputs,
    )


def farm_task_row(doc: dict) -> TaskRow:
    return TaskRow(
        index=int(doc.get('TaskID') or 0),
        frames=str(doc.get('Frames') or ''),
        status=deadline_task_status(doc.get('Stat')),
        progress=deadline_progress(doc.get('Prog')),
        worker=doc.get('Slave') or '',
        started=deadline_time(doc.get('StartRen')) or deadline_time(doc.get('Start')),
        finished=deadline_time(doc.get('Comp')),
        errors=int(doc.get('Errs') or 0),
    )


# ── filters ───────────────────────────────────────────────

def keep(
    row: JobRow, *, source: str | None = None, user: str | None = None,
    project: str | None = None, hide_finished: bool = False, text: str = '',
    ) -> bool:
    """Whether the window shows ``row`` under these filters (None: no filter).

    ``project`` matches the batch name's first word: every batch is named
    ``<project> ...`` (``pipelines._batch.batch_title``).
    """
    if source is not None and row.source != source:
        return False
    if user is not None and row.user.lower() != user.lower():
        return False
    if project is not None and row.batch.split(' ', 1)[0].lower() != project.lower():
        return False
    if hide_finished and row.status in FINISHED:
        return False
    if text:
        needle = text.lower()
        if needle not in row.name.lower() and needle not in row.batch.lower():
            return False
    return True


def batch_status(statuses: list[str]) -> str:
    """One state for a batch row: the most urgent of its jobs'."""
    for status in (queue.FAILED, queue.RENDERING, queue.QUEUED, queue.PENDING,
                   queue.SUSPENDED, UNKNOWN, queue.CANCELLED):
        if status in statuses:
            return status
    return queue.COMPLETED
