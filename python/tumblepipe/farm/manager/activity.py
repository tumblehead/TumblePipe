"""Whether this user has farm work under way, for the Farm Manager quick action's busy outline.

The asset browser asks the catalog every second
(``Catalog.get_quick_action_busy``), on the GUI thread, so the answer must
be at hand: :class:`FarmActivity` keeps it current on a daemon thread of
its own, and the question only reads it.

The thread counts the active jobs (queued or rendering) of the pipeline
user (``TH_USER``; everyone's when it is unset) in the current project
(``TH_PROJECT_PATH``; every project's when unset):

- this machine's queue, read from disk every couple of seconds;
- the Deadline farm, read with ``deadlinecommand`` (a second or more a
  call) every 20 seconds, and only when this machine can reach Deadline
  (``farm.backend.setup_problem``).

From the same reads it keeps a :class:`Summary` of the user's recent work
in the project (jobs still under way, plus those submitted in the last
:data:`RECENT_HOURS`): the shots or assets, the jobs by state, the tasks
done, a rough finish time, and which failed. The Farm Manager button's
hover popup shows it (:func:`summary_html`), and its warning badge
(``Catalog.get_quick_action_alert``) shows while any of those jobs is
failed: retrying or removing the job takes the badge away.

It reads only while someone asks (:meth:`FarmActivity.touch`): a browser
that is closed or hidden stops asking, and ten seconds later the thread
stops reading until it is asked again, so an idle session puts no load on
the farm's database. The one exception: while a batch the artist submitted
in this Houdini session is still running, it keeps reading, so the end of
that batch is noticed even with the browser closed.

From the same reads it feeds :class:`finished.SessionWatch`: the batches of
this session that ended, for the Jobs popover, the Farm Manager button's dot
and pulse, the status line and the OS notification. A batch that ends is
passed once to the ``on_finished`` callback, on this thread.

No Qt: the thread reads files and runs ``deadlinecommand``, nothing else
(*Qt and threads* in docs/development.md). It is a daemon, so it never
keeps Houdini from exiting, and :meth:`FarmActivity.stop` ends it when the
catalog is cleaned up.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import finished, rows

log = logging.getLogger(__name__)

LOCAL_INTERVAL = 2.0
FARM_INTERVAL = 20.0
# Seconds without an ask after which the thread stops reading.
IDLE_AFTER = 10.0

ACTIVE = (queue.QUEUED, queue.RENDERING)
# Jobs submitted this many hours ago or less are in the summary, as are
# older ones still under way.
RECENT_HOURS = 24.0
# The summary's waiting jobs: queued, waiting on a dependency, or paused.
WAITING = (queue.QUEUED, queue.PENDING, queue.SUSPENDED)


def active_count(job_rows: list[rows.JobRow], *, user: str = '', project: str = '') -> int:
    """How many of ``job_rows`` are queued or rendering, of ``user`` in
    ``project`` ('' for any user, any project)."""
    return sum(
        1 for row in job_rows
        if row.status in ACTIVE
        and (not user or row.user.lower() == user.lower())
        and (not project or rows.row_project(row).lower() == project.lower())
    )


def mine(row: rows.JobRow, *, user: str = '', project: str = '') -> bool:
    """Whether ``row`` is ``user``'s in ``project`` ('' for any)."""
    return (
        (not user or row.user.lower() == user.lower())
        and (not project or rows.row_project(row).lower() == project.lower())
    )


def recent_rows(
    job_rows: list[rows.JobRow], now: dt.datetime, *,
    user: str = '', project: str = '', hours: float = RECENT_HOURS,
    ) -> list[rows.JobRow]:
    """``user``'s jobs in ``project`` the summary covers: every one not
    yet ended (:func:`rows.ended`), and every one submitted at most
    ``hours`` before ``now``. A job with no readable submit time counts
    only while it has not ended."""
    since = now - dt.timedelta(hours=hours)
    picked = []
    for row in job_rows:
        if not mine(row, user=user, project=project):
            continue
        submitted = rows.parse_time(row.submitted)
        if not rows.ended(row) or (submitted is not None and submitted >= since):
            picked.append(row)
    return picked


