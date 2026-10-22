"""Every file of a project, classified.

``scan`` walks each root from :mod:`tumblepipe.archive.roots` once and gives
every file a :class:`Kind` — what it *is*, not whether to archive it (that is
:mod:`tumblepipe.archive.plan`'s call). Files that come in versions also get
the version folder they belong to (their *unit*), the series of versions it
is one of, its number, and whether it finished:

- a workfile is one ``<stem>_vNNNN.hip`` file, its series the department
  folder; it is complete by existing;
- an export version is a ``vNNNN`` folder, complete when its
  ``context.json`` is there (every exporter writes it last);
- a ``th::cache`` version is a ``cache/<name>/vNNNN`` (or ``lops_cache``)
  folder in a workfile folder, complete when its ``kind: cache`` sidecar is
  there (``pipe/cache_context``); caches from before the sidecar read as
  incomplete *and* unknown provenance, which the plan flags;
- a render version is a ``vNNNN`` folder or a ``vNNNN.<ext>`` movie.

Classification is by layout, so it works on any project the pipeline wrote,
including a delivered one opened years later without TumblePipe's config
code: only ``storage.json`` is read.
"""

from dataclasses import dataclass, field
from pathlib import Path
import os
import re

from tumblepipe import naming
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import ProjectRoots, Root, WORKFILE_PURPOSES
from tumblepipe.pipe import cache_context
from tumblepipe.util.io import load_json_if_exists


class Kind:
    CONFIG = 'config'                   # _config: the project's pipeline config
    WORKFILE = 'workfile'               # <stem>_vNNNN.hip / .hiplc / .hipnc
    WORKFILE_META = 'workfile_meta'     # context.json, _context/vNNNN.json
    WORKFILE_FILE = 'workfile_file'     # anything else an artist put in a workfile folder
    BACKUP = 'backup'                   # Houdini's backup/*_bakN.hip
    CACHE = 'cache'                     # th::cache versions in workfile folders
    LOCAL_RENDER = 'local_render'       # render/ or galleries/ inside a workfile folder
    DERIVED_TEXTURE = 'derived_texture'  # .rat / .tx made from a source texture
    EXPORT = 'export'                   # export version folders
    EXPORT_FILE = 'export_file'         # export files outside any version folder
    STAGED = 'staged'                   # export/**/_staged: pointer scenes
    FARM_JOB = 'farm_job'               # export/other/jobs: farm submission copies
    EXPORT_LOG = 'export_log'           # export/other/*: logs and other records
    RENDER = 'render'                   # the render root: frames, playblasts, dailies
    DERIVED_ROOT = 'derived_root'       # cache / proxy / turntable roots
    EDIT = 'edit'                       # the edit root (Resolve, edit exports)
    PIPELINE_DIR = 'pipeline_dir'       # otls, recipes, library: what workfiles load
    PROJECT_FILE = 'project_file'       # loose files at the project's top level
    EXTRA = 'extra'                     # a folder _config/archive.json includes
    UNMANAGED = 'unmanaged'             # any other folder: client material, delivery, temp
    ENVIRONMENT = 'environment'         # a Python venv, node_modules: re-installable
    NOISE = 'noise'                     # __pycache__, *.bak, Thumbs.db, ...


#: Top-level project folders the pipeline itself fills and workfiles load
#: from: HDAs, recipes and library downloads (``TT_DOWNLOADS_PATH``).
PIPELINE_DIRS = ('otls', 'recipes', 'library')

#: An optional per-project file naming extra top-level folders to include
#: (``{"include": ["resources"]}``) or pipeline folders to leave out
#: (``{"exclude": ["library"]}``).
ARCHIVE_CONFIG = 'archive.json'

NOISE_NAMES = frozenset({
    '__pycache__', 'Thumbs.db', 'desktop.ini', '.DS_Store', '.git',
})
_NOISE_SUFFIXES = ('.pyc', '.pyo', '.tmp')
# config.json.bak-20260922-104303, storage_convention.py.v11.bak,
# groups.json.departments.bak, context.json.bak-20260917
_BAK = re.compile(r'\.bak(?:$|[-_.\d])', re.IGNORECASE)
_WORKFILE = re.compile(r'_(v\d{4})\.(?:hip|hiplc|hipnc)$', re.IGNORECASE)
_DERIVED_TEXTURES = ('.rat', '.tx')
_CACHE_DIRS = ('cache', 'lops_cache')
#: Folders that are installed software or tool caches, not work: re-made by
#: reinstalling. A Python virtual environment is found by its pyvenv.cfg,
#: whatever it is called.
ENVIRONMENT_DIRS = frozenset({
    'node_modules', 'site-packages', '.venv', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', '.ipynb_checkpoints', '.tox',
})
_VENV_MARKER = 'pyvenv.cfg'


