"""Asking the open Asset Browsers to show what changed.

The SDK's public refresh requests (``Catalog.request_*_refresh``, TumbleTrove
>= 0.48) reach every open browser panel, from any thread, and do nothing
when none is open — headless included. :class:`BrowserRefresh` hands the
catalog's five requests to the managers as one service, so a collaborator
holds what it may ask for rather than the catalog itself.

Managers hold the one :class:`BrowserRefresh` the catalog builds and call
it at the time of the change, so a test that replaces a request on that
instance sees every caller's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tumbletrove.asset_browser.api.catalog import Catalog


class BrowserRefresh:
    """Repaint requests for the open browsers.

    ``grid()`` re-queries the grids, for changes to which cards a view
    holds (a member removed from a Root must leave that Root's drill
    view). ``card(asset_id)`` rebuilds one card in place, for a change to
    one entity (a workfile created or saved, a Multi's coverage edited).
    ``detail()`` re-fetches the detail panels showing this catalog.
    ``sidebar()`` rebuilds the sidebars. ``thumbnail(asset_id)`` drops one
    cached thumbnail and re-fetches the detail panels' previews.
    """

    def __init__(self, catalog: "Catalog") -> None:
        self.grid = catalog.request_grid_refresh
        self.card = catalog.request_card_refresh
        self.detail = catalog.request_detail_refresh
        self.sidebar = catalog.request_sidebar_refresh
        self.thumbnail = catalog.request_thumbnail_refresh
        self._todos_hooked = False

    def follow_todos(self, manager) -> None:
        """Re-fetch the detail panels whenever ``manager``'s todos change,
        so the Tasks section follows edits made in the todos dialog.
        Connected once per catalog."""
        if self._todos_hooked:
            return
        manager.items_changed.connect(lambda *_args: self.detail())
        self._todos_hooked = True
