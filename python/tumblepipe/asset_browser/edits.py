"""Edits as cards of the pipeline catalog.

An edit (``entity:/edits/<name>``, ``tumblepipe.pipe.edits``) is a cut the
editor (``th_player``) makes, saves and publishes. Each edit a project has
is a card tagged ``type:edit``, in the sidebar's Edits section, named after
the edit, showing its latest published version and its newest working
version, and whether it is the project's master edit. A publish writes no
thumbnail, so the card shows the ``film`` icon.

Its menu opens it in the editor (the newest working version), plays its
latest published version read-only, opens its working folder, and makes it
the master edit or the project none. Edits are made in the editor (Save
As), not here: the browser creates, renames and deletes none.

Ids are ``edit:<project>:<name>``. The names come from the project's
config (a cached tree, safe on the GUI thread, which the sidebar's count
reads); the versions from listing each edit's working and export folders,
on the worker thread, cached until a Refresh. ``hou`` is never imported,
so this module imports headlessly for the tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from tumbletrove.asset_browser.api.errors import AssetDiscoveryError, CatalogError
from tumbletrove.asset_browser.api.types import Asset, AssetAction, AssetDetail

from .library_kinds import LibraryKind

from tumblepipe.pipe import edits as pipe_edits
from tumblepipe.util.io import VERSION_MARKER
from tumblepipe.util.uri import Uri

if TYPE_CHECKING:
    from .clients import ClientPool

log = logging.getLogger(__name__)

ID_PREFIX = 'edit:'
TYPE_TAG = 'type:edit'
KIND = 'edit'
ICON = 'film'


@dataclass(frozen=True)
class EditRef:
    project: str
    name: str

    @property
    def asset_id(self) -> str:
        return f'{ID_PREFIX}{self.project}:{self.name}'

    @property
    def uri(self) -> Uri:
        return pipe_edits.edit_uri(self.name)


def parse_id(asset_id: str) -> Optional[EditRef]:
    """The edit an id names, None when it is not an edit id."""
    if not asset_id.startswith(ID_PREFIX):
        return None
    project, sep, name = asset_id[len(ID_PREFIX):].partition(':')
    if not sep or not project or not name or '/' in name or ':' in name:
        return None
    return EditRef(project, name)


def is_edit_id(asset_id: str) -> bool:
    return parse_id(asset_id) is not None


@dataclass(frozen=True)
class EditEntry:
    """An edit and what it has on disk."""
    ref: EditRef
    working_dir: Path
    working: tuple[str, ...]        # oldest first
    published: tuple[str, ...]      # finished only, oldest first
    is_master: bool
    updated: float                  # newest save or publish, epoch; 0 when none
    user: str                       # who made it

    @property
    def latest_working(self) -> Optional[str]:
        return self.working[-1] if self.working else None

    @property
    def latest_published(self) -> Optional[str]:
        return self.published[-1] if self.published else None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _user(path: Path) -> str:
    from tumblepipe.util.io import load_json
    try:
        data = load_json(path)
    except Exception:  # noqa: BLE001 - a sidecar that does not read names nobody
        return ''
    return str((data or {}).get('user') or '') if isinstance(data, dict) else ''


def read_entry(project: str, name: str, client, master: Optional[str]) -> EditEntry:
    """Edit ``name`` of ``project`` as it is on disk now."""
    working_dir = pipe_edits.working_dir(name, client)
    working = tuple(pipe_edits.working_versions(name, client))
    published = tuple(pipe_edits.published_versions(name, client))
    # The newest save's sidecar, and the newest publish's context.json.
    stamps = []
    if working:
        stamps.append(working_dir / pipe_edits.CONTEXT_DIR / f'{working[-1]}.json')
    if published:
        stamps.append(pipe_edits.export_dir(name, client) / published[-1] / VERSION_MARKER)
    times = [(_mtime(path), path) for path in stamps]
    updated, newest = max(times, key = lambda t: t[0], default = (0.0, None))
    if working and updated == 0.0:
        updated = _mtime(working_dir / pipe_edits.working_file_name(name, working[-1]))
    return EditEntry(
        ref = EditRef(project, name),
        working_dir = working_dir,
        working = working,
        published = published,
        is_master = master == name,
        updated = updated,
        user = '' if newest is None else _user(newest),
    )


def scan_project(project: str, client) -> list[EditEntry]:
    """Every edit of one project, by name."""
    master = pipe_edits.master_edit(client)
    return [read_entry(project, name, client, master) for name in pipe_edits.list_edits(client)]


def _note(entry: EditEntry) -> str:
    parts = []
    if entry.is_master:
        parts.append('Master edit')
    parts.append(f'working {entry.latest_working}' if entry.latest_working else 'never saved')
    return ' · '.join(parts)


MASTER_BADGE = 'MASTER'


def badge_fields(is_master: bool, asset_fields) -> dict:
    """The master edit's grid mark (#28): ``Asset.badge``, where the
    installed TumbleTrove's ``Asset`` has that field (*asset_fields*, its
    field names). An older one has no mark rather than a refused card."""
    if is_master and 'badge' in asset_fields:
        return {'badge': MASTER_BADGE}
    return {}


def card_fields(entry: EditEntry, catalog_id: str) -> dict:
    ref = entry.ref
    tags = {f'source:{catalog_id}', TYPE_TAG, f'project:{ref.project}', f'kind:{KIND}'}
    if entry.is_master:
        tags.add('status:master')
    return dict(
        id = ref.asset_id,
        name = ref.name,
        thumbnail_url = '',
        tags = frozenset(tags),
        catalog_id = catalog_id,
        kind = KIND,
        icon = ICON,
        **badge_fields(entry.is_master, Asset.__dataclass_fields__),
        metadata = {
            'project': ref.project,
            'uri': str(ref.uri),
            'version': entry.latest_published or '',
            'published': entry.latest_published or '',
            'working': entry.latest_working or '',
            'master': entry.is_master,
            'last_note': _note(entry),
            'latest_update': entry.updated,
            'last_user': entry.user,
        },
    )


def build_card(entry: EditEntry, catalog_id: str) -> Asset:
    return Asset(**card_fields(entry, catalog_id))


def build_detail(entry: EditEntry, catalog_id: str) -> AssetDetail:
    published = (f'published {entry.latest_published}'
                 if entry.latest_published else 'not published')
    return AssetDetail(
        description = f'Edit {entry.ref.name}: {published}, {_note(entry)}.',
        **card_fields(entry, catalog_id),
    )


class EditManager(LibraryKind):
    """Edit scanning and cards for :class:`PipelineCatalog`, over the
    catalog's per-project clients: a library kind (``library_kinds.py``),
    though owned by a project, not a library. Its card menu and Open in
    Editor are the catalog's (``menus.edit_items``, ``EntityOps.open_edit``),
    passed in; its Open Folder is the working folder (``asset_folder``), no drop,
    no edit fields and no delete (the editor manages edits)."""

    kind = KIND
    id_prefix = ID_PREFIX
    type_tag = TYPE_TAG

    def __init__(
        self, catalog_id: str, *, clients: 'ClientPool',
        menu_items: Optional[Callable[[Asset], list]] = None,
        open_edit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._catalog_id = catalog_id
        self._clients = clients
        self._menu_items = menu_items
        self._open_edit = open_edit
        self._cache: Optional[list[EditEntry]] = None
        self._errors: list[CatalogError] = []

    def owns(self, asset_id: str) -> bool:
        return is_edit_id(asset_id)

    def project_of(self, asset_id: str) -> Optional[str]:
        ref = parse_id(asset_id)
        return None if ref is None else ref.project

    def invalidate(self) -> None:
        self._cache = None

    @property
    def errors(self) -> list[CatalogError]:
        return list(self._errors)

    def names(self, project: str) -> list[str]:
        """The edits ``project`` has, from its config alone: safe on the GUI
        thread. [] while its client is not ready."""
        if not self._clients.is_ready(project):
            return []
        return pipe_edits.list_edits(self._clients.get(project))

    def client(self, project: str):
        """The project's client (building it if need be)."""
        return self._clients.get(project)

    def scan_all(self) -> list[EditEntry]:
        entries: list[EditEntry] = []
        errors: list[CatalogError] = []
        for project, client in self._clients.ready.items():
            try:
                entries += scan_project(project, client)
            except Exception as exc:  # noqa: BLE001 - one project must not hide the rest
                log.exception('edit scan failed for %s', project)
                errors.append(AssetDiscoveryError(
                    self._catalog_id,
                    f'the edits of {project} could not be read: {exc}',
                    cause = exc,
                ))
        self._cache = entries
        self._errors = errors
        return entries

    def entries(self) -> list[EditEntry]:
        return self.scan_all() if self._cache is None else list(self._cache)

    def find(self, asset_id: str) -> Optional[EditEntry]:
        """The edit ``asset_id`` names, read from disk now (a menu or detail
        must show what is there, not what the last scan saw); None when the
        project has no such edit."""
        ref = parse_id(asset_id)
        if ref is None:
            return None
        client = self._clients.get(ref.project)
        if ref.name not in pipe_edits.list_edits(client):
            return None
        return read_entry(ref.project, ref.name, client, pipe_edits.master_edit(client))

    def cards(self) -> list[Asset]:
        return [build_card(e, self._catalog_id) for e in self.entries()]

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        entry = self.find(asset_id)
        return None if entry is None else build_card(entry, self._catalog_id)

    def get_detail(self, asset_id: str) -> AssetDetail:
        from tumbletrove.asset_browser.api.errors import DetailBuildError

        entry = self.find(asset_id)
        if entry is None:
            raise DetailBuildError(self._catalog_id, asset_id, 'edit not found')
        return build_detail(entry, self._catalog_id)

    def get_thumbnail(self, asset: Asset):
        from tumbletrove.asset_browser.core.thumbnail import IconThumbnail
        return IconThumbnail(ICON, 128)

    def asset_folder(self, asset: Asset) -> Optional[Path]:
        """The browser's Open Folder: the edit's working folder, where its
        saved versions are. Path arithmetic only (the menu is opening on
        the GUI thread); one never saved opens its nearest parent."""
        ref = parse_id(asset.id)
        if ref is None or not self._clients.is_ready(ref.project):
            return None
        return pipe_edits.working_dir(ref.name, self._clients.get(ref.project))

    def card_menu_items(self, asset: Asset) -> list:
        return [] if self._menu_items is None else self._menu_items(asset)

    def ghost_data(self, asset: Asset):
        from tumbletrove.asset_browser.core.ghost_overlay import GhostData
        return GhostData()  # an edit drops nowhere

    def get_actions(self, detail) -> list[AssetAction]:
        actions = [AssetAction(id='open_edit', label='Open in Editor', icon='film')]
        if (detail.metadata or {}).get('published'):
            actions.append(AssetAction(
                id='play_published_edit', label='Play Published', icon='play'))
        return actions

    def execute_action(self, action_id: str, detail) -> bool:
        if action_id not in ('open_edit', 'play_published_edit'):
            return False
        if self._open_edit is not None:
            self._open_edit(detail.id, published=action_id == 'play_published_edit')
        return True
