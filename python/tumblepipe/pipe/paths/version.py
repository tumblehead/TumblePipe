import os
import threading
from contextlib import contextmanager
from typing import Optional
from pathlib import Path

from tumblepipe import naming
from tumblepipe.util.io import VERSION_MARKER

###############################################################################
# Version Paths
###############################################################################
def get_next_version_name(version_name: str) -> str:
    version_code = naming.get_version_code(version_name) + 1
    return naming.get_version_name(version_code)

def version_name_from_path(path: Path) -> Optional[str]:
    """The version a versioned file sits in, or None if it isn't in one.

    Everything versioned is laid out ``…/{version}/{file}``, so this is the
    parent directory — when it is a valid version name. For reading a version
    back off a path the resolver handed you, rather than re-deriving one.
    """
    version_name = Path(path).parent.name
    if not naming.is_valid_version_name(version_name):
        return None
    return version_name

# Per-thread listing cache, live only inside ``cached_version_listings()``.
_listing_scope = threading.local()

@contextmanager
def cached_version_listings():
    """Within this block, each folder's version listing and latest version
    are read from disk once per thread.

    For a batch that reads many versions and writes none: a scene refresh
    re-executes every import node, and they list the same rig folders shot
    after shot; on an SMB share every listing is a round trip. Re-entrant;
    the cache is dropped when the outermost block exits. A version written
    inside the block is not seen by a later listing in it
    (``get_next_version_path`` always reads the disk).
    """
    if getattr(_listing_scope, 'cache', None) is not None:
        yield
        return
    _listing_scope.cache = {}
    try:
        yield
    finally:
        _listing_scope.cache = None

def _scan_version_paths(path: Path) -> list[Path]:
    # One scandir, not exists() + iterdir() + is_dir() per entry: on Windows
    # DirEntry.is_dir() comes with the listing, so a folder of versions
    # costs one request on a network share instead of one per version.
    try:
        with os.scandir(path) as entries:
            version_paths = [
                Path(path) / entry.name
                for entry in entries
                if (naming.is_valid_version_name(entry.name) and
                    entry.is_dir())
            ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    version_paths.sort(key = lambda version_path: naming.get_version_code(version_path.name))
    return version_paths

def list_version_paths(path: Path) -> list[Path]:
    """The version folders under ``path``, oldest first; [] when it is missing."""
    cache = getattr(_listing_scope, 'cache', None)
    if cache is None:
        return _scan_version_paths(path)
    key = ('list', str(path))
    if key not in cache:
        cache[key] = _scan_version_paths(path)
    return list(cache[key])

def is_finished_version(version_path: Path) -> bool:
    """Whether a version folder is finished: its ``context.json`` is there.

    Every writer puts the sidecar in last (``copy_version_contents``, or
    after the layer it describes), so a folder without one is an export or
    build that failed or is still being written.
    """
    return (version_path / VERSION_MARKER).is_file()

def get_latest_version_path(path: Path) -> Optional[Path]:
    """The newest finished version folder under ``path``; None when there is none.

    A newer unfinished folder is read past: an external user's failed rig
    export left ``rig/v0006`` empty, and everything taking "latest" read it.
    When no version is finished the layout never wrote sidecars, and the
    newest is taken. The resolver's ``find_latest``
    (src/resolver/src/version.rs) has the same rule.
    """
    cache = getattr(_listing_scope, 'cache', None)
    if cache is None:
        return _find_latest_version_path(path)
    key = ('latest', str(path))
    if key not in cache:
        cache[key] = _find_latest_version_path(path)
    return cache[key]

def _find_latest_version_path(path: Path) -> Optional[Path]:
    version_paths = list_version_paths(path)
    for version_path in reversed(version_paths):
        if is_finished_version(version_path):
            return version_path
    return version_paths[-1] if version_paths else None

def get_next_version_path(path: Path) -> Path:
    """One past the highest version folder, finished or not, so a new
    version never lands in a folder another export is writing."""
    version_paths = _scan_version_paths(path)
    if len(version_paths) == 0: return path / naming.get_version_name(1)
    version_name = version_paths[-1].name
    next_version_name = get_next_version_name(version_name)
    return path / next_version_name
