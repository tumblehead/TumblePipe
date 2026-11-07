"""Clean-ups: which finished batches to take off the farm or this machine, and taking them off.

Two callers:

- the Farm Manager's **Clean up** menu (:func:`removable`): of the batches
  the window shows, the completed ones, the failed ones, both, or the
  superseded ones (an older run of the same work on the same entity);
- Farm Submit's runner (:class:`EntityCleanup`): before an entity is
  submitted again, its old finished batches, everyone's, in this project,
  on the backend it is submitted to.

The rules every clean-up keeps:

- whole batches only, and only when every job of the batch has ended
  (``rows.ended``: completed, failed or cancelled, no task of it rendering
  or left to run). One queued, rendering, pending or suspended job keeps its
  whole batch;
- never a batch holding a job that a job not yet ended waits on
  (:func:`protected`);
- on the farm a batch's jobs are deleted from Deadline (``DeleteJob``);
  on this machine the batch gets a ``remove`` request, which the local
  runner applies (``queue.REMOVE``). Outputs stay either way.

Choosing is pure (no Qt, no I/O); removing takes the farm's ``control`` and
the local queue's ``request`` as arguments, so tests hand it fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import rows

# The Clean up menu's kinds.
COMPLETED = 'completed'     # every job completed
FAILED = 'failed'           # ended, and a job of it failed (or, locally, was cancelled)
FINISHED = 'finished'       # ended: completed and failed together
SUPERSEDED = 'superseded'   # ended, with a newer batch of the same work shown
KINDS = (COMPLETED, FAILED, FINISHED, SUPERSEDED)

# Job ids per DeleteJob call: one call per batch would take a second each,
# one call for everything an unbounded command line.
DELETE_CHUNK = 100


@dataclass(frozen=True)
class Removal:
    """One batch a clean-up takes off, with all of its jobs."""

    source: str                     # rows.LOCAL or rows.FARM
    key: str                        # the jobs' batch_key
    name: str                       # the batch's name
    user: str
    jobs: tuple[rows.JobRow, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(job.id for job in self.jobs)

    @property
    def folder(self) -> str:
        """The local batch's folder ('' on the farm)."""
        return self.jobs[0].folder if self.jobs else ''

    @property
    def title(self) -> str:
        """The batch's name as the window shows it (``rows.batch_title``)."""
        return rows.batch_title(self.name, self.user)


def _removal(jobs: list[rows.JobRow]) -> Removal:
    first = jobs[0]
    return Removal(first.source, first.batch_key, first.batch, first.user, tuple(jobs))


def batches(job_rows: Iterable[rows.JobRow]) -> dict[str, list[rows.JobRow]]:
    """``job_rows`` by batch, batches and their jobs in first-seen order."""
    result: dict[str, list[rows.JobRow]] = {}
    for row in job_rows:
        result.setdefault(row.batch_key, []).append(row)
    return result


def protected(job_rows: Iterable[rows.JobRow]) -> set[str]:
    """The ids of the jobs some job that has not ended waits on."""
    return {dep for row in job_rows if not rows.ended(row) for dep in row.deps}


def batch_ended(jobs: list[rows.JobRow]) -> bool:
    """Whether every job of a batch has ended (an empty batch has not)."""
    return bool(jobs) and all(rows.ended(job) for job in jobs)


def _qualifies(kind: str, jobs: list[rows.JobRow]) -> bool:
    statuses = {job.status for job in jobs}
    if kind == COMPLETED:
        return statuses == {queue.COMPLETED}
    if kind == FAILED:
        return bool(statuses & {queue.FAILED, queue.CANCELLED})
    return True


def _submitted(jobs: list[rows.JobRow]) -> str:
    """When the batch was submitted: its first job's time ('' when unknown sorts oldest)."""
    return min(job.submitted for job in jobs)


def work_key(jobs: list[rows.JobRow]) -> tuple | None:
    """What a batch did, for *superseded*: where it ran, its project and
    entity, and its name without the entity, the user and the time (what
    is left names its steps: ``render [beauty]``, ``playblast animation``).
    None when its entity is not known: such a batch is never superseded."""
    first = jobs[0]
    entity = batch_entity(jobs)
    if not entity:
        return None
    title = rows.batch_title(first.batch.replace(entity, ' '), first.user)
    return (
        first.source, rows.row_project(first).lower(), entity, ' '.join(title.split()),
    )


