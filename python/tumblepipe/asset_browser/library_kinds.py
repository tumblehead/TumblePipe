"""Library kinds: the card kinds that are files, not pipeline entities.

Recipes and texture sets (and, by ``designs/libraries-pickers-poses.md``,
pickers and poses) are not assets or shots: no departments, no workfiles, no
entity URI. Each is a :class:`LibraryKind`: one object that scans its files,
makes its cards and answers every catalog call for the ids it owns. Their ids
share one shape, ``<kind>:<owner>:<path>``, so the catalog routes an id to
its kind by prefix (:meth:`LibraryKinds.for_id`) before any entity resolver
sees it.

The catalog asks :class:`LibraryKinds` instead of testing each kind in turn.
That used to be an ``if recipes... elif texture_sets...`` chain in about thirty
catalog methods, so a new kind meant thirty edits, and a missed one meant a
call that silently fell through to the entity resolvers.

Every method has the default a kind without that feature should give (no
menu, no edit fields, no delete), so a kind overrides only what it does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from tumbletrove.asset_browser.api.errors import CatalogError
from tumbletrove.asset_browser.api.types import (
    Asset, AssetAction, AssetDetail, CreationField,
)


class LibraryKind:
    """One kind of library card. Subclasses set :attr:`kind` (the cards'
    ``Asset.kind``), :attr:`id_prefix` and :attr:`type_tag`, and
    implement :meth:`owns`, :meth:`project_of`, :meth:`cards`,
    :meth:`get_asset`, :meth:`get_detail` and :meth:`get_thumbnail`."""

    kind: str = ''
    id_prefix: str = ''
    type_tag: str = ''

    # ── Ids ──────────────────────────────────────────────

    def owns(self, asset_id: str) -> bool:
        raise NotImplementedError

    def project_of(self, asset_id: str) -> Optional[str]:
        """The owner segment of one of this kind's ids."""
        raise NotImplementedError

    # ── Scanning ─────────────────────────────────────────

    def warm_up(self) -> None:
        """Worker thread, at browser start: scan now when the sidebar's
        counts need it before the first grid query. Default: nothing."""

    def invalidate(self) -> None:
        """Forget the scan; the next read rescans."""

    @property
    def errors(self) -> list[CatalogError]:
        """What the last scan could not read."""
        return []

    def contexts(self) -> list[str]:
        """Values this kind adds to the ``context`` tag group."""
        return []

    # ── Cards ────────────────────────────────────────────

    def cards(self) -> list[Asset]:
        raise NotImplementedError

    def restamp(self, items: list[Asset]) -> list[Asset]:
        """Re-evaluate whatever on this kind's cached cards depends on the
        session now (a recipe's network context). Default: unchanged."""
        return items

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        raise NotImplementedError

    def get_detail(self, asset_id: str) -> AssetDetail:
        raise NotImplementedError

    def get_thumbnail(self, asset: Asset):
        raise NotImplementedError

    def asset_folder(self, asset: Asset) -> Optional[Path]:
        """The card menu's Open Folder, or None for no entry."""
        return None

    def card_menu_items(
        self, asset: Asset,
    ) -> list[tuple[str, Callable[[], None]]]:
        return []

    def ghost_data(self, asset: Asset):
        """The drag ghost; None for the browser's plain one."""
        return None

    # ── Drops ────────────────────────────────────────────

    def on_drop(self, detail, drop) -> bool:
        return False

    # ── Actions, edit, delete ────────────────────────────

    def get_actions(self, detail) -> list[AssetAction]:
        return []

    def execute_action(self, action_id: str, detail) -> bool:
        """Run a detail action; True when it was one of this kind's."""
        return False

    def get_edit_fields(self, asset_id: str) -> list[CreationField]:
        return []

    def edit(self, asset_id: str, fields: dict) -> bool:
        return False

    def can_delete(self, asset: Asset) -> bool:
        return False

    def delete(self, asset_id: str) -> bool:
        return False


class LibraryKinds:
    """The catalog's registered library kinds, in sidebar and grid order."""

    def __init__(self, kinds: Iterable[LibraryKind]) -> None:
        self._kinds = list(kinds)
        prefixes = [k.id_prefix for k in self._kinds]
        for i, prefix in enumerate(prefixes):
            if not prefix or not prefix.endswith(':'):
                raise ValueError(f'library kind id prefix {prefix!r} must end with ":"')
            for other in prefixes[:i]:
                if prefix.startswith(other) or other.startswith(prefix):
                    raise ValueError(
                        f'library kind id prefixes {other!r} and {prefix!r} overlap')

    def __iter__(self):
        return iter(self._kinds)

    @property
    def kind_names(self) -> frozenset[str]:
        return frozenset(k.kind for k in self._kinds)

    def for_id(self, asset_id: str) -> Optional[LibraryKind]:
        """The kind that owns *asset_id*, or None for any other id."""
        if not asset_id:
            return None
        for kind in self._kinds:
            if asset_id.startswith(kind.id_prefix) and kind.owns(asset_id):
                return kind
        return None

    def owns(self, asset_id: str) -> bool:
        return self.for_id(asset_id) is not None

    def warm_up(self) -> None:
        for kind in self._kinds:
            kind.warm_up()

    def invalidate(self) -> None:
        for kind in self._kinds:
            kind.invalidate()

    def cards(self) -> list[Asset]:
        out: list[Asset] = []
        for kind in self._kinds:
            out += kind.cards()
        return out

    @property
    def errors(self) -> list[CatalogError]:
        out: list[CatalogError] = []
        for kind in self._kinds:
            out += kind.errors
        return out

    def contexts(self) -> list[str]:
        out: list[str] = []
        for kind in self._kinds:
            out += kind.contexts()
        return out

    def restamp(self, items: list[Any]) -> list[Any]:
        for kind in self._kinds:
            items = kind.restamp(items)
        return items
