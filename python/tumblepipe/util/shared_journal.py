"""The project's shared journal: review media written on any machine (#78).

The change journal (``util.events``) is per machine: a farm job writing a
playblast or render on a farm worker appends to that worker's journal,
which no Cinedex reads, so the media showed only after the player's Refresh.
Review media therefore also announce themselves here, in a folder on the
project share, which Cinedex follows alongside its own machine's journal
(``th_pipe::shared_journal``) and resolves the shot again.

Append-safe across machines: SMB and NFS give no atomic append and no
lock two machines both honour, so every change is a file of its own,
written under a temporary name and renamed into place. A reader lists the
folder and reads the names it has not seen; it never sees half a change.

Names: ``<ms>-<id>.json``, ``<ms>`` the writer's clock in milliseconds
since the epoch, 13 digits so names sort in time order, ``<id>`` 32 hex
digits, the change's id (also on the local journal's line of the same
change, so a reader following both acts on it once). While being written:
``.<ms>-<id>.tmp``. Content: one JSON object, ``{"id", "kind", "uri",
"detail", "host", "time"}`` and a newline; ``kind`` is ``playblast`` or
``render``, ``uri`` the entity, ``detail`` its department.

Bounded: every write prunes the folder: changes and temporary files older
than :data:`MAX_AGE`, then the oldest changes past :data:`MAX_FILES`. A
reader that missed a pruned change only misses a hint; Refresh still reads
everything. Ages go by the pruning writer's own clock: a machine whose
clock runs more than :data:`MAX_AGE` ahead prunes everyone's fresh changes
on each of its writes. Readers poll every 3 s, so they read most changes
before that, and Refresh covers the rest. Like the local journal it is a hint, never the source of
truth: :func:`announce` never raises.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.util import events

logger = logging.getLogger(__name__)

#: The folder under the project's config folder (``_config``).
DIR_NAME = 'journal'
#: The kinds of change that go here: review media, which any machine writes.
KINDS = (events.PLAYBLAST, events.RENDER)
#: Changes older than this (seconds, by their name) are pruned.
MAX_AGE = 60 * 60
#: At most this many changes are kept; the oldest go first.
MAX_FILES = 1000

_TIME_DIGITS = 13
_EVENT = re.compile(r'^(\d{13})-([0-9a-f]{32})\.json$')
_TEMP = re.compile(r'^\.(\d{13})-([0-9a-f]{32})\.tmp$')


@dataclass(frozen=True)
class Event:
    """One change on the shared journal."""
    id: str
    kind: str
    uri: str
    detail: str
    host: str
    time: float


def shared_dir(config_path) -> Path:
    """The shared journal's folder of the project whose config folder is
    ``config_path``."""
    return Path(config_path) / DIR_NAME


def event_name(millis: int, event_id: str) -> str:
    return f'{millis:0{_TIME_DIGITS}d}-{event_id}.json'


def temp_name(millis: int, event_id: str) -> str:
    return f'.{millis:0{_TIME_DIGITS}d}-{event_id}.tmp'


def name_time(name: str) -> int | None:
    """The milliseconds a change's or temporary file's name holds; None for
    any other name."""
    found = _EVENT.match(name) or _TEMP.match(name)
    return int(found.group(1)) if found else None


def is_event_name(name: str) -> bool:
    return _EVENT.match(name) is not None


def encode(event: Event) -> bytes:
    return (json.dumps({
        'id': event.id, 'kind': event.kind, 'uri': event.uri,
        'detail': event.detail, 'host': event.host, 'time': event.time,
    }) + '\n').encode('utf-8')


def decode(data: bytes) -> Event | None:
    """The change ``data`` holds; None for anything else (torn, garbage)."""
    try:
        value = json.loads(data)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    texts = [value.get(key) for key in ('id', 'kind', 'uri', 'detail')]
    if not all(isinstance(text, str) for text in texts):
        return None
    moment = value.get('time')
    if isinstance(moment, bool) or not isinstance(moment, (int, float)):
        return None
    host = value.get('host', '')
    return Event(*texts, host=host if isinstance(host, str) else '', time=float(moment))


def write(folder: Path, event: Event, now: float | None = None) -> Path:
    """Write ``event`` into ``folder`` (made if missing) and prune it.
    Raises on failure; :func:`announce` is the caller that must not."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    millis = int((time.time() if now is None else now) * 1000)
    temp = folder / temp_name(millis, event.id)
    final = folder / event_name(millis, event.id)
    try:
        with open(temp, 'xb') as handle:
            handle.write(encode(event))
        # A rename within one folder is atomic on SMB and NFS: a reader sees
        # no file, or all of it.
        os.rename(temp, final)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    prune(folder, now=millis / 1000)
    return final


def prune(folder: Path, now: float | None = None,
          max_age: float | None = None, max_files: int | None = None) -> int:
    """Remove changes and temporary files older than ``max_age`` seconds
    (:data:`MAX_AGE`), then the oldest changes past ``max_files``
    (:data:`MAX_FILES`). How many went. Never raises: another machine may
    be pruning the same files."""
    max_age = MAX_AGE if max_age is None else max_age
    max_files = MAX_FILES if max_files is None else max_files
    cutoff = int(((time.time() if now is None else now) - max_age) * 1000)
    try:
        names = [entry.name for entry in os.scandir(folder)]
    except OSError:
        return 0
    doomed, kept = [], []
    for name in names:
        moment = name_time(name)
        if moment is None:
            continue
        if moment < cutoff:
            doomed.append(name)
        elif is_event_name(name):
            kept.append(name)
    kept.sort()
    doomed += kept[:max(0, len(kept) - max_files)]
    removed = 0
    for name in doomed:
        try:
            os.unlink(os.path.join(folder, name))
            removed += 1
        except OSError:
            pass
    return removed


def new_id() -> str:
    return uuid.uuid4().hex


def announce(config_path, kind: str, uri, detail: str, event_id: str | None = None) -> None:
    """Tell every machine's Cinedex that ``uri``'s ``detail`` department
    has new review media. Never raises: a change that cannot be announced
    only means it shows after the player's Refresh, as before."""
    try:
        write(shared_dir(config_path), Event(
            id=event_id or new_id(), kind=kind, uri=str(uri), detail=detail,
            host=socket.gethostname(), time=time.time(),
        ))
    except Exception as exc:  # noqa: BLE001 - a hint must never break a write
        logger.warning("Could not announce a %s change of %s on the shared journal: %s", kind, uri, exc)