def batch_entity(jobs: list[rows.JobRow], context_entity: Callable | None = None) -> str:
    """The entity URI a batch is for; '' when not known, or when its jobs disagree.

    A job says it by its stamp, else by the entity in its batch's name
    (``rows.row_entity``). When none of them says, ``context_entity(job)``
    is asked, job by job, until one answers: the caller reads the job's
    context.json, a share read, so it is only asked here.
    """
    said = {rows.row_entity(job) for job in jobs} - {''}
    if len(said) == 1:
        return said.pop()
    if said or context_entity is None:
        return ''
    for job in jobs:
        uri = context_entity(job)
        if uri:
            return uri
    return ''


def removable(
    all_rows: list[rows.JobRow], shown: list[rows.JobRow], kind: str,
    ) -> list[Removal]:
    """The batches the clean-up ``kind`` removes, of those ``shown`` lists.

    A batch counts whole: shown with any of its jobs, it is judged by all
    of them in ``all_rows`` (every job ended, none waited on). For
    *superseded*, the newest of each work (:func:`work_key`) among the
    shown batches stays, finished or not, and only older finished ones go.
    Batches come back in the order ``shown`` first lists them.
    """
    if kind not in KINDS:
        raise ValueError(f'Unknown clean-up: {kind!r}')
    whole = batches(all_rows)
    shown_batches = batches(shown)
    waited_on = protected(all_rows)

    def jobs_of(key: str) -> list[rows.JobRow]:
        return whole.get(key) or shown_batches[key]

    newest: dict[tuple, tuple[str, str]] = {}
    if kind == SUPERSEDED:
        for key in shown_batches:
            work = work_key(jobs_of(key))
            if work is not None:
                stamp = (_submitted(jobs_of(key)), key)
                newest[work] = max(newest.get(work, stamp), stamp)
    result = []
    for key in shown_batches:
        jobs = jobs_of(key)
        if not batch_ended(jobs) or any(job.id in waited_on for job in jobs):
            continue
        if not _qualifies(kind, jobs):
            continue
        if kind == SUPERSEDED:
            work = work_key(jobs)
            if work is None or newest[work][1] == key:
                continue
        result.append(_removal(jobs))
    return result


def recheck(fresh_rows: list[rows.JobRow], chosen: list[Removal], kind: str) -> list[Removal]:
    """Of ``chosen`` (confirmed on an older listing), what still qualifies on a fresh one.

    The farm moves on while a confirmation is open: a failed batch retried
    since is not ended any more, and is left alone. Nothing beyond the
    jobs confirmed is removed. *Superseded* was decided against the batch
    that superseded it, which this listing need not show, so here it is
    held to the rules every clean-up keeps (``finished``).
    """
    keys = {removal.key for removal in chosen}
    confirmed = {job_id for removal in chosen for job_id in removal.ids}
    shown = [row for row in fresh_rows if row.batch_key in keys]
    again = removable(fresh_rows, shown, FINISHED if kind == SUPERSEDED else kind)
    return [removal for removal in again if set(removal.ids) <= confirmed]


def entity_removals(
    all_rows: list[rows.JobRow], project: str, entity: str,
    context_entity: Callable | None = None,
    ) -> list[Removal]:
    """The batches a new submit of ``entity`` clears first.

    Every batch of ``entity`` in ``project`` whose every job has ended,
    whoever submitted it, whatever its steps were. A job is the project's
    by its stamp, else by its batch's name; the entity's as
    :func:`batch_entity` tells (``context_entity`` is only asked for a
    finished batch of this project whose jobs carry no stamp and whose
    name names no entity).
    """
    if not project:
        raise ValueError('No project to clean up in')
    target = _uri(entity)
    if not target:
        raise ValueError('No entity to clean up after')
    waited_on = protected(all_rows)
    result = []
    for jobs in batches(all_rows).values():
        if not batch_ended(jobs) or any(job.id in waited_on for job in jobs):
            continue
        if any(rows.row_project(job).lower() != project.lower() for job in jobs):
            continue
        if _uri(batch_entity(jobs, context_entity)) != target:
            continue
        result.append(_removal(jobs))
    return result


def _uri(uri) -> str:
    return str(uri or '').strip().rstrip('/')


# ── taking them off ───────────────────────────────────────

