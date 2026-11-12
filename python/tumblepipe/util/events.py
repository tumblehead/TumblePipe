"""The same-machine change journal: one session's writes, seen by the others.

Artists keep several Houdini sessions open, publish from one, tab to the
other, and find its Asset Browser showing the departments as they were: a
browser's cards are scanned from the share once and rebuilt only for what
its own session does. Every pipeline write that changes what a card shows
(a workfile version, an export, a config write) appends one line here, and
every open browser reads the new lines and rebuilds those cards.

The journal is on local disk, per user (``journal_path``), so following it
costs a local ``stat()`` per poll, not a round trip to the share, and it
works the same on Windows, macOS and Linux with no file watcher. Writes from
other machines are not in it; those still need the browser's Refresh, except
review media (playblasts, renders), which also go to the project's shared
journal (``util.shared_journal``) that Cinedex follows (#78).

It is a hint, never the source of truth: :func:`announce` never raises, and
a reader that may have missed lines (the journal was rotated or removed)
says so, and its caller refreshes everything, as a Refresh would.

Format: UTF-8 JSON lines. The first line of every journal file is a header,
``{"journal": "<id>"}``, new each time the file is started afresh, so a
reader can tell a rotated file from the one it was reading. Writers hold
``changes.lock`` while they append or rotate: on Windows an ``O_APPEND``
write is a seek and a write, which two processes can interleave.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

JOURNAL_NAME = 'changes.jsonl'
LOCK_NAME = 'changes.lock'
# A journal past this size is started afresh by the next write. A line is
# about 250 bytes, so this holds about a thousand changes: far more than
# a reader polling twice a second ever has to catch up on.
MAX_BYTES = 256 * 1024
# Overrides the journal folder (tests point it into their temp project).
DIR_ENV = 'TH_EVENTS_DIR'

# What changed, for the ``kind`` of a Change.
WORKFILE = 'workfile'   # a workfile version was committed
EXPORT = 'export'       # an export version was finished
CONFIG = 'config'       # a config db file was written
PLAYBLAST = 'playblast' # a playblast movie was written (versioned, or a render's review movie)
RENDER = 'render'       # render frames were written
LIVE = 'live'           # a live playblast run started (pipe.live)


@dataclass(frozen=True)
class Change:
    """One journal line.

    ``project`` is :func:`project_key` of the project's config folder.
    ``uri`` is the entity (``entity:/...``) or Multi (``groups:/...``)
    that changed, '' for a config write; ``detail`` its department, or the
    config purpose. ``pid`` is the writing process.

    Readers: the Asset Browser (``asset_browser.change_feed``) and Cinedex
    (``th_pipe::events::JournalReader``), which resolves a shot again on a
    playblast, render or live change of it, and reads the project's config
    again on a config change.

    ``id`` names the change across journals: review media announced here
    and on the project's shared journal (``util.shared_journal``) carry the
    same id, so a reader following both acts on it once. '' (no key on the
    line) for a change written to this journal alone.
    """
    project: str
    kind: str
    uri: str
    detail: str
    pid: int
    time: float
    id: str = ''


def journal_dir() -> Path:
    """The per-user folder the journal lives in, on local disk."""
    override = os.environ.get(DIR_ENV)
    if override:
        return Path(override)
    home = Path.home()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or home / '.local' / 'state')
    return base / 'tumblepipe' / 'events'


def journal_path() -> Path:
    return journal_dir() / JOURNAL_NAME


def project_key(config_path) -> str:
    """How the journal names a project: its config folder, spelled one way.

    No ``resolve()``: on a mapped drive it can trade the letter for a UNC
    path (and stat the share). The browser and the writing session both
    spell a project's config folder from the same registry or environment.
    """
    return os.path.normcase(os.path.abspath(str(config_path)))


@contextmanager
def _writer_lock(folder: Path):
    """Exclusive among this machine's journal writers."""
    with open(folder / LOCK_NAME, 'a+b') as handle:
        if sys.platform == 'win32':
            import msvcrt
            handle.seek(0)
            # LK_LOCK retries for about ten seconds, then raises.
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _header() -> bytes:
    return (json.dumps({'journal': uuid.uuid4().hex}) + '\n').encode('utf-8')


def append(change: Change, path: Path | None = None) -> None:
    """Append ``change`` to the journal, starting it afresh when it is
    missing or full. Raises on failure; :func:`announce` is the caller
    that must not."""
    path = journal_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'project': change.project, 'kind': change.kind, 'uri': change.uri,
        'detail': change.detail, 'pid': change.pid, 'time': change.time,
    }
    if change.id:
        record['id'] = change.id
    line = (json.dumps(record) + '\n').encode('utf-8')
    with _writer_lock(path.parent):
        size, whole, open_line = _inspect(path)
        if not whole or size + len(line) > MAX_BYTES:
            # Started afresh when missing, full, or without a readable
            # header (a writer killed between truncating and writing it):
            # appending to that would feed lines no reader takes. Truncated
            # in place, not replaced: on Windows a rename fails while a
            # reader has the file open.
            with open(path, 'wb') as handle:
                handle.write(_header() + line)
        else:
            with open(path, 'ab') as handle:
                # A writer killed mid-line left half of one: end it, or this
                # line would be glued onto it and lost with it.
                handle.write((b'\n' if open_line else b'') + line)


