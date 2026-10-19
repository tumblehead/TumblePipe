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

def store_json(path: Path, data: dict):
    """Write *data* as JSON to *path* atomically.

    Serializes to a temporary file in the same directory first, then
    renames it over the target. This guarantees *path* is either
    fully written or untouched — a crash or force-kill mid-write
    cannot leave a truncated file.
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
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise