"""What the Farm Manager lists: jobs and tasks, local or on the farm, as plain rows.

Both sources come out in one vocabulary — the local queue's states, which
are Deadline's (``queued``, ``rendering``, ``pending``, ``suspended``,
``completed``, ``failed``, ``cancelled``; ``unknown`` for anything else) — so
the window draws and filters them the same way. Qt-free.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe.farm import stamp
from tumblepipe.farm.local import previews, queue

LOCAL = 'local'
FARM = 'farm'

UNKNOWN = 'unknown'
FINISHED = (queue.COMPLETED, queue.CANCELLED)
# The states a job ends in. A job in one of them has ended only once no
# task of it renders or is still to run (:func:`ended`): a local job is
# failed while its other tasks still render, as on Deadline.
ENDED = (queue.COMPLETED, queue.FAILED, queue.CANCELLED)
# Every state a row can show. The window marks each row with its state's
# colour and lists them in this order in its legend: live work first, then
# what needs a look, then what is done. ``unknown`` is left out of the
# legend; it means a state this vocabulary does not have.
STATES = (queue.RENDERING, queue.QUEUED, queue.PENDING, queue.SUSPENDED,
          queue.FAILED, queue.COMPLETED, queue.CANCELLED, UNKNOWN)
LEGEND = tuple(state for state in STATES if state != UNKNOWN)
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
    # The job's properties, for the details pane ('' / 0 / None: not known;
    # a local batch queued before they were recorded has few of them).
    frames: str = ''        # the frame list as Deadline writes it: 1001-1100, 1-99x2
    chunk: int = 0          # frames per task
    priority: int | None = None
    pool: str = ''
    group: str = ''
    machine: str = ''       # the machine it was submitted from
    kind: str = ''          # the farm worker it runs: render, playblast, publish...
    data_dir: str = ''      # the job's data folder, its context path relative to it
    arguments: str = ''
    department: str = ''
    started: str = ''       # ISO local time the first task started
    finished: str = ''      # ISO local time it completed
    deps: tuple[str, ...] = ()  # the ids of the jobs it waits on
    timeout: int = 0        # seconds a task may take, 0 for no limit
    # The project and entity URI the job is stamped with (farm.stamp); ''
    # for a job submitted before the stamp: see row_project / row_entity.
    project: str = ''
    entity: str = ''
    # Tasks rendering or still to run. Local: every queued, rendering or
    # suspended task. Farm: the rendering ones, and for a job not completed
    # or failed its queued, pending and suspended ones too (Deadline runs
    # nothing more of a completed or failed job).
    live_tasks: int = 0
    # Local, a partial notify job whose preview frames are done (#75,
    # farm.local.previews): when, what (010/0100 denoise v0003: 3 frames)
    # and the folder they are in. '' for every other job.
    preview_ready: str = ''
    preview_summary: str = ''
    preview_folder: str = ''


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
    pid: int = 0            # local, while it renders: the task's process
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

def worker_kind(script: str) -> str:
    """The farm worker a job runs, from its script: ``.../workers/render.py`` is ``render``."""
    if not script:
        return ''
    return Path(str(script).replace('\\', '/')).stem


def _local_frames(job: dict) -> str:
    if job.get('frames'):
        return str(job['frames'])
    spans = job.get('tasks') or []
    if not spans:
        return ''
    return frames_text(spans[0][0], spans[-1][1])


def _local_chunk(job: dict) -> int:
    if job.get('chunk_size'):
        return int(job['chunk_size'])
    spans = job.get('tasks') or []
    # A batch from before chunk_size was recorded: its first task's span,
    # which is the chunk unless a step skipped frames inside it.
    return spans[0][1] - spans[0][0] + 1 if spans else 0


_LOCAL_LIVE = (queue.QUEUED, queue.RENDERING, queue.SUSPENDED)


def local_job_rows(folder: Path, batch: dict, state: dict) -> list[JobRow]:
    statuses = queue.job_statuses(batch, state)
    ready = previews.recorded(state)
    rows = []
    for job in batch['jobs']:
        index = job['index']
        preview_entry = ready.get(str(index))
        preview = previews.entry_preview(preview_entry)
        tasks = state['jobs'][str(index)]['tasks']
        task_states = [t['status'] for t in tasks]
        starts = [t['started'] for t in tasks if t.get('started')]
        ends = [t['finished'] for t in tasks if t.get('finished')]
        project, entity = stamp.read(job.get('extra_info'))
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
            frames=_local_frames(job),
            chunk=_local_chunk(job),
            priority=job.get('priority'),
            pool=job.get('pool') or '',
            group=job.get('group') or '',
            machine=batch.get('machine', ''),
            kind=worker_kind(job.get('script', '')),
            data_dir=job.get('data_dir') or '',
            arguments=job.get('arguments') or '',
            started=min(starts) if starts else '',
            finished=max(ends) if ends and statuses[index] == queue.COMPLETED else '',
            deps=tuple(f"local:{batch['id']}/{dep}" for dep in job.get('deps', ())),
            timeout=int(job.get('timeout') or 0),
            project=project,
            entity=entity,
            live_tasks=sum(1 for s in task_states if s in _LOCAL_LIVE),
            preview_ready=str(preview_entry.get('ready') or '') if preview else '',
            preview_summary=previews.summary(preview) if preview else '',
            preview_folder=preview.folder if preview else '',
        ))
    return rows


def new_previews(job_rows: list[JobRow], seen: set[str]) -> list[JobRow]:
    """The rows whose preview frames became ready since ``seen`` (the ids
    already told about), oldest first: what the window's notice announces."""
    fresh = [row for row in job_rows if row.preview_ready and row.id not in seen]
    return sorted(fresh, key=lambda row: (row.preview_ready, row.id))


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
            pid=int(task.get('pid') or 0) if task['status'] == queue.RENDERING else 0,
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
    plugin_info = props.get('PlugInfo') if isinstance(props.get('PlugInfo'), dict) else {}
    # The ExtraInfoKeyValue pairs the job was submitted with.
    project, entity = stamp.read(props.get('ExDic'))
    live = _int(doc.get('RenderingChunks'))
    if status not in (queue.COMPLETED, queue.FAILED):
        live += sum(_int(doc.get(key)) for key in ('QueuedChunks', 'PendingChunks', 'SuspendedChunks'))
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
        frames=str(props.get('Frames') or ''),
        chunk=_int(props.get('Chunk')),
        priority=_int(props.get('Pri')) if props.get('Pri') is not None else None,
        pool=str(props.get('Pool') or ''),
        group=str(props.get('Grp') or ''),
        machine=str(doc.get('Mach') or ''),
        kind=worker_kind(plugin_info.get('ScriptFile', '')),
        data_dir=str(plugin_info.get('StartupDirectory') or ''),
        arguments=str(plugin_info.get('Arguments') or ''),
        department=str(props.get('Dept') or ''),
        started=deadline_time(doc.get('DateStart')),
        finished=deadline_time(doc.get('DateComp')) if status == queue.COMPLETED else '',
        deps=tuple(
            str(dep.get('JobID') if isinstance(dep, dict) else dep)
            for dep in props.get('Dep') or () if dep
        ),
        project=project,
        entity=entity,
        live_tasks=live,
    )


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


