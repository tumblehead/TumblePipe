"""Edits as the pipeline stores them (``designs/edit-entity.md`` §2, §4).

An edit is the entity ``entity:/edits/<name>``, made and saved by the editor
(``cinedex``, whose ``th_pipe::edit_files`` and ``th_pipe::master`` this
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
The writes are :func:`set_master_edit` and the asset browser's create,
rename and delete (:func:`create_edit`, :func:`rename_edit`,
:func:`delete_edit`); their rules are the pure ``*_problem`` functions, so
a form can ask them as the artist types.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Collection, Mapping
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


# ── Create, rename, delete (the asset browser's Edits section) ─────────


def name_problem(name: str, existing: Collection[str]) -> str:
    """Why a new edit (or an edit renamed) may not be called ``name``, given
    the project's edits ``existing``; '' when it may. The entity name rule
    (letters and digits only, no Windows device name), and no edit already
    of that name or a case variant of it (the config refuses those, and
    their folders would collide on Windows)."""
    if not name:
        return 'An edit needs a name.'
    problem = naming.entity_name_problem(name)
    if problem:
        return f"Name '{name}' {problem}."
    folded = name.casefold()
    clash = next((other for other in existing if other.casefold() == folded), None)
    if clash is not None:
        return f"There is already an edit '{clash}'."
    return ''


def rename_problem(
    old: str, new: str, existing: Collection[str], has_files: bool,
) -> str:
    """Why edit ``old`` may not be renamed ``new``; '' when it may.

    One with files on disk (a saved or published version, a movie) is
    refused, as an asset or shot with files is (``entity_files``): the
    files are named after the edit, inside and out, so the renamed edit
    would come up empty. Save it under the new name in the editor (Save
    As) and delete this one instead. The master edit may be renamed: it
    stays the master edit."""
    if new == old:
        return f"The edit is already called '{old}'."
    if new.casefold() == old.casefold():
        return f"'{new}' differs from '{old}' only by case; pick another name."
    problem = name_problem(new, [name for name in existing if name != old])
    if problem:
        return problem
    if has_files:
        return (
            f"'{old}' has saved or published versions on disk, named after it, "
            "so it can't be renamed: the renamed edit would come up empty. "
            "Open it in the editor and Save As under the new name, then "
            "delete this one."
        )
    return ''


def delete_problem(name: str, master: str | None) -> str:
    """Why edit ``name`` may not be deleted; '' when it may. The master
    edit is refused: the shots' frame ranges follow it, so the project
    must first be left without one (Clear Master Edit) or given another."""
    if master is not None and master == name:
        return (
            f"'{name}' is the master edit, the cut the shots' frame ranges "
            "follow, so it can't be deleted. Clear Master Edit (or set "
            "another edit as master) first."
        )
    return ''


def create_edit(name: str, client=None) -> Uri:
    """Create the edit ``name``, with nothing saved yet: the editor opens it
    empty, for its first Save. ValueError naming the problem when
    :func:`name_problem` refuses it. As ``th_pipe::edit_files::create_edit``
    makes one."""
    client = _client(client)
    problem = name_problem(name, list_edits(client))
    if problem:
        raise ValueError(problem)
    uri = edit_uri(name)
    client.config.add_entity(uri, {})
    return uri


def edit_folders(name: str, client=None) -> list[tuple[Path, Path]]:
    """``(storage root, folder)`` for every folder of edit ``name`` that
    exists: its working folder (``edits:/<name>``), its published versions
    (``export:/edits/<name>``) and its rendered movies
    (``render:/edits/<name>``)."""
    client = _client(client)
    folders = []
    seen = set()
    for root_uri, segments in (
        ('edits:/', (name,)),
        ('export:/', ('edits', name)),
        ('render:/', ('edits', name)),
    ):
        root = Path(client.storage.resolve(Uri.parse(root_uri)))
        folder = root.joinpath(*segments)
        key = str(folder).lower()
        if key in seen or not folder.is_dir():
            continue
        seen.add(key)
        folders.append((root, folder))
    return folders


def has_edit_files(name: str, client=None) -> bool:
    """Whether any of edit ``name``'s folders holds a file."""
    return any(
        path.is_file()
        for _root, folder in edit_folders(name, client)
        for path in folder.rglob('*')
    )