def _uri_parts(uri: str) -> list[str]:
    return [p for p in uri.split(':', 1)[-1].split('/') if p] if uri else []


def entity_label(uri: str) -> str:
    """``entity:/shots/010/0040`` as ``010/0040``: the entity's path after its kind."""
    parts = _uri_parts(uri)
    return '/'.join(parts[1:]) if len(parts) > 1 else '/'.join(parts)


def entity_kind(uri: str) -> str:
    """``shots``, ``assets``, ``groups`` or ``edits`` for an entity URI; '' for none."""
    parts = _uri_parts(uri)
    return parts[0] if parts else ''


def job_label(row: rows.JobRow) -> str:
    """What the summary calls ``row``'s work: its entity, else its batch's name."""
    return entity_label(rows.row_entity(row)) or row.batch or row.name


@dataclass(frozen=True)
class Summary:
    """The user's recent farm work in the project, for the button's hover.

    ``eta`` is when the active batches should all be done (local time), or
    None when it cannot be guessed: nothing is under way, or some active
    batch has no task done yet.
    """
    entities: tuple[str, ...] = ()      # entity URIs, sorted
    jobs: int = 0
    running: int = 0
    waiting: int = 0                    # queued, pending or suspended
    completed: int = 0
    failed: int = 0
    tasks_done: int = 0
    tasks_total: int = 0
    failed_labels: tuple[str, ...] = ()  # what failed, sorted, once each
    eta: dt.datetime | None = None

    @property
    def active(self) -> int:
        return self.running + self.waiting

    @property
    def alert(self) -> bool:
        return self.failed > 0


def estimate_finish(job_rows: list[rows.JobRow], now: dt.datetime) -> dt.datetime | None:
    """About when the batches still under way in ``job_rows`` are all done.

    Per batch with a job not ended: the time since its first job started,
    scaled by the share of its tasks still to do (failed or cancelled jobs
    that have ended are left out: their tasks will not run). Batches run
    side by side, so the latest batch sets the time. None when nothing is
    under way, or any active batch has not started or has no task done:
    there is nothing to scale.
    """
    batches: dict[str, list[rows.JobRow]] = {}
    for row in job_rows:
        batches.setdefault(row.batch_key, []).append(row)
    latest = None
    for batch in batches.values():
        if all(rows.ended(row) for row in batch):
            continue
        counted = [
            row for row in batch
            if not (rows.ended(row) and row.status in (queue.FAILED, queue.CANCELLED))
        ]
        done = sum(max(0, row.done) for row in counted)
        total = sum(max(0, row.total) for row in counted)
        starts = [t for t in (rows.parse_time(row.started) for row in counted) if t is not None]
        if not starts or done <= 0 or total <= 0:
            return None
        elapsed = (now - min(starts)).total_seconds()
        if elapsed <= 0:
            return None
        fraction = min(1.0, done / total)
        finish = now + dt.timedelta(seconds=elapsed * (1.0 - fraction) / fraction)
        latest = finish if latest is None or finish > latest else latest
    return latest


def summarize(job_rows: list[rows.JobRow], now: dt.datetime) -> Summary:
    """The :class:`Summary` of ``job_rows`` (already the user's recent ones:
    :func:`recent_rows`)."""
    return Summary(
        entities=tuple(sorted({rows.row_entity(r) for r in job_rows} - {''})),
        jobs=len(job_rows),
        running=sum(1 for r in job_rows if r.status == queue.RENDERING),
        waiting=sum(1 for r in job_rows if r.status in WAITING),
        completed=sum(1 for r in job_rows if r.status == queue.COMPLETED),
        failed=sum(1 for r in job_rows if r.status == queue.FAILED),
        tasks_done=sum(max(0, r.done) for r in job_rows),
        tasks_total=sum(max(0, r.total) for r in job_rows),
        failed_labels=tuple(sorted({job_label(r) for r in job_rows if r.status == queue.FAILED})),
        eta=estimate_finish(job_rows, now),
    )


def _count(n: int, one: str, many: str) -> str:
    return f'{n} {one if n == 1 else many}'


