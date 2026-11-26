"""What the Farm Manager does to a job, on whichever farm runs it.

A job row is this machine's (``rows.LOCAL``: its batch folder in the local
queue, changed through requests its runner applies) or the Deadline farm's
(``rows.FARM``: changed through ``deadlinecommand``, ``deadline_jobs``).
Each operation here branches on ``row.source`` once, so a local job never
reaches Deadline and a farm job never touches the local queue. (Suspend,
delete and priority go to the farm's jobs alone: ``deadline_jobs``.)

No Qt: the window runs these off the GUI thread where they read or write
the farm.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import deadline_jobs, rows


def tasks_of(row: rows.JobRow) -> list[rows.TaskRow]:
    """``row``'s tasks."""
    if row.source == rows.LOCAL:
        return rows.local_task_rows(Path(row.folder), row.job_index)
    return deadline_jobs.fetch_tasks(row.id)


def errors_of(row: rows.JobRow, limit: int = 40) -> list[tuple[str, str]]:
    """The job's error reports as ``(worker, first line)``. A local job
    keeps its errors on its tasks (``tasks_of``), so it has none here."""
    if row.source == rows.LOCAL:
        return []
    return deadline_jobs.job_errors(row.id, limit=limit)


def requeue(row: rows.JobRow, task_ids: Iterable[int]) -> None:
    """Queue ``task_ids`` of ``row`` again."""
    task_ids = tuple(task_ids)
    if row.source == rows.LOCAL:
        queue.request(Path(row.folder), queue.REQUEUE, row.job_index, list(task_ids))
    else:
        deadline_jobs.requeue_tasks(row.id, task_ids)


def pend(row: rows.JobRow) -> None:
    """Make ``row`` wait for its dependencies again. This machine's runner
    waits for a job's dependencies by itself, so a local job needs nothing."""
    if row.source == rows.FARM:
        deadline_jobs.pend(row.id)
