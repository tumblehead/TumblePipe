"""Review media: playblasts and dailies.

Playblasts are versioned movie files, ``{purpose}:/playblast/{entity}/{dept}``
(``.../{layer}`` for a render layer's), named after the shot like every other
versioned file: ``010_sh020_animation_v0003.mp4`` (``..._{layer}_v0003.mp4``
for a layer's). An editor that labels clips by file name (Resolve) can then
tell the shots apart. Up to 1.87 they were bare ``v0003.mp4``; those are still
read, and a version keeps whichever name it was written with.

A daily is the one current movie of an entity's department,
``{purpose}:/daily/{kind}/{dept}`` (``.../{layer}``) named after the entity.
"""

from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.pipe.paths.version import get_next_version_name

_MOVIE_EXTENSION = 'mp4'


def _playblast_dir(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: Optional[str],
    purpose: str
    ) -> Path:
    playblast_uri = (
        Uri.parse(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name
    )
    if render_layer_name is not None:
        playblast_uri = playblast_uri / render_layer_name
    return api.storage.resolve(playblast_uri)


def _version_of_movie(path: Path, stem: str) -> Optional[str]:
    """The version a playblast file in its folder is, None when it is none.

    ``<stem>_<version>.mp4``, or the bare ``<version>.mp4`` written up to
    1.87. Anything else (notes, a re-encode, another shot's file) is not a
    version of this one.
    """
    if naming.is_valid_version_name(path.stem):
        return path.stem
    prefix, separator, version_name = path.stem.rpartition('_')
    if separator and prefix == stem and naming.is_valid_version_name(version_name):
        return version_name
    return None


def _movie_versions(directory: Path, stem: str) -> dict[str, Path]:
    """``directory``'s playblast files by version name. Should one version
    have both names, the shot-named file wins: it is the newer convention."""
    found: dict[str, Path] = {}
    if not directory.is_dir(): return found
    for path in directory.glob(f'*.{_MOVIE_EXTENSION}'):
        version_name = _version_of_movie(path, stem)
        if version_name is None: continue
        if version_name in found and path.stem == version_name: continue
        found[version_name] = path
    return found


def _sorted_versions(versions) -> list[str]:
    return sorted(versions, key = naming.get_version_code)


def _movie_path(directory: Path, stem: str, version_name: str) -> Path:
    """A version's file: the one on disk, else the shot-named one to write."""
    named = directory / f'{stem}_{version_name}.{_MOVIE_EXTENSION}'
    if named.exists(): return named
    bare = directory / f'{version_name}.{_MOVIE_EXTENSION}'
    if bare.exists(): return bare
    return named


def _latest_movie(directory: Path, stem: str) -> Optional[Path]:
    """The highest version's file in ``directory``, None when there is none."""
    versions = _movie_versions(directory, stem)
    if not versions: return None
    return versions[_sorted_versions(versions)[-1]]


def _next_movie(directory: Path, stem: str) -> Path:
    """The shot-named file one version past the highest in ``directory``."""
    versions = _sorted_versions(_movie_versions(directory, stem))
    next_version_name = (
        get_next_version_name(versions[-1]) if versions
        else naming.get_version_name(1)
    )
    return directory / f'{stem}_{next_version_name}.{_MOVIE_EXTENSION}'


def _playblast_stem(entity_uri: Uri, department_name: str, render_layer_name: Optional[str]) -> str:
    """``<entity>_<dept>`` (``..._<layer>``): a playblast's name before its version."""
    parts = [department_name] if render_layer_name is None else [department_name, render_layer_name]
    return entity_stem(entity_uri, *parts)


def playblast_version_of(path: Path) -> str:
    """The version name of a playblast file, either naming."""
    if naming.is_valid_version_name(path.stem): return path.stem
    _, _, version_name = path.stem.rpartition('_')
    if not naming.is_valid_version_name(version_name):
        raise ValueError(f'not a playblast version: {path.name}')
    return version_name


###############################################################################
# Playblasts
###############################################################################
def get_layer_playblast_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    return _movie_path(
        _playblast_dir(entity_uri, department_name, render_layer_name, purpose),
        _playblast_stem(entity_uri, department_name, render_layer_name),
        version_name
    )

def get_playblast_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    """A version's playblast: the file on disk under either naming, else
    the shot-named file to write it to."""
    return _movie_path(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _playblast_stem(entity_uri, department_name, None),
        version_name
    )

def get_next_playblast_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Path:
    return _next_movie(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _playblast_stem(entity_uri, department_name, None)
    )

def list_playblast_versions(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> list[str]:
    """The version names of a department's playblasts, ascending."""
    return _sorted_versions(_movie_versions(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _playblast_stem(entity_uri, department_name, None)
    ))

def get_latest_playblast_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Optional[Path]:
    return _latest_movie(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _playblast_stem(entity_uri, department_name, None)
    )

###############################################################################
# Dailies
###############################################################################
def _daily_dir(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: Optional[str],
    purpose: str
    ) -> Path:
    daily_uri = (
        Uri.parse(f'{purpose}:/daily') /
        entity_uri.segments[0] /
        department_name
    )
    if render_layer_name is not None:
        daily_uri = daily_uri / render_layer_name
    return api.storage.resolve(daily_uri)

def get_layer_daily_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Path:
    directory = _daily_dir(entity_uri, department_name, render_layer_name, purpose)
    return directory / f'{entity_stem(entity_uri, render_layer_name)}.{_MOVIE_EXTENSION}'

def get_daily_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Path:
    directory = _daily_dir(entity_uri, department_name, None, purpose)
    return directory / f'{entity_stem(entity_uri)}.{_MOVIE_EXTENSION}'
