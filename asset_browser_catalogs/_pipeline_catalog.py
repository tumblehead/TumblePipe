"""Pipeline catalog implementation — the :class:`PipelineCatalog` class
and the per-project plumbing it composes.

Lives separately from ``pipeline.py`` so the top-level file the
TumbleTrove catalog registry discovers stays a minimal factory: a
docstring plus :func:`pipeline.create_catalog`. Loading pipeline.py
in turn imports this module, which carries all the catalog state and
behaviour.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from tumbletrove.asset_browser.api.catalog import Catalog
from tumbletrove.asset_browser.api.errors import (
    AssetDiscoveryError,
    CatalogError,
    CatalogInitError,
    ConfigError,
    WorkfileScanError,
)
from tumbletrove.asset_browser.api.types import (
    Asset,
    AssetAction,
    AssetDetail,
    AssetPage,
    Collection,
    CreationField,
    DetailSection,
    ListColumn,
    SortOption,
    DeckItem,
)
from tumbletrove.asset_browser.core.projects import ProjectConfig, PipelineProjectRegistry

# Companion modules. Absolute imports (rather than ``from
# ._pipeline_X``) because tumbletrove's catalog discovery loads
# pipeline.py without a parent package; pipeline.py prepends this
# directory to ``sys.path`` before importing us. _pipeline_widgets is
# imported lazily inside ``_build_departments_section`` because it
# pulls in PySide6; the catalog itself loads (and the Pipeline factory
# can introspect projects) without a Qt environment.
from _pipeline_clients import ClientPool
import _pipeline_containers as containers
from _pipeline_containers import ContainerManager, GroupContainer
from _pipeline_drops import DropRouter
from _pipeline_houdini import ProjectActivator, run_on_main_thread
from _pipeline_detail import DetailSectionBuilder
from _pipeline_resolver import AssetResolver
from _pipeline_scene import SceneManager
from _pipeline_thumbnails import ThumbnailManager
import _pipeline_uris as uris
from _pipeline_workfiles import WorkfileManager
from _pipeline_types import (
    DEPT_API_SHORT_FALLBACK,
    DEPT_ICONS,
    DEPT_SHORT_NAMES,
    SHOT_DEPT_ICONS,
    DeptVersionStore,
    cascade_counts,
    latest_version,
    projects_json_path,
    workfile_versions,
)

log = logging.getLogger(__name__)

# Group accent — matches ``TYPE_COLORS["group"]`` in asset_browser's
# theme.py. Reused as the sub-card tint when a shot/asset dept is
# superseded by a group workfile.
_GROUP_ACCENT_COLOR = "#e08c4a"


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

    def default_filter_tags(self) -> frozenset[str]:
        """Auto-activate the launch project's pill on first load.

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

    # ── Lifecycle ─────────────────────────────────────────

    def __init__(self, registry: PipelineProjectRegistry | None = None) -> None:
        self._registry = registry or PipelineProjectRegistry(projects_json_path())
        if registry is None:
            self._registry.load()
            if not self._registry:
                self._registry.bootstrap_from_env()
                if self._registry:
                    # Persist the env-bootstrapped registry so the
                    # next launch (without TH_PROJECT_PATH set) still
                    # sees this project. A failure here means we'll
                    # silently re-bootstrap on every launch — surface
                    # it instead of swallowing.
                    self._registry.save()

        # Global pipeline catalog prefs (autosave-on-scene-change, …).
        # Loaded once on init; mutators go through set_prefs() so on-disk
        # JSON stays in sync. See _pipeline_prefs.py.
        # Absolute import (rather than ``from ._pipeline_prefs``)
        # because tumbletrove's external-catalog discovery loads
        # pipeline.py without a parent package — see the sys.path
        # tweak at the top of this file.
        from _pipeline_prefs import load_prefs  # noqa: E402
        self._prefs = load_prefs()

        # Per-project Client lifecycle. Construction is deferred until
        # :meth:`warm_up_worker_thread` runs on the asset-browser worker
        # — building Clients on the GUI thread during pypanel creation
        # races against HDA loading and crashes Houdini.
        self._clients = ClientPool(self.id, self._registry)
        # Asset-id parser / project + client + URI resolver. Owns the
        # split / parse_ref / uri_for / resolve methods that used to be
        # half a dozen catalog helpers each returning None on failure.
        self._resolver = AssetResolver(
            self.id, self._registry, self._clients,
            self._list_categories_for_project,
        )
        # Thumbnail sidecar management — path resolution, file-dialog
        # picker, Scene-Viewer capture, and the grid-refresh hook that
        # invalidates thumbnail caches after a write.
        self._thumbnails = ThumbnailManager(
            split_asset_id=self._resolver.split,
            project_root_for=self._resolver.root_for,
            categories_for_project=self._list_categories_for_project,
            request_global_detail_refresh=self._request_global_detail_refresh,
        )
        # Drop handler — routes asset / shot / Root drops onto Houdini
        # network panes. Stateless; takes its dependencies via the
        # constructor.
        self._drops = DropRouter(
            activate_project=self._activate_project,
            project_for_asset_id=self._resolver.project_for,
            thumbnail_path=self._thumbnails.thumbnail_path,
            get_detail=self.get_detail,
            get_registry=self._registry,
        )
        # Workfile lifecycle — opens, "new from template / current"
        # creates, and the mtime / user attribution helpers used by
        # the detail panel. Holds a back-reference to this catalog
        # because every method calls into several catalog services.
        self._workfiles = WorkfileManager(self)
        # Detail-panel section builders. Owns the Qt widget code
        # that constructs every section in the right-hand detail
        # panel. Same backref pattern as WorkfileManager.
        self._detail = DetailSectionBuilder(self)
        # Scene-state lifecycle: save / publish / reload, autosave-
        # on-swap, and the readonly helpers that derive context from
        # the currently-loaded ``.hip`` file.
        self._scene = SceneManager(self)
        # Group / Scene (Multi / Root) container operations: collection
        # discovery, membership + dept-coverage, member add/remove, and
        # the Root context-menu actions. Owns the member-coverage cache.
        self._containers = ContainerManager(self)
        # Errors accumulated during the most recent discovery pass.
        # Drained by ``drain_discovery_errors`` so the QueryEngine can
        # attach them to the AssetPage. A new browse begins by clearing
        # this list (see ``get_assets``).
        self._discovery_errors: list[CatalogError] = []

        # Discovery cache (merged across projects). Invalidated by
        # project add / remove / refresh.
        self._cached_assets: list[Asset] | None = None
        self._cached_shots: list[Asset] | None = None

        # Per-asset, per-department version overrides — populated by
        # the detail panel's dept-row combos when a user picks an older
        # version to view, consumed by everything that reads "the
        # active version for this dept" (open / new from current / etc).
        self._dept_versions = DeptVersionStore()

        # All TH_* env / default_client / module-cache activation lives
        # in :class:`ProjectActivator`. It also captures the launch
        # project (env at construction) so the one-shot "switched
        # context" warning can fire at most once per session.
        self._activator = ProjectActivator()

        # Client construction is deferred until the first asset-browse
        # call. ``initialize()`` is intentionally a no-op (Houdini 22
        # runs it on the main thread during startup, so eager building
        # there would block Houdini load on per-project ``Path.exists``
        # SMB timeouts). Clients are built on demand by
        # ``self._clients.get`` / ``self._clients.try_get`` from
        # ``get_assets`` and the per-action helpers, which run on the
        # asset-browser worker thread.

    # ── Warm-up hooks ─────────────────────────────────────

    def initialize(self) -> None:
        """No-op. Houdini 22's asset browser invokes this on the main
        thread during startup, so any work here blocks Houdini load —
        a single registered project that points at an unreachable
        network share stalls startup for the SMB timeout per project.
        Per-project Client construction is deferred to
        :meth:`warm_up_worker_thread` (worker) and on-demand calls
        from get_assets / per-action helpers (worker thread).
        """
        return

    def warm_up_main_thread(self) -> None:
        """Pre-import ``tumblepipe.api`` on the main thread.

        Building Clients on a worker thread while another main-thread
        import is running races on Python's import lock and can
        deadlock Houdini. Importing the package once on the GUI thread
        before any worker touches it sidesteps the issue.
        """
        try:
            import tumblepipe.api  # noqa: F401
        except ImportError:
            # The pipeline package isn't installed in this environment
            # (rare — usually means hpm hasn't activated TumblePipe).
            # Don't raise: per-project init will surface a typed
            # CatalogInitError if a Client is actually requested.
            log.debug("tumblepipe.api not importable; skipping main-thread warmup")

    def warm_up_worker_thread(self) -> None:
        """Build per-project Clients on a worker thread.

        Each registered project's Client is constructed eagerly here
        so the first browse doesn't block on SMB ``Path.exists()``
        timeouts. Init failures are recorded on the slot and surfaced
        via :class:`AssetPage.errors` on the next browse.
        """
        self._clients.ensure_all()

    # ── Project lookup / activation ───────────────────────

    def _project_for_hip_path(
        self, hip_path: Path,
    ) -> ProjectConfig | None:
        """Walk every registered project and return the one whose
        ``project_path`` is a parent of ``hip_path``."""
        if not hip_path:
            return None
        try:
            hip = Path(hip_path).resolve()
        except Exception:
            return None
        for proj in self._registry.all():
            try:
                root = Path(proj.project_path).resolve()
            except Exception:
                continue
            try:
                hip.relative_to(root)
                return proj
            except ValueError:
                continue
        return None

    def _activate_project(self, project: ProjectConfig) -> None:
        """Delegate to the :class:`ProjectActivator` helper."""
        self._activator.activate(project)

    # ── Tags ──────────────────────────────────────────────

    def get_available_tags(self) -> dict[str, list[str]]:
        # Don't call self._clients.get here — get_available_tags runs
        # on the GUI thread and Client construction can block on SMB
        # Path.exists() timeouts. _list_categories / _list_sequences
        # both gate on self._clients.is_ready, so they return [] for
        # projects whose Clients haven't been warmed up yet.
        cats = self._list_categories()
        seqs = self._list_sequences()
        return {
            "source": ["pipeline"],
            "type": ["asset", "shot"],
            "category": [c.lower() for c in cats],
            "sequence": seqs,
            "project": [p.name for p in self._registry.all()],
        }

    # ── Collections ───────────────────────────────────────

    def get_collections(self) -> list[Collection]:
        """Build the collections sidebar tree.

        Each registered project gets a top-level folder with its own
        ``Assets`` / ``Shots`` / ``Groups`` / ``Scenes`` sub-sections.
        When there is only one project the per-project wrapper is
        dropped and those four sections live at the top level for a
        flatter navigation.
        """
        if not self._registry:
            return []
        projects = list(self._registry.all())
        flatten = len(projects) == 1

        result: list[Collection] = []
        for proj in projects:
            sections = self._build_project_sections(proj)
            if not sections:
                continue
            if flatten:
                result.extend(sections)
            else:
                project_tag = f"project:{proj.name}"
                result.append(Collection(
                    id=f"project_section:{proj.name}",
                    label=proj.name,
                    tag=project_tag,
                    icon="folder",
                    children=tuple(sections),
                ))
        return [cascade_counts(c) for c in result]

    def _build_project_sections(self, proj) -> list[Collection]:
        """Return [Assets, Shots, Roots, Tasks] sections for one
        project, omitting any that are empty. Multis are nested inside
        Assets / Shots based on the Multi's own context (they can't
        mix) — a single ``Multis`` subheader on each side filters the
        grid to that context's Multi cards.
        """
        project_tag = f"project:{proj.name}"
        sections: list[Collection] = []

        # Build once, then inject under Assets / Shots below.
        asset_multis, shot_multis = self._containers._build_groups_for_project(proj)

        # Multis go at the top of each side — artists favour multi-shot
        # / multi-asset workflows when one exists. The subheaders are
        # always present (even with zero children) so the affordance
        # stays one click away.
        cats = self._list_categories_for_project(proj.name)
        cat_children = [asset_multis]
        for cat in cats:
            count = self._count_for_project_category(proj.name, cat)
            cat_children.append(Collection(
                id=f"{proj.name}:category:{cat.lower()}",
                label=cat,
                count=count,
                tag=f"{project_tag}+category:{cat.lower()}",
                kind="category",
            ))
        sections.append(Collection(
            id=f"{proj.name}:assets_section",
            label="Assets",
            tag=f"{project_tag}+type:asset",
            icon="box",
            children=tuple(cat_children),
        ))

        seqs = self._list_sequences_for_project(proj.name)
        seq_children = [shot_multis]
        for seq in seqs:
            count = self._count_for_project_sequence(proj.name, seq)
            seq_children.append(Collection(
                id=f"{proj.name}:sequence:{seq}",
                label=seq,
                count=count,
                tag=f"{project_tag}+sequence:{seq}",
                kind="sequence",
            ))
        sections.append(Collection(
            id=f"{proj.name}:shots_section",
            label="Shots",
            tag=f"{project_tag}+type:shot",
            icon="clapperboard",
            children=tuple(seq_children),
        ))

        scene_children = self._containers._build_scenes_for_project(proj)
        sections.append(Collection(
            id=f"{proj.name}:scenes_section",
            label="Roots",
            icon="layers",
            tag=f"{project_tag}+type:scene",
            children=tuple(scene_children),
        ))

        pending_count, done_count = self._count_todos_for_project(proj.name)
        todo_children = [
            Collection(
                id=f"{proj.name}:todo:pending",
                label="Pending",
                count=pending_count,
                tag=f"{project_tag}+todo:pending",
                icon="circle-ellipsis",
            ),
            Collection(
                id=f"{proj.name}:todo:done",
                label="Done",
                count=done_count,
                tag=f"{project_tag}+todo:done",
                icon="circle-check",
            ),
        ]
        sections.append(Collection(
            id=f"{proj.name}:todos_section",
            label="Tasks",
            icon="list-todo",
            children=tuple(todo_children),
        ))

        return sections

    def _count_for_project_category(self, project_name: str, category: str) -> int:
        """Count assets in ``project_name`` whose category matches."""
        items = self._get_all_items()
        cat_tag = f"category:{category.lower()}"
        proj_tag = f"project:{project_name}"
        return sum(
            1 for a in items
            if cat_tag in a.tags and proj_tag in a.tags and "type:asset" in a.tags
        )

    def _count_for_project_sequence(self, project_name: str, sequence: str) -> int:
        """Count shots in ``project_name`` whose sequence matches."""
        items = self._get_all_items()
        seq_tag = f"sequence:{sequence}"
        proj_tag = f"project:{project_name}"
        return sum(
            1 for a in items
            if seq_tag in a.tags and proj_tag in a.tags
        )

    def _count_todos_for_project(
        self, project_name: str,
    ) -> tuple[int, int]:
        """Return ``(pending_count, done_count)`` for a project."""
        try:
            from tumbletrove import asset_browser
            mgr = asset_browser.get_todos()
        except Exception:
            return 0, 0
        if mgr is None:
            return 0, 0
        items = self._get_all_items()
        proj_tag = f"project:{project_name}"
        pending = done = 0
        for a in items:
            if proj_tag not in a.tags:
                continue
            status = mgr.status(self.id, a.id)
            if status == "pending":
                pending += 1
            elif status == "done":
                done += 1
        return pending, done

    def get_primary_filters(
        self, active_tags: frozenset[str] | None = None,
    ) -> list[Collection]:
        items = self._get_all_items()

        # When the user has a collection selected, scope pill counts to
        # items that satisfy the collection's non-type clauses. We strip
        # the ``type:*`` clauses because each TYPE pill represents its
        # own type — the count is "how many items of THIS type would be
        # visible if the user picked this pill", so we don't want the
        # current type clause forcing the count to zero on the others.
        if active_tags:
            non_type = frozenset(
                t for t in active_tags if not t.startswith("type:")
            )
            if non_type:
                items = [
                    a for a in items
                    if non_type.issubset(set(a.tags))
                ]

        def _tag_count(tag: str) -> int:
            return sum(1 for a in items if tag in a.tags)

        try:
            from tumbletrove import asset_browser
            mgr = asset_browser.get_todos()
        except Exception:
            mgr = None

        def _todo_count(status: str) -> int:
            if mgr is None:
                return 0
            return sum(
                1 for a in items
                if mgr.status(self.id, a.id) == status
            )

        pills: list[Collection] = [
            Collection(id="type:asset", label="Assets", tag="type:asset", icon="box",
                       count=_tag_count("type:asset")),
            Collection(id="type:shot", label="Shots", tag="type:shot", icon="clapperboard",
                       count=_tag_count("type:shot")),
            Collection(id="type:group", label="Multis", tag="type:group", icon="group",
                       count=sum(1 for _ in self._containers._iter_group_collections())),
            Collection(id="type:scene", label="Roots", tag="type:scene", icon="layers",
                       count=sum(1 for _ in self._containers._iter_scene_collections())),
        ]
        projects = list(self._registry.all())
        if len(projects) > 1:
            for proj in projects:
                pills.append(Collection(
                    id=f"project:{proj.name}",
                    label=proj.name,
                    tag=f"project:{proj.name}",
                    count=_tag_count(f"project:{proj.name}"),
                ))
        pills += [
            Collection(id="todo:pending", label="Pending", tag="todo:pending",
                       icon="circle-ellipsis", count=_todo_count("pending")),
            Collection(id="todo:done",    label="Done",    tag="todo:done",
                       icon="circle-check",   count=_todo_count("done")),
        ]
        return pills

    # ── Assets ────────────────────────────────────────────

    def get_assets(
        self, query: str = "", tags: frozenset[str] = frozenset(),
        cursor: str | None = None, page_size: int = 50,
    ) -> AssetPage:
        # Container types (Groups / Scenes) take over the grid: the
        # cards are synthesized from sidebar Collection data rather than
        # the real asset/shot index. Drill-down (card click) clears the
        # ``type:group`` / ``type:scene`` filter and replaces it with
        # the container's own ``group:`` / ``scene:`` tag, which falls
        # through to the normal member-filter branch below.
        if "type:group" in tags:
            return self._containers._get_container_assets(
                "group", query, tags, cursor, page_size,
            )
        if "type:scene" in tags:
            return self._containers._get_container_assets(
                "scene", query, tags, cursor, page_size,
            )

        # Begin a fresh discovery pass. Errors are accumulated on
        # ``self._discovery_errors`` and attached to the returned
        # AssetPage so the consumer can surface them in the UI.
        self._discovery_errors = []
        # Block until every registered project's init has finished
        # (success or fail) before aggregating discovery. Init errors
        # are recorded on slots and surfaced via per-project discovery.
        for err in self._clients.ensure_all():
            self._discovery_errors.append(err)
        all_items = self._get_all_items()

        # Handle group/scene tags (special filtering). Filter failures
        # are surfaced via AssetPage.errors rather than silently
        # widening the result set, which would defeat the filter.
        remaining_tags = set()
        for t in tags:
            try:
                if t.startswith("group:"):
                    all_items = self._containers._filter_by_group(all_items, t)
                elif t.startswith("scene:"):
                    all_items = self._containers._filter_by_scene(all_items, t)
                else:
                    remaining_tags.add(t)
            except CatalogError as err:
                self._discovery_errors.append(err)
                all_items = []  # filter failed — empty result, not all items
                break

        # Inject todo-status tags so sidebar filters can pick up assets
        # by their todo state (none / pending / done).
        self._stamp_todo_tags(all_items)

        # Filter by standard tags
        from tumbletrove.asset_browser.api.tags import match_tags
        if remaining_tags:
            filter_tags = frozenset(t for t in remaining_tags if not t.startswith("source:"))
            if filter_tags:
                all_items = [a for a in all_items if match_tags(a.tags, filter_tags)]

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
            errors=tuple(self._discovery_errors),
        )

    # ── Detail ────────────────────────────────────────────

    def get_detail(self, asset_id: str, version: str | None = None) -> AssetDetail:
        # Container ids ("group:PROJECT:ctx/name", "scene:PROJECT:path")
        # don't pass through the normal asset-id parser — route them to
        # the dedicated container detail builder so the detail panel
        # can render group/scene info (incl. the depts toggle).
        if asset_id.startswith("group:") or asset_id.startswith("scene:"):
            return self._containers._get_container_detail(asset_id)

        # asset_id format: "PROJECT/CATEGORY/AssetName" or "PROJECT/SEQ/Shot".
        parsed = self._resolver.split(asset_id)
        if parsed is None:
            return AssetDetail(
                id=asset_id, name=asset_id, thumbnail_url="",
                tags=frozenset({"source:pipeline"}),
            )
        project_name, second, third = parsed

        # Activate the asset's project so any tumblehead module-level
        # calls (variants / frame range / fps / etc.) resolve against
        # the right configuration.
        proj = self._registry.get(project_name)
        if proj is not None:
            self._activate_project(proj)

        # Detail panel shows *workfile* versions (the .hip files in the
        # dept work dir), not export/publish versions which the deck
        # popup uses.
        dept_info = self._get_department_workfile_info(asset_id)

        # Build tags + metadata. Descriptions are stored per-asset in a
        # sidecar file ({asset_root}/description.txt) — no default
        # generated text. See _edit_description.
        tags = {"source:pipeline", f"project:{project_name}"}
        metadata: dict = {
            "departments": dept_info,
            "project": project_name,
        }

        cats = self._list_categories_for_project(project_name)
        if second in cats:
            tags.add("type:asset")
            tags.add(f"category:{second.lower()}")
            metadata["category"] = second
            metadata["variants"] = self._get_variants(asset_id, "assets")
        else:
            tags.add("type:shot")
            tags.add(f"sequence:{second}")
            metadata["sequence"] = second
            fr = self._get_frame_range_obj(asset_id)
            if fr is not None:
                start = fr.start_frame
                end = fr.end_frame
                if start is not None:
                    metadata["frame_start"] = start
                if end is not None:
                    metadata["frame_end"] = end
                if start is not None and end is not None:
                    metadata["frame_total"] = end - start + 1
            fps = self._get_fps(asset_id)
            if fps is not None:
                metadata["fps"] = fps

        description = self._read_description(asset_id)

        return AssetDetail(
            id=asset_id,
            name=third,
            thumbnail_url="",
            tags=frozenset(tags),
            description=description,
            metadata=metadata,
        )

    def _get_project_name(self, asset_id: str | None = None) -> str:
        """Return the display name of the project an asset belongs to.

        Without ``asset_id``, returns an empty string — there is no
        single "current" project anymore.
        """
        if asset_id is None:
            return ""
        proj = self._resolver.project_for(asset_id)
        return proj.name if proj is not None else ""

    def _get_variants(self, asset_id: str, kind: str) -> list[str]:
        """Return variant names for an asset/shot.

        Returns ``[]`` for a malformed or unresolvable id. Raises
        :class:`ConfigError` if the variants module fails to load or
        the lookup itself raises — callers (typically :meth:`get_detail`)
        let it propagate so the consumer can render a detail-level error.
        """
        from tumblepipe.config.variants import list_variants
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return []
        try:
            return list(list_variants(uri))
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"variants lookup failed for {asset_id}: {exc}",
                cause=exc,
            ) from exc

    def _get_frame_range_obj(self, asset_id: str):
        """Return the FrameRange dataclass for a shot, or ``None`` if
        the id doesn't resolve. Raises :class:`ConfigError` on lookup
        failure (rather than masking with ``None``).
        """
        from tumblepipe.config.timeline import get_frame_range
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return None
        try:
            return get_frame_range(uri)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"frame range lookup failed for {asset_id}: {exc}",
                cause=exc,
            ) from exc

    def _get_fps(self, asset_id: str):
        """Return FPS for an entity (or project default).

        Raises :class:`ConfigError` if the timeline module's FPS lookup
        raises — the previous silent ``None`` masked config corruption.
        """
        from tumblepipe.config.timeline import get_fps
        uri = self._resolver.uri_for(asset_id)
        try:
            if uri is not None:
                fps = get_fps(uri)
                if fps is not None:
                    return fps
            return get_fps()
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"fps lookup failed for {asset_id}: {exc}",
                cause=exc,
            ) from exc

    # ── Description sidecar ───────────────────────────────

    def _description_path(self, asset_id: str) -> Path | None:
        """Return the path to ``description.txt`` for an asset/shot."""
        parsed = self._resolver.split(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._resolver.root_for(asset_id)
        if root is None:
            return None
        try:
            cats = self._list_categories_for_project(project_name)
            if second in cats:
                base = root / "assets" / second / third
            else:
                base = root / "shots" / second / third
            return base / "description.txt"
        except Exception:
            return None

    def _read_description(self, asset_id: str) -> str:
        path = self._description_path(asset_id)
        if path is None or not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            log.debug("Failed to read description at %s", path)
            return ""

    def _write_description(self, asset_id: str, text: str) -> None:
        """Write the description sidecar for an asset/shot.

        Raises :class:`ConfigError` if the asset id is unresolvable, or
        :class:`OSError` if the write fails.
        """
        path = self._description_path(asset_id)
        if path is None:
            raise ConfigError(
                self.id,
                f"cannot resolve description path for {asset_id}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n", encoding="utf-8")

    # ── Thumbnail sidecar (delegated to ThumbnailManager) ──

    def get_thumbnail(self, asset: Asset):
        return self._thumbnails.get_thumbnail(asset)

    def get_card_menu_items(self, asset: Asset, *, selected_assets=None):
        """Catalog-contributed card right-click items.

        ``selected_assets`` is provided by the browser when the right-clicked
        card is part of a multi-selection AND every selected card belongs to
        this catalog. We use it to expand "Submit Jobs…" to the whole
        selection (filtered to the same context as the right-clicked card,
        since publish/render is per-context). When ``None`` we fall through
        to a single-asset menu.
        """
        asset_id = asset.id

        # Build the submit-jobs target list: the multi-selected cards if
        # the browser provided a selection scoped to this catalog AND
        # filtered to the same context as the right-clicked card,
        # otherwise just this card.
        click_context = self._asset_context(asset)
        if selected_assets is not None and click_context is not None:
            submit_targets = [
                a for a in selected_assets
                if self._asset_context(a) == click_context
            ]
            if not submit_targets:
                submit_targets = [asset]
        else:
            submit_targets = [asset]
        submit_label = (
            "Submit Jobs…"
            if len(submit_targets) <= 1
            else f"Submit Jobs for {len(submit_targets)} selected…"
        )

        items = [
            (
                submit_label,
                lambda targets=submit_targets:
                    self._submit_jobs_for_assets(targets),
            ),
            (
                "Generate Master…",
                lambda aid=asset_id: self._generate_master_scene(aid),
            ),
            (
                "Edit description…",
                lambda aid=asset_id: self._edit_description(aid),
            ),
            (
                "Tasks…",
                lambda a=asset: self._manage_todos(a),
            ),
            (
                "Select thumbnail…",
                lambda aid=asset_id: self._thumbnails.select(aid),
            ),
            (
                "Capture thumbnail",
                lambda aid=asset_id: self._thumbnails.capture(aid),
            ),
            (
                "Open in Database Editor…",
                lambda aid=asset_id: self._open_database_editor(aid),
            ),
        ]
        # For shots, offer a "Clear scene" item when one is set directly
        # on the shot (not inherited from sequence).
        if "type:shot" in asset.tags and self._shot_has_direct_scene_ref(asset_id):
            items.append(
                (
                    "Clear Root",
                    lambda aid=asset_id: self._clear_shot_scene_ref(aid),
                )
            )

        # "Remove from <Multi/Root>" — one entry per container this
        # asset belongs to. For Multis this is full member-list
        # removal (a Multi's coverage is whole-asset, not per-dept).
        # For Roots this removes the asset from the Root's asset list
        # (shots use ``Clear Root`` above, which clears their
        # ``scene_ref`` instead).
        try:
            memberships = self.get_asset_membership(asset_id)
        except Exception:
            memberships = []
        if memberships:
            for cid, label, kind in memberships:
                if kind == "group":
                    items.append((
                        f"Remove from Multi: {label}",
                        lambda aid=asset_id, c=cid:
                            self._containers._remove_member_from_group(aid, c),
                    ))
                elif kind == "scene":
                    # Roots don't store shot membership in their
                    # ``assets`` list (shots point at a Root via
                    # ``scene_ref``), so this branch only fires for
                    # asset cards. The unified removal path covers it.
                    items.append((
                        f"Remove from Root: {label}",
                        lambda aid=asset_id, c=cid:
                            self._containers._remove_member_from_root(aid, c),
                    ))
        return items

    def _asset_context(self, asset: Asset) -> str | None:
        """Return ``'shots'`` or ``'assets'`` for an asset card, or
        ``None`` for unknown cards.
        """
        tags = asset.tags or ()
        if "type:shot" in tags:
            return "shots"
        if "type:asset" in tags:
            return "assets"
        return None

    def _submit_jobs_for_assets(self, assets: list[Asset]) -> None:
        """Open the slim submit dialog for ``assets``.

        All assets must share a project and a context. Activates the
        project before opening the dialog so ``tumblepipe.api.default_client``
        and the department lookups resolve against the right install.
        """
        if not assets:
            return
        # All from the same project — enforced by the menu's selection
        # filter, but double-check defensively.
        proj = self._resolver.project_for(assets[0].id)
        if proj is None:
            log.warning("Submit Jobs: no project for asset %s", assets[0].id)
            return
        self._activate_project(proj)

        contexts = {self._asset_context(a) for a in assets}
        contexts.discard(None)
        if len(contexts) != 1:
            log.warning(
                "Submit Jobs: mixed/empty contexts %r — aborting", contexts,
            )
            return
        context = next(iter(contexts))

        # Build URI list parallel to a display name list.
        uris: list = []
        names: list[str] = []
        for a in assets:
            uri = self._resolver.uri_for(a.id)
            if uri is None:
                log.warning("Submit Jobs: cannot resolve URI for %s", a.id)
                continue
            uris.append(uri)
            names.append(a.name or a.id.split("/")[-1])
        if not uris:
            return

        # Load submit_jobs_dialog by file path: this catalog is loaded
        # via importlib.util.spec_from_file_location (see registry.py),
        # so it has no parent package and `from .submit_jobs_dialog`
        # would raise ImportError. spec_from_file_location keeps the
        # module out of sys.path entirely, which avoids polluting the
        # global module namespace.
        try:
            import importlib.util
            import sys
            mod_name = "tumblepipe_asset_browser_submit_jobs_dialog"
            mod = sys.modules.get(mod_name)
            if mod is None:
                dlg_path = Path(__file__).parent / "submit_jobs_dialog.py"
                spec = importlib.util.spec_from_file_location(
                    mod_name, dlg_path,
                )
                if spec is None or spec.loader is None:
                    raise ImportError(
                        f"Cannot build module spec for {dlg_path}",
                    )
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
            SubmitJobsDialog = mod.SubmitJobsDialog
            import hou
            parent = hou.qt.mainWindow()
            dlg = SubmitJobsDialog(uris, names, context, parent=parent)
            dlg.exec()
        except Exception as exc:
            log.exception("Failed to open Submit Jobs dialog")
            try:
                from PySide6.QtWidgets import QMessageBox
                import hou
                QMessageBox.critical(
                    hou.qt.mainWindow(),
                    "Submit Jobs",
                    f"Failed to open dialog:\n\n{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass

    def _shot_has_direct_scene_ref(self, asset_id: str) -> bool:
        try:
            from tumblepipe.config import scene as scene_mod
            uri = self._resolver.uri_for(asset_id)
            if uri is None:
                return False
            return scene_mod.get_scene_ref(uri) is not None
        except Exception:
            return False

    def _clear_shot_scene_ref(self, asset_id: str) -> None:
        try:
            from tumblepipe.config import scene as scene_mod
            uri = self._resolver.uri_for(asset_id)
            if uri is None:
                return
            scene_mod.set_scene_ref(uri, None)
            self.invalidate_cache()
            self._request_global_detail_refresh()
        except Exception:
            log.exception("Clear scene_ref failed for %s", asset_id)

    def get_sub_card_menu_items(self, asset: Asset, sub_card_key: str):
        """Right-click items for a dept sub-card in the deck popup.

        Mirrors the detail-panel dept context menu so behavior stays
        consistent across both surfaces. Exposes ``New: Current`` and
        ``New: Template`` so the user can spawn a fresh version from
        either the loaded scene or a template regardless of whether
        the dept already has versions.
        """
        dept = sub_card_key
        asset_id = asset.id

        # Group container sub-cards: simpler menu — Open / Open
        # Location / New: Template. "New: Current" is intentionally
        # omitted for v1 since the active-scene-context resolver
        # doesn't track group workfiles yet.
        if asset_id.startswith("group:"):
            return [
                (
                    "Open Latest",
                    lambda aid=asset_id, dn=dept:
                        self._workfiles.open_group_workfile(aid, dn),
                ),
                (
                    "Open Location",
                    lambda aid=asset_id, dn=dept:
                        self._workfiles.open_group_dept_work_dir(aid, dn),
                ),
                ("__separator__", None),
                (
                    "New: Template",
                    lambda aid=asset_id, dn=dept:
                        self._workfiles.new_group_from_template(
                            aid, dn, self._request_global_detail_refresh,
                        ),
                ),
            ]

        if self._resolver.uri_for(asset_id) is None:
            return []
        versions = self._get_department_workfile_info(asset_id).get(dept, [])
        available = bool(versions)
        latest = latest_version(versions)
        scene_dv = self._scene.get_scene_dept_version(asset_id)
        is_active = bool(scene_dv) and scene_dv[0] == dept

        items: list = []
        if available:
            items.append((
                f"Open Latest ({latest})",
                lambda aid=asset_id, dn=dept, v=latest:
                    self._workfiles.open_version_now(aid, dn, v, None),
            ))
            items.append((
                "Open Location",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_dept_work_dir(aid, dn),
            ))
            items.append((
                "View Latest Export",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_latest_export(aid, dn),
            ))
            items.append((
                "Open in New Houdini",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_in_new_instance(aid, dn),
            ))
            if is_active:
                items.append((
                    "Reload Scene",
                    lambda: self._scene.reload_current_scene(None),
                ))
        else:
            items.append((
                "Open Location",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_dept_work_dir(aid, dn),
            ))

        # "Remove from <group>" — visible only when this dept is
        # currently covered by a group's workfile for this member.
        # Removing the member from the group is global (affects every
        # dept the group covers for this member), so the label is
        # phrased as "Remove from <group>" rather than per-dept.
        dept_groups = self._containers._dept_groups_for_member(asset_id)
        group_info = dept_groups.get(dept)
        if group_info:
            group_id, group_label = group_info
            items.append(("__separator__", None))
            items.append((
                f"Remove from {group_label}",
                lambda aid=asset_id, gid=group_id:
                    self._containers._remove_member_from_group(aid, gid),
            ))

        items.append(("__separator__", None))
        items.append((
            "New: Current",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_current(
                    aid, dn, self._request_global_detail_refresh,
                ),
        ))
        items.append((
            "New: Template",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_template(
                    aid, dn, self._request_global_detail_refresh,
                ),
        ))
        return items

    def _generate_master_scene(self, asset_id: str) -> None:
        """Generate a master .hip merging all department workfiles.

        Creates ``<entity>/master/<name>_master.hip``, merges each
        department's latest workfile into it, wraps each in a labelled
        network box, and arranges them left to right.
        """
        import hou

        # Activate the asset's project
        proj = self._resolver.project_for(asset_id)
        if proj is not None:
            self._activate_project(proj)

        # Get department workfiles
        dept_info = self._get_department_workfile_info(asset_id)
        if not dept_info:
            hou.ui.displayMessage(
                "No department workfiles found for this asset.",
                severity=hou.severityType.Warning,
            )
            return

        # Resolve entity path info
        parts = self._resolver.split(asset_id)
        if parts is None:
            return
        project_name, second, third = parts
        root = self._resolver.root_for(asset_id)
        if root is None:
            return
        cats = self._list_categories_for_project(project_name)
        is_asset = second in cats
        if is_asset:
            base = root / "assets" / second / third
        else:
            base = root / "shots" / second / third

        # Sort departments in pipeline order and find latest version
        dept_paths: list[tuple[str, Path]] = []
        dept_order = self._list_entity_departments(
            "assets" if is_asset else "shots",
        )
        ordered = [d for d in dept_order if d in dept_info]
        ordered += [d for d in sorted(dept_info.keys()) if d not in ordered]
        for dept in ordered:
            versions = dept_info[dept]
            if not versions:
                continue
            latest = latest_version(versions)
            hip_path = self._workfiles.workfile_path_for(asset_id, dept, latest)
            if hip_path is not None and hip_path.exists():
                dept_paths.append((dept, hip_path))

        if not dept_paths:
            hou.ui.displayMessage(
                "No workfiles found on disk.",
                severity=hou.severityType.Warning,
            )
            return

        master_dir = base / "master"
        master_dir.mkdir(parents=True, exist_ok=True)
        master_hip = master_dir / f"{third}_master.hip"

        # Confirm
        choice = hou.ui.displayMessage(
            f"Generate master scene for {third}?\n\n"
            f"Merging {len(dept_paths)} departments into:\n"
            f"{master_hip}\n\n"
            f"This will open a new scene.",
            buttons=("Generate", "Cancel"),
            title="Generate Master Scene",
        )
        if choice != 0:
            return

        # Save a blank .hip to the master path, then load it so
        # Houdini fully switches context (title bar, env, etc.).
        hou.hipFile.clear(suppress_save_prompt=True)
        hou.hipFile.save(str(master_hip))
        hou.hipFile.load(str(master_hip), suppress_save_prompt=True,
                         ignore_load_warnings=True)

        # Collect all top-level containers where nodes might merge into
        _containers = ["/obj", "/stage", "/out", "/shop", "/ch", "/tasks"]

        def _snapshot_all():
            """Return set of all node paths across all containers."""
            paths = set()
            for c in _containers:
                parent = hou.node(c)
                if parent:
                    for n in parent.children():
                        paths.add(n.path())
            return paths

        def _new_nodes_grouped(old_paths):
            """Return {container_path: [new_nodes]} after a merge."""
            grouped: dict[str, list] = {}
            for c in _containers:
                parent = hou.node(c)
                if not parent:
                    continue
                for n in parent.children():
                    if n.path() not in old_paths:
                        grouped.setdefault(c, []).append(n)
            return grouped

        x_offset = 0.0
        box_gap = 3.0

        with hou.undos.group(f"Generate master: {third}"):
            for dept_name, hip_path in dept_paths:
                old_paths = _snapshot_all()

                # Merge the department workfile
                try:
                    hou.hipFile.merge(
                        str(hip_path),
                        node_pattern="*",
                        overwrite_on_conflict=False,
                        ignore_load_warnings=True,
                    )
                except hou.LoadWarning:
                    pass
                except Exception:
                    log.exception("Failed to merge %s", hip_path)
                    continue

                # Find newly merged nodes in each container
                grouped = _new_nodes_grouped(old_paths)
                all_new = []
                for nodes in grouped.values():
                    all_new.extend(nodes)
                if not all_new:
                    continue

                # For each container that got new nodes, move + box them
                for container_path, new_nodes in grouped.items():
                    parent = hou.node(container_path)
                    if not parent or not new_nodes:
                        continue

                    # Move new nodes to the right of previous departments
                    min_x = min(n.position()[0] for n in new_nodes)
                    min_y = min(n.position()[1] for n in new_nodes)
                    offset = hou.Vector2(x_offset - min_x, -min_y)
                    for n in new_nodes:
                        n.setPosition(n.position() + offset)

                    # Create a network box around the merged nodes
                    box = parent.createNetworkBox()
                    box.setComment(dept_name.upper())
                    box.setColor(hou.Color(0, 0, 0))
                    for n in new_nodes:
                        box.addItem(n)
                    box.fitAroundContents()

                # Advance x offset based on the widest box across containers
                max_right = x_offset
                for container_path, new_nodes in grouped.items():
                    parent = hou.node(container_path)
                    if parent:
                        for nb in parent.networkBoxes():
                            if nb.comment() == dept_name.upper():
                                right = nb.position()[0] + nb.size()[0]
                                if right > max_right:
                                    max_right = right
                x_offset = max_right + box_gap

        # Save the master scene
        hou.hipFile.save()

        # Frame all in network editor
        for pane in hou.ui.paneTabs():
            if isinstance(pane, hou.NetworkEditor):
                pane.homeToSelection()
                break

        hou.ui.setStatusMessage(
            f"Master scene saved: {master_hip}",
            severity=hou.severityType.Message,
        )

    def _open_database_editor(self, asset_id: str) -> None:
        """Open the pipeline DatabaseWindow with the entity pre-selected."""
        proj = self._resolver.project_for(asset_id)
        if proj is not None:
            self._activate_project(proj)
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            import hou
            hou.ui.displayMessage(
                f"Could not resolve a database entity for '{asset_id}'.",
                severity=hou.severityType.Warning,
            )
            return
        # Delegate to the shared singleton launcher so the shelf tool and
        # this action open the same window. The launcher surfaces any
        # error to the user instead of failing silently.
        from tumblepipe.pipe.houdini.ui.database import open_database_editor
        open_database_editor(uri)

    def _edit_description(self, asset_id: str) -> None:
        """Open a multiline text dialog to edit the description sidecar."""
        from PySide6.QtWidgets import QInputDialog
        import hou
        current = self._read_description(asset_id)
        parent = hou.qt.mainWindow()
        text, ok = QInputDialog.getMultiLineText(
            parent,
            "Edit Description",
            f"Description for {asset_id}:",
            current,
        )
        if not ok:
            return
        try:
            self._write_description(asset_id, text)
        except (ConfigError, OSError):
            log.exception("Failed to write description for %s", asset_id)
            hou.ui.setStatusMessage(
                f"Failed to write description for {asset_id}",
                severity=hou.severityType.Warning,
            )
            return
        hou.ui.setStatusMessage(
            f"Description updated for {asset_id}",
            severity=hou.severityType.Message,
        )
        # Drop discovery caches so the next browse sees the edit,
        # and poke the asset browser detail panel to re-fetch.
        self._cached_assets = None
        self._cached_shots = None
        self._request_global_detail_refresh()

    def _manage_todos(self, asset) -> None:
        """Open the per-asset todos dialog."""
        try:
            from tumbletrove import asset_browser
            import hou
            from tumbletrove.asset_browser.ui.todos_dialog import TodosDialog
            mgr = asset_browser.get_todos()
            if mgr is None:
                return
            self._hook_todo_refresh(mgr)
            parent = hou.qt.mainWindow()
            dlg = TodosDialog(
                mgr, self.id, asset.id, asset.name, parent=parent,
            )
            dlg.show()
            dlg.raise_()
        except Exception:
            log.exception("Manage todos failed for %s", asset.id)

    def _stamp_todo_tags(self, items: list) -> None:
        """Rewrite each item's ``todo:*`` tags in place based on the
        current :class:`TodoManager` state. Called on every
        ``get_assets`` pass so sidebar filters (Pending / Done) stay
        current without needing a cache invalidation.
        """
        try:
            from tumbletrove import asset_browser
            mgr = asset_browser.get_todos()
        except Exception:
            mgr = None
        if mgr is None:
            return
        for a in items:
            status = mgr.status(self.id, a.id)
            base = frozenset(t for t in a.tags if not t.startswith("todo:"))
            if status is None:
                if base != a.tags:
                    object.__setattr__(a, "tags", base)
                continue
            new_tags = set(base)
            new_tags.add("todo:any")
            new_tags.add(f"todo:{status}")
            object.__setattr__(a, "tags", frozenset(new_tags))

    def _hook_todo_refresh(self, mgr) -> None:
        """Connect :class:`TodoManager` changes to the detail refresh
        once per catalog instance so add/remove via the dialog re-
        renders the detail panel's todos section live."""
        if getattr(self, "_todos_refresh_hooked", False):
            return
        try:
            mgr.items_changed.connect(
                lambda *_a: self._request_global_detail_refresh()
            )
            self._todos_refresh_hooked = True
        except Exception:
            log.exception("Failed to connect todo refresh hook")

    def _request_global_grid_refresh(self) -> None:
        """Ask every open asset browser to re-query the grid.

        Used after operations that change membership / visibility of
        the currently-filtered set (e.g. "Remove from Root" — the
        removed asset shouldn't keep appearing in the Root's drill
        view). Equivalent to the user clicking the refresh button.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from tumbletrove.asset_browser.ui.browser import AssetBrowserWidget
            for w in QApplication.allWidgets():
                if not isinstance(w, AssetBrowserWidget):
                    continue
                try:
                    w._refresh()
                except Exception:
                    log.exception(
                        "browser._refresh failed in global grid refresh",
                    )
        except Exception:
            log.debug("Global grid refresh failed", exc_info=True)

    def _request_card_refresh_for_id(self, asset_id: str) -> None:
        """Rebuild ``asset_id``'s card and swap it in any open grid.

        Used by create/edit actions on container (Multi/Root) cards
        where the detail-panel-driven refresh path (``refresh_cb`` →
        ``detail_refresh_requested`` → ``_on_quick_action_done``)
        wouldn't fire because the current detail isn't this container.
        """
        try:
            fresh = self.refresh_single_asset(asset_id)
        except Exception:
            log.debug(
                "refresh_single_asset failed for %s", asset_id,
                exc_info=True,
            )
            return
        if fresh is None:
            return
        try:
            from PySide6.QtWidgets import QApplication
            from tumbletrove.asset_browser.ui.browser import AssetBrowserWidget
            for w in QApplication.allWidgets():
                if not isinstance(w, AssetBrowserWidget):
                    continue
                grid = getattr(w, "_grid", None)
                if grid is None or not hasattr(grid, "update_card_asset"):
                    continue
                try:
                    grid.update_card_asset(fresh)
                except Exception:
                    log.debug(
                        "update_card_asset failed for %s", asset_id,
                        exc_info=True,
                    )
        except Exception:
            log.debug(
                "card refresh walk failed for %s", asset_id,
                exc_info=True,
            )

    def _request_global_detail_refresh(self) -> None:
        """Ask any open asset browser detail panels to re-fetch.

        Used by edit flows (e.g. Edit Description) that don't have the
        per-invocation ``refresh_detail`` callback to hand. Walks the
        Qt widget tree for ``DetailPanel`` instances and emits their
        refresh signal.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from tumbletrove.asset_browser.ui.detail_panel import DetailPanel
            for w in QApplication.allWidgets():
                if isinstance(w, DetailPanel):
                    w.detail_refresh_requested.emit()
        except Exception:
            log.debug("Global detail refresh failed")

    # ── Detail panel layout (sections built by DetailSectionBuilder) ──

    def get_detail_sections(
        self, detail: AssetDetail,
    ) -> list[DetailSection] | None:
        # Container details (Groups / Scenes) get a slim section list
        # — no scene-actions row, no todos. Groups also get a
        # Departments tab where the user can toggle which depts the
        # group covers; scenes don't (no editable depts field).
        kind = detail.kind
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

        # Asset actions live at the TOP of the detail panel so their
        # placement is consistent across assets — the user always knows
        # where Save/Publish/Refresh are regardless of selection.
        # Section title shows the loaded scene context
        # ('PROP/TemplateTest / lookdev / v0024'), or 'Scene Actions'
        # when there's no pipeline scene loaded.
        scene_ctx = self._scene.get_loaded_scene_context()
        if scene_ctx is not None:
            try:
                segs = scene_ctx.entity_uri.segments
                short = (
                    f"{segs[1]}/{segs[2]}"
                    if len(segs) >= 3 else str(scene_ctx.entity_uri)
                )
            except Exception:
                short = str(scene_ctx.entity_uri)
            try:
                import hou
                scene_proj = self._project_for_hip_path(
                    Path(hou.hipFile.path()),
                )
            except Exception:
                scene_proj = None
            project_prefix = (
                f"{scene_proj.name}/" if scene_proj is not None else ""
            )
            actions_title = (
                f"{project_prefix}{short} / {scene_ctx.department_name} "
                f"/ {scene_ctx.version_name or '?'}"
            )
        else:
            actions_title = "Scene Actions"

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
        # Stash for use inside the combined info section (the actions
        # widget builds inline rather than as a tab now).
        self._actions_section_title = actions_title
        return sections

    # ── Settings ──────────────────────────────────────────
    # :meth:`get_settings_widget` returns a multi-project management
    # widget for the gear-icon dialog (project list + Add / Remove
    # buttons) — the per-catalog way to expose richer settings UI than
    # the framework's default text fields.

    def get_settings_widget(self, parent=None):
        """Return a multi-project management widget for the gear-icon
        settings dialog."""
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "asset_browser_catalog__pipeline_settings_widget",
            Path(__file__).parent / "_pipeline_settings_widget.py",
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.PipelineSettingsWidget(self, parent=parent)

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

        # Drop discovery cache so the next browse re-aggregates.
        self._cached_assets = None
        self._cached_shots = None

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
        if "type:shot" in asset.tags:
            return GhostData(nodes=[GhostNode("th::import_shot::1.0", 0.0, 0.0)])
        return GhostData(nodes=[GhostNode("th::import_asset::1.0", 0.0, 0.0)])

    def attach_network_thumbnail(self, asset_id, raw_node, drop) -> None:
        """Attach the asset/shot's ``thumbnail.png`` sidecar as a
        ``hou.NetworkImage`` next to the given import node. Public
        because the browser host calls it for non-catalog-owned drops
        too (e.g. import_layer nodes created by the sub-card path)."""
        self._drops.attach_network_thumbnail(asset_id, raw_node, drop)

    def on_drop(self, detail, drop) -> bool:
        return self._drops.on_drop(detail, drop)

    def on_sub_drop(self, asset, sub_keys, drop) -> bool:
        return self._drops.on_sub_drop(asset, sub_keys, drop)

    def on_multi_drop(self, assets, drop) -> bool:
        return self._drops.on_multi_drop(assets, drop)

    # ── Quick Actions (top bar) ────────────────────────────

    def get_quick_actions(self):
        from tumbletrove.asset_browser.api.catalog import QuickAction
        return [
            QuickAction(id="save", label="Save", icon="save-all", tooltip="Save current scene"),
            QuickAction(id="publish", label="Publish", icon="send", tooltip="Publish exports"),
            QuickAction(id="reload", label="Reload", icon="rotate-ccw", tooltip="Reload current scene"),
        ]

    def get_quick_actions_label(self) -> str:
        try:
            import hou
            hip = hou.hipFile.path()
            if hip and hip != "untitled.hip":
                from pathlib import PurePath
                return PurePath(hip).name
        except Exception:
            pass
        return ""

    def execute_quick_action(self, action_id: str, done_cb=None) -> None:
        if action_id == "save":
            self._scene.save_current_scene(done_cb)
        elif action_id == "publish":
            self._scene.publish_current_scene(done_cb)
        elif action_id == "reload":
            self._scene.reload_current_scene(done_cb)

    def get_quick_action_menu_items(self, action_id: str):
        """Right-click items for a quick-action toolbar button (asset_browser
        >= 0.7.8; older hosts simply never call this).

        Only ``save`` carries one: an *inline* emergency save. The normal
        left-click Save defers to the main-thread event loop, which is frozen
        while Houdini sits in its crash-report dialog — so the queued save
        never lands. The menu entry runs the save directly from the context-
        menu handler so it still completes during a crash. See
        :meth:`SceneManager.emergency_save_current_scene`.
        """
        if action_id == "save":
            return [("Emergency Save (off-thread)", self._emergency_save)]
        return []

    def _emergency_save(self) -> None:
        # Intentionally NOT wrapped in run_catalog_op / gui_dispatch: deferring
        # would re-queue onto the same dead event loop the normal Save uses,
        # defeating the point. We run inline (the menu handler's nested loop
        # pumps during the crash dialog), accepting the brief GUI block.
        self._scene.emergency_save_current_scene()

    def get_quick_action_hover(self, action_id: str) -> str | None:
        """Return rich-text HTML for the hover popup above a quick action.

        - ``save``: last mtime of the currently-loaded hip file.
        - ``publish``: mtime of the latest published version for the
          current scene's (entity, dept, variant) tuple.

        Returns ``None`` for actions we don't track (e.g. ``reload``) so
        the button falls back to its regular QToolTip.
        """
        from tumbletrove.asset_browser.core.hover_info import format_age_html

        if action_id == "save":
            try:
                import hou
                hip = hou.hipFile.path()
                if not hip:
                    return None
                p = Path(hip)
                if not p.exists():
                    return format_age_html("Last saved", None)
                return format_age_html("Last saved", p.stat().st_mtime)
            except Exception:
                return None

        if action_id == "publish":
            scene_ctx = self._scene.get_loaded_scene_context()
            if scene_ctx is None:
                return None
            try:
                from tumblepipe.pipe.paths import latest_export_path
                variant = getattr(scene_ctx, "variant_name", None) or "default"
                path = latest_export_path(
                    scene_ctx.entity_uri, variant, scene_ctx.department_name,
                )
                if path is None or not path.exists():
                    return format_age_html("Last published", None)
                return format_age_html("Last published", path.stat().st_mtime)
            except Exception:
                return None

        return None

    def get_asset_hover_widget(self, asset):
        """Pipeline-specific asset hover popup, built as a widget tree.

        Compact at-a-glance card so the user can see asset info without
        opening the detail panel. Sections (top → bottom):

        - Big title + type/project/category subtitle
        - Tag pills (custom tags only; structural ones suppressed)
        - Department icon grid — all known depts for the context.
          Active depts tinted, inactive depts deep-grey. Each icon has
          a native Qt tooltip showing its name + version on hover.
        - Member/shot/sequence rows for Multi/Root containers

        Built as a widget tree (rather than HTML) so sub-elements like
        the dept icons can carry their own tooltips. All reads come
        from ``asset.metadata`` and ``asset.tags`` — no I/O.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
        )
        from tumbletrove.asset_browser.core.theme import (
            BG_MID, BORDER, FONT_BODY, FONT_FAMILY, FONT_SMALL,
            FONT_TINY, FONT_TITLE, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )

        if asset is None:
            return None

        name = asset.name or "Untitled"
        tags = list(asset.tags or ())
        metadata = asset.metadata or {}

        type_tag = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("type:")), None
        )
        type_label = {
            "asset": "Asset",
            "shot": "Shot",
            "group": "Multi",
            "scene": "Root",
        }.get(type_tag, type_tag.title() if type_tag else "")
        project = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("project:")),
            None,
        )
        category = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("category:")),
            None,
        )

        root = QWidget()
        root.setStyleSheet(
            f"QWidget {{ background: transparent; "
            f'font-family: "{FONT_FAMILY}"; color: {TEXT_PRIMARY}; }}'
        )
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── Title (bigger than the rest) ──────────────
        title = QLabel(name, root)
        title.setStyleSheet(
            f"color:{TEXT_PRIMARY}; font-weight:700; "
            f"font-size:{FONT_TITLE}px; background:transparent;"
        )
        layout.addWidget(title)

        meta_line = " · ".join(
            v for v in (type_label, project, category) if v
        )
        if meta_line:
            subtitle = QLabel(meta_line, root)
            subtitle.setStyleSheet(
                f"color:{TEXT_SECONDARY}; font-size:{FONT_BODY}px; "
                f"background:transparent;"
            )
            layout.addWidget(subtitle)

        def add_separator():
            layout.addSpacing(10)
            sep = QFrame(root)
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"color:{BORDER}; background:{BORDER};")
            sep.setFixedHeight(1)
            layout.addWidget(sep)
            layout.addSpacing(6)

        def add_section_header(text):
            hdr = QLabel(text.upper(), root)
            hdr.setStyleSheet(
                f"color:{TEXT_DIM}; font-size:{FONT_TINY}px; "
                f"font-weight:600; letter-spacing:1px; background:transparent;"
            )
            layout.addWidget(hdr)

        # ── Tags ─────────────────────────────────────
        # Pills only for non-structural tags. type:/status:/project:/
        # category: are surfaced in title/subtitle or on the card.
        suppress = ("type:", "status:", "project:", "category:")
        pill_tags = [t for t in tags if not t.startswith(suppress)]
        if pill_tags:
            add_separator()
            add_section_header("Tags")
            pills_widget = QWidget(root)
            pills_layout = QHBoxLayout(pills_widget)
            pills_layout.setContentsMargins(0, 4, 0, 0)
            pills_layout.setSpacing(4)
            for tag in pill_tags:
                display = tag.split(":", 1)[-1] if ":" in tag else tag
                pill = QLabel(display, pills_widget)
                pill.setStyleSheet(
                    f"background-color:{BG_MID}; "
                    f"border:1px solid {BORDER}; border-radius:8px; "
                    f"padding:1px 8px; color:{TEXT_SECONDARY}; "
                    f"font-size:{FONT_SMALL}px;"
                )
                pills_layout.addWidget(pill)
            pills_layout.addStretch(1)
            layout.addWidget(pills_widget)

        # ── Department grid ──────────────────────────
        if type_tag in ("asset", "shot"):
            grid_widget = self._hover_dept_grid_widget(
                root, type_tag, metadata, asset,
            )
            if grid_widget is not None:
                add_separator()
                add_section_header("Departments")
                layout.addWidget(grid_widget)

        # ── Container info (Multi / Root only) ──────
        extras: list[tuple[str, str]] = []
        # member_count is a typed field now (0 for non-containers); only
        # Multi/Root cards should surface the Members row.
        if type_tag in ("group", "scene"):
            extras.append(("Members", str(asset.member_count)))
        if "shot_count" in metadata:
            extras.append(("Shots", str(metadata["shot_count"])))
        if "sequence" in metadata and metadata["sequence"]:
            extras.append(("Sequence", str(metadata["sequence"])))
        if extras:
            add_separator()
            for k, v in extras:
                row = QLabel(
                    f"<span style='color:{TEXT_DIM};'>{k}:</span> "
                    f"<span style='color:{TEXT_PRIMARY};'>{v}</span>",
                    root,
                )
                row.setStyleSheet(
                    f"font-size:{FONT_BODY}px; background:transparent;"
                )
                layout.addWidget(row)

        return root

    def _hover_dept_grid_widget(self, parent, type_tag, metadata, asset):
        """Build the department icon grid as a QWidget.

        Each icon carries a :class:`ButtonHoverInfo` popup that fans out
        on hover (when the parent popup is pinned) and shows version,
        last user, last save, and last publish for that department.

        Returns ``None`` if no departments are configured for the
        context, so the caller knows to skip the section entirely.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGridLayout, QLabel, QWidget
        from tumbletrove.asset_browser.core.hover_info import (
            ButtonHoverInfo, format_age_html,
        )
        from tumbletrove.asset_browser.core.icons import icon_pixmap
        from tumbletrove.asset_browser.core.theme import (
            BORDER, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )
        from tumblepipe.config.department import list_departments

        context = "shots" if type_tag == "shot" else "assets"
        try:
            all_depts = [
                d for d in list_departments(
                    context, include_generated=False,
                )
                if d.enabled
            ]
        except Exception:
            return None
        if not all_depts:
            return None

        raw_depts = metadata.get("departments")
        active_versions: dict[str, str] = {}
        if isinstance(raw_depts, dict):
            for k, v in raw_depts.items():
                if isinstance(v, list) and v:
                    active_versions[k] = str(v[-1])
                elif isinstance(v, str) and v:
                    active_versions[k] = v
        elif isinstance(raw_depts, list):
            for d in raw_depts:
                active_versions[str(d)] = ""

        asset_id = getattr(asset, "id", None) if asset is not None else None
        workfiles = getattr(self, "_workfiles", None)

        active_color = TEXT_PRIMARY
        inactive_color = "#3e3e44"
        is_shot = type_tag == "shot"

        grid_widget = QWidget(parent)
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        # Min 3, max 4 columns. Below 4 depts we still reserve a 3-wide
        # grid so single-dept rows don't visually collapse to one icon.
        n_per_row = min(4, max(3, len(all_depts)))

        def _make_content_fn(dept_name, display_name, is_active, version):
            def _content():
                title = (
                    f"<div style='color:{TEXT_PRIMARY}; font-weight:600; "
                    f"font-size:13px;'>{display_name}</div>"
                )
                if is_active:
                    sub_text = f"Active · {version}" if version else "Active"
                else:
                    sub_text = "Inactive"
                subtitle = (
                    f"<div style='color:{TEXT_SECONDARY}; margin-top:2px;'>"
                    f"{sub_text}</div>"
                )

                rows: list[str] = []
                if is_active and asset_id and workfiles is not None:
                    if version:
                        try:
                            user = workfiles.get_user_for_version(
                                asset_id, dept_name, version,
                            )
                        except Exception:
                            user = None
                        if user:
                            rows.append(
                                f"<div style='margin-top:6px;'>"
                                f"<span style='color:{TEXT_DIM};'>User:</span> "
                                f"<span style='color:{TEXT_PRIMARY};'>"
                                f"{user}</span></div>"
                            )
                        try:
                            saved_dt = workfiles.get_mtime_for_version(
                                asset_id, dept_name, version,
                            )
                        except Exception:
                            saved_dt = None
                        saved_ts = (
                            saved_dt.timestamp() if saved_dt is not None
                            else None
                        )
                        rows.append(
                            f"<div style='margin-top:6px;'>"
                            f"{format_age_html('Last saved', saved_ts)}"
                            f"</div>"
                        )
                    try:
                        pub_dt = workfiles.get_latest_export_mtime(
                            asset_id, dept_name,
                        )
                    except Exception:
                        pub_dt = None
                    pub_ts = (
                        pub_dt.timestamp() if pub_dt is not None else None
                    )
                    rows.append(
                        f"<div style='margin-top:6px;'>"
                        f"{format_age_html('Last published', pub_ts)}"
                        f"</div>"
                    )

                sep = ""
                if rows:
                    sep = (
                        f"<div style='border-top:1px solid {BORDER}; "
                        f"margin:8px 0 0 0;'></div>"
                    )
                return title + subtitle + sep + "".join(rows)
            return _content

        for i, dept in enumerate(all_depts):
            row, col = divmod(i, n_per_row)
            icon_name = None
            if is_shot:
                icon_name = SHOT_DEPT_ICONS.get(dept.name)
            if icon_name is None:
                icon_name = DEPT_ICONS.get(dept.name, "package")
            is_active = dept.name in active_versions
            color = active_color if is_active else inactive_color
            pm = icon_pixmap(icon_name, 22, color)

            icon_label = QLabel(grid_widget)
            icon_label.setPixmap(pm)
            icon_label.setFixedSize(28, 28)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet("background: transparent;")
            icon_label.setAttribute(Qt.WA_Hover, True)

            display_name = (dept.short or dept.name).title()
            version = active_versions.get(dept.name) or "" if is_active else ""

            # ButtonHoverInfo replaces the plain QToolTip with a richer
            # popup (version, user, last save, last publish). Only fires
            # when the parent hover popup is pinned (cursor can't reach
            # the dept icons otherwise — see ButtonHoverInfo docstring).
            ButtonHoverInfo(
                icon_label,
                _make_content_fn(dept.name, display_name, is_active, version),
                placement="above",
            )

            grid.addWidget(icon_label, row, col)

        grid.setColumnStretch(n_per_row, 1)
        return grid_widget

    # ── Entity Creation ────────────────────────────────

    def get_creation_options(self, tags=frozenset()):
        from tumbletrove.asset_browser.api.catalog import CreationOption
        has_asset = "type:asset" in tags
        has_shot = "type:shot" in tags
        opts = []
        if has_asset or not has_shot:
            opts.append(CreationOption("new_asset", "New Asset...", "plus"))
        if has_shot or not has_asset:
            opts.append(CreationOption("new_shot", "New Shot...", "plus"))
        opts.append(CreationOption("new_group", "New Multi...", "users"))
        opts.append(CreationOption("new_scene", "New Root...", "layers"))
        return opts

    def _resolve_project_from_tags(self, tags):
        """Extract project name from tags like 'project:growth'."""
        for t in tags:
            if t.startswith("project:"):
                return t.split(":", 1)[1]
        # Single project fallback
        projects = list(self._registry.all())
        if len(projects) == 1:
            return projects[0].name
        return ""

    def get_creation_fields(self, option_id, tags=frozenset()):
        from tumbletrove.asset_browser.api.catalog import CreationField
        projects = [p.name for p in self._registry.all()]
        default_proj = self._resolve_project_from_tags(tags)

        if option_id == "new_asset":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            # Category dropdown — strict (no free-text). To create a new
            # category, use the right-click "New category…" action on the
            # Assets section header, which routes to ``new_category``.
            cats = (
                self._list_categories_for_project(default_proj)
                if default_proj else self._list_categories()
            )
            # When invoked from a category row's right-click menu the
            # caller passes a ``category:<name>`` tag; lock the field so
            # the user sees the bound context but can't redirect it.
            # The tag is lowercased (``category:char``) but URI segments
            # preserve case (``entity:/assets/CHAR/...``) — look up the
            # original-case label from the project's category list so we
            # don't silently spawn a duplicate lowercase category.
            default_cat = ""
            for t in tags:
                if t.startswith("category:"):
                    tag_val = t.split(":", 1)[1]
                    default_cat = next(
                        (c for c in cats if c.lower() == tag_val.lower()),
                        tag_val,
                    )
                    break
            fields.append(CreationField(
                "category", "Category",
                field_type="dropdown",
                choices=tuple(cats),
                default=default_cat,
                allow_new=False,
                readonly=bool(default_cat),
            ))
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_shot":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            seqs = (
                self._list_sequences_for_project(default_proj)
                if default_proj else self._list_sequences()
            )
            default_seq = ""
            for t in tags:
                if t.startswith("sequence:"):
                    default_seq = t.split(":", 1)[1]
                    break
            fields.append(CreationField(
                "sequence", "Sequence",
                field_type="dropdown",
                choices=tuple(seqs),
                default=default_seq,
                allow_new=False,
                readonly=bool(default_seq),
            ))
            fields.append(CreationField(
                "frame_start", "Frame Start",
                field_type="int", default="1001",
            ))
            fields.append(CreationField(
                "frame_end", "Frame End",
                field_type="int", default="1100",
            ))
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_category":
            # The only place free-text category entry survives. Reached
            # via right-click "New category…" on the Assets section
            # header. Creates an empty category-only entity
            # (``entity:/assets/<name>``) — tumblepipe supports
            # parent-only entities, so no first asset is required.
            fields = [
                CreationField("category", "Category", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_sequence":
            # Symmetric with new_category: creates an empty
            # sequence-only entity (``entity:/shots/<name>``). Shot
            # frame ranges live on individual shot entities, not the
            # sequence parent, so no frame fields here.
            fields = [
                CreationField("sequence", "Sequence", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_group":
            fields = [
                CreationField("name", "Name", required=True),
                CreationField(
                    "context", "Context",
                    field_type="dropdown",
                    choices=("shots", "assets"),
                    default="shots",
                ),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_scene":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        return []

    def create_entity(self, option_id, fields):
        import hou

        # Resolve project
        projects = list(self._registry.all())
        proj_name = fields.get("project", "")
        if not proj_name and len(projects) == 1:
            proj_name = projects[0].name
        proj = self._registry.get(proj_name)
        if proj is None:
            hou.ui.displayMessage(f"Unknown project: {proj_name}")
            return None

        self._activate_project(proj)
        try:
            client = self._clients.get(proj_name)
        except CatalogInitError as err:
            hou.ui.displayMessage(str(err))
            return None

        # Parent-only entity creation: ``new_category`` and
        # ``new_sequence`` register a 2-segment URI (no asset/shot name)
        # so the bucket exists in the sidebar even with no children
        # yet. tumblepipe supports this natively — see
        # ``ensure_sequence`` in tools/csv_shot_import.py.
        if option_id == "new_category":
            category = fields.get("category", "").strip()
            if not category:
                hou.ui.displayMessage("Category is required.")
                return None
            cat_uri = uris.entity_category(category)
            if client.config.get_properties(cat_uri) is not None:
                hou.ui.displayMessage(
                    f"Category '{category}' already exists."
                )
                return None
            client.config.add_entity(cat_uri, {})
            self._cached_assets = None
            return None  # No card to select — empty category

        if option_id == "new_sequence":
            sequence = fields.get("sequence", "").strip()
            if not sequence:
                hou.ui.displayMessage("Sequence is required.")
                return None
            seq_uri = uris.entity_sequence(sequence)
            if client.config.get_properties(seq_uri) is not None:
                hou.ui.displayMessage(
                    f"Sequence '{sequence}' already exists."
                )
                return None
            client.config.add_entity(seq_uri, {})
            self._cached_shots = None
            return None  # No card to select — empty sequence

        name = fields.get("name", "").strip()
        if not name:
            hou.ui.displayMessage("Name is required.")
            return None

        if option_id == "new_asset":
            category = fields.get("category", "").strip()
            if not category:
                hou.ui.displayMessage("Category is required.")
                return None
            entity_uri = uris.entity_asset(category, name)
            if client.config.get_properties(entity_uri) is not None:
                hou.ui.displayMessage(
                    f"Asset '{name}' already exists in '{category}'."
                )
                return None
            client.config.add_entity(entity_uri, {"name": name})
            self._cached_assets = None
            return f"{proj_name}/{category}/{name}"

        if option_id == "new_shot":
            sequence = fields.get("sequence", "").strip()
            if not sequence:
                hou.ui.displayMessage("Sequence is required.")
                return None
            frame_start = int(fields.get("frame_start", "1001"))
            frame_end = int(fields.get("frame_end", "1100"))
            entity_uri = uris.entity_shot(sequence, name)
            if client.config.get_properties(entity_uri) is not None:
                hou.ui.displayMessage(
                    f"Shot '{name}' already exists in '{sequence}'."
                )
                return None
            client.config.add_entity(
                entity_uri,
                {"frame_start": frame_start, "frame_end": frame_end},
            )
            self._cached_shots = None
            return f"{proj_name}/{sequence}/{name}"

        if option_id == "new_group":
            context = fields.get("context", "shots").strip()
            try:
                from tumblepipe.config import groups as grp_mod
                group_uri = uris.group_in_context(context, name)
                existing = grp_mod.get_group(group_uri)
                if existing is not None:
                    hou.ui.displayMessage(
                        f"Multi '{name}' already exists in {context}."
                    )
                    return None
                grp_mod.add_group(context, name, [], [])
                hou.ui.setStatusMessage(
                    f"Created Multi: {name} ({context})",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Failed to create group %s", name)
                hou.ui.displayMessage(f"Failed to create Multi: {name}")
                return None
            return None  # Groups aren't assets — no card to select

        if option_id == "new_scene":
            try:
                from tumblepipe.config import scenes as scn_mod
                scene_uri = uris.scene(name)
                existing = scn_mod.get_scene(scene_uri)
                if existing is not None:
                    hou.ui.displayMessage(
                        f"Root '{name}' already exists."
                    )
                    return None
                scn_mod.add_scene(name)
                hou.ui.setStatusMessage(
                    f"Created Root: {name}",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Failed to create scene %s", name)
                hou.ui.displayMessage(f"Failed to create Root: {name}")
                return None
            return None  # Scenes aren't assets — no card to select

        return None

    # ── Entity Edit / Delete ─────────────────────────────

    def get_edit_fields(self, asset_id: str) -> list[CreationField]:
        parts = self._resolver.split(asset_id)
        if parts is None:
            return []
        project_name, second, third = parts
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return []
        client = self._clients.get(project_name)
        try:
            props = client.config.get_properties(uri) or {}
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to read properties for {asset_id}: {exc}",
                cause=exc,
            ) from exc

        is_shot = str(uri).startswith("entity:/shots/")
        if is_shot:
            return [
                CreationField(
                    key="project", label="Project",
                    field_type="text", readonly=True,
                    initial=project_name, required=False,
                ),
                CreationField(
                    key="sequence", label="Sequence",
                    field_type="text", readonly=True,
                    initial=second, required=False,
                ),
                CreationField(
                    key="name", label="Name",
                    field_type="text", readonly=True,
                    initial=third, required=False,
                ),
                CreationField(
                    key="frame_start", label="Frame Start",
                    field_type="int",
                    initial=int(props.get("frame_start", 1001) or 1001),
                ),
                CreationField(
                    key="frame_end", label="Frame End",
                    field_type="int",
                    initial=int(props.get("frame_end", 1100) or 1100),
                ),
            ]
        return [
            CreationField(
                key="project", label="Project",
                field_type="text", readonly=True,
                initial=project_name, required=False,
            ),
            CreationField(
                key="category", label="Category",
                field_type="text", readonly=True,
                initial=second, required=False,
            ),
            CreationField(
                key="name", label="Name",
                field_type="text", readonly=True,
                initial=third, required=False,
            ),
        ]

    def edit_entity(self, asset_id, fields):
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return False
        parts = self._resolver.split(asset_id)
        if parts is None:
            return False
        project_name = parts[0]
        client = self._clients.get(project_name)
        writable = {}
        if "frame_start" in fields:
            try:
                writable["frame_start"] = int(fields["frame_start"])
            except (TypeError, ValueError):
                pass
        if "frame_end" in fields:
            try:
                writable["frame_end"] = int(fields["frame_end"])
            except (TypeError, ValueError):
                pass
        if not writable:
            return False
        try:
            client.config.set_properties(uri, writable)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to write properties for {asset_id}: {exc}",
                cause=exc,
            ) from exc
        self.invalidate_cache()
        return True

    def delete_entity(self, asset_id):
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return False
        parts = self._resolver.split(asset_id)
        if parts is None:
            return False
        project_name = parts[0]
        client = self._clients.get(project_name)
        try:
            client.config.remove_entity(uri)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to remove entity {asset_id}: {exc}",
                cause=exc,
            ) from exc
        self.invalidate_cache()
        return True

    def delete_bucket(
        self, project_name: str, kind: str, bucket_name: str,
    ) -> tuple[bool, str]:
        """Remove an empty category/sequence parent entity.

        ``kind`` is ``"category"`` or ``"sequence"``. Refuses if the
        bucket still has children (the UI surfaces the returned
        message). The bucket name comes from the row's label so it
        carries the original case — don't lowercase it again here.
        Returns ``(ok, message)``.
        """
        client = self._clients.get(project_name)
        if client is None:
            return False, f"project '{project_name}' not loaded"
        if kind == "category":
            child_count = self._count_for_project_category(
                project_name, bucket_name,
            )
            uri = uris.entity_category(bucket_name)
            child_word = "asset" if child_count == 1 else "assets"
            label = "category"
        elif kind == "sequence":
            child_count = self._count_for_project_sequence(
                project_name, bucket_name,
            )
            uri = uris.entity_sequence(bucket_name)
            child_word = "shot" if child_count == 1 else "shots"
            label = "sequence"
        else:
            return False, f"unknown bucket kind: {kind!r}"
        if child_count > 0:
            return (
                False,
                f"{label.capitalize()} '{bucket_name}' still contains "
                f"{child_count} {child_word}. Remove them first.",
            )
        if client.config.get_properties(uri) is None:
            # Bucket was inferred from URIs that have all been removed
            # already — nothing left to delete on disk, but tell the
            # caller to refresh.
            self._cached_assets = None
            self._cached_shots = None
            return True, f"{label.capitalize()} '{bucket_name}' was already empty."
        try:
            client.config.remove_entity(uri)
        except Exception as exc:
            return False, f"failed to remove {label} '{bucket_name}': {exc}"
        if kind == "category":
            self._cached_assets = None
        else:
            self._cached_shots = None
        return True, f"Deleted {label} '{bucket_name}'."

    # ── Group / Scene lifecycle ──────────────────────────

    def delete_collection(self, collection_id: str) -> bool:
        ref = containers.parse(collection_id)
        if ref is None:
            return False
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return False
        self._activate_project(proj)
        try:
            if isinstance(ref, GroupContainer):
                from tumblepipe.config import groups as grp_mod
                grp_mod.remove_group(ref.uri)
            else:
                from tumblepipe.config import scenes as scn_mod
                scn_mod.remove_scene(ref.uri)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to delete {ref.kind} {collection_id}: {exc}",
                cause=exc,
            ) from exc
        self.invalidate_cache()
        return True

    def get_collection_edit_fields(
        self, collection_id: str,
    ) -> list[CreationField]:
        ref = containers.parse(collection_id)
        if not isinstance(ref, GroupContainer):
            return []
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return []
        self._activate_project(proj)
        from tumblepipe.config import groups as grp_mod
        from tumblepipe.config import department as dept_mod
        grp = grp_mod.get_group(ref.uri)
        if grp is None:
            return []
        known_depts = tuple(
            d.name for d in
            dept_mod.list_departments(ref.context, include_generated=False)
        )
        current = tuple(str(d) for d in getattr(grp, "departments", ()))
        return [
            CreationField(
                key="departments",
                label="Departments",
                field_type="multi_select",
                choices=known_depts,
                initial=current,
                required=False,
            ),
        ]

    def edit_collection(
        self, collection_id: str, fields: dict,
    ) -> bool:
        ref = containers.parse(collection_id)
        if not isinstance(ref, GroupContainer):
            return False
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return False
        self._activate_project(proj)
        from tumblepipe.config import groups as grp_mod
        grp = grp_mod.get_group(ref.uri)
        if grp is None:
            return False
        current = {str(d) for d in getattr(grp, "departments", ())}
        new_set = set(fields.get("departments", ()) or ())
        to_add = new_set - current
        to_remove = current - new_set
        try:
            for d in to_add:
                grp_mod.add_department(ref.uri, d)
            for d in to_remove:
                grp_mod.remove_department(ref.uri, d)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to edit group {collection_id}: {exc}",
                cause=exc,
            ) from exc
        if to_add or to_remove:
            self._invalidate_membership_cache()
            # Multi card needs to reflect added/removed depts on its
            # deck. The collection id IS the card's drill tag, so we
            # can refresh the card directly.
            try:
                self._request_card_refresh_for_id(collection_id)
            except Exception:
                log.exception(
                    "card refresh failed after edit_collection %s",
                    collection_id,
                )
        return True

    def add_assets_to_collection(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        ref = containers.parse(collection_id)
        if ref is None:
            return (0, 0, "")
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return (0, 0, "")
        try:
            self._activate_project(proj)
        except Exception:
            return (0, 0, "")
        if isinstance(ref, GroupContainer):
            added, skipped, skip_reasons = self._containers._add_assets_to_group(
                ref, asset_ids,
            )
        else:
            added, skipped, skip_reasons = self._containers._add_assets_to_scene(
                ref, asset_ids,
            )
        if added:
            self._invalidate_membership_cache()
        total = added + skipped
        if skipped and added:
            msg = f"Added {added} of {total} — {skipped} skipped ({', '.join(sorted(skip_reasons))})"
        elif skipped:
            msg = f"Skipped {skipped} ({', '.join(sorted(skip_reasons))})"
        else:
            msg = f"Added {added}"
        return (added, skipped, msg)

    def remove_assets_from_collection(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        ref = containers.parse(collection_id)
        if ref is None:
            return (0, 0, "")
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return (0, 0, "")
        try:
            self._activate_project(proj)
        except Exception:
            return (0, 0, "")
        if isinstance(ref, GroupContainer):
            removed, skipped = self._containers._remove_assets_from_group(ref, asset_ids)
        else:
            result = self._containers._remove_assets_from_scene(ref, asset_ids)
            if result is None:
                return (0, len(asset_ids), "set_scene_assets failed")
            removed, skipped = result
        if removed:
            self._invalidate_membership_cache()
        if removed and skipped:
            msg = f"Removed {removed}, skipped {skipped}"
        elif skipped:
            msg = f"Skipped {skipped}"
        else:
            msg = f"Removed {removed}"
        return (removed, skipped, msg)

    # ── Per-Asset Actions ────────────────────────────────

    def get_actions(self, detail: AssetDetail) -> list[AssetAction]:
        actions = []

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
        if action_id == "open_export":
            path = self._resolve_export_path(detail.id if detail else "")
            if path and path.exists():
                import subprocess
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(path)],
                    creationflags=0x08000000,
                )

        elif action_id == "open_db_editor" and detail:
            self._open_database_editor(detail.id)

        elif action_id == "import_asset" and detail:
            self._import_asset_to_scene(detail)

        elif action_id.startswith("open_workfile:"):
            dept = action_id.split(":", 1)[1]
            target_id = detail.id if detail else ""
            if target_id.startswith("group:"):
                self._workfiles.open_group_workfile(target_id, dept)
            else:
                self._workfiles.open_workfile(target_id, dept)

    # ── Sub-cards (departments) ───────────────────────────

    def get_sub_cards(self, asset: Asset) -> list[DeckItem]:
        # Group container cards: one sub-card per dept the group
        # covers. "missing" status (no action_id) for covered depts
        # that don't have a workfile yet — right-click → "New:
        # Template" creates one. Active-version tracking against the
        # currently-loaded scene is deferred (groups don't have a
        # ``_get_scene_dept_version`` equivalent yet).
        if "type:group" in asset.tags:
            # ``departments`` on a group card is dict[str, str]: dept
            # name → latest version (empty string when uncovered).
            depts_dict = asset.metadata.get("departments") or {}
            covered = list(depts_dict.keys())
            ctx = (
                asset.context
                or self._containers._group_context_from_tag(asset.id)
                or "shots"
            )
            canonical = self._list_entity_departments(ctx)
            order = {name: i for i, name in enumerate(canonical)}
            sorted_depts = sorted(
                covered,
                key=lambda n: (order.get(n, len(order)), n),
            )
            cards: list[DeckItem] = []
            for dept_name in sorted_depts:
                short = DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
                latest = depts_dict.get(dept_name) or ""
                if latest:
                    cards.append(DeckItem(
                        key=dept_name,
                        label=short,
                        status="available",
                        detail=latest,
                        icon=DEPT_ICONS.get(dept_name, "package"),
                        action_id=f"open_workfile:{dept_name}",
                        dismiss_on_click=True,
                    ))
                else:
                    cards.append(DeckItem(
                        key=dept_name,
                        label=short,
                        status="missing",
                        icon=DEPT_ICONS.get(dept_name, "package"),
                    ))
            return cards

        depts = asset.metadata.get("departments", {})

        cards = []
        # Get all possible departments for this entity type
        is_shot = "type:shot" in asset.tags
        all_depts = self._list_entity_departments("shots" if is_shot else "assets")

        # Detect the loaded scene's dept so we can mark it "active".
        scene_dv = self._scene.get_scene_dept_version(asset.id)
        active_dept = scene_dv[0] if scene_dv else None

        # Group coverage: depts where this member's workfile is
        # superseded by a group's multi-shot workfile. The sub-card
        # detail line shows "ⓖ GroupLabel" instead of a version, and
        # the click action route through ``open_workfile:<dept>`` —
        # which now resolves via ``latest_hip_file_path_with_context``
        # and lands on the group's hip automatically.
        dept_groups = self._containers._dept_groups_for_member(asset.id)

        def _icon_for(dept: str) -> str:
            if is_shot and dept in SHOT_DEPT_ICONS:
                return SHOT_DEPT_ICONS[dept]
            return DEPT_ICONS.get(dept, "package")

        for dept_name in all_depts:
            short = DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
            version = depts.get(dept_name)
            group_info = dept_groups.get(dept_name)
            if group_info:
                _group_id, group_label = group_info
                status = (
                    "active" if dept_name == active_dept else "available"
                )
                # Detail text stays short — sub-cards are narrow, so a
                # long group name overflows. The tint (border + icon)
                # signals "covered by a group"; the tooltip carries
                # the full group name for hover discovery.
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail="ⓜ",
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
                    dismiss_on_click=True,
                    tint=_GROUP_ACCENT_COLOR,
                    tooltip=(
                        f"{short} — covered by Multi: {group_label}\n"
                        "Click to open the Multi's workfile"
                    ),
                ))
            elif version:
                status = (
                    "active" if dept_name == active_dept else "available"
                )
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail=version,
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
                    dismiss_on_click=True,
                ))
            else:
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status="missing",
                    icon=_icon_for(dept_name),
                ))

        return cards

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
        return [
            ListColumn(key="name", label="Name"),
            ListColumn(key="category", label="Category", width=100),
            ListColumn(key="dept_count", label="Depts", width=60, align="center"),
        ]

    # ── Cache invalidation ────────────────────────────────

    def invalidate_cache(self) -> None:
        """Drop discovered asset/shot caches; next query re-fetches.

        Also reloads each already-built project config's on-disk entity
        snapshot. The ``ProjectConfigConvention`` reads ``db/*.json``
        once in ``__init__`` and ``list_entities`` serves purely from
        that in-memory ``self.cache`` — so without this, a "refresh"
        re-runs discovery against a stale snapshot and never sees
        entities written out-of-process (another Houdini session, the
        project browser, another artist). Reloading the config here is
        far cheaper than rebuilding every Client (the Shift+Click "hard"
        reset path), which is the only thing that previously picked up
        external additions.
        """
        self._cached_assets = None
        self._cached_shots = None
        self._containers.invalidate_membership_cache()
        for name, client in self._clients.ready.items():
            config = getattr(client, "config", None)
            refresh = getattr(config, "refresh_cache", None)
            if not callable(refresh):
                continue
            try:
                refresh()
            except Exception:
                log.exception(
                    "config.refresh_cache failed for project %s", name,
                )

    def _invalidate_membership_cache(self) -> None:
        """Drop only the member-coverage cache.

        Membership changes don't alter ``_cached_assets`` /
        ``_cached_shots`` — those enumerate which entities *exist*, not
        who they belong to. Skipping their bust spares a costly
        filesystem rediscovery pass on the next sidebar / grid query
        (which dominates the perceived latency of add/remove ops).
        """
        self._containers.invalidate_membership_cache()

    # ── Container operations (delegated to ContainerManager) ──
    #
    # The asset browser invokes these by name on the catalog instance —
    # the four Root context-menu actions plus membership lookup. The
    # implementations live on ContainerManager (see _pipeline_containers);
    # these thin shims keep the framework-facing surface on the catalog.

    def open_container_location(self, collection_id: str) -> bool:
        return self._containers.open_container_location(collection_id)

    def list_root_assigned_shots(self, collection_id: str) -> list:
        return self._containers.list_root_assigned_shots(collection_id)

    def rebuild_root_assigned_shots(
        self, collection_id: str,
    ) -> tuple[int, list]:
        return self._containers.rebuild_root_assigned_shots(collection_id)

    def export_root_usd(self, collection_id: str) -> bool:
        return self._containers.export_root_usd(collection_id)

    def get_asset_membership(
        self, asset_id: str,
    ) -> list[tuple[str, str, str]]:
        return self._containers.get_asset_membership(asset_id)

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

    # ── Internal helpers ──────────────────────────────────

    def _get_all_items(self) -> list[Asset]:
        """List assets + shots (across projects)."""
        if (
            self._cached_assets is not None
            and self._cached_shots is not None
        ):
            return self._cached_assets + self._cached_shots
        assets, shots = self._discover_entities()
        # Don't cache an empty result when no clients were ready yet —
        # a transient "no projects loaded" state would otherwise stick
        # forever and require a manual refresh to recover.
        has_projects = any(True for _ in self._registry.all())
        no_clients_ready = not self._clients.ready
        if (
            not assets and not shots
            and has_projects and no_clients_ready
        ):
            return []
        self._cached_assets = assets
        self._cached_shots = shots
        return self._cached_assets + self._cached_shots

    def _discover_entities(self) -> tuple[list[Asset], list[Asset]]:
        """Aggregate assets and shots from every registered project in
        one pass over ``list_entities``.

        Per-project failures are recorded on ``self._discovery_errors``
        (cleared at the start of each browse) so the consumer can
        surface them via :class:`AssetPage.errors`. Returns
        ``(assets, shots)`` — two lists sharing one pass per project,
        which matters because ``list_entities`` is a remote-ish call.
        """
        assets_by_id: dict[str, Asset] = {}
        shots_by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client, init_err = self._clients.try_get(proj.name)
            if init_err is not None:
                self._discovery_errors.append(init_err)
                continue
            assert client is not None
            # Activate this project's env so latest_export_path resolves
            # against the right config.
            self._activate_project(proj)
            try:
                all_entities = client.config.list_entities(None, closure=True)
            except Exception as exc:
                self._discovery_errors.append(AssetDiscoveryError(
                    self.id,
                    f"list_entities failed for {proj.name}: {exc}",
                    cause=exc,
                ))
                continue
            for entity in all_entities:
                segs = entity.uri.segments
                if len(segs) < 3:
                    continue
                kind = segs[0]
                if kind == "assets":
                    card = self._build_asset_card(proj, segs)
                    assets_by_id.setdefault(card.id, card)
                elif kind == "shots":
                    card = self._build_shot_card(proj, entity, segs)
                    shots_by_id.setdefault(card.id, card)
        return list(assets_by_id.values()), list(shots_by_id.values())

    def _build_asset_card(
        self, proj: ProjectConfig, segs: tuple[str, ...],
    ) -> Asset:
        category = segs[1]
        name = segs[2]
        asset_id = f"{proj.name}/{category}/{name}"

        # Use our own filesystem-based scan instead of
        # ``latest_export_path`` — that helper caches
        # ``TH_PROJECT_PATH`` at import time and returns paths under
        # the wrong project for non-launch projects.
        # ``_get_department_workfile_info`` returns a
        # ``{dept: [versions]}`` mapping; collapse to
        # ``{dept: latest_version}`` for the deck popup label.
        workfile_info = self._get_department_workfile_info(asset_id)
        depts = {
            dept: versions[-1]
            for dept, versions in workfile_info.items()
            if versions
        }

        return Asset(
            id=asset_id,
            name=name,
            thumbnail_url="",
            tags=frozenset({
                "source:pipeline",
                "type:asset",
                f"category:{category.lower()}",
                f"project:{proj.name}",
            }),
            has_sub_cards=True,
            metadata={
                "departments": depts,
                "category": category,
                "project": proj.name,
                "dept_count": len(depts),
                "latest_update": self._workfiles.latest_update_timestamp(
                    asset_id, depts,
                ),
            },
        )

    def refresh_single_asset(self, asset_id: str):
        """Rebuild an Asset object with fresh workfile info.

        Returns a new Asset with updated department versions, or None.
        Used for lightweight card refresh after save/create without
        rebuilding the entire grid.
        """
        # Container ids (groups/scenes) go through a separate rebuild
        # path — they're synthesised, not discovered from list_entities.
        if asset_id.startswith("group:"):
            return self._refresh_group_asset(asset_id)
        if asset_id.startswith("scene:"):
            return self._refresh_scene_asset(asset_id)

        parts = self._resolver.split(asset_id)
        if parts is None:
            return None
        project_name, second, third = parts
        proj = self._registry.get(project_name)
        if proj is None:
            return None

        self._activate_project(proj)
        workfile_info = self._get_department_workfile_info(asset_id)
        depts = {
            dept: versions[-1]
            for dept, versions in workfile_info.items()
            if versions
        }

        cats = self._list_categories_for_project(project_name)
        is_asset = second in cats
        tags = {
            "source:pipeline",
            f"type:{'asset' if is_asset else 'shot'}",
            f"{'category' if is_asset else 'sequence'}:{second.lower()}",
            f"project:{project_name}",
        }

        from tumbletrove.asset_browser.api.catalog import Asset
        return Asset(
            id=asset_id,
            name=third,
            thumbnail_url="",
            tags=frozenset(tags),
            has_sub_cards=True,
            metadata={
                "departments": depts,
                **({"category": second, "project": project_name} if is_asset
                   else {"sequence": second, "project": project_name}),
                "dept_count": len(depts),
                "latest_update": self._workfiles.latest_update_timestamp(
                    asset_id, depts,
                ),
            },
            catalog_id=self.id,
        )

    def _refresh_group_asset(self, group_id: str):
        """Rebuild a Multi container Asset (post-create / -edit).

        Re-fetches the group's departments and the latest workfile
        version per dept so the deck-popup sub-cards switch from
        "missing" to "available" without a hard grid refresh.
        """
        try:
            _, rest = group_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return None
        proj = self._registry.get(proj_name)
        if proj is None or not self._clients.is_ready(proj_name):
            return None
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
        except Exception:
            log.debug("group refresh imports failed", exc_info=True)
            return None
        try:
            grp = grp_mod.get_group(uris.group(path))
        except Exception:
            log.debug("get_group failed for %s", group_id, exc_info=True)
            return None
        if grp is None:
            return None

        ctx_seg = path.split("/", 1)[0] if "/" in path else "assets"
        label = path.rsplit("/", 1)[-1] if path else group_id
        members = list(getattr(grp, "members", ()))
        # Drop the cached coverage so other shots/assets pick up
        # member-list changes on their next sub-card render too.
        self._containers.invalidate_membership_cache()

        # Build a stand-in Collection just to drive the synthesizer —
        # the only fields the synthesizer reads are ``tag``, ``label``,
        # and ``count``.
        proxy = Collection(
            id=group_id,
            label=label,
            tag=group_id,
            count=len(members),
            kind="group",
        )
        asset = self._containers._container_collection_to_asset(proxy, proj_name, "group")
        # replace() carries every typed framework field (kind, drill_tag,
        # member_count, has_sub_cards, …) across — only catalog_id changes.
        return dataclasses.replace(asset, catalog_id=self.id)

    def _refresh_scene_asset(self, scene_id: str):
        """Rebuild a Root container Asset (no workfile detail today —
        scenes don't have per-dept workfiles — but the count and tag
        set still need to be fresh after edits)."""
        try:
            _, rest = scene_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return None
        proj = self._registry.get(proj_name)
        if proj is None or not self._clients.is_ready(proj_name):
            return None
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
        except Exception:
            return None
        try:
            scn = scn_mod.get_scene(uris.scene(path))
        except Exception:
            return None
        if scn is None:
            return None
        label = path.rsplit("/", 1)[-1] if path else scene_id
        count = len(getattr(scn, "assets", ()))
        proxy = Collection(
            id=scene_id,
            label=label,
            tag=scene_id,
            count=count,
            kind="scene",
        )
        asset = self._containers._container_collection_to_asset(proxy, proj_name, "scene")
        # replace() carries the typed framework fields across; only the
        # catalog_id stamp changes.
        return dataclasses.replace(asset, catalog_id=self.id)

    def _build_shot_card(
        self, proj: ProjectConfig, entity, segs: tuple[str, ...],
    ) -> Asset:
        sequence = segs[1]
        shot_name = segs[2]
        asset_id = f"{proj.name}/{sequence}/{shot_name}"

        # See _build_asset_card: use our own filesystem scan instead of
        # latest_export_path to dodge the cross-project
        # tumblehead.pipe.paths cache.
        workfile_info = self._get_department_workfile_info(asset_id)
        depts = {
            dept: versions[-1]
            for dept, versions in workfile_info.items()
            if versions
        }

        frame_range = ""
        try:
            from tumblepipe.config.timeline import get_frame_range
            fr = get_frame_range(entity.uri)
        except Exception as exc:
            # Skip frame range for this entity rather than tanking the
            # whole shot discovery — but log so the failure is visible.
            log.warning(
                "frame range lookup failed for %s: %s", entity.uri, exc,
            )
            fr = None
        if fr is not None:
            start = fr.start_frame
            end = fr.end_frame
            if start is not None and end is not None:
                frame_range = f"{start}-{end}"

        return Asset(
            id=asset_id,
            name=f"{sequence}_{shot_name}",
            thumbnail_url="",
            tags=frozenset({
                "source:pipeline",
                "type:shot",
                f"sequence:{sequence}",
                f"project:{proj.name}",
            }),
            has_sub_cards=True,
            metadata={
                "departments": depts,
                "sequence": sequence,
                "project": proj.name,
                "frame_range": frame_range,
                "dept_count": len(depts),
                "latest_update": self._workfiles.latest_update_timestamp(
                    asset_id, depts,
                ),
            },
        )

    def _list_categories_for_project(self, project_name: str) -> list[str]:
        """Return the asset categories registered for ``project_name``.

        Returns empty list when the Client isn't yet READY (so the
        sidebar can render before background init finishes). Raises
        :class:`ConfigError` if the underlying enumeration fails.
        """
        if not self._clients.is_ready(project_name):
            return []
        client = self._clients.get(project_name)
        try:
            entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to list categories for {project_name}: {exc}",
                cause=exc,
            ) from exc
        # Include both populated categories (``assets/<cat>/<asset>``,
        # ≥3 segments) AND empty category-only entities
        # (``assets/<cat>``, exactly 2 segments) registered via
        # ``add_entity`` on the parent URI. The browser previously only
        # surfaced categories that had at least one asset, hiding
        # standalone ones that tumblepipe supports natively.
        return sorted({
            e.uri.segments[1]
            for e in entities
            if len(e.uri.segments) >= 2 and e.uri.segments[0] == "assets"
        })

    def _list_sequences_for_project(self, project_name: str) -> list[str]:
        """Return the shot sequences registered for ``project_name``.

        See :meth:`_list_categories_for_project` for the contract.
        """
        if not self._clients.is_ready(project_name):
            return []
        client = self._clients.get(project_name)
        try:
            entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to list sequences for {project_name}: {exc}",
                cause=exc,
            ) from exc
        # Same as _list_categories_for_project: include 2-segment
        # sequence-only entities, not just sequences that have shots.
        return sorted({
            e.uri.segments[1]
            for e in entities
            if len(e.uri.segments) >= 2 and e.uri.segments[0] == "shots"
        })

    def _list_categories(self) -> list[str]:
        """Aggregate categories across every initialized project."""
        out: set[str] = set()
        for proj in self._registry.all():
            out.update(self._list_categories_for_project(proj.name))
        return sorted(out)

    def _list_sequences(self) -> list[str]:
        """Aggregate sequences across every initialized project."""
        out: set[str] = set()
        for proj in self._registry.all():
            out.update(self._list_sequences_for_project(proj.name))
        return sorted(out)

    def _list_entity_departments(self, context: str) -> list[str]:
        """List department names for 'assets' or 'shots'."""
        try:
            from tumblepipe.config.department import list_departments
            return [d.name for d in list_departments(context, include_generated=False)]
        except Exception:
            if context == "assets":
                return ["model", "lookdev", "rig"]
            return ["layout", "animation", "lighting", "render", "comp"]

    def _list_entity_dept_shorts(self, context: str) -> dict[str, str]:
        """Return ``{dept_name: short}`` for every department, preferring
        the API-declared short when present and falling back to
        :data:`DEPT_API_SHORT_FALLBACK` (matched case-insensitively).
        Departments with no resolvable short are simply omitted."""
        result: dict[str, str] = {}
        try:
            from tumblepipe.config.department import list_departments
            depts = list_departments(context, include_generated=False)
        except Exception:
            return result
        for d in depts:
            if d.short:
                result[d.name] = d.short
                continue
            fallback = DEPT_API_SHORT_FALLBACK.get(d.name.lower())
            if fallback:
                result[d.name] = fallback
        return result

    def _count_assets_in_category(self, category: str) -> int:
        items = self._get_all_items()
        return sum(1 for a in items if f"category:{category.lower()}" in a.tags and "type:asset" in a.tags)

    def _count_shots_in_sequence(self, sequence: str) -> int:
        items = self._get_all_items()
        return sum(1 for a in items if f"sequence:{sequence}" in a.tags)

    def _get_department_workfile_info(self, asset_id: str) -> dict[str, list[str]]:
        """Return ``{dept: [workfile_versions]}`` parsed from .hip filenames.

        Workfile filenames follow ``{prefix}_{version}.hip[nc|lc]``.
        We extract the trailing ``vNNNN`` token from the stem; this
        matches the convention used by ``next_hip_file_path``.
        """
        parsed = self._resolver.split(asset_id)
        if parsed is None:
            return {}
        project_name, second, third = parsed
        root = self._resolver.root_for(asset_id)
        if root is None:
            return {}
        try:
            cats = self._list_categories_for_project(project_name)
            if second in cats:
                base = root / "assets" / second / third
                context = "assets"
            else:
                base = root / "shots" / second / third
                context = "shots"

            result: dict[str, list[str]] = {}
            for dept in self._list_entity_departments(context):
                versions = workfile_versions(base / dept)
                if versions:
                    result[dept] = versions
            return result
        except OSError as exc:
            raise WorkfileScanError(
                self.id,
                f"failed to scan workfile versions for {asset_id}: {exc}",
                cause=exc,
            ) from exc

    def _get_department_info(self, asset_id: str) -> dict[str, list[str]]:
        """Get {dept_name: [versions]} for an asset/shot (publish versions)."""
        parsed = self._resolver.split(asset_id)
        if parsed is None:
            return {}
        project_name, second, third = parsed
        proj = self._registry.get(project_name)
        if proj is None:
            return {}
        # Activate the project so latest_export_path resolves against
        # its config rather than whichever project happens to be in env.
        self._activate_project(proj)
        try:
            from tumblepipe.pipe.paths import (
                list_version_paths, latest_export_path,
            )
        except Exception:
            return {}
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return {}
        cats = self._list_categories_for_project(project_name)
        context = "assets" if second in cats else "shots"
        try:
            result: dict[str, list[str]] = {}
            for dept in self._list_entity_departments(context):
                path = latest_export_path(uri, "default", dept)
                if path:
                    versions = list_version_paths(path.parent)
                    result[dept] = [v.name for v in versions]
            return result
        except Exception:
            log.debug("Failed to get department info for %s", asset_id)
            return {}

    def _resolve_export_path(self, asset_id: str) -> Path | None:
        if not asset_id:
            return None
        client = self._resolver.client_for(asset_id)
        if client is None:
            return None
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return None
        try:
            export_uri = uris.export_for_entity(uri)
            return client.storage.resolve(export_uri)
        except Exception:
            return None

    def _import_asset_to_scene(self, detail: AssetDetail) -> None:
        """Create a th::import_asset::1.0 LOP in /stage for the asset.

        ``execute_action`` is invoked on a worker thread, but ``hou.node``
        / ``createNode`` are not thread-safe — dispatch to the GUI loop.
        """

        from _pipeline_drops import entity_uri_for
        name = detail.name.replace(" ", "_")
        asset_id = detail.id
        entity_uri = entity_uri_for(detail)

        def _do_import():
            try:
                import hou
                stage = hou.node("/stage")
                if stage is None or entity_uri is None:
                    return
                from tumblepipe.pipe.houdini.lops import import_asset
                node = import_asset.create(stage, name)
                node.set_asset_uri(uris.parse(entity_uri))
                node.execute()
                raw = node.native()
                raw.setDisplayFlag(True)
                raw.setSelected(True, clear_all_selected=True)
            except Exception:
                log.exception("Failed to import asset %s", asset_id)

        run_on_main_thread(_do_import)

