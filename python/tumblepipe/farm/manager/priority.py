"""A farm job's Deadline priority, as the Farm Manager shows and sets it (Remy, 2026-11-20).

The Priority column shows each job's priority and a batch row its jobs'
(one number when they agree, ``low–high`` when not). Editing the cell, or
the job menu's *Set priority*, sets it on the selected farm jobs with one
``deadlinecommand SetJobSetting <ids> Priority <value>`` (the call
deadline-mcp makes), then reads the farm again to check Deadline took it.

Only farm jobs have a priority to set: this machine's queue runs its tasks
in order and keeps the priority only as a record of what was submitted.

Qt-free and Deadline-free: the window passes the rows, and the call that
reaches Deadline is handed in, so the tests drive it with a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tumblepipe.farm.manager import rows

# Deadline's job priority range: 0 to the repository's Maximum Priority,
# 100 unless changed, which is also what TumblePipe submits within
# (farm.job.Job.job_info checks 0..100).
MIN_PRIORITY = 0
MAX_PRIORITY = 100

LABEL = 'Set priority'
TOOLTIP = ('The Deadline priority of the selected farm jobs (a batch row: all of its '
           'jobs). Higher renders first.')


class PriorityNotApplied(RuntimeError):
    """Deadline answered, but a fresh read does not show the new priority."""


@dataclass(frozen=True)
class Preset:
    name: str
    value: int

    @property
    def label(self) -> str:
        return f'{self.name} ({self.value})'


def clamp(value) -> int:
    """``value`` as a priority Deadline takes: a whole number in 0..100."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'Not a priority: {value!r}') from None
    return max(MIN_PRIORITY, min(MAX_PRIORITY, number))


def farm_job_ids(selected: Iterable[rows.JobRow]) -> list[str]:
    """The farm jobs among ``selected`` (a batch row is passed as its jobs),
    each once, in order; this machine's jobs have no priority to set."""
    seen: dict[str, None] = {}
    for row in selected:
        if row.source == rows.FARM and row.id:
            seen.setdefault(row.id, None)
    return list(seen)


def known(values: Iterable[int | None]) -> list[int]:
    return [value for value in values if value is not None]


def text(values: Iterable[int | None]) -> str:
    """The Priority cell: '' when none is known, ``50`` when they agree,
    ``40–60`` when a batch's jobs differ."""
    found = known(values)
    if not found:
        return ''
    low, high = min(found), max(found)
    return str(low) if low == high else f'{low}–{high}'


def start_value(values: Iterable[int | None], default: int = 50) -> int:
    """What the editor starts at: the jobs' most common priority (the
    highest of a tie), ``default`` when none is known."""
    found = known(values)
    if not found:
        return clamp(default)
    counts: dict[int, int] = {}
    for value in found:
        counts[value] = counts.get(value, 0) + 1
    return clamp(max(counts, key=lambda value: (counts[value], value)))


def presets(found) -> list[Preset]:
    """The project's priority presets (``config.farm.list_priority_presets``,
    objects with ``name`` and ``value``, or ``(name, value)`` pairs) as menu
    entries: highest first, then by name; a preset whose value is not a
    number is left out, one out of range clamped, a repeated name kept once."""
    out: dict[str, Preset] = {}
    seen: set[str] = set()
    for each in found or ():
        name, value = (each if isinstance(each, tuple) else (getattr(each, 'name', None),
                                                             getattr(each, 'value', None)))
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        if isinstance(value, bool):
            continue
        try:
            out[name] = Preset(name, clamp(value))
        except ValueError:
            continue
    return sorted(out.values(), key=lambda p: (-p.value, p.name))


def changed(selected: Iterable[rows.JobRow], value: int) -> list[str]:
    """The farm jobs among ``selected`` whose priority is not ``value`` yet."""
    by_id = {row.id: row for row in selected if row.source == rows.FARM}
    return [job_id for job_id in farm_job_ids(by_id.values()) if by_id[job_id].priority != value]


def unapplied(fresh: Iterable[rows.JobRow], job_ids: Iterable[str], value: int) -> list[str]:
    """The ``job_ids`` a fresh read of the farm does not show at ``value``;
    a job gone from the farm (deleted meanwhile) is not counted."""
    by_id = {row.id: row for row in fresh}
    return [job_id for job_id in job_ids if job_id in by_id and by_id[job_id].priority != value]


def set_priority(
    job_ids: Iterable[str], value,
    send: Callable[[list[str], int], object],
    fetch: Callable[[], list[rows.JobRow]] | None = None,
    ) -> int:
    """Set ``value`` (clamped) on ``job_ids`` with one ``send(ids, value)``;
    with ``fetch``, read the farm again and raise :class:`PriorityNotApplied`
    when a job does not show it. Returns the value set. Nothing is sent for
    no jobs."""
    ids = list(dict.fromkeys(str(job_id) for job_id in job_ids if job_id))
    number = clamp(value)
    if not ids:
        return number
    send(ids, number)
    if fetch is not None:
        missed = unapplied(fetch(), ids, number)
        if missed:
            more = f' and {len(missed) - 3} more' if len(missed) > 3 else ''
            raise PriorityNotApplied(
                f'Deadline did not take priority {number} for {len(missed)} of {len(ids)} '
                f'job(s): {", ".join(missed[:3])}{more}'
            )
    return number