def rename_edit(old: str, new: str, client=None) -> Uri:
    """Rename edit ``old`` to ``new``; the new URI. ValueError naming the
    problem when :func:`rename_problem` refuses it. The master edit stays
    the master edit, under its new name."""
    client = _client(client)
    existing = list_edits(client)
    if old not in existing:
        raise ValueError(f'There is no edit {old}')
    problem = rename_problem(old, new, existing, has_edit_files(old, client))
    if problem:
        raise ValueError(problem)
    was_master = master_edit(client) == old
    client.config.rename_entity(edit_uri(old), new)
    if was_master:
        try:
            set_master_edit(new, client)
        except BaseException:
            client.config.rename_entity(edit_uri(new), old)
            raise
    return edit_uri(new)


def delete_edit(name: str, client=None) -> list[tuple[Path, Path]]:
    """Delete edit ``name``: its folders move to ``_deleted/<stamp>/`` in
    their storage roots (as an asset's or shot's do, ``entity_files``: left
    in place, a new edit of the same name would pick its versions back
    up), then its config row goes. All or nothing: the folders move back
    if the config write fails. ValueError when :func:`delete_problem`
    refuses it (the master edit); ``entity_files.ArchiveError`` when a
    folder will not move (a file held open). Returns the folders moved,
    ``(from, to)``."""
    from tumblepipe.pipe.entity_files import archive_folders, restore_entity_folders

    client = _client(client)
    if name not in list_edits(client):
        raise ValueError(f'There is no edit {name}')
    problem = delete_problem(name, master_edit(client))
    if problem:
        raise ValueError(problem)
    moved = archive_folders(edit_folders(name, client))
    try:
        client.config.remove_entity(edit_uri(name))
    except BaseException:
        restore_entity_folders(moved)
        raise
    return moved


# ── The edit's first shot (its card's thumbnail) ───────────────────────


def first_shot(document: Mapping) -> str | None:
    """The shot (``entity:/shots/<sequence>/<shot>``) of the first clip of
    an edit document that plays a shot: the earliest clip on any video
    track (``at``, ties to the lower track), slugs and media skipped. None
    for a document with no shot clip, or one not shaped like an edit."""
    best = None
    tracks = document.get('video') if isinstance(document, Mapping) else None
    if not isinstance(tracks, list):
        return None
    for track_index, track in enumerate(tracks):
        clips = track.get('clips') if isinstance(track, Mapping) else None
        if not isinstance(clips, list):
            continue
        for clip in clips:
            if not isinstance(clip, Mapping):
                continue
            source = clip.get('source')
            at = clip.get('at')
            if not isinstance(source, Mapping) or source.get('kind') != 'shot':
                continue
            shot = source.get('shot')
            if not isinstance(shot, str) or not shot:
                continue
            if isinstance(at, bool) or not isinstance(at, (int, float)):
                continue
            key = (at, track_index)
            if best is None or key < best[0]:
                best = (key, shot)
    return None if best is None else best[1]


def document_path(name: str, client=None) -> Path | None:
    """The newest document of edit ``name``: its newest working version,
    else its latest published version; None when it has neither."""
    working = working_versions(name, client)
    if working:
        return working_dir(name, client) / working_file_name(name, working[-1])
    published = published_versions(name, client)
    if published:
        version = published[-1]
        return export_dir(name, client) / version / working_file_name(name, version)
    return None


def first_shot_uri(name: str, client=None) -> Uri | None:
    """The shot edit ``name`` opens on (:func:`first_shot` of its newest
    document), None when it has no document, it does not read, or it plays
    no shot."""
    path = document_path(name, client)
    if path is None:
        return None
    try:
        document = json.loads(path.read_bytes().decode('utf-8'))
        shot = first_shot(document)
        if shot is None:
            return None
        uri = Uri.parse(shot)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if uri.purpose != 'entity' or len(uri.segments) != 3 or uri.segments[0] != 'shots':
        return None
    return uri


# ── The edit a shot preview opens (Auguu, 2026-11-22) ──────────────────
#
# Play on shot cards used to open an untitled edit of them, so an artist
# with a saved cut had to find it again in File > Open every time. Now it
# opens the edit the shots are in, on the first of them (as Live view):
# the master edit when it holds them all, else the edit holding them all
# that was opened or saved last. With no edit holding them all it is the
# untitled edit as before; File > Open still lists every edit.

#: The file Cinedex notes each edit it opens in, beside its layout file
#: (``recent_edits.rs`` in ``src/cinedex``): ``{"<project root>": {"<edit>":
#: <seconds since 1970>}}``, the root spelled with forward slashes.
RECENT_EDITS_FILE = 'recent_edits.json'


