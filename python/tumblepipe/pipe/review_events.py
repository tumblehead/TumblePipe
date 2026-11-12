"""Review media announcing itself on the change journal (``util.events``).

Cinedex shows a shot's playblasts, renders and live playblasts as takes.
It reads a shot's takes from the share when it resolves the shot, and
again only when told to: every write of review media on this machine
appends a journal line naming the shot and department, and the player
resolves that shot again (designs/shot-dashboard-live-playblast.md §5.4,
phase 6). A farm job on another machine writes to that machine's journal,
which nobody reads, so playblasts and renders also go to the project's
shared journal on the share (``util.shared_journal``, #78), which every
machine's player follows; the same id on both, so a player that reads
both resolves the shot once.

The farm workers know only the paths they write, so :func:`owner` reads the
shot and department back out of a path under the ``render:/playblast`` or
``render:/render`` root (``paths.review``, ``paths.render``): the entity's
three segments, then the department.
"""

from __future__ import annotations

import logging
import os

from tumblepipe.api import api
from tumblepipe.util import events, shared_journal
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)

#: The storage roots review media is written under, and the kind of change
#: a write there is.
ROOTS = (('render:/playblast', events.PLAYBLAST), ('render:/render', events.RENDER))
#: Entity kinds whose media live under those roots.
ENTITY_KINDS = ('shots', 'assets')
#: Segments of an entity URI under a root: kind, group, name.
ENTITY_SEGMENTS = 3


def _parts(path) -> list[str]:
    """``path``'s parts as the platform compares them (no case on Windows)."""
    spelled = os.path.normcase(os.path.abspath(str(path)))
    return [p for p in spelled.replace('\\', '/').split('/') if p]


def owner_under(path, root) -> tuple[Uri, str] | None:
    """The entity and department whose media ``path`` is, when it lies
    under ``root`` as ``<kind>/<group>/<name>/<department>/...``; None
    otherwise. Spelled as on disk: the entity from ``path``, not ``root``."""
    whole, base = _parts(path), _parts(root)
    if len(whole) < len(base) + ENTITY_SEGMENTS + 2 or whole[:len(base)] != base:
        return None
    # The original spelling of the parts after the root (normcase lowers).
    original = [p for p in str(os.path.abspath(str(path))).replace('\\', '/').split('/') if p]
    rest = original[len(original) - (len(whole) - len(base)):]
    kind, group, name, department = rest[:ENTITY_SEGMENTS + 1]
    if kind not in ENTITY_KINDS:
        return None
    try:
        uri = Uri.parse(f'entity:/{kind}/{group}/{name}')
    except ValueError:
        return None
    return uri, department


def owner(path) -> tuple[str, Uri, str] | None:
    """``(kind, entity, department)`` of review media at ``path``, None for
    a path under neither review root."""
    for root_uri, kind in ROOTS:
        try:
            root = api.storage.resolve(Uri.parse(root_uri))
        except Exception:  # noqa: BLE001 - no storage: nothing to announce
            return None
        found = owner_under(path, root)
        if found is not None:
            return (kind, *found)
    return None


def announce(kind: str, entity_uri, department: str) -> None:
    """Announce review media of ``entity_uri``'s ``department`` on this
    machine's journal and, for a playblast or render, on the project's
    shared journal under one id. Never raises."""
    try:
        config_path = api.config.config_path
    except Exception as exc:  # noqa: BLE001 - a hint must never break a write
        logger.warning("Could not announce a %s change of %s: %s", kind, entity_uri, exc)
        return
    shared = kind in shared_journal.KINDS
    change_id = shared_journal.new_id() if shared else ''
    events.announce(config_path, kind, entity_uri, department, id=change_id)
    if shared:
        shared_journal.announce(config_path, kind, entity_uri, department, change_id)


def announce_written(paths) -> int:
    """Announce each shot and department that review media at ``paths``
    belongs to, once each; paths elsewhere (a daily) are passed over.
    How many were announced. Never raises."""
    seen = set()
    for path in paths:
        try:
            found = owner(path)
        except Exception:  # noqa: BLE001
            found = None
        if found is None:
            continue
        kind, uri, department = found
        key = (kind, str(uri), department)
        if key in seen:
            continue
        seen.add(key)
        announce(kind, uri, department)
    return len(seen)
