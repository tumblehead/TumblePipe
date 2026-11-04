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
    owner_label = ('Project library' if ref.owner == LIBRARY_OWNER
                   else ref.owner.rsplit('/', 1)[-1])
    thumb = entry.thumbnail_path
    return dict(
        id=ref.asset_id,
        name=ref.name,
        thumbnail_url=str(thumb) if thumb else '',
        tags=frozenset({
            f'source:{catalog_id}', TYPE_TAG, f'project:{ref.project}',
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


def place(entry: TextureSetEntry, detail, drop) -> bool:
    """Drop *entry*'s newest version; False where it cannot go."""
    if drop.context == 'lop':
        from tumbletrove.asset_browser.catalogs._placement import finish_placement

        from tumblepipe.pipe.houdini.lops import import_texture

        node = import_texture.place(drop.network, entry.ref.name,
                                    entry.ref.owner_uri)
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


class TextureSetManager:
    """Texture set scanning, cards and drops for :class:`PipelineCatalog`.

    *export_roots* gives each registered project's export folder (None for
    a project whose client is not ready); it is called on the worker.
    """

    def __init__(self, catalog_id: str, *,
                 export_roots: Callable[[], dict[str, Optional[Path]]]) -> None:
        self._catalog_id = catalog_id
        self._export_roots = export_roots
        self._cache: Optional[list[TextureSetEntry]] = None
        self._errors: list[CatalogError] = []

    def invalidate(self) -> None:
        self._cache = None

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
        return str(entry.thumbnail_path)

    def on_drop(self, detail, drop) -> bool:
        entry = self.find(detail.id)
        return False if entry is None else place(entry, detail, drop)