def _inspect(path: Path) -> tuple[int, bool, bool]:
    """``(size, header readable, last line unfinished)`` of the journal;
    ``(0, False, False)`` when there is none."""
    try:
        with open(path, 'rb') as handle:
            whole = _header_id(handle.readline()) is not None
            size = handle.seek(0, os.SEEK_END)
            if size == 0:
                return 0, False, False
            handle.seek(size - 1)
            return size, whole, handle.read(1) != b'\n'
    except FileNotFoundError:
        return 0, False, False


def announce(config_path, kind: str, uri='', detail: str = '', id: str = '') -> None:
    """Tell this machine's other sessions that ``uri`` in the project at
    ``config_path`` changed. Never raises: a change that cannot be
    announced only means the other browsers refresh when asked, as before.
    ``id``: the change's id when it goes to the shared journal too."""
    try:
        append(Change(
            project=project_key(config_path), kind=kind, uri=str(uri),
            detail=detail, pid=os.getpid(), time=time.time(), id=id,
        ))
    except Exception as exc:  # noqa: BLE001 - a hint must never break a write
        logger.warning("Could not announce a %s change of %s: %s", kind, uri, exc)


def _parse(raw: bytes) -> Change | None:
    try:
        data = json.loads(raw)
        change = Change(
            project=str(data['project']), kind=str(data['kind']),
            uri=str(data['uri']), detail=str(data['detail']),
            pid=int(data['pid']), time=float(data['time']),
        )
        # Optional: only changes on the shared journal too carry one.
        change_id = data.get('id', '')
        if isinstance(change_id, str) and change_id:
            change = dataclasses.replace(change, id=change_id)
        return change
    except (ValueError, KeyError, TypeError):
        return None


class JournalReader:
    """Follows the journal from the moment it is made.

    :meth:`poll` returns the changes appended since the last poll (none
    on the first: history is not replayed), or None when some may have
    been missed — the journal was started afresh or removed since — and
    the caller should refresh everything. A poll that finds the journal
    as it was costs one local ``stat()``.
    """

    def __init__(self, path: Path | None = None):
        self.path = journal_path() if path is None else path
        self._journal: str | None = None   # the header id being followed
        self._offset = 0                   # bytes of it consumed
        self._stamp: tuple[int, int] | None = None
        self._started = False

    def poll(self) -> list[Change] | None:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            lost = self._journal is not None
            self._journal, self._offset, self._stamp = None, 0, None
            self._started = True
            return None if lost else []
        stamp = (info.st_mtime_ns, info.st_size)
        if self._started and stamp == self._stamp:
            return []
        try:
            with open(self.path, 'rb') as handle:
                header = handle.readline()
                journal = _header_id(header)
                if journal is None:
                    return []  # a writer is starting it; read it next time
                if not self._started:
                    # Follow from the end: what is already there was
                    # written before this reader existed.
                    self._journal, self._offset = journal, info.st_size
                    self._started, self._stamp = True, stamp
                    return []
                lost = False
                if journal == self._journal and info.st_size < self._offset:
                    # Shorter than what was read, under the same header:
                    # cut by something other than a writer. Follow it from
                    # its end again; what was cut may be lost.
                    self._offset, self._stamp = info.st_size, stamp
                    return None
                if journal != self._journal:
                    # A file begun after the last poll. When it replaced
                    # one this reader followed, that one's last lines may
                    # be lost; when there was none, every line is new.
                    lost = self._journal is not None
                    self._journal, self._offset = journal, len(header)
                changes = self._read(handle)
        except OSError:
            return []  # held by a writer for a moment; read it next time
        self._stamp = stamp
        return None if lost else changes

    def _read(self, handle) -> list[Change]:
        """The complete lines from the handle's position; a torn last line
        (a writer mid-append) is left for the next poll."""
        handle.seek(self._offset)
        data = handle.read()
        end = data.rfind(b'\n')
        if end < 0:
            return []
        self._offset += end + 1
        changes = []
        for raw in data[:end].split(b'\n'):
            if raw.strip():
                change = _parse(raw)
                if change is not None:
                    changes.append(change)
        return changes


def _header_id(line: bytes) -> str | None:
    if not line.endswith(b'\n'):
        return None
    try:
        journal = json.loads(line).get('journal')
    except (ValueError, AttributeError):
        return None
    return journal if isinstance(journal, str) else None
