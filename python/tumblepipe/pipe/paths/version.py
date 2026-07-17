from typing import Optional
from pathlib import Path

from tumblepipe.api import api

###############################################################################
# Version Paths
###############################################################################
def get_next_version_name(version_name: str) -> str:
    version_code = api.naming.get_version_code(version_name) + 1
    return api.naming.get_version_name(version_code)

def version_name_from_path(path: Path) -> Optional[str]:
    """The version a versioned file sits in, or None if it isn't in one.

    Everything versioned is laid out ``…/{version}/{file}``, so this is the
    parent directory — when it is a valid version name. For reading a version
    back off a path the resolver handed you, rather than re-deriving one.
    """
    version_name = Path(path).parent.name
    if not api.naming.is_valid_version_name(version_name):
        return None
    return version_name

def list_version_paths(path: Path) -> list[Path]:
    if not path.exists(): return []
    version_paths = [
        version_path
        for version_path in path.iterdir()
        if (version_path.is_dir() and
            api.naming.is_valid_version_name(version_path.name))
    ]
    version_paths.sort(key = lambda version_path: api.naming.get_version_code(version_path.name))
    return version_paths

def get_latest_version_path(path: Path) -> Optional[Path]:
    version_paths = list_version_paths(path)
    if len(version_paths) == 0: return None
    return version_paths[-1]

def get_next_version_path(path: Path) -> Path:
    version_paths = list_version_paths(path)
    if len(version_paths) == 0: return path / 'v0001'
    version_name = version_paths[-1].name
    next_version_name = get_next_version_name(version_name)
    return path / next_version_name
