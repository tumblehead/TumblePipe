"""Published texture sets as cards of the pipeline catalog.

A texture set (``tumblepipe.pipe.textures``) is published into an asset's
``_textures`` or the project library under the project's export folder.
Each set with a finished version is a card tagged ``type:texture``, named
after the set, showing its newest version; its base colour is the
thumbnail when Qt can read the format.

A drop reads the newest version where it is, it is never copied:

- into LOPs, a ``th::import_texture`` pinned to that version (Status says
  when a newer one is published, Update takes it);
- into a shading network or Copernicus, the same nodes a Poly Haven drop
  builds (tumbletrove's ``place_texture_set``), reading the version's files
  through ``$TH_EXPORT_PATH``.

The cards carry ``texture_placed`` (tumbletrove ``PLACED_KEY``) so the
browser never asks for, nor copies to, a texture destination for them.

Ids are ``texture:<project>:<owner>/<set>``, ``<owner>`` being ``library``
or the asset's ``assets/<category>/<asset>``. The scan lists
``export/textures`` and ``export/assets/<category>/<asset>/_textures``
(one listing per category, one per asset with sets) on the worker thread,
and is cached until a Refresh. ``hou`` is imported only where a drop needs
it, so this module imports headlessly for the tests.

A library (``libraries.py``) is laid out like an export folder, so its
``textures/`` scan the same way, owned by ``@<library>``. A library set is
never referenced where it is: a drop copies its version into the scene's
project library first (``textures.copy_version``, reused for the same
version) and pins the node to the copy, so no farm job or archive needs the
library. Share to Library / Copy to Project copy versions the same way.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from tumbletrove.asset_browser.api.errors import AssetDiscoveryError, CatalogError
from tumbletrove.asset_browser.api.types import Asset, AssetDetail

from tumblepipe.pipe import textures as T
from .libraries import is_library_owner, library_name, owner_tag
from .library_kinds import LibraryKind
from tumblepipe.util.uri import Uri

log = logging.getLogger(__name__)

ID_PREFIX = 'texture:'
TYPE_TAG = 'type:texture'
LIBRARY_OWNER = 'library'
_KIND = 'texture'
#: tumbletrove's ``texture_destination.PLACED_KEY`` (spelled here so this
#: module imports with a tumbletrove that predates it).
PLACED_KEY = 'texture_placed'
#: Formats a thumbnail can be read from.
_THUMB_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}


@dataclass(frozen=True)
class TextureSetRef:
    project: str
    owner: str          # 'library' or 'assets/<category>/<asset>'
    name: str

    @property
    def asset_id(self) -> str:
        return f'{ID_PREFIX}{self.project}:{self.owner}/{self.name}'

    @property
    def owner_uri(self) -> Optional[Uri]:
        if self.owner == LIBRARY_OWNER:
            return None
        return Uri.parse('entity:/' + self.owner)


def parse_id(asset_id: str) -> Optional[TextureSetRef]:
    """The ref an id names, None when it is not a texture set id."""
    if not asset_id.startswith(ID_PREFIX):
        return None
    rest = asset_id[len(ID_PREFIX):]
    project, sep, path = rest.partition(':')
    if not sep or not project:
        return None
    owner, _, name = path.rpartition('/')
    segments = owner.split('/')
    if not name or not owner:
        return None
    if owner != LIBRARY_OWNER and (
            len(segments) != 3 or segments[0] != 'assets'
            or not all(segments)):
        return None
    return TextureSetRef(project, owner, name)


def is_texture_id(asset_id: str) -> bool:
    return parse_id(asset_id) is not None


@dataclass(frozen=True)
class TextureSetEntry:
    ref: TextureSetRef
    set_dir: Path
    version: str
    data: dict

    @property
    def version_path(self) -> Path:
        return self.set_dir / self.version

    @property
    def roles(self) -> list[str]:
        return [m['role'] for m in self.data.get('maps', [])]

    @property
    def thumbnail_path(self) -> Optional[Path]:
        for m in self.data.get('maps', []):
            if m.get('role') == 'diffuse' and not m.get('tiles'):
                path = self.version_path / m['file']
                if path.suffix.lower() in _THUMB_EXTENSIONS:
                    return path
        return None


def _entry(project: str, owner: str, set_dir: Path) -> Optional[TextureSetEntry]:
    version = T.latest_version(set_dir)
    if version is None:
        return None
    data = T.read_version(set_dir / version)
    if data is None:
        return None
    return TextureSetEntry(TextureSetRef(project, owner, set_dir.name),
                           set_dir, version, data)


def _dirs(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        return []


def scan_export(project: str, export_root: Path) -> list[TextureSetEntry]:
    """Every published set under one project's export folder: the
    library's, then each asset's, sorted by owner and name."""
    out: list[TextureSetEntry] = []
    export_root = Path(export_root)
    for name in T.list_sets(export_root / T.LIBRARY_SEGMENT):
        entry = _entry(project, LIBRARY_OWNER,
                       export_root / T.LIBRARY_SEGMENT / name)
        if entry is not None:
            out.append(entry)
    for category in _dirs(export_root / 'assets'):
        for asset in _dirs(category):
            sets = asset / T.ASSET_SEGMENT
            owner = f'assets/{category.name}/{asset.name}'
            for name in T.list_sets(sets):
                entry = _entry(project, owner, sets / name)
                if entry is not None:
                    out.append(entry)
    return out


def _timestamp(data: dict) -> float:
    try:
        return datetime.fromisoformat(data.get('timestamp') or '').timestamp()
    except ValueError:
        return 0.0


def card_fields(entry: TextureSetEntry, catalog_id: str) -> dict:
    ref = entry.ref
    if is_library_owner(ref.project):
        owner_label = f'Library {library_name(ref.project)}'
    elif ref.owner == LIBRARY_OWNER:
        owner_label = 'Project library'
    else:
        owner_label = ref.owner.rsplit('/', 1)[-1]
    thumb = entry.thumbnail_path
    return dict(
        id=ref.asset_id,
        name=ref.name,
        thumbnail_url=str(thumb) if thumb else '',
        tags=frozenset({
            f'source:{catalog_id}', TYPE_TAG, owner_tag(ref.project),
            f'kind:{_KIND}',
        }),
        catalog_id=catalog_id,
        kind=_KIND,
        icon='' if thumb else 'image',
        metadata={
            'project': ref.project,
            'owner': owner_label,
            'owner_uri': '' if ref.owner_uri is None else str(ref.owner_uri),
            'version': entry.version,
            'maps': ', '.join(entry.roles),
            'latest_update': _timestamp(entry.data),
            'last_user': str(entry.data.get('user') or ''),
            PLACED_KEY: True,
        },
    )


def build_card(entry: TextureSetEntry, catalog_id: str) -> Asset:
    return Asset(**card_fields(entry, catalog_id))


def build_detail(entry: TextureSetEntry, catalog_id: str) -> AssetDetail:
    fields = card_fields(entry, catalog_id)
    meta = fields['metadata']
    return AssetDetail(
        description=(f"Texture set {entry.ref.name} ({meta['owner']}), "
                     f"{entry.version}: {meta['maps']}"),
        **fields,
    )


def portable_maps(entry: TextureSetEntry) -> dict[str, str]:
    """Role -> the version's file, spelled ``$TH_EXPORT_PATH/...``."""
    return {role: T.portable_path(path)
            for role, path in T.version_maps(entry.version_path).items()}