def entities_text(entities: tuple[str, ...]) -> str:
    """``3 shots``, ``1 asset``, ``4 entities`` (mixed kinds)."""
    kinds = {entity_kind(uri) for uri in entities}
    kind = kinds.pop() if len(kinds) == 1 else ''
    one, many = {'shots': ('shot', 'shots'), 'assets': ('asset', 'assets')}.get(
        kind, ('entity', 'entities'),
    )
    return _count(len(entities), one, many)


def eta_text(eta: dt.datetime, now: dt.datetime) -> str:
    """``about 14:35 (in 1 h 20 min)``; a later day names its weekday."""
    left = max(0, int((eta - now).total_seconds()))
    hours, rest = divmod(left, 3600)
    minutes = rest // 60
    if hours:
        within = f'{hours} h {minutes} min' if minutes else f'{hours} h'
    else:
        within = f'{max(1, minutes)} min'
    clock = eta.strftime('%H:%M') if eta.date() == now.date() else eta.strftime('%a %H:%M')
    return f'about {clock} (in {within})'


#: How many failed shots the hover names before "N more".
FAILED_NAMED = 6


def summary_html(
    summary: Summary | None, now: dt.datetime, *,
    hours: float = RECENT_HOURS, dim: str = '#a0a0a0', warn: str = '#f0a030',
    ) -> str:
    """The Farm Manager button's hover body: the summary as compact rich text.

    One line when nothing ran recently, or before the first count.
    """
    window = f'{hours:g} hours'
    if summary is None:
        return f"<div style='color:{dim};'>Counting your farm jobs...</div>"
    if summary.jobs == 0:
        return f"<div style='color:{dim};'>No farm jobs of yours in the last {window}.</div>"
    lines = [f"<div style='color:{dim};'>Your jobs: the last {window}, and any still running</div>"]
    head = _count(summary.jobs, 'job', 'jobs')
    if summary.entities:
        head = f'{entities_text(summary.entities)} &middot; {head}'
    lines.append(f"<div style='font-weight:600;'>{head}</div>")
    failed = f'{summary.failed} failed'
    if summary.failed:
        failed = f"<span style='color:{warn}; font-weight:600;'>{failed}</span>"
    states = [
        f'{summary.running} running', f'{summary.waiting} queued',
        f'{summary.completed} completed', failed,
    ]
    lines.append(f"<div>{' &middot; '.join(states)}</div>")
    if summary.tasks_total:
        lines.append(f'<div>Tasks: {summary.tasks_done} of {summary.tasks_total} done</div>')
    if summary.active and summary.eta is not None:
        lines.append(f'<div>Done {eta_text(summary.eta, now)}</div>')
    if summary.failed_labels:
        named = [html.escape(label) for label in summary.failed_labels[:FAILED_NAMED]]
        more = len(summary.failed_labels) - len(named)
        if more:
            named.append(f'{more} more')
        lines.append(f"<div style='color:{warn};'>&#9888; Failed: {', '.join(named)}</div>")
    return ''.join(lines)


def identity(environ=None) -> tuple[str, str]:
    """``(user, project)`` this session works as; '' for what is not set."""
    environ = os.environ if environ is None else environ
    project_path = (environ.get('TH_PROJECT_PATH') or '').strip()
    project = Path(project_path.replace('\\', '/')).name if project_path else ''
    return environ.get('TH_USER') or '', project


def _local_rows() -> list[rows.JobRow]:
    return rows.local_rows(queue.root())[0]


def _farm_rows() -> list[rows.JobRow]:
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.fetch_jobs()


def _farm_ready() -> bool:
    from tumblepipe.farm.backend import FARM, setup_problem
    return setup_problem(FARM) is None


