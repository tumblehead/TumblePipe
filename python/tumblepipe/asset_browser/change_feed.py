"""Following this machine's change journal: other sessions' writes, shown.

An artist publishes from one Houdini, tabs to another, and its browser
must show the new department versions without a Refresh. Each pipeline
write appends a line to the same-machine journal (``util.events``); a
:class:`ChangeFeed` reads the new lines on a timer and turns them into the
same refreshes the session's own writes ask for:

- a workfile commit or an export of an asset or shot rebuilds that card,
  off the GUI thread, as a save in this session does;
- one of a Multi (shown on every member's card), a config write by
  another process (the entity list or a Multi may have changed), or a
  journal that may have lost lines drops the caches and re-queries the
  grids, as Refresh does. A config write also expires that project's
  config stamps, so the next read checks the disk instead of trusting a
  stamp window (``config.store.set_stamp_ttl``).

This session's own config writes are left out: the code that made them
already refreshed what they change. Its own workfile commits and exports
are not, since only some paths that make them refresh the browser, and
one extra card rebuild costs a single card scan.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable

from tumblepipe.util import events
from tumblepipe.util.uri import Uri

from . import uris

log = logging.getLogger(__name__)

# How often an open browser looks at the journal: a local stat() each time.
POLL_MS = 500


@dataclass
class Plan:
    """What one batch of changes asks of the browser."""
    refresh_all: bool = False
    cards: set[str] = field(default_factory=set)
    expire: set[str] = field(default_factory=set)  # config folders

    def __bool__(self) -> bool:
        return self.refresh_all or bool(self.cards) or bool(self.expire)


def plan(
    changes: list[events.Change] | None,
    projects: Iterable,
    own_pid: int,
) -> Plan:
    """The refreshes ``changes`` (``JournalReader.poll``) ask for.

    ``projects`` are the browser's ``ProjectConfig``s; a change to a
    project it does not show is ignored. None (lines may be lost) asks
    for everything. Pure, for the tests.
    """
    if changes is None:
        return Plan(refresh_all=True)
    by_key = {events.project_key(p.config_path): p for p in projects}
    result = Plan()
    for change in changes:
        proj = by_key.get(change.project)
        if proj is None:
            continue
        if change.kind == events.CONFIG:
            if change.pid != own_pid:
                result.expire.add(proj.config_path)
                result.refresh_all = True
            continue
        if change.kind not in (events.WORKFILE, events.EXPORT):
            continue
        try:
            uri = Uri.parse(change.uri)
            result.cards.add(uris.asset_id_for(proj.name, uri))
        except ValueError:
            # A Multi's workfile shows on every member's card, and its
            # card is a collection: refresh everything, as its save does.
            result.refresh_all = True
    if result.refresh_all:
        result.cards.clear()
    return result


class ChangeFeed:
    """Applies the journal's changes to one catalog.

    ``rebuild_card(asset_id)`` rebuilds one card off the GUI thread
    (``PipelineCatalog.rebuild_card_async``); ``refresh_all()`` drops the
    caches and re-queries the grids; ``refresh_detail()`` re-fetches the
    detail panels; ``expire(config_path)`` makes that project's next
    config read check the disk.
    """

    def __init__(
        self,
        projects: Callable[[], Iterable],
        rebuild_card: Callable[[str], None],
        refresh_all: Callable[[], None],
        refresh_detail: Callable[[], None],
        expire: Callable[[str], None],
        reader: events.JournalReader | None = None,
        own_pid: int | None = None,
    ) -> None:
        self._projects = projects
        self._rebuild_card = rebuild_card
        self._refresh_all = refresh_all
        self._refresh_detail = refresh_detail
        self._expire = expire
        self._reader = reader or events.JournalReader()
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._timer = None

    def poll(self) -> Plan:
        """Read the new lines and refresh what they name; the plan applied.
        Never raises: a broken poll must not stop the next one."""
        try:
            todo = plan(self._reader.poll(), self._projects(), self._own_pid)
            for config_path in todo.expire:
                self._expire(config_path)
            if todo.refresh_all:
                self._refresh_all()
            for asset_id in sorted(todo.cards):
                self._rebuild_card(asset_id)
            if todo:
                self._refresh_detail()
            return todo
        except Exception:
            log.exception("Following the change journal failed")
            return Plan()

    def start(self, interval_ms: int = POLL_MS) -> None:
        """Poll on a Qt timer on this (the GUI) thread, from now on."""
        if self._timer is not None:
            return
        from PySide6.QtCore import QTimer

        self._reader.poll()  # begin at the journal's end
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
