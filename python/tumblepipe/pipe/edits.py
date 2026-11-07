"""Edits as the pipeline stores them (``designs/edit-entity.md`` §2, §4).

An edit is the entity ``entity:/edits/<name>``, made and saved by the editor
(``th_player``, whose ``th_pipe::edit_files`` and ``th_pipe::master`` this
module reads the same way):

- **working versions**: ``edits:/<name>/edit/<name>_edit_vNNNN.json``;
- **published versions**: ``export:/edits/<name>/default/edit/vNNNN/``,
  holding ``<name>_edit_vNNNN.json``, its ``.otio`` and ``context.json``,
  written last. A version folder without ``context.json`` is unfinished
  and is not listed.
- **the master edit**: ``master_edit`` on ``config:/project``, the URI
  ``entity:/edits/<name>``; unset or empty, the project has none.
- **the rendered movie** of a published version, made on the farm by Render
  Edit: ``render:/edits/<name>/<name>_edit_vNNNN.mp4``.

Each function reads one project: ``client`` (a ``tumblepipe.api.Client``,
as the asset browser holds one per project), else the active project.
Nothing here writes but :func:`set_master_edit`.
"""

from __future__ import annotations

from pathlib import Path

from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.paths.export import get_export_uri
from tumblepipe.pipe.paths.version import is_finished_version, list_version_paths

#: The edits' one department.
DEPARTMENT = 'edit'
#: The channel published edits go to.
CHANNEL = 'default'
#: The working versions' sidecar folder (``_context/vNNNN.json``).
CONTEXT_DIR = '_context'
#: Where ``master_edit`` lives.
PROJECT_URI = Uri.parse('config:/project')
MASTER_KEY = 'master_edit'

EDITS_ROOT = Uri.parse('entity:/edits')


def _client(client):
    if client is not None:
        return client
    from tumblepipe.api import api
    return api


def edit_uri(name: str) -> Uri:
    """The edit ``entity:/edits/<name>``."""
    return EDITS_ROOT / name


def edit_name(uri: Uri) -> str:
    """The name of edit ``uri``; ValueError when it is not an edit."""
    if uri.purpose != 'entity' or len(uri.segments) != 2 or uri.segments[0] != 'edits':
        raise ValueError(f'{uri} is not an edit')
    return uri.segments[1]


def is_edit_uri(uri: Uri) -> bool:
    """Whether ``uri`` is an edit, ``entity:/edits/<name>``."""
    return uri.purpose == 'entity' and len(uri.segments) == 2 and uri.segments[0] == 'edits'


def list_edits(client=None) -> list[str]:
    """The project's edits, by name."""
    config = _client(client).config
    return sorted(
        uri.segments[1] for uri in config.list_entity_uris(EDITS_ROOT)
        if is_edit_uri(uri)
    )


def working_dir(name: str, client=None) -> Path:
    """Where edit ``name``'s working versions are."""
    return Path(_client(client).storage.resolve(Uri.parse('edits:/') / name / DEPARTMENT))


def working_file_name(name: str, version: str) -> str:
    """The file of version ``version`` of edit ``name``, working or published."""
    return f'{name}_{DEPARTMENT}_{version}.json'


def working_versions(name: str, client=None) -> list[str]:
    """Edit ``name``'s working versions, oldest first."""
    prefix = f'{name}_{DEPARTMENT}_'
    try:
        files = [path.name for path in working_dir(name, client).iterdir() if path.is_file()]
    except (FileNotFoundError, NotADirectoryError):
        return []
    versions = [
        file[len(prefix):-len('.json')] for file in files
        if file.startswith(prefix) and file.endswith('.json')
    ]
    return sorted(
        (version for version in versions if naming.is_valid_version_name(version)),
        key = naming.get_version_code
    )


def export_dir(name: str, client=None) -> Path:
    """Where edit ``name``'s published versions are."""
    return Path(_client(client).storage.resolve(get_export_uri(edit_uri(name), CHANNEL, DEPARTMENT)))


def published_versions(name: str, client=None) -> list[str]:
    """Edit ``name``'s finished published versions, oldest first: a version
    folder without its ``context.json`` is unfinished and left out."""
    return [
        path.name for path in list_version_paths(export_dir(name, client))
        if is_finished_version(path)
    ]


def movie_file_name(name: str, version: str) -> str:
    """The movie Render Edit makes of version ``version`` of edit ``name``."""
    return f'{name}_{DEPARTMENT}_{version}.mp4'


def movie_uri(name: str, version: str) -> Uri:
    """``render:/edits/<name>/<name>_edit_vNNNN.mp4``, the movie Render Edit
    makes of a published version (``farm/pipelines/edit_render.py``)."""
    if not naming.is_valid_version_name(version):
        raise ValueError(f'{version!r} is not a version')
    return Uri.parse('render:/edits') / name / movie_file_name(name, version)


def movie_path(name: str, version: str, client=None) -> Path:
    """Where the movie of version ``version`` of edit ``name`` is written."""
    return Path(_client(client).storage.resolve(movie_uri(name, version)))


def master_edit(client=None) -> str | None:
    """The name of the project's master edit, None when it has none. A
    ``master_edit`` that names no edit raises ValueError."""
    properties = _client(client).config.get_properties(PROJECT_URI) or {}
    value = properties.get(MASTER_KEY)
    if not isinstance(value, str) or not value:
        return None
    return edit_name(Uri.parse(value))


def set_master_edit(name: str | None, client=None) -> None:
    """Make edit ``name`` the master edit, or with None leave the project
    without one. Refuses an edit the project does not have."""
    client = _client(client)
    if name is not None and name not in list_edits(client):
        raise ValueError(f'There is no edit {name}')
    config = client.config
    own = dict(config.get_own_properties(PROJECT_URI) or {})
    own[MASTER_KEY] = '' if name is None else str(edit_uri(name))
    config.set_own_properties(PROJECT_URI, own)
