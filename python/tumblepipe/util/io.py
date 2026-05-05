from typing import Optional
from pathlib import Path
import json

def load_text(path: Path) -> Optional[str]:
    if not path.exists(): return None
    if not path.is_file(): return None
    with path.open('r') as file:
        return file.read()

def store_text(path: Path, data: str):
    path.parent.mkdir(parents = True, exist_ok = True)
    with path.open('w') as file:
        file.write(data)

def load_json(path: Path) -> Optional[dict]:
    if not path.exists(): return None
    if not path.is_file(): return None
    if path.suffix != '.json': return None
    with path.open('r') as file:
        return json.load(file)

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