def remove_local(
    removals: list[Removal], root_path: Path | None = None, *,
    request: Callable | None = None, start: Callable | None = None,
    ) -> int:
    """Ask the local runner to remove each local batch of ``removals``; returns how many.

    The runner applies the requests, so it is started if it stopped
    (``runner.ensure_running``).
    """
    from tumblepipe.farm.local import runner
    request = request or queue.request
    folders = sorted({r.folder for r in removals if r.source == rows.LOCAL and r.folder})
    for folder in folders:
        request(Path(folder), queue.REMOVE)
    if folders:
        (start or runner.ensure_running)(root_path)
    return len(folders)


def delete_chunks(removals: list[Removal]) -> list[list[Removal]]:
    """The farm batches of ``removals`` in groups of at most :data:`DELETE_CHUNK`
    job ids, whole batches each (a bigger batch is a group of its own)."""
    groups: list[list[Removal]] = []
    size = 0
    for removal in (r for r in removals if r.source == rows.FARM and r.jobs):
        if groups and size + len(removal.jobs) <= DELETE_CHUNK:
            groups[-1].append(removal)
            size += len(removal.jobs)
        else:
            groups.append([removal])
            size = len(removal.jobs)
    return groups


@dataclass(frozen=True)
class Cleaned:
    """What a clean-up removed, and why it stopped short (``error``)."""

    batches: int = 0
    jobs: int = 0
    error: str | None = None


def delete_farm(removals: list[Removal], control: Callable | None = None) -> Cleaned:
    """Delete the farm batches of ``removals`` from Deadline, a chunk per call.

    A failed call stops there: what was deleted is counted, and the error
    says why the rest was not.
    """
    if control is None:
        from tumblepipe.farm.manager import deadline_jobs
        control = deadline_jobs.control
    done_batches = done_jobs = 0
    for group in delete_chunks(removals):
        ids = [job_id for removal in group for job_id in removal.ids]
        try:
            control('delete', ids)
        except Exception as error:
            return Cleaned(done_batches, done_jobs, str(error) or type(error).__name__)
        done_batches += len(group)
        done_jobs += len(ids)
    return Cleaned(done_batches, done_jobs)


class EntityCleanup:
    """Farm Submit's clean-up: an entity's old finished batches, before it is submitted again.

    Called with an entity's URI, once per entity of a submission, on the
    backend the submission goes to: it reads that backend afresh (the farm
    with ``GetJobs``, this machine's queue from disk), chooses with
    :func:`entity_removals`, and removes. A job's context.json is read at
    most once per run, and only when :func:`batch_entity` asks.

    Args:
        backend: ``farm.backend.FARM`` or ``LOCAL``.
        project: the project name the submission is in.
        fetch_farm / control: Deadline's jobs and job command
            (``deadline_jobs.fetch_jobs`` / ``control``); fakes in tests.
        local_root / request / start: the local queue's folder, its
            request writer and the runner starter.
        read_context: ``(data_dir, arguments) -> dict``, a job's context.json.
    """

    def __init__(
        self, backend: str, project: str, *,
        fetch_farm: Callable | None = None, control: Callable | None = None,
        local_root: Path | None = None, request: Callable | None = None,
        start: Callable | None = None, read_context: Callable | None = None,
        ) -> None:
        from tumblepipe.farm.backend import normalize
        self.backend = normalize(backend)
        self.project = project
        self._fetch_farm = fetch_farm
        self._control = control
        self._local_root = local_root
        self._request = request
        self._start = start
        self._read_context = read_context or rows.read_job_context
        self._contexts: dict[str, str] = {}

    def _rows(self) -> list[rows.JobRow]:
        from tumblepipe.farm.backend import LOCAL
        if self.backend == LOCAL:
            return rows.local_rows(self._root())[0]
        fetch = self._fetch_farm
        if fetch is None:
            from tumblepipe.farm.manager import deadline_jobs
            fetch = deadline_jobs.fetch_jobs
        return list(fetch())

    def _root(self) -> Path:
        return Path(self._local_root) if self._local_root is not None else queue.root()

    def _context_entity(self, job: rows.JobRow) -> str:
        if job.id not in self._contexts:
            self._contexts[job.id] = rows.context_entity(
                self._read_context(job.data_dir, job.arguments) if job.arguments else {}
            )
        return self._contexts[job.id]

    def __call__(self, entity: str) -> Cleaned:
        from tumblepipe.farm.backend import LOCAL
        chosen = entity_removals(self._rows(), self.project, entity, self._context_entity)
        if not chosen:
            return Cleaned()
        if self.backend == LOCAL:
            remove_local(chosen, self._root(), request=self._request, start=self._start)
            return Cleaned(len(chosen), sum(len(r.jobs) for r in chosen))
        return delete_farm(chosen, self._control)