def document_shots(document: Mapping) -> frozenset[str]:
    """Every shot (``entity:/shots/<sequence>/<shot>``) an edit document
    plays: the shot clips of its video tracks. Empty for a document not
    shaped like an edit."""
    shots = set()
    tracks = document.get('video') if isinstance(document, Mapping) else None
    for track in tracks if isinstance(tracks, list) else ():
        clips = track.get('clips') if isinstance(track, Mapping) else None
        for clip in clips if isinstance(clips, list) else ():
            source = clip.get('source') if isinstance(clip, Mapping) else None
            if isinstance(source, Mapping) and source.get('kind') == 'shot':
                shot = source.get('shot')
                if isinstance(shot, str) and shot:
                    shots.add(shot)
    return frozenset(shots)


def preview_edit(
    shots: Collection[str],
    master: str | None,
    edits: Mapping[str, tuple[Collection[str], float]],
) -> str | None:
    """The edit a preview of ``shots`` opens, of ``edits`` (each edit's
    name to the shots it plays and when it was last used, in seconds):
    the ``master`` edit when it plays every one of them, else the edit
    playing every one that was used last (the lower name on a tie); None
    when no edit plays them all, or there are no shots."""
    wanted = set(shots)
    if not wanted:
        return None
    holding = {name: used for name, (played, used) in edits.items() if wanted <= set(played)}
    if master is not None and master in holding:
        return master
    if not holding:
        return None
    return min(holding, key=lambda name: (-holding[name], name))


def _project_spelling(path: str) -> str:
    """A project root spelled one way whichever way it was given (slashes,
    a trailing one, case)."""
    return str(path).replace('\\', '/').rstrip('/').lower()


def recent_edits_path(environ=None, platform: str | None = None) -> Path:
    """This user's file of the edits Cinedex opened (:data:`RECENT_EDITS_FILE`),
    in Cinedex's prefs folder: ``%LOCALAPPDATA%/tumblepipe/cinedex`` on
    Windows, ``~/Library/Application Support/tumblepipe/cinedex`` on macOS,
    ``$XDG_CONFIG_HOME`` (or ``~/.config``) ``/tumblepipe/cinedex`` on Linux."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path(environ.get('HOME') or environ.get('USERPROFILE') or Path.home())
    if platform.startswith('win'):
        base = Path(environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    elif platform == 'darwin':
        base = home / 'Library' / 'Application Support'
    else:
        base = Path(environ.get('XDG_CONFIG_HOME') or home / '.config')
    return base / 'tumblepipe' / 'cinedex' / RECENT_EDITS_FILE


def recent_opens(value, project: str) -> dict[str, float]:
    """When each edit of ``project`` (its root) was last opened, as the
    file's JSON ``value`` says; nothing it says that is not a time."""
    if not isinstance(value, Mapping):
        return {}
    spelled = _project_spelling(project)
    opens: dict[str, float] = {}
    for key, edits in value.items():
        if not isinstance(key, str) or _project_spelling(key) != spelled or not isinstance(edits, Mapping):
            continue
        for name, at in edits.items():
            if isinstance(name, str) and not isinstance(at, bool) and isinstance(at, (int, float)):
                opens[name] = max(opens.get(name, 0.0), float(at))
    return opens


def load_recent_opens(project: str, path: Path | None = None) -> dict[str, float]:
    """:func:`recent_opens` of the file at ``path`` (this user's, by
    default); nothing when there is none or it does not read."""
    path = recent_edits_path() if path is None else path
    try:
        value = json.loads(path.read_bytes().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return recent_opens(value, project)


def edit_for_shots(shots: Collection[Uri], project: str, client=None, recent: Path | None = None) -> str | None:
    """The edit of the project at ``project`` that a preview of ``shots``
    opens (:func:`preview_edit`): each edit's newest document read for its
    shots, last used being the later of when Cinedex last opened it
    (:func:`load_recent_opens`, the file at ``recent``) and when that
    document was written. An edit whose document does not read is left
    out; a ``master_edit`` naming no edit counts as none."""
    client = _client(client)
    try:
        master = master_edit(client)
    except ValueError:
        master = None
    opens = load_recent_opens(project, recent)
    edits: dict[str, tuple[frozenset[str], float]] = {}
    for name in list_edits(client):
        path = document_path(name, client)
        if path is None:
            continue
        try:
            document = json.loads(path.read_bytes().decode('utf-8'))
            written = path.stat().st_mtime
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        edits[name] = (document_shots(document), max(written, opens.get(name, 0.0)))
    return preview_edit([str(shot) for shot in shots], master, edits)
