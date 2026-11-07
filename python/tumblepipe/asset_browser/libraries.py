"""Libraries: folders outside every project, shared by the projects that list them.

A library holds the same files a project holds for its library kinds, laid
out the same way (``designs/libraries-pickers-poses.md`` §3)::

    <library>/
      library.json      # optional: {"name": "Studio", "read_only": false}
      recipes/<context>/<slug>/
      textures/<set>/vNNNN/

Each registered project lists its libraries:

1. ``TH_LIBRARY_PATH`` for the project this Houdini was launched for (the
   one ``bootstrap_from_env`` registers): a path list, ``;`` on Windows and
   ``:`` elsewhere, like ``HOUDINI_PATH``. TumbleTrove Desktop sets it per
   project in its Environment Variables. These are never written to
   ``projects.json``: the launch environment says them again every launch.
2. The paths the artist added on the Projects settings page
   (``ProjectConfig.library_paths``), after them.

Two projects listing the same folder share it, and the browser shows it once.
A library is named by its ``library.json`` ``name`` or, without one, its
folder's name. The name is the owner in its cards' ids
(``recipe:@Studio:sop/scatter``), so it must be unique across everything
registered: a second library of the same name is reported, not merged.

Libraries are only ever read when an item is used or listed, never by a farm
job: what is loaded (recipes) is used in place, and what is referenced while
cooking (texture sets) is copied into the project first.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

log = logging.getLogger(__name__)

ENV_VAR = 'TH_LIBRARY_PATH'
LIBRARY_FILE = 'library.json'
#: Owner segment prefix that tells a library's ids from a project's.
OWNER_PREFIX = '@'
#: Tag key of a library card (a project card carries ``project:``).
TAG_KEY = 'library'

SOURCE_ENV = 'env'
SOURCE_SETTINGS = 'settings'

_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')


@dataclass(frozen=True)
class LibraryPath:
    """One path a project lists, and where it came from."""

    path: str
    source: str          # SOURCE_ENV or SOURCE_SETTINGS


@dataclass(frozen=True)
class Library:
    name: str
    path: Path
    read_only: bool = False

    @property
    def owner(self) -> str:
        """The owner segment of this library's card ids."""
        return OWNER_PREFIX + self.name

    @property
    def tag(self) -> str:
        return f'{TAG_KEY}:{self.name}'


class LibraryError(Exception):
    """A listed library that cannot be used, with why, for an error row."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path


def is_library_owner(owner: str) -> bool:
    return owner.startswith(OWNER_PREFIX) and len(owner) > len(OWNER_PREFIX)


def library_name(owner: str) -> str:
    """``@Studio`` -> ``Studio``."""
    return owner[len(OWNER_PREFIX):]


def owner_tag(owner: str) -> str:
    """``project:<name>`` for a project's card, ``library:<name>`` for a
    library's (whose owner is ``@<name>``): a library card is in no
    project's scope, it has its own sidebar section."""
    if is_library_owner(owner):
        return f'{TAG_KEY}:{library_name(owner)}'
    return f'project:{owner}'


def split_paths(value: str, sep: str = os.pathsep) -> list[str]:
    """A path list as ``HOUDINI_PATH`` spells one; blanks dropped.

    ``&`` (Houdini's "the default path here") means nothing for a library
    and is dropped too.
    """
    out = []
    for part in (value or '').split(sep):
        part = part.strip()
        if part and part != '&':
            out.append(part)
    return out


def _key(path: str) -> str:
    """Path identity for de-duplication: case and separators folded on
    Windows, trailing separators dropped."""
    p = os.path.normpath(path.strip())
    return os.path.normcase(p)


def project_paths(
    settings_paths: Iterable[str], *, launched: bool,
    environ: Optional[Mapping[str, str]] = None, sep: str = os.pathsep,
) -> list[LibraryPath]:
    """The library paths one project lists: the launch environment's first
    (only for the *launched* project), then its settings, de-duplicated."""
    environ = os.environ if environ is None else environ
    listed: list[LibraryPath] = []
    if launched:
        listed += [LibraryPath(p, SOURCE_ENV)
                   for p in split_paths(environ.get(ENV_VAR, ''), sep)]
    listed += [LibraryPath(str(p).strip(), SOURCE_SETTINGS)
               for p in settings_paths if str(p).strip()]
    seen: set[str] = set()
    out = []
    for lp in listed:
        k = _key(lp.path)
        if k in seen:
            continue
        seen.add(k)
        out.append(lp)
    return out


def _fallback_name(path: Path) -> str:
    leaf = path.name or path.anchor.strip('\\/:') or 'library'
    name = re.sub(r'[^A-Za-z0-9_-]+', '_', leaf).strip('_-')
    return name or 'library'