@dataclass(frozen=True)
class Item:
    root: str                   # the Root's purpose
    rel: str                    # POSIX path relative to the root
    size: int
    kind: str
    unit: str | None = None     # the version folder (or file) it belongs to
    series: str | None = None   # the folder holding that unit's versions
    version: int | None = None
    complete: bool = True

    def path(self, roots: ProjectRoots) -> Path:
        return roots.get(self.root).path.joinpath(*self.rel.split('/'))


@dataclass
class Inventory:
    roots: ProjectRoots
    items: list[Item] = field(default_factory=list)
    #: cache unit key ('<root>:<unit>') -> its sidecar, for complete caches
    cache_contexts: dict[str, dict] = field(default_factory=dict)
    #: _config/archive.json, or {} without one
    archive_config: dict = field(default_factory=dict)
    #: paths that could not be read (permission, vanished mid-walk)
    errors: list[str] = field(default_factory=list)


def is_noise(parts: tuple[str, ...]) -> bool:
    name = parts[-1]
    if any(part in NOISE_NAMES for part in parts):
        return True
    if name.lower().endswith(_NOISE_SUFFIXES) or name.endswith('~'):
        return True
    return _BAK.search(name) is not None


def _version_index(parts, *, stems: bool = False) -> int | None:
    """Index of the first part that is a version name (``v0003``).

    With ``stems``, a file named ``v0003.mp4`` counts too.
    """
    for index, part in enumerate(parts):
        name = part.split('.', 1)[0] if stems else part
        if naming.is_valid_version_name(name):
            return index
    return None


def _join(parts) -> str:
    return '/'.join(parts)


def _walk(path: Path, skip: set[Path], errors: list[str]):
    """``(relative parts, size)`` for every file under ``path``.

    Does not follow symlinks, and does not descend into ``skip`` (the roots
    walked on their own).
    """
    stack = [(path, ())]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(long_path(directory)))
        except OSError as error:
            errors.append(f'{directory}: {error}')
            continue
        for entry in entries:
            parts = prefix + (entry.name,)
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = directory / entry.name
                    if child in skip:
                        continue
                    stack.append((child, parts))
                elif entry.is_file(follow_symlinks=False):
                    yield parts, entry.stat(follow_symlinks=False).st_size
            except OSError as error:
                errors.append(f'{entry.path}: {error}')


