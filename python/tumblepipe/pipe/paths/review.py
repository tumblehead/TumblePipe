"""Review media: playblasts and dailies.

Playblasts are versioned movie files, ``{purpose}:/playblast/{entity}/{dept}``
(``.../{layer}`` for a render layer's) holding ``v0001.mp4``, ``v0002.mp4``,
... A daily is the one current movie of an entity's department,
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


def _file_versions(directory: Path, ext: str) -> list[str]:
    """The version names of ``directory``'s ``<version>.<ext>`` files, ascending.

    Files whose stem is not a version (notes, a re-encode) are not versions.
    """
    return sorted(
        filter(
            naming.is_valid_version_name,
            (path.stem for path in directory.glob(f'*.{ext}'))
        ),
        key = naming.get_version_code
    )


def _latest_versioned_file(directory: Path, ext: str) -> Optional[Path]:
    """The highest ``<version>.<ext>`` in ``directory``, None when there is none."""
    version_names = _file_versions(directory, ext)
    if not version_names: return None
    return directory / f'{version_names[-1]}.{ext}'


def _next_versioned_file(directory: Path, ext: str) -> Path:
    """The ``<version>.<ext>`` one past the highest in ``directory``."""
    version_names = _file_versions(directory, ext)
    next_version_name = (
        get_next_version_name(version_names[-1]) if version_names
        else naming.get_version_name(1)
    )
    return directory / f'{next_version_name}.{ext}'


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
    directory = _playblast_dir(entity_uri, department_name, render_layer_name, purpose)
    return directory / f'{version_name}.{_MOVIE_EXTENSION}'

def get_playblast_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    directory = _playblast_dir(entity_uri, department_name, None, purpose)
    return directory / f'{version_name}.{_MOVIE_EXTENSION}'

def get_next_playblast_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Path:
    return _next_versioned_file(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _MOVIE_EXTENSION
    )

def list_playblast_versions(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> list[str]:
    """The version names of a department's playblasts, ascending."""
    directory = _playblast_dir(entity_uri, department_name, None, purpose)
    if not directory.is_dir(): return []
    return _file_versions(directory, _MOVIE_EXTENSION)

def get_latest_playblast_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Optional[Path]:
    return _latest_versioned_file(
        _playblast_dir(entity_uri, department_name, None, purpose),
        _MOVIE_EXTENSION
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