def place(entry: TextureSetEntry, detail, drop, *, pin: bool = False) -> bool:
    """Drop *entry*'s newest version (with *pin*, exactly ``entry.version``:
    a library set's copy, which need not be the project's newest); False
    where it cannot go."""
    if drop.context == 'lop':
        from tumbletrove.asset_browser.catalogs._placement import finish_placement

        from tumblepipe.pipe.houdini.lops import import_texture

        node = import_texture.place(drop.network, entry.ref.name,
                                    entry.ref.owner_uri,
                                    version=entry.version if pin else None)
        if drop.selected_nodes:
            node.setInput(0, drop.selected_nodes[0])
        return finish_placement(node, f'import_{entry.ref.name}', drop,
                                f'Imported {entry.ref.name} {entry.version}')
    if drop.context not in ('vop', 'cop'):
        return False
    from tumbletrove.asset_browser.catalogs._placement import place_texture_set

    # The files are published: no destination applies (and none is copied).
    plain = dataclasses.replace(drop, texture_destination=None)
    named = dataclasses.replace(detail, id=entry.ref.name, name=entry.ref.name)
    return place_texture_set(named, portable_maps(entry), plain)


class TextureSetManager(LibraryKind):
    """Texture set scanning, cards and drops for :class:`PipelineCatalog`.

    *export_roots* gives each registered project's export folder (None for
    a project whose client is not ready); it is called on the worker.
    """

    kind = _KIND
    id_prefix = ID_PREFIX
    type_tag = TYPE_TAG

    def owns(self, asset_id: str) -> bool:
        return is_texture_id(asset_id)

    def project_of(self, asset_id: str) -> Optional[str]:
        ref = parse_id(asset_id)
        return None if ref is None else ref.project

    def asset_folder(self, asset: Asset) -> Optional[Path]:
        entry = self.find(asset.id)
        return None if entry is None else entry.set_dir

    def ghost_data(self, asset: Asset):
        from tumbletrove.asset_browser.core.ghost_overlay import GhostData, GhostNode
        return GhostData(nodes=[GhostNode('th::import_texture::1.0', 0.0, 0.0)])

    def can_delete(self, asset: Asset) -> bool:
        return False

    def __init__(self, catalog_id: str, *,
                 export_roots: Callable[[], dict[str, Optional[Path]]],
                 libraries=None, refresh=None,
                 session_project: Optional[Callable[[], Optional[str]]] = None,
                 ) -> None:
        self._catalog_id = catalog_id
        self._export_roots = export_roots
        self._libraries = libraries
        self._refresh = refresh
        # The project the open scene belongs to: a library set dropped into
        # it is copied into that project's library first.
        self._session_project = session_project
        self._cache: Optional[list[TextureSetEntry]] = None
        self._errors: list[CatalogError] = []
        # Each library's set count from warm_up, for the sidebar until the
        # full scan (first grid query) replaces it.
        self._library_counts: dict[str, int] = {}

    def invalidate(self) -> None:
        self._cache = None
        self._library_counts = {}

    def warm_up(self) -> None:
        """Count the libraries' sets (a few folders each) so their sidebar
        rows have counts on first paint. The projects' sets, under every
        asset's folder on the share, still wait for the first grid query."""
        counts: dict[str, int] = {}
        for lib in self._library_list(fresh=True):
            try:
                counts[lib.owner] = len(scan_export(lib.owner, lib.path))
            except Exception:  # noqa: BLE001 - scan_all reports it to the page
                log.exception('texture set count failed for library %s', lib.name)
        self._library_counts = counts

    @property
    def errors(self) -> list[CatalogError]:
        return list(self._errors)

    def scan_all(self) -> list[TextureSetEntry]:
        entries: list[TextureSetEntry] = []
        errors: list[CatalogError] = []
        for project, root in self._export_roots().items():
            if root is None:
                continue
            try:
                entries += scan_export(project, root)
            except Exception as exc:  # noqa: BLE001 - one project must not hide the rest
                log.exception('texture set scan failed for %s', project)
                errors.append(AssetDiscoveryError(
                    self._catalog_id,
                    f'texture sets of {project} could not be read: {exc}',
                    cause=exc,
                ))
        for lib in self._library_list(fresh=True):
            try:
                # A library is laid out like a project's export folder.
                entries += scan_export(lib.owner, lib.path)
            except Exception as exc:  # noqa: BLE001 - one library must not hide the rest
                log.exception('texture set scan failed for library %s', lib.name)
                errors.append(AssetDiscoveryError(
                    self._catalog_id,
                    f'texture sets of library {lib.name} could not be read: {exc}',
                    cause=exc,
                ))
        self._cache = entries
        self._errors = errors
        return entries

    def entries(self) -> list[TextureSetEntry]:
        return self.scan_all() if self._cache is None else list(self._cache)

    def find(self, asset_id: str) -> Optional[TextureSetEntry]:
        ref = parse_id(asset_id)
        if ref is None:
            return None
        for entry in self.entries():
            if entry.ref == ref:
                return entry
        # Published since the last scan (a drop right after a publish).
        self.invalidate()
        return next((e for e in self.entries() if e.ref == ref), None)

    def cards(self) -> list[Asset]:
        return [build_card(e, self._catalog_id) for e in self.entries()]

    def count(self, owner: str) -> int:
        """How many sets *owner* (a project or ``@library``) had at the last
        scan, or a library's at warm-up before that; never scans (the
        sidebar, on the GUI thread)."""
        if self._cache is None:
            return self._library_counts.get(owner, 0)
        return sum(1 for e in self._cache if e.ref.project == owner)

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        entry = self.find(asset_id)
        return None if entry is None else build_card(entry, self._catalog_id)

    def get_detail(self, asset_id: str) -> AssetDetail:
        from tumbletrove.asset_browser.api.errors import DetailBuildError

        entry = self.find(asset_id)
        if entry is None:
            raise DetailBuildError(self._catalog_id, asset_id,
                                   'texture set not found')
        return build_detail(entry, self._catalog_id)

    def get_thumbnail(self, asset: Asset):
        entry = self.find(asset.id)
        if entry is None or entry.thumbnail_path is None:
            from tumbletrove.asset_browser.core.thumbnail import IconThumbnail
            return IconThumbnail('image', 128)
        # A Path: TumbleTrove fetches a str as a URL ("unknown url type: c").
        return entry.thumbnail_path

    def on_drop(self, detail, drop) -> bool:
        entry = self.find(detail.id)
        if entry is None:
            return False
        if not is_library_owner(entry.ref.project):
            return place(entry, detail, drop)
        # A shared library's set: the scene must read a copy inside its own
        # project (the farm and the archive never see a library), so copy
        # the version into the project's library, pinned to that copy.
        if drop.context not in ('lop', 'vop', 'cop'):
            return False
        project = self._session_project() if self._session_project else None
        if not project:
            raise ValueError(
                f'{entry.ref.name} is in library {library_name(entry.ref.project)}: '
                'open a project workfile to use it, so it can be copied into '
                'that project')
        copied = self.copy_to(entry, project)
        return place(copied, detail, drop, pin=True)

    # ── Copying between projects and libraries ───────────

    def _library_list(self, fresh: bool = False) -> list:
        if self._libraries is None:
            return []
        resolved = (self._libraries.current() if fresh
                    else self._libraries.cached())
        return list(resolved.libraries)

    def _set_dir_for(self, owner: str, name: str) -> Path:
        """Where *owner* (a project, or ``@library``) keeps library set
        *name*'s versions."""
        if is_library_owner(owner):
            lib = next((lib for lib in self._library_list()
                        if lib.owner == owner), None)
            if lib is None:
                raise ValueError(f'library {library_name(owner)} is not listed')
            if lib.read_only:
                raise ValueError(f'library {lib.name} is read-only')
            return Path(lib.path) / T.LIBRARY_SEGMENT / name
        root = self._export_roots().get(owner)
        if root is None:
            raise ValueError(f'project {owner} is not ready (no export folder)')
        return Path(root) / T.LIBRARY_SEGMENT / name

    def share_targets(self, entry: TextureSetEntry) -> list[tuple[str, str]]:
        """``(owner, label)`` *entry* can be copied to: every writable
        library for a project's set, every ready project for a library's."""
        if is_library_owner(entry.ref.project):
            return [(name, name) for name, root in self._export_roots().items()
                    if root is not None]
        return [(lib.owner, lib.name) for lib in self._library_list()
                if not lib.read_only]

    def copy_to(self, entry: TextureSetEntry, owner: str) -> TextureSetEntry:
        """Copy *entry*'s version into *owner*'s library sets (reusing an
        earlier copy of the same version); the copy as an entry."""
        from tumblepipe.api import get_user_name

        set_dir = self._set_dir_for(owner, entry.ref.name)
        version_path = T.copy_version(
            entry.version_path, set_dir,
            copied_from=f'{entry.ref.asset_id}/{entry.version}',
            user=get_user_name())
        data = T.read_version(version_path) or {}
        copied = TextureSetEntry(TextureSetRef(owner, LIBRARY_OWNER, entry.ref.name),
                                 set_dir, version_path.name, data)
        self.invalidate()
        return copied

    def card_menu_items(self, asset: Asset):
        entry = self.find(asset.id)
        if entry is None:
            return []
        verb = ('Copy to Project' if is_library_owner(entry.ref.project)
                else 'Share to Library')

        def _copy(owner: str, label: str):
            def _run():
                copied = self.copy_to(entry, owner)
                if self._refresh is not None:
                    self._refresh.grid()
                import hou
                hou.ui.setStatusMessage(
                    f'Copied texture set {entry.ref.name} {entry.version} to '
                    f'{label} as {copied.version}',
                    severity=hou.severityType.Message)
            return _run

        return [(f'{verb}: {label}', _copy(owner, label))
                for owner, label in self.share_targets(entry)]