class _Classifier:
    def __init__(self, inventory: Inventory):
        self.inventory = inventory
        self._complete: dict[str, bool] = {}
        self._venv: dict[tuple, bool] = {}

    def in_environment(self, root: Root, dirs: tuple[str, ...]) -> bool:
        """Whether a folder on the way to a file is an installed environment."""
        if any(part in ENVIRONMENT_DIRS for part in dirs):
            return True
        for depth in range(1, len(dirs) + 1):
            key = (root.purpose,) + dirs[:depth]
            if key not in self._venv:
                self._venv[key] = long_path(root.path.joinpath(*dirs[:depth], _VENV_MARKER)).is_file()
            if self._venv[key]:
                return True
        return False

    # Units ------------------------------------------------------------------

    def _export_complete(self, root: Root, unit: tuple[str, ...]) -> bool:
        key = f'{root.purpose}:{_join(unit)}'
        if key not in self._complete:
            self._complete[key] = long_path(root.path.joinpath(*unit, 'context.json')).is_file()
        return self._complete[key]

    def _cache_complete(self, root: Root, unit: tuple[str, ...]) -> bool:
        key = f'{root.purpose}:{_join(unit)}'
        if key not in self._complete:
            data = cache_context.load_cache_context(long_path(root.path.joinpath(*unit)))
            self._complete[key] = data is not None
            if data is not None:
                self.inventory.cache_contexts[key] = data
        return self._complete[key]

    def _unit_item(self, root, parts, size, kind, index, complete) -> Item:
        unit = parts[:index + 1]
        version_name = unit[-1].split('.', 1)[0]
        return Item(
            root.purpose, _join(parts), size, kind,
            unit=_join(unit), series=_join(unit[:-1]),
            version=naming.get_version_code(version_name),
            complete=complete,
        )

    # Roots ------------------------------------------------------------------

    def workfile_item(self, root: Root, parts, size) -> Item:
        rel = _join(parts)
        dirs = parts[:-1]
        if 'backup' in dirs:
            return Item(root.purpose, rel, size, Kind.BACKUP)
        for cache_dir in _CACHE_DIRS:
            if cache_dir in dirs:
                at = dirs.index(cache_dir)
                # <dept>/cache/<name>/<vNNNN>/...
                if at + 2 < len(parts) - 1 and naming.is_valid_version_name(parts[at + 2]):
                    index = at + 2
                    return self._unit_item(
                        root, parts, size, Kind.CACHE, index,
                        self._cache_complete(root, parts[:index + 1]),
                    )
                return Item(root.purpose, rel, size, Kind.CACHE, complete=False)
        if '_context' in dirs or parts[-1] == 'context.json':
            return Item(root.purpose, rel, size, Kind.WORKFILE_META)
        if 'render' in dirs or 'galleries' in dirs:
            # A local render, or Solaris's render gallery snapshots.
            return Item(root.purpose, rel, size, Kind.LOCAL_RENDER)
        match = _WORKFILE.search(parts[-1])
        if match is not None:
            return Item(
                root.purpose, rel, size, Kind.WORKFILE,
                unit=rel, series=_join(dirs),
                version=naming.get_version_code(match.group(1).lower()),
            )
        if parts[-1].lower().endswith(_DERIVED_TEXTURES):
            return Item(root.purpose, rel, size, Kind.DERIVED_TEXTURE)
        return Item(root.purpose, rel, size, Kind.WORKFILE_FILE)

    def export_item(self, root: Root, parts, size) -> Item:
        rel = _join(parts)
        if parts[0] == 'other' and len(parts) > 1:
            if parts[1] == 'jobs':
                return Item(root.purpose, rel, size, Kind.FARM_JOB)
            return Item(root.purpose, rel, size, Kind.EXPORT_LOG)
        index = _version_index(parts[:-1])
        kind = Kind.STAGED if '_staged' in parts[:-1] else Kind.EXPORT
        if index is None:
            return Item(root.purpose, rel, size, kind if kind == Kind.STAGED else Kind.EXPORT_FILE)
        complete = True if kind == Kind.STAGED else self._export_complete(root, parts[:index + 1])
        return self._unit_item(root, parts, size, kind, index, complete)

    def render_item(self, root: Root, parts, size) -> Item:
        index = _version_index(parts, stems=True)
        if index is None:
            return Item(root.purpose, _join(parts), size, Kind.RENDER)
        return self._unit_item(root, parts, size, Kind.RENDER, index, True)

    def project_item(self, root: Root, parts, size) -> Item:
        rel = _join(parts)
        config = self.inventory.archive_config
        if len(parts) == 1:
            return Item(root.purpose, rel, size, Kind.PROJECT_FILE)
        top = parts[0]
        if top in config.get('exclude', ()):
            return Item(root.purpose, rel, size, Kind.UNMANAGED)
        if top in config.get('include', ()):
            return Item(root.purpose, rel, size, Kind.EXTRA)
        if top in PIPELINE_DIRS:
            return Item(root.purpose, rel, size, Kind.PIPELINE_DIR)
        return Item(root.purpose, rel, size, Kind.UNMANAGED)

    def classify(self, root: Root, parts, size) -> Item:
        if is_noise(parts):
            return Item(root.purpose, _join(parts), size, Kind.NOISE)
        if root.purpose != 'config' and self.in_environment(root, parts[:-1]):
            return Item(root.purpose, _join(parts), size, Kind.ENVIRONMENT)
        if root.purpose == 'config':
            return Item(root.purpose, _join(parts), size, Kind.CONFIG)
        if root.purpose in WORKFILE_PURPOSES:
            return self.workfile_item(root, parts, size)
        if root.purpose == 'export':
            return self.export_item(root, parts, size)
        if root.purpose == 'render':
            return self.render_item(root, parts, size)
        if root.purpose == 'edit':
            return Item(root.purpose, _join(parts), size, Kind.EDIT)
        if root.purpose in ('cache', 'proxy', 'turntable'):
            return Item(root.purpose, _join(parts), size, Kind.DERIVED_ROOT)
        return self.project_item(root, parts, size)


def load_archive_config(config_path: Path) -> dict:
    data = load_json_if_exists(Path(config_path) / ARCHIVE_CONFIG) or {}
    if not isinstance(data, dict):
        return {}
    return {
        key: tuple(str(name) for name in data.get(key, ()) or ())
        for key in ('include', 'exclude')
    }


def scan(roots: ProjectRoots) -> Inventory:
    """Classify every file under ``roots``."""
    inventory = Inventory(roots, archive_config=load_archive_config(roots.config_path))
    classifier = _Classifier(inventory)
    all_paths = {root.path for root in roots.roots}
    for root in roots.roots:
        if not root.path.is_dir():
            continue
        skip = {path for path in all_paths if path != root.path}
        for parts, size in _walk(root.path, skip, inventory.errors):
            inventory.items.append(classifier.classify(root, parts, size))
    return inventory
