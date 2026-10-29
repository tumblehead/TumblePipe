"""The :class:`PipelineCatalog` — the TumbleTrove catalog of the pipeline.

The class is the host-facing surface and the composition root: it builds
each collaborator with the narrow set of services it needs and routes every
host hook to it. The work lives in the companion modules:

- ``discovery`` — cards, details, the discovery cache, per-bucket counts;
- ``sidebar`` — the collections tree;
- ``entity_ops`` — creating, editing, deleting assets and shots, and their
  per-entity actions;
- ``menus`` / ``deck`` / ``hover`` — card menus, department decks, hovers;
- ``containers`` — Multis and Roots; ``container_menu`` — their menu;
- ``workfiles`` / ``scene`` — workfile open / create, the loaded scene;
- ``detail`` — the detail panel's sections;
- ``recipes`` / ``nodes`` — the two non-entity card kinds, routed by id;
- ``refresh`` — asking the open browsers to repaint.

Recipe (``recipe:``), node (``node:``) and container (``group:`` /
``scene:``) ids are routed before anything entity-shaped sees them.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from tumbletrove.asset_browser.api.catalog import Catalog
from tumbletrove.asset_browser.api.errors import CatalogError
from tumbletrove.asset_browser.api.types import (
    Asset,
    AssetAction,
    AssetDetail,
    AssetPage,
    Collection,
    CreationField,
    DeckItem,
    DetailSection,
    ListColumn,
    SortOption,
)
from .projects import (
    PipelineProjectRegistry,
    ProjectConfig,
)

from tumblepipe.pipe.houdini.ui.report import (
    reporting, run_on_main_thread,
)

from . import containers as containers
from . import nodes as nodes
from . import recipes as recipes
from .clients import ClientPool
from .container_menu import ContainerMenu
from .containers import ContainerManager
from .deck import DeckBuilder
from .dept_scope import DepartmentScope
from .detail import DetailSectionBuilder
from .discovery import EntityIndex, ProjectBuckets
from .drops import DropRouter
from .entity_ops import EntityOps
from .hover import AssetHover
from .houdini import ProjectActivator
from .menus import PipelineMenus, reporting_menu
from .nodes import PipelineNodes
from .recipes import RecipeManager
from .refresh import BrowserRefresh
from .resolver import AssetResolver
from .scene import SceneManager
from .sidebar import Sidebar
from .thumbnails import ThumbnailManager
from .types import DeptVersionStore, projects_json_path
from .workfiles import WorkfileManager


class PipelineCatalog(Catalog):
    """Browse assets and shots from the Tumblehead pipeline."""

    @property
    def id(self) -> str:
        return "pipeline"

    @property
    def name(self) -> str:
        return "TumblePipe"

    @property
    def icon(self) -> str:
        # Ship the catalog's brand icon from this package — the asset
        # browser's icon loader supports absolute paths so external
        # catalogs don't need to bake icons into TumbleTrove itself.
        return str(Path(__file__).parent / "icons" / "tumblepipe.png")

    def get_sidebar_subtitle(self) -> str:
        """Project name shown under the catalog name in the sidebar.

        Only when the tree is flattened (single registered project —
        see :meth:`get_collections`): that's the case where no tree
        row carries the project name, so the header is the one place
        it can live.
        """
        projects = list(self._registry.all()) if self._registry else []
        if len(projects) == 1:
            return projects[0].name
        return ""

    def default_filter_tags(self) -> frozenset[str]:
        """Scope the grid to the launch project on first load.

        When Houdini is launched from a project ``.bat`` (which sets
        ``TH_PROJECT_PATH``), the browser pre-filters to that project
        instead of merging every registered project's assets together.
        Returns an empty set when Houdini wasn't launched from a
        project context.
        """
        launch_name = self._activator.launch_project_name
        if launch_name:
            return frozenset({f"project:{launch_name}"})
        return frozenset()

    def project_for_ref(self, asset_id: str) -> str | None:
        """Project name an asset id belongs to (its first path segment),
        or ``None`` when the id doesn't parse.

        Lets the browser scope the Favourites view to the launch project
        — favourites are stored under this one catalog id across every
        project, so without this a star set in one project would surface
        in another. Pure string split (no client build)."""
        ref = recipes.parse_id(asset_id)
        if ref is not None:
            return ref.project
        if nodes.is_node_id(asset_id):
            # A package node belongs to every project. The browser keeps a
            # favourite only when this equals the scoped (launch) project,
            # so answer with that one; None would hide the star everywhere.
            return self._activator.launch_project_name or None
        parts = self._resolver.split(asset_id)
        return parts[0] if parts else None

    # ── Lifecycle ─────────────────────────────────────────

    def __init__(
        self, registry: PipelineProjectRegistry | None = None,
        *, nodes: PipelineNodes | None = None,
    ) -> None:
        """``registry`` and ``nodes`` replace the defaults (the user's
        ``projects.json``; this package's own HDAs) — for tests."""
        self._registry = registry or PipelineProjectRegistry(projects_json_path())
        if registry is None:
            self._registry.load()
            if not self._registry:
                self._registry.bootstrap_from_env()
                if self._registry:
                    # Persist the env-bootstrapped registry so the next
                    # launch (without TH_PROJECT_PATH set) still sees this
                    # project. A failure here would re-bootstrap on every
                    # launch, so it raises.
                    self._registry.save()

        # Global catalog prefs (autosave-on-scene-change, …); mutators go
        # through prefs.save_prefs so the JSON on disk stays in sync.
        from .prefs import load_prefs
        self._prefs = load_prefs()

        # Per-project Clients. Built by warm_up_worker_thread on the
        # browser's worker: building them on the GUI thread during panel
        # creation races HDA loading and crashes Houdini.
        self._clients = ClientPool(self.id, self._registry)
        # All TH_* env / default_client activation, and the launch project
        # (env at construction) for the one-shot "switched context" warning.
        self._activator = ProjectActivator()
        self._buckets = ProjectBuckets(self.id, self._registry, self._clients)
        self._resolver = AssetResolver(
            self.id, self._registry, self._clients, self._buckets.categories,
        )
        self._dept_scope = DepartmentScope(self._resolver, self._activator)
        self._refresh = BrowserRefresh(self)
        # Per-asset, per-department version overrides: the detail panel's
        # version combos write them; opens and "new from current" read them.
        self._dept_versions = DeptVersionStore()

        self._scene = SceneManager(
            self.id,
            resolver=self._resolver, activator=self._activator,
            refresh=self._refresh, invalidate_cache=self.invalidate_cache,
            prefs=self._prefs, dept_versions=self._dept_versions,
        )
        self._workfiles = WorkfileManager(
            resolver=self._resolver, registry=self._registry,
            clients=self._clients, activator=self._activator,
            scene=self._scene, refresh=self._refresh,
            invalidate_cache=self.invalidate_cache,
        )
        self._index = EntityIndex(
            self.id,
            registry=self._registry, clients=self._clients,
            resolver=self._resolver, activator=self._activator,
            buckets=self._buckets, dept_scope=self._dept_scope,
            workfiles=self._workfiles,
        )
        self._containers = ContainerManager(
            self.id,
            registry=self._registry, clients=self._clients,
            resolver=self._resolver, activator=self._activator,
            refresh=self._refresh, invalidate_cache=self.invalidate_cache,
        )
        self._container_menu = ContainerMenu(self._containers)
        # Recipes: node clusters saved under <project>/recipes/ (see
        # recipes.py). Nodes: every HDA this package ships, package-wide
        # (see nodes.py).
        self._recipes = RecipeManager(
            self.id, registry=self._registry, refresh=self._refresh,
        )
        self._nodes = PipelineNodes(self.id) if nodes is None else nodes
        self._thumbnails = ThumbnailManager(
            entity_dir=self._resolver.entity_dir, refresh=self._refresh,
        )
        self._ops = EntityOps(
            self.id,
            registry=self._registry, clients=self._clients,
            resolver=self._resolver, activator=self._activator,
            buckets=self._buckets, index=self._index,
            dept_scope=self._dept_scope, scene=self._scene,
            recipes=self._recipes, refresh=self._refresh,
            invalidate_cache=self.invalidate_cache,
        )
        self._detail = DetailSectionBuilder(
            self.id,
            registry=self._registry, clients=self._clients,
            resolver=self._resolver, activator=self._activator,
            workfiles=self._workfiles, scene=self._scene,
            containers=self._containers, dept_scope=self._dept_scope,
            dept_versions=self._dept_versions, refresh=self._refresh,
            edit_departments=self._ops.edit_departments,
            export_path=self._index.export_path,
        )
        self._menus = PipelineMenus(
            resolver=self._resolver, index=self._index, ops=self._ops,
            workfiles=self._workfiles, scene=self._scene,
            containers=self._containers, thumbnails=self._thumbnails,
            refresh=self._refresh,
            edit_group_departments=self._edit_group_departments,
        )
        self._deck = DeckBuilder(
            containers=self._containers, scene=self._scene,
            workfiles=self._workfiles, dept_scope=self._dept_scope,
        )
        self._hover = AssetHover(
            dept_scope=self._dept_scope, workfiles=self._workfiles,
        )
        self._sidebar = Sidebar(
            registry=self._registry, buckets=self._buckets, index=self._index,
            containers=self._containers, recipes=self._recipes,
            nodes=self._nodes,
        )
        # Drops onto Houdini network panes.
        self._drops = DropRouter(
            activate_project=self._activator.activate,
            project_for_asset_id=self._resolver.project_for,
            project_for_path=self._resolver.project_for_path,
            thumbnail_path=self._thumbnails.thumbnail_path,
            get_detail=self.get_detail,
            get_registry=self._registry,
        )

    # ── Warm-up hooks ─────────────────────────────────────

    def warm_up_main_thread(self) -> None:
        """Pre-import ``tumblepipe.api`` on the main thread.

        Building Clients on a worker thread while another main-thread
        import is running races on Python's import lock and can
        deadlock Houdini. Importing the package once on the GUI thread
        before any worker touches it sidesteps the issue.
        """
        import tumblepipe.api  # noqa: F401

    def warm_up_worker_thread(self) -> None:
        """Build per-project Clients on a worker thread.

        Each registered project's Client is constructed eagerly here
        so the first browse doesn't block on SMB ``Path.exists()``
        timeouts. Init failures are recorded on the slot and surfaced
        via :class:`AssetPage.errors` on the next browse.
        """
        self._clients.ensure_all()
        # Recipes scan the project share too; doing it here means the
        # sidebar's Recipes section has its counts on first paint rather
        # than after the first grid query.
        self._recipes.scan_all()

    # ── Tags ──────────────────────────────────────────────

    def get_available_tags(self) -> dict[str, list[str]]:
        # Don't call self._clients.get here — get_available_tags runs
        # on the GUI thread and Client construction can block on SMB
        # Path.exists() timeouts. _list_categories / _list_sequences
        # both gate on self._clients.is_ready, so they return [] for
        # projects whose Clients haven't been warmed up yet.
        cats = self._buckets.all_categories()
        seqs = self._buckets.all_sequences()
        node_contexts = {f.context for f in self._nodes.families()}
        return {
            "source": ["pipeline"],
            "type": ["asset", "shot", "recipe", "node"],
            "category": list(cats),
            "sequence": seqs,
            "context": sorted(set(self._recipes.contexts()) | node_contexts),
            "project": [p.name for p in self._registry.all()],
            **self._nodes.available_tags(),
        }

    # ── Collections ───────────────────────────────────────

    def get_collections(self) -> list[Collection]:
        return self._sidebar.collections()

    # ── Assets ────────────────────────────────────────────

    def get_assets(
        self, query: str = "", tags: frozenset[str] = frozenset(),
        cursor: str | None = None, page_size: int = 50,
    ) -> AssetPage:
        # Remembered for get_asset's single-card refresh — see
        # EntityIndex.apply_category_prefix.
        self._index.last_query_tags = tags

        # The Nodes section lists package HDAs, not project entities: no
        # discovery pass, and no project filter (see PipelineNodes).
        if nodes.TYPE_TAG in tags:
            return self._nodes.get_assets(query, tags, cursor, page_size)

        # Container types (Groups / Scenes) take over the grid: the
        # cards are synthesized from sidebar Collection data rather than
        # the real asset/shot index. Drill-down (card click) clears the
        # ``type:group`` / ``type:scene`` filter and replaces it with
        # the container's own ``group:`` / ``scene:`` tag, which falls
        # through to the normal member-filter branch below.
        if "type:group" in tags:
            return self._containers.page(
                "group", query, tags, cursor, page_size,
            )
        if "type:scene" in tags:
            return self._containers.page(
                "scene", query, tags, cursor, page_size,
            )

        # A fresh discovery pass; what fails is attached to the returned
        # page so the browser can show it. Block until every registered
        # project's init has finished (success or fail) first.
        errors: list[CatalogError] = list(self._clients.ensure_all())
        # Recipes ride along so the project root, the launch-project scope
        # and the All view list them beside the entities; the
        # ``type:recipe`` / ``context:`` atoms narrow to them like any
        # other row. What their scan could not read is shown beside them.
        all_items = self._index.items(errors) + self._recipes.cards()
        errors.extend(self._recipes.errors)

        # Handle group/scene tags (special filtering). Filter failures
        # are surfaced via AssetPage.errors rather than silently
        # widening the result set, which would defeat the filter.
        remaining_tags = set()
        for t in tags:
            try:
                if t.startswith("group:"):
                    all_items = self._containers.filter_by_group(all_items, t)
                elif t.startswith("scene:"):
                    all_items = self._containers.filter_by_scene(all_items, t)
                else:
                    remaining_tags.add(t)
            except CatalogError as err:
                errors.append(err)
                all_items = []  # filter failed — empty result, not all items
                break

        # Filter by standard tags
        from tumbletrove.asset_browser.api.tags import match_tags
        if remaining_tags:
            filter_tags = frozenset(t for t in remaining_tags if not t.startswith("source:"))
            if filter_tags:
                all_items = [a for a in all_items if match_tags(a.tags, filter_tags)]

        # Contextual path prefix ("Clash/SET") when categories are
        # mixed in the view. Applied before the search filter so the
        # query matches what's on screen (searching "clash" finds the
        # Clash assets from the Assets root).
        all_items = self._index.apply_category_prefix(all_items, tags)

        # A recipe card's disabled state is "saved in another context
        # than the network editor shows now"; the cards are cached, the
        # editor moves, and the browser re-queries on a context switch
        # precisely so this can be re-read.
        all_items = self._recipes.restamp_disabled(all_items)

        # Search
        if query:
            q = query.lower()
            all_items = [a for a in all_items if q in a.name.lower()]

        # Pin the active scene asset to the first position and flag it
        # so the grid renders a highlight border. ``is_current_scene``
        # is now a typed frozen field, so rebuild via dataclasses.replace
        # instead of mutating in place — this also stops the flag from
        # leaking back into cached Asset objects (the old in-place dict
        # mutation shared the cache's metadata, which is why it had to
        # clear stale flags).
        scene_id = self._scene.get_scene_asset_id()
        if scene_id:
            pinned = [
                dataclasses.replace(a, is_current_scene=True)
                for a in all_items if a.id == scene_id
            ]
            rest = [a for a in all_items if a.id != scene_id]
            all_items = pinned + rest

        # Paginate
        page = int(cursor) if cursor else 0
        start = page * page_size
        end = start + page_size
        page_items = all_items[start:end]
        next_cursor = str(page + 1) if end < len(all_items) else None

        return AssetPage(
            assets=page_items,
            cursor=next_cursor,
            total=len(all_items),
            errors=_distinct(errors),
        )

    def get_asset(self, asset_id: str):
        """A fresh card for one id (a save, a create, an edit), or None."""
        if containers.parse(asset_id) is not None:
            return self._containers.card(asset_id)
        if recipes.is_recipe_id(asset_id):
            return self._recipes.get_asset(asset_id)
        if nodes.is_node_id(asset_id):
            return self._nodes.get_asset(asset_id)
        return self._index.card(asset_id)

    # ── Detail ────────────────────────────────────────────

    def get_detail(self, asset_id: str, version: str | None = None) -> AssetDetail:
        if containers.parse(asset_id) is not None:
            return self._containers.detail(asset_id)
        if recipes.is_recipe_id(asset_id):
            return self._recipes.get_detail(asset_id)
        if nodes.is_node_id(asset_id):
            return self._nodes.get_detail(asset_id, version)
        return self._index.detail(asset_id, self._ops.read_description(asset_id))

    def get_detail_sections(
        self, detail: AssetDetail,
    ) -> list[DetailSection] | None:
        # Container details (Groups / Scenes) get a slim section list
        # — no scene-actions row, no todos. Groups also get a
        # Departments tab where the user can toggle which depts the
        # group covers; scenes don't (no editable depts field).
        kind = detail.kind
        if kind in ("recipe", nodes.KIND):
            # The framework's own tags / description sections say it all
            # — a recipe or node has no departments, todos or entity URI.
            return None
        if kind == "group":
            return [
                DetailSection(
                    key="info",
                    title="Info",
                    icon="info",
                    widget_factory=self._detail.build_container_info_section,
                ),
                # Multis share the rich asset/shot Departments section
                # so version dropdowns + meta rows render uniformly.
                # The per-row coverage toggle is injected when
                # ``is_multi`` is detected.
                DetailSection(
                    key="departments",
                    title="Departments",
                    icon="layers-3",
                    widget_factory=self._detail.build_combined_departments_section,
                ),
            ]
        if kind == "scene":
            return [
                DetailSection(
                    key="info",
                    title="Info",
                    icon="info",
                    widget_factory=self._detail.build_container_info_section,
                ),
            ]

        # Entity sections. This list is rebuilt on every card click now
        # that selection drives the pane, so it does no filesystem work:
        # the loaded-scene title it used to compute here (a hip read plus
        # a project lookup per click) was stashed on the instance and
        # never read by anything.
        sections: list[DetailSection] = [
            DetailSection(
                key="info",
                title="Info",
                icon="info",
                widget_factory=self._detail.build_combined_info_section,
            ),
            DetailSection(
                key="departments",
                title="Departments",
                icon="layers-3",
                widget_factory=self._detail.build_combined_departments_section,
            ),
            DetailSection(
                key="todos",
                title="Tasks",
                icon="list-todo",
                widget_factory=self._detail.build_todos_section,
            ),
        ]
        return sections

    def get_thumbnail(self, asset: Asset):
        if recipes.is_recipe_id(asset.id):
            return self._recipes.get_thumbnail(asset)
        if nodes.is_node_id(asset.id):
            return self._nodes.get_thumbnail(asset)
        return self._thumbnails.get_thumbnail(asset)

    def get_asset_hover_widget(self, asset):
        if recipes.is_recipe_id(asset.id) or nodes.is_node_id(asset.id):
            # The framework's generic hover: a recipe's or node's facts are
            # the card's own tags and metadata.
            return None
        return self._hover.widget(asset)

    # ── Menus ─────────────────────────────────────────────
    # Every entry is its own failure boundary (menus.reporting_menu).

    def get_card_menu_items(self, asset: Asset, *, selected_assets=None):
        """Card right-click items. ``selected_assets`` is the browser's
        multi-selection when every selected card is ours: Submit Jobs…
        then covers the selection's cards of the clicked card's context."""
        if recipes.is_recipe_id(asset.id):
            items = self._recipes.card_menu_items(asset)
        elif nodes.is_node_id(asset.id):
            items = self._nodes.card_menu_items(asset)
        else:
            items = self._menus.card_items(asset, selected_assets=selected_assets)
        return reporting_menu(items)

    def get_deck_item_menu_items(self, asset: Asset, deck_item_key: str):
        return reporting_menu(self._menus.deck_item_items(asset, deck_item_key))

    def _edit_group_departments(self, asset_id: str) -> None:
        """Choose which departments a Multi covers, from its card menu.

        Hosts the same section the detail panel's Departments tab shows
        for a Multi (a coverage toggle per row, written through on click),
        in a dialog: the card menu reaches it without selecting the Multi.
        """
        import hou
        from tumbletrove.common.gui import is_main_thread
        if not is_main_thread():
            # Qt objects are thread-affine and a modal built off the GUI
            # thread corrupts memory rather than raising — see *Qt and
            # threads* in docs/development.md.
            run_on_main_thread(
                reporting(
                    f"Editing the departments for {asset_id}",
                    lambda: self._edit_group_departments(asset_id),
                ),
            )
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
        from tumbletrove.asset_browser.api.catalog import DetailContext
        from tumbletrove.asset_browser.core.theme import scaled

        detail = self.get_detail(asset_id)
        dlg = QDialog(hou.qt.mainWindow())
        dlg.setWindowTitle(f"Departments — {detail.name}")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(scaled(6))
        layout.addWidget(self._detail.build_combined_departments_section(
            DetailContext(detail=detail, parent=dlg),
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.resize(scaled(420), scaled(360))
        dlg.exec()
        # Each toggle refreshes the card and the detail panel as it writes.

    # ── Deck items (departments) ──────────────────────────

    def get_deck_items(self, asset: Asset) -> list[DeckItem]:
        if recipes.is_recipe_id(asset.id) or nodes.is_node_id(asset.id):
            return []
        return self._deck.items(asset)

    # ── Settings ──────────────────────────────────────────
    # A project list with Add / Remove is richer than declarations can
    # express, so this page carries its own widget and owns its own
    # persistence — the framework supplies the page and its slot.

    def settings_page(self):
        """Our page in TumbleTrove's shared settings dialog: the
        multi-project management widget."""
        from tumbletrove.settings import page

        def _widget(parent):
            # An ordinary import now. This was a spec_from_file_location
            # dance because discovery left these modules outside any package,
            # so a sibling could not simply be imported.
            from .settings_widget import PipelineSettingsWidget
            return PipelineSettingsWidget(self, parent=parent)

        return page(title="Projects", widget_factory=_widget)

    def _apply_project_changes(
        self, new_projects: list[ProjectConfig],
    ) -> None:
        """Replace the registry contents with ``new_projects`` and
        re-init clients for any added entries.

        Removed projects' clients are dropped (their cached data is
        invalidated). The discovery cache is always invalidated.
        """
        existing_names = set(self._registry.names)
        new_names = {p.name for p in new_projects}

        # Remove projects that disappeared.
        for stale in existing_names - new_names:
            self._registry.remove(stale, save=False)
            self._clients.remove(stale)

        # Add / replace each entry.
        for proj in new_projects:
            existing = self._registry.get(proj.name)
            self._registry.add(proj, save=False)
            # If the entry's paths changed, force re-init on next browse.
            if existing is None or (
                existing.project_path != proj.project_path
                or existing.config_path != proj.config_path
            ):
                self._clients.remove(proj.name)

        self._registry.save()

        # Drop the discovery cache so the next browse re-aggregates.
        self._index.invalidate()

        # Build any newly-added (or path-changed) Clients eagerly on
        # the calling thread (Apply runs from the GUI-thread settings
        # widget). This is the only safe place to construct Clients.
        # Failures are recorded on the slot and surfaced on next browse
        # — don't fail the Apply over an unreachable share.
        for proj in self._registry.all():
            if not self._clients.is_ready(proj.name):
                self._clients.try_get(proj.name)

    # ── Drop handling (delegated to DropRouter) ──────────

    def get_ghost_data(self, asset):
        from tumbletrove.asset_browser.core.ghost_overlay import GhostData, GhostNode
        if recipes.is_recipe_id(asset.id):
            return self._recipes.get_ghost_data(asset.id)
        if nodes.is_node_id(asset.id):
            return self._nodes.get_ghost_data(asset.id)
        if "type:shot" in asset.tags:
            return GhostData(nodes=[GhostNode("th::import_shot::1.0", 0.0, 0.0)])
        return GhostData(nodes=[GhostNode("th::import_asset::1.0", 0.0, 0.0)])

    def on_drop(self, detail, drop) -> bool:
        if recipes.is_recipe_id(detail.id):
            return self._recipes.on_drop(detail, drop)
        if nodes.is_node_id(detail.id):
            return self._nodes.on_drop(detail, drop)
        return self._drops.on_drop(detail, drop)

    def on_deck_drop(self, asset, deck_keys, drop) -> bool:
        return self._drops.on_deck_drop(asset, deck_keys, drop)

    def on_multi_drop(self, assets, drop) -> bool:
        # A selection holding a recipe falls back to one on_drop per
        # card: the router bundles assets into an import_assets node
        # and would silently leave the recipe behind.
        if any(recipes.is_recipe_id(a.id) for a in assets):
            return False
        # Node cards create one node each. Mixed with entities the nodes
        # decline, and each card falls back to its own on_drop, for the
        # same reason as recipes.
        if any(nodes.is_node_id(a.id) for a in assets):
            return self._nodes.on_multi_drop(assets, drop)
        return self._drops.on_multi_drop(assets, drop)

    # ── Session panel ──────────────────────────────────────

    def grid_follows_hip_file(self) -> bool:
        """The open workfile's card is pinned and marked current, so the
        grid follows a hip change."""
        return True

    def session_panel_mode(self) -> str:
        """The right pane follows the grid selection, like every other
        catalog's.

        It used to be ``"replace"``: a session pane pinned to the open
        ``.hip``, on the reasoning that "what am I in?" is the question
        this catalog's users ask of that pane. Artists asked for the
        opposite, and the report is the stronger evidence — browsing is
        comparing, and a pane that answers about lilGuy while you click
        through five other assets answers nobody's question. Clicking a
        card now retargets the pane.

        Nothing about the open scene is lost by this: the toolbar label
        names the open workfile (``CHAR_lilGuy_model_v0044.hipnc``
        carries entity, department and version), the grid pins its card,
        and the selected asset's Departments section accents the
        department that scene is in.
        """
        return "off"

    # ── Quick Actions (top bar) ────────────────────────────

    def get_quick_actions(self):
        from tumbletrove.asset_browser.api.catalog import QuickAction
        return [
            QuickAction(id="save", label="Save", icon="save-all", tooltip="Save current scene"),
            QuickAction(id="publish", label="Publish", icon="send", tooltip="Publish exports"),
            QuickAction(
                id="render", label="Render", icon="clapperboard",
                tooltip="Submit render jobs for the current scene's entity",
            ),
            QuickAction(
                id="entity_editor", label="Entity Editor", icon="list",
                tooltip="The project's assets and shots as spreadsheets: add, "
                        "edit and retire them",
            ),
            QuickAction(
                id="farm", label="Farm Submit", icon="layout-grid",
                tooltip="Publish, playblast and render any shots on the farm",
            ),
            QuickAction(
                id="farm_manager", label="Farm Manager", icon="server",
                tooltip="Follow farm and local render jobs: progress, logs and errors",
            ),
            QuickAction(
                id="update", label="Update", icon="download",
                tooltip="Re-import latest published versions into the "
                        "current scene (no scene reload)",
            ),
            QuickAction(id="reload", label="Reload", icon="file-input", tooltip="Reload current scene"),
        ]

    def get_quick_actions_label(self) -> str:
        import hou
        if not hou.hipFile.isNewFile():
            return Path(hou.hipFile.path()).name
        return ""

    def execute_quick_action(self, action_id: str, done_cb=None) -> None:
        if action_id == "save":
            self._scene.save_current_scene(done_cb)
        elif action_id == "publish":
            self._scene.publish_current_scene(done_cb)
        elif action_id == "render":
            self._scene.render_current_scene(done_cb)
        elif action_id == "entity_editor":
            self._open_scene_entity_editor(done_cb)
        elif action_id == "farm":
            self._scene.open_farm(done_cb)
        elif action_id == "farm_manager":
            self._scene.open_farm_manager(done_cb)
        elif action_id == "update":
            self._scene.update_scene_imports(done_cb)
        elif action_id == "reload":
            self._scene.reload_current_scene(done_cb)

    def _open_scene_entity_editor(self, done_cb=None) -> None:
        """The Entity Editor quick action. Magnus asked for it beside Render:
        the editor was only reachable from the "+" card and card menus.

        Opens the loaded workfile's project on its entity's tab, group and
        row; an untitled or off-pipeline scene opens on Shots and asks for
        the project when several are registered.
        """
        def _open():
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                project_name, kind, group, name = "", "shots", "", ""
                hip_path = Path(hou.hipFile.path())
                proj = (
                    None if hou.hipFile.isNewFile()
                    else self._resolver.project_for_path(hip_path)
                )
                if proj is not None:
                    project_name = proj.name
                    context = get_workfile_context(hip_path)
                    segments = (
                        context.entity_uri.segments if context is not None else []
                    )
                    if len(segments) == 3 and segments[0] in ("assets", "shots"):
                        kind, group, name = segments
                self._ops.open_entity_editor(project_name, kind, group, name)
            finally:
                if callable(done_cb):
                    done_cb()

        run_on_main_thread(reporting("Opening the Entity Editor", _open))

    def get_quick_action_menu_items(self, action_id: str):
        """Right-click items for a quick-action toolbar button.

        Only ``save`` carries one: an *inline* emergency save. The normal
        left-click Save defers to the main-thread event loop, which is frozen
        while Houdini sits in its crash-report dialog — so the queued save
        never lands. The menu entry runs the save directly from the context-
        menu handler so it still completes during a crash. See
        :meth:`SceneManager.emergency_save_current_scene`.
        """
        if action_id == "save":
            return reporting_menu(
                [("Emergency Save (off-thread)", self._emergency_save)],
            )
        return []

    def _emergency_save(self) -> None:
        # Intentionally NOT wrapped in run_catalog_op / gui_dispatch: deferring
        # would re-queue onto the same dead event loop the normal Save uses,
        # defeating the point. We run inline (the menu handler's nested loop
        # pumps during the crash dialog), accepting the brief GUI block.
        self._scene.emergency_save_current_scene()

    def get_quick_action_hover_html(self, action_id: str) -> str | None:
        """Return rich-text HTML for the hover popup above a quick action.

        - ``save``: last mtime of the currently-loaded hip file.
        - ``publish``: mtime of the latest published version for the
          current scene's (entity, dept, channel) tuple.

        Returns ``None`` for actions we don't track (e.g. ``reload``) so
        the button falls back to its regular QToolTip.
        """
        from tumbletrove.asset_browser.core.hover_html import format_age_html

        if action_id == "save":
            import hou
            if hou.hipFile.isNewFile():
                return format_age_html("Last saved", None)
            return format_age_html(
                "Last saved", Path(hou.hipFile.path()).stat().st_mtime,
            )

        if action_id == "publish":
            scene_ctx = self._scene.get_loaded_scene_context()
            if scene_ctx is None:
                return None
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(
                scene_ctx.entity_uri, "default", scene_ctx.department_name,
            )
            if path is None:
                return format_age_html("Last published", None)
            return format_age_html("Last published", path.stat().st_mtime)

        return None

    # ── Entity creation / edit / delete ───────────────────

    def get_creation_options(self, tags=frozenset()):
        return self._ops.creation_options(tags)

    def node_drop_creation_option(self, tags=frozenset()):
        return self._ops.node_drop_creation_option(tags)

    def get_creation_fields(self, option_id, tags=frozenset()):
        return self._ops.creation_fields(option_id, tags)

    def create_entity(self, option_id, fields):
        """The new card's id, or None. A failure raises into the browser,
        which reports it."""
        return self._ops.create(option_id, fields)

    # A Multi / Root card's detail panel offers Edit… and Delete like an
    # entity's, and the host calls these with the card's id — a container
    # id, which no entity resolver can split. Route it to the container.

    def get_edit_fields(self, asset_id: str) -> list[CreationField]:
        if containers.parse(asset_id) is not None:
            return self._containers.edit_fields(asset_id)
        if recipes.is_recipe_id(asset_id):
            return self._recipes.get_edit_fields(asset_id)
        if nodes.is_node_id(asset_id):
            return []
        return self._ops.edit_fields(asset_id)

    def edit_entity(self, asset_id, fields):
        if containers.parse(asset_id) is not None:
            return self._containers.edit(asset_id, fields)
        if recipes.is_recipe_id(asset_id):
            return self._recipes.edit(asset_id, fields)
        if nodes.is_node_id(asset_id):
            return False
        return self._ops.edit(asset_id, fields)

    def can_delete_entity(self, asset: Asset) -> bool:
        """Entities and recipes; not a node card (nothing to delete) nor
        a container card, whose menu has its own Delete."""
        return not (
            nodes.is_node_id(asset.id)
            or containers.parse(asset.id) is not None
        )

    def delete_entity(self, asset_id):
        if containers.parse(asset_id) is not None:
            return self._containers.delete(asset_id)
        if recipes.is_recipe_id(asset_id):
            return self._recipes.delete(asset_id)
        if nodes.is_node_id(asset_id):
            return False
        return self._ops.delete(asset_id)

    def bucket_child_count(
        self, project_name: str, kind: str, bucket_name: str,
    ) -> int:
        return self._ops.bucket_child_count(project_name, kind, bucket_name)

    def delete_bucket(
        self, project_name: str, kind: str, bucket_name: str,
    ) -> tuple[bool, str]:
        return self._ops.delete_bucket(project_name, kind, bucket_name)

    # ── Group / Scene lifecycle (delegated to ContainerManager) ──

    def container_kind_label(self, kind: str) -> str:
        return self._container_menu.kind_label(kind)

    def get_container_actions(self, collection_id: str, kind: str):
        return self._container_menu.actions(collection_id, kind)

    def execute_container_action(
        self, action_id: str, collection_id: str, parent,
    ) -> str:
        return self._container_menu.execute(action_id, collection_id, parent)

    def collection_from_assets_option(self):
        return self._ops.collection_from_assets_option()

    def delete_collection(self, collection_id: str) -> bool:
        return self._containers.delete(collection_id)

    def get_collection_edit_fields(self, collection_id: str) -> list[CreationField]:
        return self._containers.edit_fields(collection_id)

    def edit_collection(self, collection_id: str, fields: dict) -> bool:
        return self._containers.edit(collection_id, fields)

    def add_assets_to_collection(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        return self._containers.add_assets(collection_id, asset_ids)

    def remove_assets_from_collection(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        return self._containers.remove_assets(collection_id, asset_ids)

    def get_asset_membership(
        self, asset_id: str,
    ) -> list[tuple[str, str, str]]:
        if recipes.is_recipe_id(asset_id) or nodes.is_node_id(asset_id):
            return []
        return self._containers.get_asset_membership(asset_id)

    # ── Per-Asset Actions ────────────────────────────────

    def get_actions(self, detail: AssetDetail) -> list[AssetAction]:
        actions = []

        if recipes.is_recipe_id(detail.id):
            return self._recipes.get_actions(detail)
        if nodes.is_node_id(detail.id):
            return self._nodes.get_actions(detail)

        # A Multi / Root has no export folder and no entity row in the
        # database editor, so those two actions could only fail on it —
        # one silently, one with a "cannot resolve" dialog. Offer what
        # works: Edit (coverage / asset list) and Delete, both routed by
        # container id.
        if containers.parse(detail.id) is not None:
            return [
                AssetAction(id="edit_entity", label="Edit…", icon="settings"),
                AssetAction(
                    id="delete_entity", label="Delete", icon="x",
                    destructive=True,
                ),
            ]

        is_asset = "type:asset" in detail.tags

        if is_asset:
            actions.append(AssetAction(
                id="import_asset",
                label="Import to Scene",
                icon="download",
            ))

        actions.append(AssetAction(
            id="open_export",
            label="Open Export Folder",
            icon="folder-open",
        ))

        actions.append(AssetAction(
            id="open_db_editor",
            label="Open in Database Editor…",
            icon="database",
        ))

        actions.append(AssetAction(
            id="edit_entity",
            label="Edit…",
            icon="settings",
        ))
        actions.append(AssetAction(
            id="delete_entity",
            label="Delete",
            icon="x",
            destructive=True,
        ))

        return actions

    def execute_action(
        self, action_id, detail, file=None, progress=None,
    ) -> None:
        """Run a detail-panel or deck-item action, on the browser's worker.
        A failure raises into the browser, which reports it."""
        if detail and recipes.is_recipe_id(detail.id):
            self._recipes.execute_action(action_id, detail)
            return
        if detail and nodes.is_node_id(detail.id):
            self._nodes.execute_action(action_id, detail)
            return

        if action_id == "open_export":
            path = self._index.export_path(detail.id if detail else "")
            if path is None:
                return
            if not path.exists():
                import hou
                hou.ui.setStatusMessage(
                    f"{detail.name} has no exports yet.",
                    severity=hou.severityType.Warning,
                )
                return
            from tumblepipe.util import desktop
            desktop.open_in_file_browser(path)

        elif action_id == "open_db_editor" and detail:
            self._ops.open_database_editor(detail.id)

        elif action_id == "import_asset" and detail:
            self._ops.import_asset_to_scene(detail)

        elif action_id.startswith("open_workfile:"):
            dept = action_id.split(":", 1)[1]
            target_id = detail.id if detail else ""
            if target_id.startswith("group:"):
                self._workfiles.open_group_workfile(target_id, dept)
            else:
                self._workfiles.open_workfile(target_id, dept)

        elif action_id.startswith("new_from_template:"):
            # Double-click on a dept card that has no workfile yet:
            # the same thing its right-click "New from Template" does.
            dept = action_id.split(":", 1)[1]
            target_id = detail.id if detail else ""
            # new_from_template routes a Multi id to its group create.
            self._workfiles.new_from_template(
                target_id, dept, self._refresh.detail,
            )

    # ── Sort & List columns ───────────────────────────────

    def get_sort_options(self) -> list[SortOption]:
        return [
            SortOption(key="name_asc", label="Name A-Z"),
            SortOption(key="name_desc", label="Name Z-A", reverse=True),
            SortOption(
                key="updated_desc",
                label="Latest Update",
                metadata_key="latest_update",
                reverse=True,
            ),
            SortOption(
                key="updated_asc",
                label="Oldest Update",
                metadata_key="latest_update",
            ),
        ]

    def get_list_columns(self) -> list[ListColumn]:
        # ``last_user`` + ``latest_update`` + ``last_note`` are filled
        # per-card by ``_latest_update_user_note``. ``fmt="reltime"`` renders
        # the epoch mtime as a compact "2h ago".
        # ``deck_note``: the version note the artist typed at save time.
        # Sits next to Version because that is what it annotates — reading
        # "v0042 | fixed elbow topology" across is the whole point.
        # Top-level rows show the newest dept's note, mirroring how
        # User/Edited summarise that same version. Widest fixed column
        # here: notes are sentences, and anything narrower elides them to
        # uselessness.
        return [
            ListColumn(key="name", label="Name"),
            # ``deck_detail``: expanded dept deck rows put their detail
            # text (workfile version, or "ⓜ" for Multi-covered depts)
            # here instead of trailing the dept name. Top-level rows
            # leave the cell empty — an entity has no single version.
            ListColumn(key="version", label="Version", width=90, deck_detail=True),
            ListColumn(key="last_note", label="Note", width=220, deck_note=True),
            ListColumn(key="category", label="Category", width=100),
            ListColumn(key="dept_count", label="Depts", width=55, align="center"),
            # ``deck_user``/``deck_edited``: expanded dept deck rows
            # land their per-workfile author + save time in the same
            # columns the entity-level values use.
            ListColumn(key="last_user", label="User", width=110,
                       deck_user=True),
            ListColumn(
                key="latest_update", label="Edited", width=90,
                fmt="reltime", align="right", deck_edited=True,
            ),
        ]

    # ── Cache invalidation ────────────────────────────────

    def invalidate_cache(self) -> None:
        """Drop discovered asset/shot caches; next query re-fetches.

        These are the *catalog's own* discovery caches (cards, recipes,
        nodes, Multi membership). Config reads need no refresh here:
        ``JsonConfigStore`` keeps ``db/*.json`` coherent and reloads any
        externally-written file on the next read. A project still on the
        pre-v1 frozen convention is unmigrated — run
        ``scripts/migrate_config.py`` on it.
        """
        self._index.invalidate()
        self._recipes.invalidate()
        self._nodes.invalidate()
        self._containers.invalidate_membership_cache()

    def _reset_project_clients(self) -> None:
        """Drop per-project API client state (Shift+Click full reset).

        Wipes both READY clients and any FAILED slots, so subsequent
        browses retry from scratch. Also clears the activation guard
        so the next ``_activate_project`` rebuilds the tumblehead
        Client even when called with the previously-active project.
        """
        self._clients.clear()
        self._activator.reset()
        self.invalidate_cache()


def _distinct(errors: list[CatalogError]) -> tuple[CatalogError, ...]:
    """``errors`` with repeats dropped, in first-seen order. A project whose
    Client cannot be built is attempted by ``ensure_all`` and again by the
    discovery pass, and both report it; the browser counted the one failure
    as two ("⚠ 2 catalog errors") and listed it twice."""
    seen: set[str] = set()
    out: list[CatalogError] = []
    for err in errors:
        key = str(err)
        if key not in seen:
            seen.add(key)
            out.append(err)
    return tuple(out)