# ── whose job, and is it over ─────────────────────────────

# An entity URI in a batch's name: every pipeline names its batch
# ``<project> <kinds...> <entity uri> ...`` (``pipelines._batch.batch_title``).
_ENTITY_URI = re.compile(r'\bentity:/(?:shots|assets|groups|edits)/\S+')


def ended(row: JobRow) -> bool:
    """Whether nothing of ``row`` runs any more, or will: it completed,
    failed or was cancelled, and no task of it renders or is left to run."""
    return row.status in ENDED and row.live_tasks == 0


def row_project(row: JobRow) -> str:
    """The project ``row`` is for: its stamp, else its batch name's first
    word (a batch is named ``<project> ...``)."""
    return row.project or row.batch.split(' ', 1)[0]


def name_entity(name: str) -> str:
    """The entity URI a batch's name carries; '' when it carries none."""
    match = _ENTITY_URI.search(name or '')
    return match.group(0) if match else ''


def row_entity(row: JobRow) -> str:
    """The entity URI ``row`` is for: its stamp, else the one its batch's
    name carries; '' when neither says (its context.json may: :func:`context_entity`)."""
    return row.entity or name_entity(row.batch)


def context_entity(context) -> str:
    """The entity URI a job's ``context.json`` names; '' when none.

    A job's context names it as ``entity.uri`` (publish, render, collapse,
    stage...), ``entity_uri`` (build, sync) or ``edit_uri`` (Render Edit);
    an encode or a notify names none.
    """
    if not isinstance(context, dict):
        return ''
    entity = context.get('entity')
    if isinstance(entity, dict) and isinstance(entity.get('uri'), str):
        return entity['uri']
    for key in ('entity_uri', 'edit_uri'):
        if isinstance(context.get(key), str):
            return context[key]
    return ''


