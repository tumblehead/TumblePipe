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

It reads only while someone asks (:meth:`FarmActivity.touch`): a browser
that is closed or hidden stops asking, and ten seconds later the thread
stops reading until it is asked again, so an idle session puts no load on
the farm's database.

No Qt: the thread reads files and runs ``deadlinecommand``, nothing else
(*Qt and threads* in docs/development.md). It is a daemon, so it never
keeps Houdini from exiting, and :meth:`FarmActivity.stop` ends it when the
catalog is cleaned up.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import rows

log = logging.getLogger(__name__)

LOCAL_INTERVAL = 2.0
FARM_INTERVAL = 20.0
# Seconds without an ask after which the thread stops reading.
IDLE_AFTER = 10.0

ACTIVE = (queue.QUEUED, queue.RENDERING)


def active_count(job_rows: list[rows.JobRow], *, user: str = '', project: str = '') -> int:
    """How many of ``job_rows`` are queued or rendering, of ``user`` in
    ``project`` ('' for any user, any project)."""
    return sum(
        1 for row in job_rows
        if row.status in ACTIVE
        and (not user or row.user.lower() == user.lower())
        and (not project or rows.row_project(row).lower() == project.lower())
    )


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

    :attr:`busy` and :attr:`count` read what the thread last counted; they
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
        ) -> None:
        self._local_rows = local_rows
        self._farm_rows = farm_rows
        self._farm_ready = farm_ready
        self._who = who
        self._local_interval = local_interval
        self._farm_interval = farm_interval
        self._idle_after = idle_after
        self._clock = clock
        self._local: list = []
        self._farm: list = []
        self._next_farm = 0.0
        self._asked: float | None = None
        self._count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def count(self) -> int:
        return self._count

    @property
    def busy(self) -> bool:
        return self._count > 0

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
        """Whether nobody has asked for a while (or ever): the thread does not read."""
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
        self._count = active_count(self._local + self._farm, user=user, project=project)
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
