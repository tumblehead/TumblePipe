from typing import Optional
from pathlib import Path
import json


def store_text(path: Path, data: str):
    path.parent.mkdir(parents = True, exist_ok = True)
    with path.open('w') as file:
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


def store_json(path: Path, data: dict):
    """Write *data* as JSON to *path* atomically.

    Serializes to a temporary file in the same directory first, then
    renames it over the target. This guarantees *path* is either
    fully written or untouched — a crash or force-kill mid-write
    cannot leave a truncated file. A reader holding *path* open delays
    the rename (see :func:`replace_file`) instead of failing it.
    """
    import os
    import tempfile
    path.parent.mkdir(parents = True, exist_ok = True)
    fd, tmp = tempfile.mkstemp(
        suffix = '.json',
        dir = str(path.parent),
    )
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(data, file, indent = 4)
        replace_file(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: The sidecar a published version folder is finished by.
VERSION_MARKER = 'context.json'


def copy_version_contents(src: Path, dst: Path, marker: str = VERSION_MARKER, copy=None):
    """Copy a staged version folder's contents into ``dst``, ``marker`` last.

    A version folder (an export, a staged scene) counts as finished when its
    ``context.json`` is there: the archive, ``graph.scan`` and the docs all
    read it that way. Copying the staged folder in directory order could put
    the sidecar on the server before the layer it describes, so a copy cut
    short left a version that looked finished but was not. Everything else
    goes first; the marker is then copied beside its final name and renamed
    into place, so it appears whole, and only once the rest is there.
    Existing files in ``dst`` are overwritten (``dirs_exist_ok``).
    """
    import shutil
    copy = copy or shutil.copy
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