# ── filters ───────────────────────────────────────────────

def keep(
    row: JobRow, *, source: str | None = None, user: str | None = None,
    project: str | None = None, hide_finished: bool = False, text: str = '',
    ) -> bool:
    """Whether the window shows ``row`` under these filters (None: no filter).

    ``project`` matches the project the job is stamped with, else the
    batch name's first word (:func:`row_project`).
    """
    if source is not None and row.source != source:
        return False
    if user is not None and row.user.lower() != user.lower():
        return False
    if project is not None and row_project(row).lower() != project.lower():
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


#: Job states that mean work is still going.
_LIVE = (queue.RENDERING, queue.QUEUED, queue.PENDING)


def batch_status_text(statuses: list[str]) -> str:
    """A batch row's Status text: :func:`batch_status`, except that a batch
    with failed jobs that is still working says both, ``rendering · 2
    failed``, so a row whose render is at 23/24 does not read as dead
    because two encodes failed (#46). Its dot stays the failed red."""
    status = batch_status(statuses)
    failed = statuses.count(queue.FAILED)
    live = next((s for s in _LIVE if s in statuses), None)
    if status == queue.FAILED and live is not None:
        return f'{live} · {failed} failed'
    return status


def failed_with_job(task: 'TaskRow', job_status: str) -> bool:
    """A task Deadline failed with its job: failed, its job failed, and it
    has no error of its own (its job's error limit was reached elsewhere)."""
    return (
        task.status == queue.FAILED and job_status == queue.FAILED
        and not task.error and not task.errors
    )


def job_failure_text(errors: list[tuple[str, str]]) -> str:
    """Why a job failed, from its error reports' ``(worker, first line)``:
    ``failed with its job: 7 errors on abel-5060, 5 on john-3090 (No
    licenses could be found)``, workers by count, the commonest message."""
    if not errors:
        return 'failed with its job (it has no error reports)'
    counts: dict[str, int] = {}
    messages: dict[str, int] = {}
    for worker, message in errors:
        counts[worker or 'an unknown worker'] = counts.get(worker or 'an unknown worker', 0) + 1
        if message:
            messages[message] = messages.get(message, 0) + 1
    order = sorted(counts, key=lambda w: (-counts[w], w))
    first, rest = order[0], order[1:]
    noun = 'error' if counts[first] == 1 else 'errors'
    text = f'failed with its job: {counts[first]} {noun} on {first}'
    text += ''.join(f', {counts[w]} on {w}' for w in rest)
    if messages:
        common = sorted(messages, key=lambda m: (-messages[m], m))[0]
        text += f' ({common})'
    return text


# ── display ───────────────────────────────────────────────

_STAMP = re.compile(r'\s+\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$')
_ENTITY = re.compile(r'\bentity:/(?:shots|assets|groups)/(\S+)')


def batch_title(name: str, user: str = '') -> str:
    """A batch's name without what its row's columns already say.

    Batches are named ``<project> <kinds...> <entity> <user> <timestamp>``
    (``pipelines._batch.batch_title``); the User and Submitted columns
    carry the last two, and ``entity:/shots/SQ010/SH0030`` reads as
    ``SQ010/SH0030``. A name in any other shape is shortened only where
    it matches.
    """
    title = _STAMP.sub('', name)
    if user and title.endswith(' ' + user):
        title = title[:-len(user) - 1]
    return _ENTITY.sub(r'\1', title) or name


