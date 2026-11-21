"""Render again: the Farm Manager's repair of some tasks of a job (#92).

A frame came out glitched (gabriel, a GPU error), and the job that rendered
it is still in the Farm Manager. *Render again* on its task renders those
frames again into the same version, from the same job files, and the jobs
that follow it (denoise, slapcomp, mp4s, notify, comp) do their part again.

Requeuing a task alone would do nothing: every frame worker skips a frame
whose receipt (``receipt_path``, its resume marker) exists. So the plan
names, per job, the tasks to requeue and the receipts to delete first:

- the chosen job: the selected tasks, and every receipt of their frames;
- each job that waits on it, directly or through others, in the same
  batch: when its context has a ``receipt_path``, the tasks holding a frame
  rendered again, and only those frames' receipts (a denoise of 20 frames
  denoises the one frame again, not 20); when it has none (an mp4, a
  notify, a sync: they write their outputs whole), all of its tasks. A job
  that waits is put back to wait for the jobs it depends on, so a denoise
  never reads a frame before it is rendered again.

Pure: rows, tasks and contexts come in, a :class:`Plan` comes out. The
window reads what it needs off the GUI thread and carries the plan out
(:func:`carry_out`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tumblepipe.farm.frames import FRAME_TOKEN, frame_file
from tumblepipe.farm.manager import rows
from tumblepipe.farm.manager.run_here import RunHereError, parse_frames

LABEL = 'Render again'

TOOLTIP = (
    'Render the selected tasks again into the same version, from the same '
    'job files, and the frames of them in the jobs that follow (denoise, '
    'slapcomp, mp4s, comp): for a glitched frame. Their receipts are '
    'deleted first, or the workers would skip frames already done.'
)


class RenderAgainError(ValueError):
    """Why the tasks cannot be rendered again."""


@dataclass(frozen=True)
class Part:
    """One job's share: the tasks to requeue and the receipts to delete
    first. ``waits``: a job after the chosen one, put back to wait for the
    jobs it depends on."""
    row: rows.JobRow
    tasks: tuple[int, ...]
    receipts: tuple[Path, ...]
    waits: bool


@dataclass(frozen=True)
class Plan:
    frames: tuple[int, ...]
    parts: tuple[Part, ...]

    @property
    def task_count(self) -> int:
        return sum(len(part.tasks) for part in self.parts)

    @property
    def receipt_count(self) -> int:
        return sum(len(part.receipts) for part in self.parts)


def task_frames(task: rows.TaskRow) -> list[int]:
    """Every frame number a task's span covers, first to last: a stepped
    task's frames in between included, which hold no receipt anyway."""
    try:
        frames = parse_frames(task.frames)
    except RunHereError as e:
        raise RenderAgainError(f'task {task.index}: {e}') from e
    if not frames:
        return []
    return list(range(frames[0], frames[-1] + 1))


def dependents(job: rows.JobRow, all_rows: Iterable[rows.JobRow]) -> list[rows.JobRow]:
    """The jobs of ``job``'s batch that wait on it, directly or through
    each other, in dependency order."""
    batch = [
        row for row in all_rows
        if row.source == job.source and row.batch_key == job.batch_key and row.id != job.id
    ]
    # Every job that waits on it, directly or through another
    reached = {job.id}
    changed = True
    while changed:
        changed = False
        for row in batch:
            if row.id not in reached and any(dep in reached for dep in row.deps):
                reached.add(row.id)
                changed = True
    # Then each after every one of them it waits on: a comp waiting on the
    # render and the denoise is requeued (and pended) after the denoise,
    # whatever order the jobs are listed in.
    waiting = [row for row in batch if row.id in reached]
    placed = {job.id}
    ordered: list[rows.JobRow] = []
    while waiting:
        ready = [row for row in waiting
                 if all(dep in placed or dep not in reached for dep in row.deps)]
        if not ready:
            # A dependency cycle (never written by a submit): keep the rest
            # in listed order rather than drop them.
            ready = waiting[:1]
        for row in ready:
            placed.add(row.id)
            ordered.append(row)
        waiting = [row for row in waiting if row.id not in placed]
    return ordered


def receipt_pattern(context: dict | None) -> Path | None:
    """A frame job's receipt pattern (its context's ``receipt_path``), None
    for a job that writes no per-frame receipts."""
    if not isinstance(context, dict):
        return None
    pattern = context.get('receipt_path')
    if not isinstance(pattern, str) or FRAME_TOKEN not in Path(pattern).name:
        return None
    return Path(pattern)


def _receipts(pattern: Path | None, frames: Iterable[int], exists: Callable[[Path], bool]) -> list[Path]:
    if pattern is None:
        return []
    return [path for path in (frame_file(pattern, f) for f in sorted(set(frames))) if exists(path)]


def plan(
    job: rows.JobRow,
    selected: list[rows.TaskRow],
    all_rows: list[rows.JobRow],
    *,
    tasks_of: Callable[[rows.JobRow], list[rows.TaskRow]],
    context_of: Callable[[rows.JobRow], dict | None],
    exists: Callable[[Path], bool],
) -> Plan:
    """What *Render again* does for ``selected`` tasks of ``job``.

    Args:
        job: The job the tasks are of.
        selected: The tasks picked.
        all_rows: Every job listed (the batch's other jobs among them).
        tasks_of: A job's tasks.
        context_of: A job's ``context.json`` (``rows.read_job_context``).
        exists: Whether a file exists.
    """
    if not selected:
        raise RenderAgainError('no task selected')
    frames: set[int] = set()
    for task in selected:
        frames.update(task_frames(task))
    if not frames:
        raise RenderAgainError('the selected tasks have no frames')
    parts = [Part(
        row = job,
        tasks = tuple(sorted({task.index for task in selected})),
        receipts = tuple(_receipts(receipt_pattern(context_of(job)), frames, exists)),
        waits = False,
    )]
    for row in dependents(job, all_rows):
        tasks = tasks_of(row)
        pattern = receipt_pattern(context_of(row))
        if pattern is None:
            chosen = [task.index for task in tasks]
            receipts: list[Path] = []
        else:
            chosen, again = [], set()
            for task in tasks:
                inside = frames.intersection(task_frames(task))
                if inside:
                    chosen.append(task.index)
                    again.update(inside)
            receipts = _receipts(pattern, again, exists)
        if chosen:
            parts.append(Part(
                row = row, tasks = tuple(sorted(chosen)), receipts = tuple(receipts),
                waits = True,
            ))
    return Plan(frames = tuple(sorted(frames)), parts = tuple(parts))


def summary(the_plan: Plan) -> str:
    """``3 frames, 5 tasks in 4 jobs``: what the confirmation names."""
    frames = len(the_plan.frames)
    jobs = len(the_plan.parts)
    tasks = the_plan.task_count
    return (
        f"{frames} frame{'s' if frames != 1 else ''}, {tasks} task{'s' if tasks != 1 else ''} "
        f"in {jobs} job{'s' if jobs != 1 else ''}"
    )


def carry_out(
    the_plan: Plan,
    *,
    delete: Callable[[Path], None],
    requeue: Callable[[rows.JobRow, tuple[int, ...]], None],
    wait: Callable[[rows.JobRow], None],
) -> None:
    """Delete each part's receipts, then requeue its tasks, the chosen job
    first; a waiting part is put back to wait for its dependencies once its
    tasks are queued. Raises on the first step that fails, having done the
    steps before it."""
    for part in the_plan.parts:
        for path in part.receipts:
            delete(path)
        requeue(part.row, part.tasks)
        if part.waits:
            wait(part.row)
