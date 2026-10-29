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

def list_version_paths(path: Path) -> list[Path]:
    if not path.exists(): return []
    version_paths = [
        version_path
        for version_path in path.iterdir()
        if (version_path.is_dir() and
            naming.is_valid_version_name(version_path.name))
    ]
    version_paths.sort(key = lambda version_path: naming.get_version_code(version_path.name))
    return version_paths

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
    version_paths = list_version_paths(path)
    for version_path in reversed(version_paths):
        if is_finished_version(version_path):
            return version_path
    return version_paths[-1] if version_paths else None

def get_next_version_path(path: Path) -> Path:
    """One past the highest version folder, finished or not, so a new
    version never lands in a folder another export is writing."""
    version_paths = list_version_paths(path)
    if len(version_paths) == 0: return path / naming.get_version_name(1)
    version_name = version_paths[-1].name
    next_version_name = get_next_version_name(version_name)
    return path / next_version_name
