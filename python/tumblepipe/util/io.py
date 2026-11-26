from typing import Optional
from pathlib import Path
import json


def store_text(path: Path, data: str):
    path.parent.mkdir(parents = True, exist_ok = True)
    with path.open('w', encoding = 'utf-8') as file:
        file.write(data)

def load_json(path: Path):
    """The JSON document at ``path``.

    Raises FileNotFoundError when there is none and ValueError for a path
    that is not a ``.json`` file. A caller for which absence is a normal
    state says so with :func:`load_json_if_exists` or its own ``exists()``.
    """
    if path.suffix != '.json':
        raise ValueError(f"not a .json file: {path}")
    with path.open('r', encoding='utf-8') as file:
        return json.load(file)

def load_json_if_exists(path: Path) -> Optional[dict]:
    """The JSON document at ``path``, or None when no file is there.

    Only absence is None: a path that exists but is not a readable ``.json``
    file raises like :func:`load_json`.
    """
    if not path.exists(): return None
    return load_json(path)

#: How long a rename over a file another process is reading keeps retrying.
REPLACE_TIMEOUT = 5.0


def replace_file(src, dst, timeout: float = REPLACE_TIMEOUT) -> None:
    """``os.replace(src, dst)``, waiting out a reader that has ``dst`` open.

    On Windows a file opened without FILE_SHARE_DELETE (every plain Python
    ``open()``, and most readers over SMB) cannot be renamed over:
    ``os.replace`` fails with "Access is denied" until the reader closes it.
    The project database is re-read by every session whenever its mtime
    changes, so a burst of writes (an Entity Editor Apply) routinely lands
    while some session is mid-read. Those reads take milliseconds; retry with
    a growing back-off before giving up with the last error.
    """
    import os
    import time
    deadline = time.monotonic() + timeout
    delay = 0.01
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace *path* with *data* in one step: a reader sees the old file or
    the new one, never part of it.

    Writes a temporary file in the same folder, then renames it over the
    target. A crash or force-kill mid-write cannot leave a truncated file,
    and a write that fails removes its temporary file. A reader holding
    *path* open delays the rename (see :func:`replace_file`) instead of
    failing it.
    """
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    fd, tmp = tempfile.mkstemp(
        prefix = f'.{path.name}.',
        suffix = '.tmp',
        dir = str(path.parent),
    )
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        replace_file(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def store_json(path: Path, data: dict):
    """Write *data* as JSON to *path* atomically (:func:`write_bytes_atomic`).

    ``indent=4``, non-ASCII escaped, LF line ends and a final newline: the
    bytes the Rust config migrator (``th_project_core::to_json_string``)
    writes, so a migration that changes one key changes one line.
    """
    write_bytes_atomic(path, (json.dumps(data, indent = 4) + '\n').encode('ascii'))


#: The sidecar a published version folder is finished by.
VERSION_MARKER = 'context.json'


#: The most a chunked copy reads at once: large enough that a multi-GB layer
#: copies to the share at wire speed, small enough to report every ~0.1 s.
COPY_BUFFER = 16 * 1024 * 1024

#: How often a copy's progress goes to the installed progress reporter
#: (a dialog's status line), and to the log (a farm task's output).
COPY_REPORT_SECONDS = 0.25
COPY_LOG_SECONDS = 15.0


def copy_file_chunked(src, dst, on_bytes=None, buffer_size: int | None = None):
    """``shutil.copy(src, dst)``, calling ``on_bytes(n)`` as each chunk lands.

    The same result as ``shutil.copy``: ``dst``'s bytes are ``src``'s, and
    its permission bits too (``copymode``); a ``dst`` that is a folder gets
    ``src``'s name inside it. An error mid-copy raises, as there. The buffer
    is at most ``buffer_size`` (:data:`COPY_BUFFER`) and at most the file's
    size, so a folder of small files does not allocate 16 MB for each.
    """
    import os
    import shutil
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    with open(src, 'rb') as reader:
        size = os.fstat(reader.fileno()).st_size
        buffer = bytearray(min(buffer_size or COPY_BUFFER, max(size, 1)))
        with memoryview(buffer) as view, open(dst, 'wb') as writer:
            while True:
                count = reader.readinto(buffer)
                if not count:
                    break
                writer.write(view[:count])
                if on_bytes is not None:
                    on_bytes(count)
    shutil.copymode(src, dst)
    return dst


def format_bytes(count: float) -> str:
    """``count`` bytes as a short size: ``512 B``, ``40.2 MB``, ``3.0 GB``."""
    for unit, size in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if count >= size:
            return f'{count / size:.1f} {unit}'
    return f'{int(count)} B'


class CopyProgress:
    """A copy's running total, reported as ``label: 1.2 / 3.0 GB, 45 MB/s``.

    ``add`` is called per chunk; the text goes to the installed progress
    reporter (``util.progress``) at most every ``report_seconds``, and to the
    log at most every ``log_seconds``, so a farm task's output gets a line a
    quarter of a minute, not one per chunk. ``on_progress(done, total)``, when
    given, sees every chunk.
    """

    def __init__(
        self, label: str, total: int, on_progress=None, clock=None,
        report_seconds: float = COPY_REPORT_SECONDS,
        log_seconds: float = COPY_LOG_SECONDS,
    ):
        import time
        self.label = label
        self.total = int(total)
        self.done = 0
        self._on_progress = on_progress
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self._report_seconds = report_seconds
        self._log_seconds = log_seconds
        self._reported = self._start
        self._logged = self._start

    def message(self) -> str:
        total = max(self.total, self.done)
        elapsed = self._clock() - self._start
        text = f'{self.label}: {format_bytes(self.done)} / {format_bytes(total)}'
        if elapsed > 0 and self.done > 0:
            text += f', {self.done / elapsed / (1 << 20):.0f} MB/s'
        return text

    def start(self) -> None:
        from tumblepipe.util.progress import report_progress
        report_progress(f'{self.label}: {format_bytes(self.total)}')

    def add(self, count: int) -> None:
        from tumblepipe.util.progress import report_progress
        self.done += count
        if self._on_progress is not None:
            self._on_progress(self.done, max(self.total, self.done))
        now = self._clock()
        if now - self._reported >= self._report_seconds:
            self._reported = now
            report_progress(self.message())
        if now - self._logged >= self._log_seconds:
            self._logged = now
            _logger().info(self.message())

    def finish(self) -> None:
        """The last report: the whole size, and one log line for the copy."""
        from tumblepipe.util.progress import report_progress
        message = self.message()
        report_progress(message)
        _logger().info(message)


def _logger():
    import logging
    return logging.getLogger(__name__)


def _tree_size(path: Path) -> int:
    """The bytes in the files under ``path`` (a file's own size for a file)."""
    import os
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def copy_version_contents(
    src: Path, dst: Path, marker: str = VERSION_MARKER, copy=None,
    label: str | None = None, on_progress=None,
):
    """Copy a staged version folder's contents into ``dst``, ``marker`` last.

    A version folder (an export, a staged scene) counts as finished when its
    ``context.json`` is there: the archive, ``graph.scan`` and the docs all
    read it that way. Copying the staged folder in directory order could put
    the sidecar on the server before the layer it describes, so a copy cut
    short left a version that looked finished but was not. Everything else
    goes first; the marker is then copied beside its final name and renamed
    into place, so it appears whole, and only once the rest is there.
    Existing files in ``dst`` are overwritten (``dirs_exist_ok``).

    A published layer is often several GB, so the copy reports its progress
    (:class:`CopyProgress`): ``label`` (default ``copying <dst name> to
    server``) with the bytes copied, the total and the throughput, to the
    installed progress reporter and now and then to the log.
    ``on_progress(done, total)`` sees every step. Without ``copy`` each file
    is copied in 16 MB chunks (:func:`copy_file_chunked`, ``shutil.copy``'s
    result); a given ``copy`` (``shutil.copy2`` to keep times) reports per
    file.
    """
    import shutil
    progress = CopyProgress(
        label or f'copying {dst.name} to server', _tree_size(src), on_progress,
    )
    if copy is None:
        def copy(a, b):
            return copy_file_chunked(a, b, progress.add)
    else:
        given = copy

        def copy(a, b):
            result = given(a, b)
            progress.add(Path(a).stat().st_size)
            return result
    progress.start()
    dst.mkdir(parents=True, exist_ok=True)
    marker_src = None
    for item in sorted(src.iterdir()):
        if item.name == marker and item.is_file():
            marker_src = item
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, copy_function=copy)
        else:
            copy(item, target)
    if marker_src is not None:
        partial = dst / (marker + '.partial')
        copy(marker_src, partial)
        replace_file(partial, dst / marker)
    progress.finish()