class FarmActivity:
    """The user's active farm jobs, counted on a daemon thread.

    :attr:`busy`, :attr:`count`, :attr:`summary` and :attr:`alert` read
    what the thread last counted; they
    never wait on it. A source that cannot be read keeps its last count
    (a Deadline hiccup does not blink the outline off), and the farm counts
    nothing while this machine cannot reach Deadline.

    The sources and the clock are arguments, for tests.
    """

    def __init__(
        self, *,
        local_rows: Callable[[], list] = _local_rows,
        farm_rows: Callable[[], list] = _farm_rows,
        farm_ready: Callable[[], bool] = _farm_ready,
        who: Callable[[], tuple[str, str]] = identity,
        local_interval: float = LOCAL_INTERVAL,
        farm_interval: float = FARM_INTERVAL,
        idle_after: float = IDLE_AFTER,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], dt.datetime] = dt.datetime.now,
        recent_hours: float = RECENT_HOURS,
        session: 'finished.SessionWatch | None' = None,
        on_finished: Callable[[list], None] | None = None,
        ) -> None:
        self._local_rows = local_rows
        self._farm_rows = farm_rows
        self._farm_ready = farm_ready
        self._who = who
        self._local_interval = local_interval
        self._farm_interval = farm_interval
        self._idle_after = idle_after
        self._clock = clock
        self._wall = wall
        self._recent_hours = recent_hours
        self._summary: Summary | None = None
        self._local: list = []
        self._farm: list = []
        self._next_farm = 0.0
        self._asked: float | None = None
        self._count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = session if session is not None else finished.SessionWatch()
        self._on_finished = on_finished

    @property
    def count(self) -> int:
        return self._count

    @property
    def busy(self) -> bool:
        return self._count > 0

    @property
    def summary(self) -> Summary | None:
        """The user's recent work as last counted; None before the first count."""
        return self._summary

    @property
    def alert(self) -> bool:
        """Whether some of the user's recent jobs here are failed."""
        return self._summary is not None and self._summary.alert

    @property
    def recent_hours(self) -> float:
        return self._recent_hours

    @property
    def session(self) -> 'finished.SessionWatch':
        """The batches of this Houdini session that ended (the Jobs popover)."""
        return self._session

    def set_on_finished(self, callback: Callable[[list], None] | None) -> None:
        """Call ``callback(reports)`` (on this watcher's thread) with the
        batches that ended at each read; each batch once."""
        self._on_finished = callback

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start counting, once; a stopped watcher stays stopped."""
        if self._thread is not None or self._stop.is_set():
            return
        self._thread = threading.Thread(
            target=self._run, name='tumblepipe-farm-activity', daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """End the thread after the step it is on. Does not wait for it: a
        ``deadlinecommand`` under way finishes on its own."""
        self._stop.set()

    def touch(self) -> None:
        """Someone asked: keep reading for :data:`IDLE_AFTER` seconds more."""
        self._asked = self._clock()

    def idle(self) -> bool:
        """Whether nobody has asked for a while (or ever), and no batch of
        this session is still running: the thread does not read."""
        if self._session.watching:
            return False
        return self._asked is None or self._clock() - self._asked > self._idle_after

    def poll(self) -> int:
        """One step: read what is due, count, and return the count."""
        try:
            self._local = list(self._local_rows())
        except Exception:
            log.debug('Farm activity: the local queue could not be read', exc_info=True)
        now = self._clock()
        if now >= self._next_farm:
            self._next_farm = now + self._farm_interval
            try:
                self._farm = list(self._farm_rows()) if self._farm_ready() else []
            except Exception:
                log.debug('Farm activity: Deadline could not be read', exc_info=True)
        user, project = self._who()
        job_rows = self._local + self._farm
        self._count = active_count(job_rows, user=user, project=project)
        now = self._wall()
        recent = recent_rows(
            job_rows, now, user=user, project=project, hours=self._recent_hours,
        )
        # One assignment: a reader on another thread sees the old summary or the new.
        self._summary = summarize(recent, now)
        try:
            ended = self._session.update(job_rows, now, user=user, project=project)
        except Exception:
            log.debug('Farm activity: the session watch failed', exc_info=True)
            ended = []
        if ended and self._on_finished is not None:
            try:
                self._on_finished(ended)
            except Exception:
                log.debug('Farm activity: announcing finished jobs failed', exc_info=True)
        return self._count

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.idle():
                self._stop.wait(self._local_interval)
                continue
            try:
                self.poll()
            except Exception:
                log.debug('Farm activity: a step failed', exc_info=True)
            self._stop.wait(self._local_interval)