def when_text(iso: str, today: dt.date | None = None) -> str:
    """``2026-10-29T14:03:11`` as ``Today 14:03``, ``Yesterday 14:03``,
    ``Oct 29 14:03`` within the year, else ``2025-10-29``."""
    try:
        moment = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ''
    today = today or dt.date.today()
    clock = moment.strftime('%H:%M')
    if moment.date() == today:
        return f'Today {clock}'
    if moment.date() == today - dt.timedelta(days=1):
        return f'Yesterday {clock}'
    if moment.year == today.year:
        return f"{moment.strftime('%b')} {moment.day} {clock}"
    return moment.date().isoformat()


# ── times ─────────────────────────────────────────────────

def parse_time(iso: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def seconds_between(started: str, finished: str = '', now: dt.datetime | None = None) -> float | None:
    """Seconds from ``started`` to ``finished`` (to ``now`` without one); None without a start."""
    start = parse_time(started)
    if start is None:
        return None
    end = parse_time(finished) if finished else (now or dt.datetime.now())
    if end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def task_seconds(task: TaskRow, now: dt.datetime | None = None) -> float | None:
    """How long the task rendered, or has been rendering; None when it has not run.

    A queued task keeps no times (a retry clears them), so only a rendering
    task (up to ``now``) and one that ran to an end have a duration.
    """
    if not task.started:
        return None
    if task.status == queue.RENDERING:
        return seconds_between(task.started, '', now)
    if task.finished:
        return seconds_between(task.started, task.finished)
    return None


def duration_text(seconds: float | None) -> str:
    """``0:03:05`` as Deadline's Monitor shows a render time; ``2d 01:00:00`` past a day."""
    if seconds is None or seconds < 0:
        return ''
    whole = int(seconds)
    days, rest = divmod(whole, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f'{days}d {hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{hours}:{minutes:02d}:{secs:02d}'


_FRAME_ITEM = re.compile(r'^(-?\d+)(?:-(-?\d+)(?:x(\d+))?)?$')


def frame_count(text: str) -> int:
    """How many frames a Deadline frame list names (``1001-1010``, ``1-99x2``, ``1,5,9``); 0 when unreadable."""
    total = 0
    for item in re.split(r'[,\s]+', str(text or '').strip()):
        if not item:
            continue
        match = _FRAME_ITEM.match(item)
        if match is None:
            return 0
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        step = int(match.group(3) or 1)
        if step < 1:
            return 0
        total += abs(last - first) // step + 1
    return total


@dataclass(frozen=True)
class JobTimes:
    started: str = ''                   # the first task's start
    finished: str = ''                  # the last task's end, once nothing is left to run
    elapsed: float | None = None        # wall clock from the first start
    render_total: float | None = None   # every task's render time added up
    task_average: float | None = None   # per completed task
    frame_average: float | None = None  # per completed frame
    remaining: float | None = None      # render time the tasks left need, at the average
    completed: int = 0


_LEFT = (queue.QUEUED, queue.PENDING, queue.SUSPENDED)


def job_times(tasks: list[TaskRow], now: dt.datetime | None = None) -> JobTimes:
    """What the Monitor's job panel says about time, from the job's tasks."""
    now = now or dt.datetime.now()
    starts = [t.started for t in tasks if parse_time(t.started)]
    if not starts:
        return JobTimes()
    started = min(starts, key=parse_time)
    durations = {t.index: task_seconds(t, now) for t in tasks}
    rendering = [t for t in tasks if t.status == queue.RENDERING]
    done = [t for t in tasks if t.status == queue.COMPLETED and durations[t.index] is not None]
    ends = [t.finished for t in tasks if parse_time(t.finished)]
    left = [t for t in tasks if t.status in _LEFT]
    last_end = max(ends, key=parse_time) if ends else ''
    finished = last_end if last_end and not rendering and not left else ''
    elapsed = seconds_between(started, '' if rendering or not last_end else last_end, now)
    known = [d for d in durations.values() if d is not None]
    task_average = frame_average = remaining = None
    if done:
        spent = sum(durations[t.index] for t in done)
        task_average = spent / len(done)
        frames = sum(frame_count(t.frames) for t in done)
        frame_average = spent / frames if frames else None
        if left or rendering:
            remaining = task_average * len(left) + sum(
                max(0.0, task_average - (durations[t.index] or 0.0)) for t in rendering
            )
    return JobTimes(
        started=started, finished=finished, elapsed=elapsed,
        render_total=sum(known) if known else None,
        task_average=task_average, frame_average=frame_average,
        remaining=remaining, completed=len(done),
    )


# ── job details ───────────────────────────────────────────

@dataclass(frozen=True)
class Detail:
    key: str
    value: str
    tip: str = ''
    path: str = ''          # an output: what Open folder opens


XPU_TIP = (
    'Karma XPU renders on the GPU (NVIDIA, OptiX) and the CPU at the same '
    'time. The CPU also loads the scene, builds geometry and writes the '
    'images, so high CPU use during a GPU render is normal. Farm and local '
    'renders always use XPU.'
)
_HYTHON = {'publish', 'export', 'stage', 'collapse', 'build'}
_PYTHON = {'notify', 'sync', 'slapcomp'}


def renderer_details(kind: str, context: dict | None) -> list[Detail]:
    """What renders the job and on which devices, from its worker and context
    (None while it is being read; an old playblast's context has no look:
    those rendered with Karma)."""
    loaded = isinstance(context, dict)
    context = context if loaded else {}
    if kind == 'render':
        result = [
            Detail('Renderer', 'Karma (husk)'),
            Detail('Engine', 'XPU: GPU and CPU together', XPU_TIP),
        ]
        tiles = context.get('tile_count')
        if isinstance(tiles, int) and tiles > 1:
            result.append(Detail('Tiles per frame', str(tiles)))
        return result + _resolution(context)
    if kind == 'playblast':
        if not loaded:
            return []   # its context is not read yet: which renderer is unknown
        look = context.get('look') if isinstance(context.get('look'), dict) else {}
        if look.get('renderer', 'karma') == 'viewport':
            result = [
                Detail('Renderer', 'TumbleView (hdThPlayblast)'),
                Detail('Engine', 'CPU only, a Karma licence'),
            ]
            if look.get('shading'):
                result.append(Detail('Shading', str(look['shading'])))
            if look.get('lighting'):
                result.append(Detail('Lighting', str(look['lighting'])))
            if look.get('cull'):
                result.append(Detail('Culling', str(look['cull'])))
            if look.get('transparency'):
                result.append(
                    Detail('Transparency', str(look['transparency'])))
        else:
            result = [
                Detail('Renderer', 'Karma preview (husk)'),
                Detail('Engine', 'XPU: GPU and CPU together', XPU_TIP),
            ]
            if look.get('samples'):
                result.append(Detail('Samples', str(look['samples'])))
        return result + _resolution(context)
    if kind == 'denoise':
        cpu = bool(context.get('force_cpu'))
        return [
            Detail('Renderer', 'Intel Open Image Denoise (idenoise)'),
            Detail('Engine', 'CPU' if cpu else 'GPU when it has one, else CPU'),
        ]
    if kind == 'composite':
        return [Detail('Renderer', 'Houdini COPs (hython)'), Detail('Engine', 'CPU')]
    if kind in _HYTHON:
        return [Detail('Runs', 'Houdini (hython), no rendering')]
    if kind == 'mp4':
        return [Detail('Runs', 'ffmpeg, encoding a video')]
    if kind == 'edit_render':
        result = [
            Detail('Renderer', 'TumblePipe editor (cinedex --render), then ffmpeg'),
            Detail('Engine', 'CPU only, no licence'),
        ]
        if isinstance(context.get('edit_uri'), str) and isinstance(context.get('version'), str):
            result.append(Detail('Edit', f"{context['edit_uri']}@{context['version']}"))
        return result
    if kind in _PYTHON:
        return [Detail('Runs', 'Python, no rendering')]
    return []


def _resolution(context: dict) -> list[Detail]:
    res = context.get('res')
    if isinstance(res, (list, tuple)) and len(res) == 2 and all(isinstance(v, int) for v in res):
        return [Detail('Resolution', f'{res[0]} × {res[1]}')]
    return []


def read_job_context(data_dir: str, arguments: str) -> dict:
    """The job's ``context.json`` (its first argument, relative to its data dir); {} when unreadable.

    Reads the job folder, which is on the project share: call it off the GUI thread.
    """
    import json
    tokens = (arguments or '').split()
    if not tokens or not tokens[0].endswith('.json'):
        return {}
    first = tokens[0]
    if re.match(r'^[A-Za-z]:[\\/]', first) or first.startswith(('/', '\\')):
        path = Path(first)
    elif data_dir:
        path = Path(data_dir) / first
    else:
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _entity(row: JobRow, context: dict) -> tuple[str, str]:
    """``(entity, department)``: the job's stamp, else its context's, else
    the entity in the batch's name."""
    entity = context.get('entity') if isinstance(context.get('entity'), dict) else {}
    uri = row.entity or str(entity.get('uri') or '')
    if uri:
        short = _ENTITY.sub(r'\1', uri)
    else:
        match = _ENTITY.search(row.batch)
        short = match.group(1) if match else ''
    return short, str(entity.get('department') or row.department or '')


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def job_details(
    row: JobRow, tasks: list[TaskRow], context: dict | None = None,
    names: dict[str, str] | None = None, now: dt.datetime | None = None,
    ) -> list[tuple[str, list[Detail]]]:
    """The details pane of ``row``: ``(section, details)``, details with nothing to say left out.

    ``tasks`` are the job's (for times; [] when not loaded), ``context`` its
    context.json, ``names`` job names by id (for its dependencies).
    """
    raw_context = context
    context = context if isinstance(context, dict) else {}
    names = names or {}
    entity, department = _entity(row, context)
    job = [
        Detail('Name', row.name),
        Detail('Kind', row.kind),
        Detail('Entity', entity),
        Detail('Department', department),
        Detail('Status', row.status),
        Detail('Runs on', 'This machine' if row.source == LOCAL else 'The Deadline farm'),
        Detail('Submitted by', row.user + (f' on {row.machine}' if row.machine and row.user else '')),
        Detail('Submitted', when_text(row.submitted), row.submitted),
        Detail('Batch', row.batch),
        Detail('Job ID', row.id),
    ]
    scheduling = [
        Detail('Priority', '' if row.priority is None else str(row.priority)),
        Detail('Pool', row.pool),
        Detail('Group', row.group),
        Detail('Depends on', ', '.join(names.get(dep, dep) for dep in row.deps)),
        Detail('Task timeout', duration_text(row.timeout) if row.timeout else ''),
    ]
    counts = {status: sum(1 for t in tasks if t.status == status) for status in
              (queue.COMPLETED, queue.RENDERING, queue.FAILED)}
    states = ', '.join(f'{n} {status}' for status, n in counts.items() if n)
    count = frame_count(row.frames)
    frames = [
        Detail('Frames', row.frames),
        Detail('Frame count', str(count) if count else ''),
        Detail('Frames per task', str(row.chunk) if row.chunk else ''),
        Detail('Tasks', f'{row.total}' + (f' ({states})' if states else '')),
    ]
    times = job_times(tasks, now)
    started = times.started or row.started
    finished = times.finished or row.finished
    elapsed = times.elapsed
    if elapsed is None and started:
        elapsed = seconds_between(started, finished, now)
    time = [
        Detail('Started', when_text(started), started),
        Detail('Finished', when_text(finished), finished),
        Detail('Elapsed', duration_text(elapsed), 'Wall-clock time since the first task started'),
        Detail('Total render time', duration_text(times.render_total),
               "Every task's render time added up"),
        Detail('Average per task', duration_text(times.task_average),
               f'Over {_count(times.completed, "completed task")}'),
        Detail('Average per frame', duration_text(times.frame_average)),
        Detail('Render time left', duration_text(times.remaining),
               'Estimated: the tasks left, at the average task time'),
    ]
    output = [
        Detail('Output' if len(row.outputs) == 1 else f'Output {i + 1}', path, path, path)
        for i, path in enumerate(row.outputs)
    ]
    # What it renders with and how long it takes first: what a glance at a
    # render is for. The window puts this machine's load after Renderer.
    sections = [
        ('Job', job), ('Renderer', renderer_details(row.kind, raw_context)), ('Time', time),
        ('Frames', frames), ('Scheduling', scheduling), ('Output', output),
    ]
    result = []
    for title, details in sections:
        kept = [d for d in details if d.value]
        if kept:
            result.append((title, kept))
    return result