def read_library(path: str) -> Library:
    """The library at *path*; raises :class:`LibraryError` when it is not
    a folder or its ``library.json`` is unreadable or names it badly."""
    root = Path(path)
    if not root.is_dir():
        raise LibraryError(path, f'library folder {path} is not reachable')
    doc: dict = {}
    marker = root / LIBRARY_FILE
    if marker.exists():
        try:
            doc = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise LibraryError(path, f'{marker} could not be read: {exc}') from exc
        if not isinstance(doc, dict):
            raise LibraryError(path, f'{marker} is not a JSON object')
    raw = doc.get('name')
    if raw is None:
        name = _fallback_name(root)
    else:
        name = str(raw).strip()
        if not _NAME_RE.match(name):
            raise LibraryError(
                path, f'library name {name!r} in {marker} must be letters, '
                      'digits, "-" or "_", starting with a letter or digit')
    return Library(name=name, path=root, read_only=bool(doc.get('read_only', False)))


def write_library_file(path: str | os.PathLike, name: str,
                       read_only: bool = False) -> None:
    """Make *path* a named library (the settings page's New Library)."""
    if not _NAME_RE.match(name):
        raise ValueError(f'library name {name!r} must be letters, digits, "-" or "_"')
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_FILE).write_text(
        json.dumps({'name': name, 'read_only': read_only}, indent=2) + '\n',
        encoding='utf-8')


@dataclass(frozen=True)
class Resolved:
    """Every registered project's libraries, once each."""

    libraries: list[Library]
    #: project name -> names of the libraries it lists, in its order.
    by_project: dict[str, list[str]]
    errors: list[LibraryError]

    def get(self, name: str) -> Optional[Library]:
        return next((lib for lib in self.libraries if lib.name == name), None)


class LibraryIndex:
    """The registered projects' libraries, read once per Refresh.

    :meth:`current` reads the ``library.json`` files (the share): worker
    thread, or right after a local write. :meth:`cached` never touches the
    disk, for the sidebar on the GUI thread.
    """

    def __init__(self, registry, *, launched_name=None,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        self._registry = registry
        self._launched_name = launched_name
        self._environ = environ
        self._resolved: Optional[Resolved] = None

    def _launched(self) -> str:
        if self._launched_name is not None:
            return self._launched_name() if callable(self._launched_name) \
                else self._launched_name
        from .access import launched_project_name
        return launched_project_name(self._environ)

    def invalidate(self) -> None:
        self._resolved = None

    def current(self) -> Resolved:
        if self._resolved is None:
            self._resolved = resolve(self._registry.all(),
                                     launched_name=self._launched(),
                                     environ=self._environ)
        return self._resolved

    def cached(self) -> Resolved:
        return self._resolved or Resolved([], {}, [])

    def env_paths(self, project_name: str) -> list[str]:
        """The launch environment's paths for *project_name* (the settings
        page shows them locked); empty for every other project."""
        if project_name != self._launched():
            return []
        environ = os.environ if self._environ is None else self._environ
        return split_paths(environ.get(ENV_VAR, ''))


def resolve(
    projects: Iterable, *, launched_name: str,
    environ: Optional[Mapping[str, str]] = None, sep: str = os.pathsep,
) -> Resolved:
    """Read every library the registered *projects* list.

    *projects* are ``ProjectConfig``s (``name``, ``library_paths``). A path
    two projects list is read once; a path that fails is an error, and the
    rest still load; a second library with a name already taken is an
    error naming both folders.
    """
    libraries: list[Library] = []
    by_key: dict[str, Optional[Library]] = {}
    by_project: dict[str, list[str]] = {}
    errors: list[LibraryError] = []
    for proj in projects:
        names = by_project.setdefault(proj.name, [])
        for lp in project_paths(getattr(proj, 'library_paths', ()) or (),
                                launched=proj.name == launched_name,
                                environ=environ, sep=sep):
            k = _key(lp.path)
            if k not in by_key:
                try:
                    lib = read_library(lp.path)
                except LibraryError as err:
                    errors.append(err)
                    by_key[k] = None
                    continue
                clash = next((o for o in libraries if o.name == lib.name), None)
                if clash is not None:
                    errors.append(LibraryError(
                        lp.path, f'library {lp.path} is named {lib.name!r}, '
                                 f'like {clash.path}; give one of them another '
                                 f'name in its {LIBRARY_FILE}'))
                    by_key[k] = None
                    continue
                libraries.append(lib)
                by_key[k] = lib
            lib = by_key[k]
            if lib is not None and lib.name not in names:
                names.append(lib.name)
    return Resolved(libraries, by_project, errors)
