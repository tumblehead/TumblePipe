"""Pipeline catalog — browse production assets and shots from any
number of registered Tumblehead projects.

Projects are stored in
``~/.config/asset_browser/projects.json`` (see
:class:`asset_browser.core.projects.ProjectRegistry`). Each entry holds
``project_path`` and ``config_path``; the registry's ``pipeline_path``
field is ignored at runtime. The active TumblePipe install is read from
``$TH_PIPELINE_PATH`` (set globally by hpm via the package's ``[env]``
block) so it tracks hpm upgrades automatically.

The ``TH_*`` env vars are authoritative for the launch session: every
``create_catalog`` call passes them through
:meth:`ProjectRegistry.bootstrap_from_env`, which adds the env-driven
project on first run and refreshes its paths on subsequent runs if
they've changed (e.g. config dir renamed ``_config`` → ``_config2``).
``projects.json`` is just an off-session cache so non-env-launched
sessions can browse the same projects.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# tumbletrove's registry loads this module via
# ``importlib.util.spec_from_file_location`` for external catalogs,
# which deliberately does not attach the module to a package — so
# relative imports (``from ._pipeline_types import ...``) fail with
# ImportError. Adding our own directory to sys.path lets the companion
# module be imported absolutely, the same way external catalogs are
# documented to import their helpers.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from asset_browser.api.catalog import Catalog
from asset_browser.api.errors import (
    AssetDiscoveryError,
    CatalogError,
    CatalogInitError,
    ConfigError,
    DetailBuildError,
    TagQueryError,
    WorkfileScanError,
)
from asset_browser.api.types import (
    Asset,
    AssetAction,
    AssetDetail,
    AssetPage,
    Collection,
    CreationField,
    DetailContext,
    DetailSection,
    ListColumn,
    SortOption,
    SubCard,
)
from asset_browser.core.projects import ProjectConfig, ProjectRegistry

# Value types and module-level utilities live in a companion module so
# this file stays focused on the catalog implementation. Re-exported
# under their historical names for backwards compatibility within this
# file. Absolute import (rather than ``from ._pipeline_types``) because
# tumbletrove's external-catalog discovery loads pipeline.py without a
# parent package — see the sys.path tweak above.
from _pipeline_types import (  # noqa: E402
    DEPT_ICONS as _DEPT_ICONS,
    DEPT_SHORT_NAMES as _DEPT_SHORT_NAMES,
    PERMANENT_INIT_ERRORS as _PERMANENT_INIT_ERRORS,
    SHOT_DEPT_ICONS as _SHOT_DEPT_ICONS,
    AssetId,
    ClientSlot,
    ClientState,
    cascade_counts as _cascade_counts,
    projects_json_path as _projects_json_path,
)

log = logging.getLogger(__name__)

# Group accent — matches ``TYPE_COLORS["group"]`` in asset_browser's
# theme.py. Reused as the sub-card tint when a shot/asset dept is
# superseded by a group workfile.
_GROUP_ACCENT_COLOR = "#e08c4a"


def create_catalog():
    """Factory function — called by the registry on discovery.

    Returns a :class:`PipelineCatalog` whenever there's at least one
    registered project (either persisted in ``projects.json`` or
    bootstrappable from ``TH_PROJECT_PATH``). Returns ``None`` when no
    pipeline configuration is available so the catalog disappears
    cleanly from the dropdown.

    Project scoping: when ``TH_PROJECT_PATH`` is set (i.e. Houdini was
    launched from a project ``.bat``), the catalog exposes ONLY that
    project — other persisted projects stay on disk but are hidden for
    this session. The project is also auto-registered to disk on first
    launch so subsequent manual-launch sessions can see it alongside
    other registered projects.

    Fails closed: any exception in registry construction is logged and
    causes the catalog to be skipped. The asset browser invokes this on
    Houdini's main thread during startup, so a raised exception here
    would otherwise propagate up and stall Houdini load.
    """
    try:
        registry = ProjectRegistry(_projects_json_path())
        registry.load()
        # Add the env-driven project on first run, and refresh its
        # paths on subsequent runs if TH_* env vars have changed since
        # they were last cached. The env vars are authoritative for the
        # launch session — projects.json is just the off-session cache.
        registry.bootstrap_from_env()

        env_proj = os.environ.get("TH_PROJECT_PATH", "").strip()
        if env_proj:
            env_name = Path(env_proj).name or "default"
            if env_name in registry.names:
                # Scope this session to the launch-project only.
                scoped = ProjectRegistry(_projects_json_path())
                entry = registry.get(env_name)
                if entry is not None:
                    scoped.add(entry, save=False)
                registry = scoped

        if not registry:
            log.debug(
                "Pipeline catalog skipped — no projects registered and "
                "TH_PROJECT_PATH not set",
            )
            return None
        return PipelineCatalog(registry)
    except Exception:
        log.exception("Pipeline catalog skipped — registry load failed")
        return None


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
        if self._launch_project_name:
            return frozenset({f"project:{self._launch_project_name}"})
        return frozenset()

    # ── Lifecycle ─────────────────────────────────────────

    def __init__(self, registry: ProjectRegistry | None = None) -> None:
        self._registry = registry or ProjectRegistry(_projects_json_path())
        if registry is None:
            self._registry.load()
            if not self._registry:
                self._registry.bootstrap_from_env()
                if self._registry:
                    try:
                        self._registry.save()
                    except Exception:
                        log.exception("Failed to save bootstrapped projects.json")

        # Global pipeline catalog prefs (autosave-on-scene-change, …).
        # Loaded once on init; mutators go through set_prefs() so on-disk
        # JSON stays in sync. See _pipeline_prefs.py.
        # Absolute import (rather than ``from ._pipeline_prefs``)
        # because tumbletrove's external-catalog discovery loads
        # pipeline.py without a parent package — see the sys.path
        # tweak at the top of this file.
        from _pipeline_prefs import load_prefs  # noqa: E402
        self._prefs = load_prefs()

        # Per-project Client instances. Built eagerly on the calling
        # thread (typically the GUI thread during pypanel creation).
        # Building Clients on background threads while Houdini's
        # pypanel construction is also running races against HDA
        # loading and crashes the process — see the long history
        # of attempts in this file's git log.
        # Per-project Client lifecycle. Replaces the historical
        # _clients dict + _init_attempted set, which conflated "not yet
        # built" with "build failed" and prevented retries on transient
        # failures. See ClientSlot / ClientState above.
        self._client_slots: dict[str, ClientSlot] = {}
        # Serializes Client construction — tumblepipe's Client
        # constructor reads TH_* env vars from its config_convention.py,
        # so two concurrent builds race on the env and cross-wire each
        # other's paths into the wrong config.
        self._build_client_lock = threading.Lock()
        # Errors accumulated during the most recent discovery pass.
        # Drained by ``drain_discovery_errors`` so the QueryEngine can
        # attach them to the AssetPage. A new browse begins by clearing
        # this list (see ``get_assets``).
        self._discovery_errors: list[CatalogError] = []

        # Discovery cache (merged across projects). Invalidated by
        # project add / remove / refresh.
        self._cached_assets: list[Asset] | None = None
        self._cached_shots: list[Asset] | None = None

        # Per-asset, per-department version override.
        # Shape: {asset_id: {dept_name: version_label}}
        self._dept_version_overrides: dict[str, dict[str, str]] = {}

        # Tracks whether we've already warned the user about activating
        # a project that doesn't match Houdini's launch project. The
        # warning is one-shot per session.
        self._launch_project_path = os.environ.get(
            "TH_PROJECT_PATH", "",
        ).strip()
        self._launch_project_name = ""
        if self._launch_project_path:
            try:
                self._launch_project_name = Path(
                    self._launch_project_path
                ).name
            except Exception:
                pass
        self._activation_warned = False

        # Tracks the project_path currently bound to the tumblehead
        # default_client + ``TH_*`` env. ``_activate_project`` skips
        # its expensive reset/rebuild when the requested project
        # already matches both this attribute and the live env var.
        # ``None`` means "no activation has happened yet" — the first
        # call must run the full patching pass even if the project
        # matches the launch env, because tumblepipe modules cached at
        # import time may still hold a stale ``api`` reference.
        self._active_project_path: str | None = None

        # Client construction is deferred until the first asset-browse
        # call. ``initialize()`` is intentionally a no-op (Houdini 22
        # runs it on the main thread during startup, so eager building
        # there would block Houdini load on per-project ``Path.exists``
        # SMB timeouts). Clients are built on demand by
        # ``_get_or_build_client`` / ``_try_get_client`` from
        # ``get_assets`` and the per-action helpers, which run on the
        # asset-browser worker thread.

    # ── Per-project init ──────────────────────────────────

    def _slot(self, project_name: str) -> ClientSlot:
        """Return (creating if missing) the slot for ``project_name``."""
        slot = self._client_slots.get(project_name)
        if slot is None:
            slot = ClientSlot()
            self._client_slots[project_name] = slot
        return slot

    def _build_client_blocking(self, proj: ProjectConfig):
        """Construct one project's :class:`tumblehead.api.Client`
        synchronously on the calling thread.

        Idempotent for READY slots. FAILED_PERMANENT slots re-raise the
        stored error. FAILED_TRANSIENT and UNTRIED slots attempt
        (re-)construction.

        Construction is serialised against a catalog-wide lock because
        tumblepipe's ``Client`` reads ``TH_*`` env vars inside its
        ``ProjectConfigConvention`` ``__init__``, so two concurrent
        builds race on the env and cross-wire each other's config.

        Raises:
            CatalogInitError: when construction fails. The slot is
                updated to FAILED_TRANSIENT or FAILED_PERMANENT
                depending on the underlying cause.
        """
        slot = self._slot(proj.name)
        if slot.state is ClientState.READY:
            return slot.client
        if slot.state is ClientState.FAILED_PERMANENT:
            assert slot.error is not None
            raise slot.error

        with self._build_client_lock:
            slot = self._slot(proj.name)
            if slot.state is ClientState.READY:
                return slot.client
            if slot.state is ClientState.FAILED_PERMANENT:
                assert slot.error is not None
                raise slot.error
            return self._build_client_locked(proj, slot)

    def _build_client_locked(
        self, proj: ProjectConfig, slot: ClientSlot,
    ):
        """Lock-held construction. Updates ``slot`` and returns the
        Client on success; raises :class:`CatalogInitError` on failure.
        """
        import time
        slot.last_attempt = time.time()
        try:
            import sys
            # TH_PIPELINE_PATH is owned by hpm — set in the package's
            # [env] block to the active install. Never per-project.
            # Missing key here means hpm hasn't activated the package;
            # that's a permanent failure (KeyError).
            pipeline_path = os.environ["TH_PIPELINE_PATH"]
            py_path = str(
                Path(pipeline_path) / "houdini" / "TumblePipe" / "python"
            )
            if py_path not in sys.path:
                sys.path.insert(0, py_path)

            # Browsing must NOT mutate process env. The resolver
            # (resolver-src/src/env.rs) and tumblepipe.api free-functions
            # (get_project_path/get_pipeline_path/...) read TH_* env to
            # determine the *user-active* project — the one whose hip
            # file is open. That signal is owned by ``_activate_project``;
            # constructing per-project Clients here is a passive lookup
            # and feeds Client via explicit args below.
            from tumblepipe.api import Client
            client = Client(
                Path(proj.project_path),
                Path(pipeline_path),
                Path(proj.config_path),
            )
        except BaseException as exc:
            permanent = isinstance(exc, _PERMANENT_INIT_ERRORS)
            err = CatalogInitError(
                self.id,
                str(exc) or type(exc).__name__,
                project=proj.name,
                cause=exc,
            )
            slot.state = (
                ClientState.FAILED_PERMANENT
                if permanent
                else ClientState.FAILED_TRANSIENT
            )
            slot.client = None
            slot.error = err
            log.exception(
                "Pipeline API init failed for project %s "
                "(state=%s)", proj.name, slot.state.name,
            )
            raise err from exc

        slot.state = ClientState.READY
        slot.client = client
        slot.error = None
        log.info(
            "Pipeline API initialized for %s: %s",
            proj.name, client.PROJECT_PATH,
        )
        return client

    def _get_or_build_client(self, project_name: str):
        """Return the Client for ``project_name`` (building on demand).

        Raises :class:`CatalogInitError` if the project isn't registered
        or the underlying Client construction fails.
        """
        slot = self._client_slots.get(project_name)
        if slot is not None and slot.state is ClientState.READY:
            return slot.client
        proj = self._registry.get(project_name)
        if proj is None:
            raise CatalogInitError(
                self.id,
                f"unknown project {project_name!r}",
            )
        return self._build_client_blocking(proj)

    def _try_get_client(
        self, project_name: str,
    ) -> tuple[object | None, CatalogInitError | None]:
        """Return (client, error) for ``project_name`` without raising.

        For aggregators that need to skip projects that fail to init
        while collecting errors for surfacing via AssetPage.errors.
        """
        try:
            return (self._get_or_build_client(project_name), None)
        except CatalogInitError as err:
            return (None, err)

    def _ensure_all_clients(self) -> list[CatalogInitError]:
        """Attempt to build Clients for every registered project.

        Returns the list of init errors that occurred. Does not raise —
        per-project failures are collected for aggregator surfacing.
        """
        errors: list[CatalogInitError] = []
        for proj in self._registry.all():
            _, err = self._try_get_client(proj.name)
            if err is not None:
                errors.append(err)
        return errors

    @property
    def _ready_clients(self) -> dict[str, object]:
        """Read-only view of currently-READY clients keyed by name."""
        return {
            name: slot.client
            for name, slot in self._client_slots.items()
            if slot.state is ClientState.READY and slot.client is not None
        }

    def _is_ready(self, project_name: str) -> bool:
        slot = self._client_slots.get(project_name)
        return slot is not None and slot.state is ClientState.READY

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
        self._ensure_all_clients()

    # ── Project / asset_id helpers ────────────────────────

    def _split_asset_id(self, asset_id: str) -> AssetId | None:
        """Parse a 3-segment asset_id ``"PROJECT/CAT/Name"`` (or
        ``"PROJECT/SEQ/Shot"``).

        Thin wrapper around :meth:`AssetId.parse` kept as an instance
        method because most callers reach for it via ``self.``.
        """
        return AssetId.parse(asset_id)

    def _project_for_asset_id(self, asset_id: str) -> ProjectConfig | None:
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return None
        return self._registry.get(parts[0])

    def _client_for_asset_id(self, asset_id: str):
        """Return the Client for ``asset_id``'s project, or None if the
        id is malformed or the project's Client failed to initialize.

        Logs init errors but does not raise — callers handle the None
        case explicitly. Use :meth:`_get_or_build_client` directly when
        a missing Client should be a hard error.
        """
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return None
        client, _err = self._try_get_client(parts[0])
        return client

    def _project_root_for_asset_id(self, asset_id: str) -> Path | None:
        """Return the on-disk project root for an asset, falling back
        to the registry value when the Client isn't ready yet."""
        client = self._client_for_asset_id(asset_id)
        if client is not None:
            try:
                return Path(client.PROJECT_PATH)
            except Exception:
                pass
        proj = self._project_for_asset_id(asset_id)
        if proj is not None:
            return Path(proj.project_path)
        return None

    def _uri_for_asset_id(self, asset_id: str):
        """Return a ``tumblehead.util.uri.Uri`` for an asset_id of the
        form ``"PROJECT/CAT/Name"`` or ``"PROJECT/SEQ/Shot"``. Returns
        ``None`` if parsing fails."""
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return None
        project_name, second, third = parts
        try:
            from tumblepipe.util.uri import Uri
        except Exception:
            return None
        # Use the project's category list to disambiguate asset vs shot.
        cats = self._list_categories_for_project(project_name)
        kind = "assets" if second in cats else "shots"
        try:
            return Uri.parse_unsafe(f"entity:/{kind}/{second}/{third}")
        except Exception:
            return None

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
        """Set ``TH_*`` env vars to ``project``'s paths and reset the
        tumblehead default_client singleton.

        Called before ``hou.hipFile.load`` / ``save`` / ``clear`` so
        downstream tumblehead operations resolve against the right
        project. Safe to call from any thread (including bg init):
        the one-shot "switched context" warning is dispatched through
        ``_gui_singleshot`` so it never touches ``hou.ui`` directly.

        Fast path: when the requested project already matches both
        ``self._active_project_path`` AND the live ``TH_PROJECT_PATH``
        env var, the reset/rebuild is skipped. ``Client`` construction
        is the dominant per-op cost (~100 ms — reads disk + execs
        project-config modules), so this guard makes back-to-back ops
        on the same project effectively free.
        """
        if project is None:
            return
        if (
            self._active_project_path is not None
            and self._active_project_path == project.project_path
            and os.environ.get("TH_PROJECT_PATH") == project.project_path
        ):
            return
        os.environ["TH_PROJECT_PATH"] = project.project_path
        os.environ["TH_CONFIG_PATH"] = project.config_path
        os.environ["TH_EXPORT_PATH"] = f"{project.project_path}/export"
        try:
            from tumblepipe.api import reset_default_client, default_client
            reset_default_client()
            # Many tumblehead modules cache `api = default_client()` at
            # module level. Patch them all so they use the new project.
            new_client = default_client()
            for mod_path in (
                "tumblepipe.pipe.paths",
                "tumblepipe.config.timeline",
                "tumblepipe.config.variants",
                "tumblepipe.config.department",
                "tumblepipe.pipe.houdini.lops.import_layer",
            ):
                try:
                    import sys
                    mod = sys.modules.get(mod_path)
                    if mod is not None and hasattr(mod, "api"):
                        mod.api = new_client
                except Exception:
                    pass
        except Exception:
            log.debug("reset_default_client unavailable")
        self._active_project_path = project.project_path

        if (
            self._launch_project_path
            and project.project_path != self._launch_project_path
            and not self._activation_warned
        ):
            self._activation_warned = True
            proj_name = project.name
            try:
                from asset_browser.core.thumbnail import _gui_singleshot

                def _show_warning():
                    try:
                        import hou
                        hou.ui.setStatusMessage(
                            f"Switched pipeline context to {proj_name}. "
                            "Some operations may require a Houdini restart.",
                            severity=hou.severityType.Warning,
                        )
                    except Exception:
                        pass

                _gui_singleshot(_show_warning)
            except Exception:
                pass

    # ── Tags ──────────────────────────────────────────────

    def get_available_tags(self) -> dict[str, list[str]]:
        # Don't call _get_or_build_client here — get_available_tags
        # runs on the GUI thread and Client construction can block on
        # SMB Path.exists() timeouts. _list_categories /
        # _list_sequences both gate on _is_ready, so they return []
        # for projects whose Clients haven't been warmed up yet.
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
        return [_cascade_counts(c) for c in result]

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
        asset_multis, shot_multis = self._build_groups_for_project(proj)

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
            ))
        sections.append(Collection(
            id=f"{proj.name}:shots_section",
            label="Shots",
            tag=f"{project_tag}+type:shot",
            icon="clapperboard",
            children=tuple(seq_children),
        ))

        scene_children = self._build_scenes_for_project(proj)
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
            import asset_browser
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

    def _filter_by_group(self, items: list, tag: str) -> list:
        """Filter assets to members of a pipeline group.

        Tag format: ``group:PROJECT:CONTEXT/NAME``. Returns ``items``
        unchanged when the project's Client isn't yet READY (sidebar
        renders early). Raises :class:`TagQueryError` if the group
        lookup itself fails — silently returning all items would
        defeat the user's filter and hide the real problem.
        """
        _, rest = tag.split(":", 1)
        proj_name, group_path = rest.split(":", 1)
        proj = self._registry.get(proj_name)
        if proj is None or not self._is_ready(proj_name):
            return items
        self._activate_project(proj)
        try:
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.util.uri import Uri
            group_uri = Uri.parse_unsafe(f"groups:/{group_path}")
            group = grp_mod.get_group(group_uri)
        except Exception as exc:
            raise TagQueryError(
                self.id,
                f"failed to resolve group {group_path!r} in {proj_name}: {exc}",
                cause=exc,
            ) from exc
        if group is None:
            return []
        member_ids = set()
        for member_uri in group.members:
            segs = (
                member_uri.segments if hasattr(member_uri, 'segments')
                else str(member_uri).replace("entity:/", "").split("/")
            )
            if len(segs) >= 3:
                member_ids.add(f"{proj_name}/{segs[1]}/{segs[2]}")
            elif len(segs) >= 2:
                member_ids.add(f"{proj_name}/{segs[0]}/{segs[1]}")
        return [a for a in items if a.id in member_ids]

    def _filter_by_scene(self, items: list, tag: str) -> list:
        """Filter assets to those in a pipeline scene.

        Tag format: ``scene:PROJECT:PATH``. See :meth:`_filter_by_group`
        for the contract — raises :class:`TagQueryError` on lookup
        failure.
        """
        _, rest = tag.split(":", 1)
        proj_name, scene_path = rest.split(":", 1)
        proj = self._registry.get(proj_name)
        if proj is None or not self._is_ready(proj_name):
            return items
        self._activate_project(proj)
        try:
            from tumblepipe.config import scenes as scn_mod
            from tumblepipe.util.uri import Uri
            scene_uri = Uri.parse_unsafe(f"scenes:/{scene_path}")
            scene = scn_mod.get_scene(scene_uri)
        except Exception as exc:
            raise TagQueryError(
                self.id,
                f"failed to resolve scene {scene_path!r} in {proj_name}: {exc}",
                cause=exc,
            ) from exc
        if scene is None:
            return []
        asset_ids = set()
        for entry in scene.assets:
            uri = entry.asset
            segs = (
                uri.segments if hasattr(uri, 'segments')
                else str(uri).replace("entity:/", "").split("/")
            )
            if len(segs) >= 3:
                asset_ids.add(f"{proj_name}/{segs[1]}/{segs[2]}")
            elif len(segs) >= 2:
                asset_ids.add(f"{proj_name}/{segs[0]}/{segs[1]}")
        return [a for a in items if a.id in asset_ids]

    def _list_group_leaves_by_context(
        self, proj,
    ) -> dict[str, list[Collection]]:
        """Return ``{"assets": [...], "shots": [...]}`` of Multi leaf
        Collections for one project.

        Multis are locked to a single context (their URI is either
        ``groups:/shots/...`` or ``groups:/assets/...``), so the
        split is intrinsic to the data — this helper just surfaces
        it as a dict for callers that need either the per-context
        subsection (sidebar tree) or the flat list
        (``_iter_group_collections``).
        """
        result: dict[str, list[Collection]] = {"assets": [], "shots": []}
        if not self._is_ready(proj.name):
            return result
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            for ctx in ("shots", "assets"):
                ctx_groups = grp_mod.list_groups(ctx)
                for g in ctx_groups:
                    name = g.uri.segments[-1] if g.uri.segments else str(g.uri)
                    member_count = len(g.members)
                    result[ctx].append(Collection(
                        id=f"group:{proj.name}:{ctx}/{name}",
                        label=name,
                        count=member_count,
                        tag=f"group:{proj.name}:{ctx}/{name}",
                        kind="group",
                    ))
        except Exception:
            log.debug("Failed to list groups for %s", proj.name, exc_info=True)
        return result

    def _build_groups_for_project(self, proj):
        """Return ``(asset_multis_subheader, shot_multis_subheader)``
        for one project.

        Each subheader is a single ``Multis`` Collection whose
        children are the actual Multi leaves; the subheader's tag
        filters the grid to that context's Multi cards via
        ``type:group+multi_context:<assets|shots>``. The headers are
        always returned (never ``None``) — including empty contexts
        — so artists can always see and click through to the Multi
        grid view, and right-click → "New Multi…" stays one motion
        away even before the first Multi exists.

        Callers inject these into the Assets / Shots sections rather
        than rendering a separate top-level Multis section, so the
        sidebar mirrors the inherent context split without nesting
        twice.
        """
        project_tag = f"project:{proj.name}"
        leaves = self._list_group_leaves_by_context(proj)
        asset_sub = Collection(
            id=f"{proj.name}:multis_section:assets",
            label="Multis",
            icon="group",
            tag=f"{project_tag}+type:group+multi_context:assets",
            count=len(leaves["assets"]),
            children=tuple(leaves["assets"]),
        )
        shot_sub = Collection(
            id=f"{proj.name}:multis_section:shots",
            label="Multis",
            icon="group",
            tag=f"{project_tag}+type:group+multi_context:shots",
            count=len(leaves["shots"]),
            children=tuple(leaves["shots"]),
        )
        return asset_sub, shot_sub

    def _build_scenes_for_project(self, proj) -> list[Collection]:
        """Build Scene collections for a single project."""
        children: list[Collection] = []
        if not self._is_ready(proj.name):
            return children
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
            for s in scn_mod.list_scenes():
                path = "/".join(s.uri.segments) if s.uri.segments else str(s.uri)
                name = s.uri.segments[-1] if s.uri.segments else path
                asset_count = len(s.assets)
                children.append(Collection(
                    id=f"scene:{proj.name}:{path}",
                    label=name,
                    count=asset_count,
                    tag=f"scene:{proj.name}:{path}",
                    kind="scene",
                ))
        except Exception:
            log.debug("Failed to list scenes for %s", proj.name, exc_info=True)
        return children

    def _iter_group_collections(self):
        """Yield ``(group_collection, project_name)`` for every Multi
        leaf in every registered project. Reads via the per-context
        leaf list so it doesn't depend on the sidebar tree shape.
        """
        for proj in self._registry.all():
            leaves = self._list_group_leaves_by_context(proj)
            for ctx in ("shots", "assets"):
                for grp in leaves[ctx]:
                    yield grp, proj.name

    def _iter_scene_collections(self):
        """Yield ``(scene_collection, project_name)`` for every scene
        in every registered project."""
        for proj in self._registry.all():
            for scn in self._build_scenes_for_project(proj):
                yield scn, proj.name

    def _container_collection_to_asset(
        self, collection: "Collection", project_name: str, kind: str,
    ) -> Asset:
        """Wrap a Group/Scene Collection in an Asset so the grid can
        render it as a card. The ``drill_tag`` metadata key signals to
        the browser's card-click handler that the card represents a
        container — clicking it should drill into its members rather
        than open a detail panel.

        For groups, the asset also advertises ``has_sub_cards=True``
        and a flat ``departments`` list so the deck popup can render
        one sub-card per dept (mirroring the shot/asset open-workfile
        UX).
        """
        metadata: dict = {
            "kind": kind,
            "drill_tag": collection.tag,
            "member_count": collection.count,
        }
        if kind == "scene":
            # Surface a dirty flag so the renderer can paint an
            # "unsaved" indicator on Roots whose JSON has drifted
            # from the latest exported USD.
            try:
                metadata["dirty"] = self._root_is_dirty(collection.tag)
            except Exception:
                metadata["dirty"] = False
        if kind == "group":
            # Stash the Multi's context (``assets`` / ``shots``) so
            # ``_get_container_assets`` can filter by it when the
            # sidebar's nested **Multis** subheader is active. The
            # context comes from the second URI segment, i.e. the
            # ``ctx`` in ``group:PROJECT:ctx/name``.
            try:
                _, _, group_path = collection.tag.split(":", 2)
                ctx = group_path.split("/", 1)[0] if "/" in group_path else ""
            except ValueError:
                ctx = ""
            if ctx:
                metadata["context"] = ctx
            covered = self._group_departments_from_tag(
                collection.tag, project_name,
            )
            if covered:
                workfile_info = self._get_group_department_workfile_info(
                    collection.tag,
                )
                # Same shape as shot/asset metadata: {dept: latest_version
                # or ""}. Dept-name iteration order (for sub-card render)
                # comes from get_sub_cards, which sorts by the project's
                # canonical dept order.
                depts_dict = {}
                for dept in covered:
                    versions = workfile_info.get(dept) or []
                    depts_dict[dept] = versions[-1] if versions else ""
                metadata["departments"] = depts_dict
                metadata["has_sub_cards"] = True
        return Asset(
            id=collection.tag,
            name=collection.label,
            thumbnail_url="",
            tags=frozenset({
                f"type:{kind}",
                f"project:{project_name}",
                "source:pipeline",
                collection.tag,
            }),
            metadata=metadata,
        )

    def open_container_location(self, collection_id: str) -> bool:
        """Open the Multi or Root's on-disk storage folder in Explorer.

        Mirrors :meth:`_open_dept_work_dir` for shots/assets: resolves
        the container's URI to a filesystem path via tumblepipe's
        storage layer and shells out to ``explorer``. Walks up to the
        nearest existing ancestor if the leaf folder hasn't been
        created yet (e.g. a brand-new Multi with no workfiles).
        """
        import sys
        from pathlib import Path

        def _bail(msg: str) -> bool:
            log.warning("open_container_location: %s (%s)", msg, collection_id)
            try:
                print(
                    f"[asset_browser] open_container_location failed for "
                    f"{collection_id}: {msg}",
                    file=sys.stderr,
                )
            except Exception:
                pass
            return False

        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return _bail("could not parse collection id")
        kind, proj_name, path = parsed
        proj = self._registry.get(proj_name)
        if proj is None:
            return _bail(f"unknown project {proj_name!r}")
        try:
            self._activate_project(proj)
            from tumblepipe import api as tp_api
            from tumblepipe.util.uri import Uri
        except Exception:
            log.exception("open_container_location: imports failed")
            return _bail("tumblepipe imports failed")

        candidate_uris: list = []
        try:
            if kind == "group":
                uri = self._group_uri(path)
                # Multis live at two possible roots — the config side
                # (``groups:/``) holds the JSON definition, and the
                # workfile side mirrors the entity layout. Try both;
                # the first one that resolves to an existing folder
                # wins. Walk up either if needed.
                candidate_uris.append(
                    Uri.parse_unsafe("groups:/") / uri.segments
                )
                candidate_uris.append(
                    Uri.parse_unsafe("project:/") / uri.segments
                )
            elif kind == "scene":
                uri = self._scene_uri(path)
                # Roots export to ``export:/scenes/<path>/_staged`` —
                # that's where the .usda layer versions land. Plain
                # ``export:/scenes/<path>`` is the parent.
                candidate_uris.append(
                    Uri.parse_unsafe("export:/scenes/")
                    / uri.segments / "_staged"
                )
                candidate_uris.append(
                    Uri.parse_unsafe("export:/scenes/") / uri.segments
                )
                # Fall back to the scene's config folder if no export
                # has happened yet so the user lands somewhere
                # meaningful.
                candidate_uris.append(
                    Uri.parse_unsafe("scenes:/") / uri.segments
                )
            else:
                return _bail(f"unsupported kind {kind!r}")
        except Exception:
            log.exception("open_container_location: uri build failed")
            return _bail("URI build failed")

        target: Path | None = None
        attempts: list[str] = []
        for uri in candidate_uris:
            try:
                resolved = tp_api.storage.resolve(uri)
            except Exception:
                attempts.append(f"resolve({uri}) raised")
                continue
            if resolved is None:
                attempts.append(f"resolve({uri}) -> None")
                continue
            p = Path(str(resolved))
            attempts.append(f"resolve({uri}) -> {p}")
            walked = p
            while not walked.exists() and walked.parent != walked:
                walked = walked.parent
            if walked.exists():
                target = walked
                break
        if target is None:
            return _bail(
                "no candidate path resolved to an existing folder: "
                + " ; ".join(attempts)
            )
        try:
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
            return True
        except Exception:
            log.exception(
                "open_container_location: shell failed for %s", target,
            )
            return _bail(f"shell failed for {target}")

    def list_root_assigned_shots(self, collection_id: str) -> list:
        """Return a list of shot URIs whose ``scene_ref`` points at
        this Root. Empty list on any failure.
        """
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return []
        kind, proj_name, path = parsed
        if kind != "scene":
            return []
        proj = self._registry.get(proj_name)
        if proj is None:
            return []
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
        except Exception:
            log.exception("list_root_assigned_shots: imports failed")
            return []
        scene_uri = self._scene_uri(path)
        try:
            shots = list(scn_mod.find_shots_with_scene_ref(scene_uri))
        except Exception:
            log.exception(
                "find_shots_with_scene_ref failed for %s", scene_uri,
            )
            return []
        return shots

    def rebuild_root_assigned_shots(
        self, collection_id: str,
    ) -> tuple[int, list]:
        """Regenerate root + build staged USD for every shot whose
        ``scene_ref`` points at this Root. Returns ``(ok_count,
        failed_uris)``. Best-effort: a shot failure logs and continues.
        """
        shots = self.list_root_assigned_shots(collection_id)
        if not shots:
            return (0, [])
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return (0, [])
        kind, proj_name, _path = parsed
        proj = self._registry.get(proj_name)
        if proj is None:
            return (0, [])
        try:
            self._activate_project(proj)
            from tumblepipe.config import scene as scene_mod
        except Exception:
            log.exception("rebuild_root_assigned_shots: imports failed")
            return (0, list(shots))
        ok = 0
        failed: list = []
        for shot_uri in shots:
            try:
                scene_mod.generate_root_version(shot_uri)
            except Exception:
                log.exception(
                    "generate_root_version failed for %s", shot_uri,
                )
                failed.append(shot_uri)
                continue
            try:
                self._build_shot_staged(shot_uri)
            except Exception:
                log.exception(
                    "build_shot_staged failed for %s", shot_uri,
                )
                failed.append(shot_uri)
                continue
            ok += 1
        return (ok, failed)

    def export_root_usd(self, collection_id: str) -> bool:
        """Re-export a Root's scene USD via ``export_scene_version``.

        Manual replacement for the auto-staging that used to fire on
        every membership edit. Returns ``True`` on success.
        """
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return False
        kind, proj_name, path = parsed
        if kind != "scene":
            return False
        proj = self._registry.get(proj_name)
        if proj is None:
            return False
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
        except Exception:
            log.exception("export_root_usd: imports failed")
            return False
        scene_uri = self._scene_uri(path)
        try:
            scn_mod.export_scene_version(scene_uri)
        except Exception:
            log.exception(
                "export_scene_version failed for %s", scene_uri,
            )
            return False
        # Re-fetch the Root card so the dirty indicator clears now
        # that scene JSON matches the freshly-written context.json.
        try:
            self._request_card_refresh_for_id(collection_id)
        except Exception:
            log.exception(
                "card refresh after export failed for %s", collection_id,
            )
        return True

    def _build_shot_staged(self, shot_uri) -> None:
        """Build a shot's staged USD locally.

        Mirrors the legacy Scene Editor's ``_execute_build_local`` —
        resolves the next staged file path, reads the shot's frame
        range, and runs the build task. Raises on failure so callers
        can surface the error in their status message.
        """
        from tumblepipe.pipe.paths import next_staged_file_path
        from tumblepipe.config.timeline import get_frame_range
        from tumblepipe.farm.tasks.build import build as build_task

        output_path = next_staged_file_path(shot_uri)
        frame_range = get_frame_range(shot_uri)
        render_range = (
            frame_range.full_range() if frame_range else None
        )
        if render_range is None:
            raise RuntimeError(
                f"No frame range found for shot: {shot_uri}"
            )
        result = build_task.main(shot_uri, output_path, render_range)
        if result != 0:
            raise RuntimeError(
                f"Build failed with exit code {result} for {shot_uri}"
            )

    def _toggle_group_dept_coverage(
        self, group_id: str, dept: str, enabled: bool,
    ) -> None:
        """Add or remove ``dept`` from the Multi's covered list.

        Wraps ``add_department`` / ``remove_department`` with cache
        invalidation + card and detail-panel refresh so the user sees
        the deck and the Departments tab catch up on the next paint.
        """
        if not group_id.startswith("group:"):
            return
        try:
            _, rest = group_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
        except Exception:
            log.exception("toggle dept coverage: import failed")
            return
        group_uri = self._group_uri(path)
        try:
            grp = grp_mod.get_group(group_uri)
        except Exception:
            log.exception("toggle dept coverage: get_group failed")
            return
        if grp is None:
            return
        current = {str(d) for d in getattr(grp, "departments", ())}
        try:
            if enabled and dept not in current:
                grp_mod.add_department(group_uri, dept)
            elif (not enabled) and dept in current:
                grp_mod.remove_department(group_uri, dept)
            else:
                return  # already in target state, no-op
        except Exception:
            log.exception(
                "toggle dept coverage failed for %s/%s", group_id, dept,
            )
            return
        self._invalidate_membership_cache()
        try:
            self._request_card_refresh_for_id(group_id)
        except Exception:
            log.exception("card refresh after dept toggle failed")
        try:
            self._request_global_detail_refresh()
        except Exception:
            log.exception("detail refresh after dept toggle failed")

    def _remove_member_from_group(
        self, asset_id: str, collection_id: str,
    ) -> None:
        """Remove a single shot/asset from a pipeline group.

        Wraps :meth:`remove_assets_from_collection` with status
        feedback + cache invalidation so the deck refreshes on the
        next render without the user having to hard-refresh. Logs and
        bails on any unexpected exception.
        """
        if not asset_id or not collection_id:
            return
        try:
            removed, skipped, msg = self.remove_assets_from_collection(
                collection_id, [asset_id],
            )
        except Exception:
            log.exception(
                "remove_assets_from_collection failed for %s / %s",
                collection_id, asset_id,
            )
            return
        if msg:
            try:
                import hou
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )
            except Exception:
                log.info("status: %s", msg)
        if removed:
            self._invalidate_membership_cache()
            self._request_global_detail_refresh()
            try:
                self._request_global_grid_refresh()
            except Exception:
                pass

    def _get_member_groups_for_project(
        self, proj_name: str,
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """Return ``{member_asset_id: {dept: (group_id, group_label)}}``
        for the shots and assets in ``proj_name`` covered by one or
        more groups. ``group_id`` is the same id our catalog uses for
        the group's drill/edit/remove actions (``group:PROJECT:ctx/name``).
        Cached on the catalog; dropped by :meth:`invalidate_cache`.
        First-write-wins when several groups cover the same
        (member, dept) pair.
        """
        cache = getattr(self, "_member_groups_cache", None)
        if cache is None:
            cache = {}
            self._member_groups_cache = cache
        if proj_name in cache:
            return cache[proj_name]

        coverage: dict[str, dict[str, tuple[str, str]]] = {}
        proj = self._registry.get(proj_name)
        if proj is None or not self._is_ready(proj_name):
            cache[proj_name] = coverage
            return coverage

        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            for ctx in ("shots", "assets"):
                try:
                    ctx_groups = grp_mod.list_groups(ctx)
                except Exception:
                    log.debug(
                        "list_groups(%s) failed for %s", ctx, proj_name,
                        exc_info=True,
                    )
                    continue
                for g in ctx_groups:
                    segs = getattr(g.uri, "segments", None) or []
                    group_label = segs[-1] if segs else str(g.uri)
                    # Reuse the catalog's collection-id convention so
                    # the remove action can plug straight into
                    # ``remove_assets_from_collection``.
                    group_path = "/".join(segs) if segs else group_label
                    group_id = f"group:{proj_name}:{group_path}"
                    depts = [str(d) for d in getattr(g, "departments", ())]
                    if not depts:
                        continue
                    for member_uri in g.members:
                        m_segs = (
                            member_uri.segments
                            if hasattr(member_uri, "segments")
                            else str(member_uri)
                                .replace("entity:/", "").split("/")
                        )
                        if len(m_segs) >= 3:
                            member_id = f"{proj_name}/{m_segs[1]}/{m_segs[2]}"
                        elif len(m_segs) >= 2:
                            member_id = f"{proj_name}/{m_segs[0]}/{m_segs[1]}"
                        else:
                            continue
                        bucket = coverage.setdefault(member_id, {})
                        for dept in depts:
                            bucket.setdefault(
                                dept, (group_id, group_label),
                            )
        except Exception:
            log.debug(
                "Member group coverage failed for %s", proj_name,
                exc_info=True,
            )

        cache[proj_name] = coverage
        return coverage

    def _dept_groups_for_member(
        self, asset_id: str,
    ) -> dict[str, tuple[str, str]]:
        """Return ``{dept: (group_id, group_label)}`` for an asset/shot
        covered by a group. Empty when the asset isn't a group member.
        """
        parts = asset_id.split("/", 1)
        if not parts or not parts[0]:
            return {}
        coverage = self._get_member_groups_for_project(parts[0])
        return coverage.get(asset_id, {})

    def _root_is_dirty(self, scene_id: str) -> bool:
        """Return ``True`` when a Root has unsaved changes vs the last
        exported USD.

        Compares the Root's current ``assets`` list against the asset
        list recorded in the latest exported version's ``context.json``.
        If they differ — or no export exists yet and the Root has any
        members — the Root is dirty. Used to surface a visual cue on
        the card so users know when an explicit ``Export Root USD`` is
        needed before downstream shots rebuild correctly.
        """
        parsed = self._parse_collection_id(scene_id)
        if parsed is None or parsed[0] != "scene":
            return False
        _, proj_name, path = parsed
        proj = self._registry.get(proj_name)
        if proj is None or not self._is_ready(proj_name):
            return False
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
            from tumblepipe import api as tp_api
            from tumblepipe.util.uri import Uri
            scene_uri = self._scene_uri(path)
            scene = scn_mod.get_scene(scene_uri)
        except Exception:
            log.debug(
                "_root_is_dirty: get_scene failed for %s",
                scene_id, exc_info=True,
            )
            return False
        if scene is None:
            return False
        current = [
            (
                str(e.asset),
                int(getattr(e, "instances", 1) or 1),
                str(getattr(e, "variant", "default") or "default"),
            )
            for e in getattr(scene, "assets", ())
        ]
        try:
            from tumblepipe.pipe.paths import get_scene_staged_path
            export_root = get_scene_staged_path(scene_uri)
        except Exception:
            log.debug(
                "_root_is_dirty: get_scene_staged_path failed for %s",
                scene_id, exc_info=True,
            )
            return bool(current)
        from pathlib import Path
        export_root = Path(str(export_root))
        if not export_root.exists():
            return bool(current)
        try:
            versions = sorted(
                p for p in export_root.iterdir()
                if p.is_dir() and p.name.startswith("v")
            )
        except Exception:
            return bool(current)
        if not versions:
            return bool(current)
        ctx_path = versions[-1] / "context.json"
        if not ctx_path.exists():
            return bool(current)
        try:
            import json
            with open(ctx_path, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception:
            log.debug(
                "_root_is_dirty: context.json read failed for %s",
                scene_id, exc_info=True,
            )
            return False
        exported = [
            (
                str(a.get("asset", "")),
                int(a.get("instances", 1) or 1),
                str(a.get("variant", "default") or "default"),
            )
            for a in ctx.get("parameters", {}).get("assets", [])
        ]
        return sorted(current) != sorted(exported)

    def _group_context_from_tag(self, group_tag: str) -> str | None:
        """Return ``"shots"`` / ``"assets"`` for a ``group:`` id, or
        ``None`` if the tag is malformed. The context is the first
        path segment after the project name.
        """
        try:
            _, rest = group_tag.split(":", 1)
            _, path = rest.split(":", 1)
        except ValueError:
            return None
        ctx = path.split("/", 1)[0] if "/" in path else path
        if ctx in ("shots", "assets"):
            return ctx
        return None

    def _get_group_department_workfile_info(
        self, group_tag: str,
    ) -> dict[str, list[str]]:
        """Return ``{dept: [latest_version]}`` for a group's workfile
        tree.

        Resolution goes through ``latest_hip_file_path_with_context``
        (the same helper :meth:`_open_group_workfile` and
        :meth:`_new_group_from_template` use), so paths line up with
        the open/create flows even if the on-disk layout doesn't match
        a naive ``<root>/groups/<ctx>/<name>/<dept>`` walk. Returns
        ``{}`` on any lookup failure — callers treat empty as "no
        versions yet".
        """
        try:
            _, rest = group_tag.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return {}
        proj = self._registry.get(proj_name)
        if proj is None or not self._is_ready(proj_name):
            return {}
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.pipe import paths as paths_mod
            group_uri = self._group_uri(path)
            grp = grp_mod.get_group(group_uri)
        except Exception:
            log.debug(
                "Group lookup failed for %s", group_tag, exc_info=True,
            )
            return {}
        if grp is None:
            return {}
        result: dict[str, list[str]] = {}
        for dept in getattr(grp, "departments", ()):
            dept_name = str(dept)
            try:
                hip_path = paths_mod.latest_hip_file_path_with_context(
                    group_uri, dept_name,
                )
            except Exception:
                log.debug(
                    "latest_hip_file_path_with_context failed for "
                    "%s/%s", group_tag, dept_name, exc_info=True,
                )
                continue
            if hip_path is None or not hip_path.exists():
                continue
            stem = hip_path.stem
            tail = stem.rsplit("_", 1)
            if (
                len(tail) == 2
                and tail[1].startswith("v")
                and tail[1][1:].isdigit()
            ):
                result[dept_name] = [tail[1]]
        return result

    def _group_departments_from_tag(
        self, group_tag: str, project_name: str,
    ) -> list[str]:
        """Return the list of dept-name strings a group covers.

        ``group_tag`` is the same id we use for drill-down — format
        ``group:PROJECT:ctx/name``. Returns ``[]`` on any lookup
        failure (logged at debug); callers treat empty as "no sub-
        cards available".
        """
        try:
            _, rest = group_tag.split(":", 1)
            _, path = rest.split(":", 1)
        except ValueError:
            return []
        proj = self._registry.get(project_name)
        if proj is None or not self._is_ready(project_name):
            return []
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            grp = grp_mod.get_group(self._group_uri(path))
        except Exception:
            log.debug("Failed to resolve group %s", group_tag, exc_info=True)
            return []
        if grp is None:
            return []
        return [str(d) for d in getattr(grp, "departments", ())]

    def _get_container_assets(
        self, kind: str, query: str, tags: frozenset[str],
        cursor: str | None, page_size: int,
    ) -> AssetPage:
        """Return synthetic Group/Scene cards for the grid.

        Called from :meth:`get_assets` when the active filter is
        ``type:group`` / ``type:scene``. Respects the ``project:``
        pill, the search box, and standard pagination.
        """
        self._discovery_errors = []
        for err in self._ensure_all_clients():
            self._discovery_errors.append(err)

        if kind == "group":
            items = [
                self._container_collection_to_asset(g, p, "group")
                for g, p in self._iter_group_collections()
            ]
        else:
            items = [
                self._container_collection_to_asset(s, p, "scene")
                for s, p in self._iter_scene_collections()
            ]

        project_tags = {t for t in tags if t.startswith("project:")}
        if project_tags:
            items = [
                a for a in items
                if any(pt in a.tags for pt in project_tags)
            ]

        # Sidebar's nested **Multis** subheaders (under Assets / Shots)
        # filter by context via ``multi_context:assets|shots``. Only
        # Multi cards carry a ``context`` metadata key today, so a
        # non-matching kind (e.g. scenes) would silently disappear if
        # this clause ever leaked into a ``type:scene`` query — but
        # the sidebar never builds such a combined tag, so the gate
        # below is restricted to the group branch.
        if kind == "group":
            multi_ctx_tags = {
                t.split(":", 1)[1]
                for t in tags
                if t.startswith("multi_context:")
            }
            if multi_ctx_tags:
                items = [
                    a for a in items
                    if a.metadata.get("context") in multi_ctx_tags
                ]

        if query:
            q = query.lower()
            items = [a for a in items if q in a.name.lower()]

        page = int(cursor) if cursor else 0
        start = page * page_size
        end = start + page_size
        page_items = items[start:end]
        next_cursor = str(page + 1) if end < len(items) else None
        return AssetPage(
            assets=page_items,
            cursor=next_cursor,
            total=len(items),
            errors=tuple(self._discovery_errors),
        )

    def _get_container_detail(self, asset_id: str) -> AssetDetail:
        """Build an AssetDetail for a Group/Scene id.

        ``asset_id`` is the container's tag — same format the sidebar
        uses (``group:PROJECT:ctx/name`` / ``scene:PROJECT:path``). The
        detail carries enough metadata for the panel to render the
        Info tab + departments toggle (groups only).
        """
        try:
            kind, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return AssetDetail(
                id=asset_id, name=asset_id, thumbnail_url="",
                tags=frozenset({"source:pipeline"}),
            )

        proj = self._registry.get(proj_name)
        if proj is not None:
            try:
                self._activate_project(proj)
            except Exception:
                log.exception("activate_project failed in container detail")

        metadata: dict = {
            "kind": kind,
            "drill_tag": asset_id,
            "project": proj_name,
            "path": path,
        }

        if kind == "group":
            try:
                from tumblepipe.config import groups as grp_mod
                from tumblepipe.config import department as dept_mod
                grp = grp_mod.get_group(self._group_uri(path))
                covered: list[str] = []
                if grp is not None:
                    metadata["member_count"] = len(grp.members)
                    covered = [
                        str(d) for d in getattr(grp, "departments", ())
                    ]
                # Shape ``departments`` like shots/assets ({dept:
                # [versions]}) so the rich dept section can render
                # version dropdowns + active highlights uniformly.
                # The covered/uncovered toggle state lives separately
                # in ``covered_departments``.
                metadata["departments"] = (
                    self._get_group_department_workfile_info(asset_id)
                )
                metadata["covered_departments"] = covered
                ctx = path.split("/", 1)[0] if "/" in path else "assets"
                metadata["context"] = ctx
                try:
                    metadata["known_departments"] = [
                        d.name for d in
                        dept_mod.list_departments(ctx, include_generated=False)
                    ]
                except Exception:
                    metadata["known_departments"] = []
            except Exception:
                log.exception(
                    "Failed to populate group detail for %s", asset_id,
                )
        elif kind == "scene":
            try:
                from tumblepipe.config import scenes as scn_mod
                scn = scn_mod.get_scene(self._scene_uri(path))
                if scn is not None:
                    metadata["member_count"] = len(scn.assets)
            except Exception:
                log.exception(
                    "Failed to populate scene detail for %s", asset_id,
                )

        name = path.rsplit("/", 1)[-1] if path else asset_id
        return AssetDetail(
            id=asset_id,
            name=name,
            thumbnail_url="",
            tags=frozenset({
                f"type:{kind}",
                f"project:{proj_name}",
                "source:pipeline",
            }),
            metadata=metadata,
        )

    def get_asset_membership(
        self, asset_id: str,
    ) -> list[tuple[str, str, str]]:
        """Return ``(collection_id, label, kind)`` for every group and
        scene that contains *asset_id*.

        Used by the detail panel to show "belongs to" chips.
        """
        result: list[tuple[str, str, str]] = []
        try:
            # asset_id looks like "PROJECT/CTX/NAME"
            parts = asset_id.split("/", 1)
            if len(parts) < 2:
                return result
            proj_name = parts[0]
            proj = self._registry.get(proj_name)
            if proj is None or not self._is_ready(proj_name):
                return result
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.config import scenes as scn_mod
            from tumblepipe.util.uri import Uri

            # Normalise the entity ID so it matches member URIs.
            entity_suffix = parts[1]  # "CTX/NAME"

            # Groups
            for ctx in ("shots", "assets"):
                for g in grp_mod.list_groups(ctx):
                    for member_uri in g.members:
                        segs = (
                            member_uri.segments
                            if hasattr(member_uri, "segments")
                            else str(member_uri)
                            .replace("entity:/", "")
                            .split("/")
                        )
                        mid = "/".join(segs[-2:]) if len(segs) >= 2 else ""
                        if mid == entity_suffix:
                            name = (
                                g.uri.segments[-1]
                                if g.uri.segments
                                else str(g.uri)
                            )
                            cid = f"group:{proj_name}:{ctx}/{name}"
                            result.append((cid, name, "group"))
                            break

            # Scenes — Roots can hold an asset two different ways:
            #   1. Listed in the Root's ``assets`` (asset cards).
            #   2. A shot's ``scene_ref`` pointing at the Root (shot
            #      cards).
            # Both paths feed the same "Remove from Root <name>"
            # context menu item, so we collect them together.
            assigned_scene_uri = None
            if entity_suffix.startswith("shots/"):
                try:
                    from tumblepipe.config import scene as scene_mod_pkg
                    shot_uri = Uri.parse_unsafe(f"entity:/{entity_suffix}")
                    assigned_scene_uri = (
                        scene_mod_pkg.get_scene_ref(shot_uri)
                    )
                    if assigned_scene_uri is None:
                        resolved, _src = (
                            scene_mod_pkg.get_inherited_scene_ref(shot_uri)
                        )
                        assigned_scene_uri = resolved
                except Exception:
                    log.debug(
                        "scene_ref lookup failed for %s",
                        asset_id, exc_info=True,
                    )

            for s in scn_mod.list_scenes():
                matched = False
                for entry in s.assets:
                    uri = entry.asset
                    segs = (
                        uri.segments
                        if hasattr(uri, "segments")
                        else str(uri)
                        .replace("entity:/", "")
                        .split("/")
                    )
                    mid = "/".join(segs[-2:]) if len(segs) >= 2 else ""
                    if mid == entity_suffix:
                        matched = True
                        break
                if not matched and assigned_scene_uri is not None:
                    try:
                        if str(s.uri) == str(assigned_scene_uri):
                            matched = True
                    except Exception:
                        pass
                if matched:
                    path = (
                        "/".join(s.uri.segments)
                        if s.uri.segments
                        else str(s.uri)
                    )
                    name = (
                        s.uri.segments[-1]
                        if s.uri.segments
                        else path
                    )
                    cid = f"scene:{proj_name}:{path}"
                    result.append((cid, name, "scene"))
        except Exception:
            log.debug(
                "Failed to query membership for %s",
                asset_id,
                exc_info=True,
            )
        return result

    def get_primary_filters(self) -> list[Collection]:
        items = self._get_all_items()

        def _tag_count(tag: str) -> int:
            return sum(1 for a in items if tag in a.tags)

        try:
            import asset_browser
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
                       count=sum(1 for _ in self._iter_group_collections())),
            Collection(id="type:scene", label="Roots", tag="type:scene", icon="layers",
                       count=sum(1 for _ in self._iter_scene_collections())),
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
            return self._get_container_assets(
                "group", query, tags, cursor, page_size,
            )
        if "type:scene" in tags:
            return self._get_container_assets(
                "scene", query, tags, cursor, page_size,
            )

        # Begin a fresh discovery pass. Errors are accumulated on
        # ``self._discovery_errors`` and attached to the returned
        # AssetPage so the consumer can surface them in the UI.
        self._discovery_errors = []
        # Block until every registered project's init has finished
        # (success or fail) before aggregating discovery. Init errors
        # are recorded on slots and surfaced via per-project discovery.
        for err in self._ensure_all_clients():
            self._discovery_errors.append(err)
        all_items = self._get_all_items()

        # Handle group/scene tags (special filtering). Filter failures
        # are surfaced via AssetPage.errors rather than silently
        # widening the result set, which would defeat the filter.
        remaining_tags = set()
        for t in tags:
            try:
                if t.startswith("group:"):
                    all_items = self._filter_by_group(all_items, t)
                elif t.startswith("scene:"):
                    all_items = self._filter_by_scene(all_items, t)
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
        from asset_browser.api.tags import match_tags
        if remaining_tags:
            filter_tags = frozenset(t for t in remaining_tags if not t.startswith("source:"))
            if filter_tags:
                all_items = [a for a in all_items if match_tags(a.tags, filter_tags)]

        # Search
        if query:
            q = query.lower()
            all_items = [a for a in all_items if q in a.name.lower()]

        # Pin the active scene asset to the first position and mark
        # its metadata so the grid renders a highlight border. Clear
        # the flag on every other item — assets are cached and the
        # active hip changes independently of a rescan.
        scene_id = self._get_scene_asset_id()
        for a in all_items:
            if a.metadata.get("is_current_scene") and a.id != scene_id:
                a.metadata["is_current_scene"] = False
        if scene_id:
            pinned = [a for a in all_items if a.id == scene_id]
            rest = [a for a in all_items if a.id != scene_id]
            for a in pinned:
                a.metadata["is_current_scene"] = True
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
            return self._get_container_detail(asset_id)

        # asset_id format: "PROJECT/CATEGORY/AssetName" or "PROJECT/SEQ/Shot".
        parsed = self._split_asset_id(asset_id)
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
                    try:
                        metadata["frame_total"] = end - start + 1
                    except Exception:
                        pass
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
        proj = self._project_for_asset_id(asset_id)
        return proj.name if proj is not None else ""

    def _get_variants(self, asset_id: str, kind: str) -> list[str]:
        """Return variant names for an asset/shot.

        Returns ``[]`` for a malformed or unresolvable id. Raises
        :class:`ConfigError` if the variants module fails to load or
        the lookup itself raises — callers (typically :meth:`get_detail`)
        let it propagate so the consumer can render a detail-level error.
        """
        from tumblepipe.config.variants import list_variants
        uri = self._uri_for_asset_id(asset_id)
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
        uri = self._uri_for_asset_id(asset_id)
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
        uri = self._uri_for_asset_id(asset_id)
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
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
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

    def _write_description(self, asset_id: str, text: str) -> bool:
        path = self._description_path(asset_id)
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.strip() + "\n", encoding="utf-8")
            return True
        except Exception:
            log.exception("Failed to write description at %s", path)
            return False

    # ── Thumbnail sidecar ─────────────────────────────────

    def _thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the path to ``thumbnail.png`` for an asset/shot.

        Sidecar lives next to ``description.txt`` in the asset/shot
        root directory so it follows the same project share semantics.
        """
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
        if root is None:
            return None
        try:
            cats = self._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            return root / kind / second / third / "thumbnail.png"
        except Exception:
            return None

    def get_thumbnail(self, asset: Asset):
        """Return the path to the asset's sidecar thumbnail if present.

        Falls back to an empty string (placeholder icon) when there
        isn't one yet.
        """
        p = self._thumbnail_path(asset.id)
        if p is not None and p.exists():
            return p
        return ""

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
                lambda aid=asset_id: self._select_thumbnail(aid),
            ),
            (
                "Capture thumbnail",
                lambda aid=asset_id: self._capture_thumbnail(aid),
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
                            self._remove_member_from_group(aid, c),
                    ))
                elif kind == "scene":
                    # Roots don't store shot membership in their
                    # ``assets`` list (shots point at a Root via
                    # ``scene_ref``), so this branch only fires for
                    # asset cards. The unified removal path covers it.
                    items.append((
                        f"Remove from Root: {label}",
                        lambda aid=asset_id, c=cid:
                            self._remove_member_from_root(aid, c),
                    ))
        return items

    def _remove_member_from_root(
        self, asset_id: str, collection_id: str,
    ) -> None:
        """Remove an asset from a Root's member list.

        Wraps :meth:`remove_assets_from_collection` (the scene branch
        prunes the Root's ``assets`` and re-writes the JSON; staging
        is the user's manual ``Export Root USD`` action). Surfaces
        status + refreshes the affected detail panels.
        """
        if not asset_id or not collection_id:
            return
        try:
            removed, skipped, msg = self.remove_assets_from_collection(
                collection_id, [asset_id],
            )
        except Exception:
            log.exception(
                "remove_assets_from_collection failed for %s / %s",
                collection_id, asset_id,
            )
            return
        if msg:
            try:
                import hou
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )
            except Exception:
                log.info("status: %s", msg)
        if removed:
            self._invalidate_membership_cache()
            try:
                self._request_global_detail_refresh()
            except Exception:
                pass
            # Re-query the grid so the removed asset disappears from
            # the current view immediately (otherwise the user sees a
            # phantom card until the next refresh button click).
            try:
                self._request_global_grid_refresh()
            except Exception:
                pass

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
        proj = self._project_for_asset_id(assets[0].id)
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
            uri = self._uri_for_asset_id(a.id)
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
            uri = self._uri_for_asset_id(asset_id)
            if uri is None:
                return False
            return scene_mod.get_scene_ref(uri) is not None
        except Exception:
            return False

    def _clear_shot_scene_ref(self, asset_id: str) -> None:
        try:
            from tumblepipe.config import scene as scene_mod
            uri = self._uri_for_asset_id(asset_id)
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
                        self._open_group_workfile(aid, dn),
                ),
                (
                    "Open Location",
                    lambda aid=asset_id, dn=dept:
                        self._open_group_dept_work_dir(aid, dn),
                ),
                ("__separator__", None),
                (
                    "New: Template",
                    lambda aid=asset_id, dn=dept:
                        self._new_group_from_template(
                            aid, dn, self._request_global_detail_refresh,
                        ),
                ),
            ]

        if self._uri_for_asset_id(asset_id) is None:
            return []
        versions = self._get_department_workfile_info(asset_id).get(dept, [])
        available = bool(versions)
        latest = sorted(versions)[-1] if available else None
        scene_dv = self._get_scene_dept_version(asset_id)
        is_active = bool(scene_dv) and scene_dv[0] == dept

        items: list = []
        if available:
            items.append((
                f"Open Latest ({latest})",
                lambda aid=asset_id, dn=dept, v=latest:
                    self._open_version_now(aid, dn, v, None),
            ))
            items.append((
                "Open Location",
                lambda aid=asset_id, dn=dept:
                    self._open_dept_work_dir(aid, dn),
            ))
            items.append((
                "View Latest Export",
                lambda aid=asset_id, dn=dept:
                    self._open_latest_export(aid, dn),
            ))
            items.append((
                "Open in New Houdini",
                lambda aid=asset_id, dn=dept:
                    self._open_in_new_instance(aid, dn),
            ))
            if is_active:
                items.append((
                    "Reload Scene",
                    lambda: self._reload_current_scene(None),
                ))
        else:
            items.append((
                "Open Location",
                lambda aid=asset_id, dn=dept:
                    self._open_dept_work_dir(aid, dn),
            ))

        # "Remove from <group>" — visible only when this dept is
        # currently covered by a group's workfile for this member.
        # Removing the member from the group is global (affects every
        # dept the group covers for this member), so the label is
        # phrased as "Remove from <group>" rather than per-dept.
        dept_groups = self._dept_groups_for_member(asset_id)
        group_info = dept_groups.get(dept)
        if group_info:
            group_id, group_label = group_info
            items.append(("__separator__", None))
            items.append((
                f"Remove from {group_label}",
                lambda aid=asset_id, gid=group_id:
                    self._remove_member_from_group(aid, gid),
            ))

        items.append(("__separator__", None))
        items.append((
            "New: Current",
            lambda aid=asset_id, dn=dept:
                self._new_from_current(
                    aid, dn, self._request_global_detail_refresh,
                ),
        ))
        items.append((
            "New: Template",
            lambda aid=asset_id, dn=dept:
                self._new_from_template(
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
        proj = self._project_for_asset_id(asset_id)
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
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return
        project_name, second, third = parts
        root = self._project_root_for_asset_id(asset_id)
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
            latest = sorted(versions)[-1]
            hip_path = self._workfile_path_for(asset_id, dept, latest)
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

    _database_window = None  # singleton DatabaseWindow

    def _open_database_editor(self, asset_id: str) -> None:
        """Open the pipeline DatabaseWindow with the entity pre-selected."""
        proj = self._project_for_asset_id(asset_id)
        if proj is not None:
            self._activate_project(proj)
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return
        try:
            from tumblepipe.api import default_client
            from tumblepipe.pipe.houdini.ui.project_browser.windows import (
                DatabaseWindow,
            )

            import hou
            parent = hou.qt.mainWindow()
            # Reuse or create the singleton window
            w = PipelineCatalog._database_window
            if w is None or not w.isVisible():
                api = default_client()
                w = DatabaseWindow(api, parent=parent)
                PipelineCatalog._database_window = w
            w.select_entity(uri)
            w.show()
            w.raise_()
            w.activateWindow()
        except Exception:
            log.exception("Failed to open database editor for %s", asset_id)

    def _edit_description(self, asset_id: str) -> None:
        """Open a multiline text dialog to edit the description sidecar."""
        try:
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
            if not self._write_description(asset_id, text):
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
        except Exception:
            log.exception("Edit description failed for %s", asset_id)

    def _manage_todos(self, asset) -> None:
        """Open the per-asset todos dialog."""
        try:
            import asset_browser
            import hou
            from asset_browser.ui.todos_dialog import TodosDialog
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
            import asset_browser
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
            from asset_browser.ui.browser import AssetBrowserWidget
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
            from asset_browser.ui.browser import AssetBrowserWidget
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
            from asset_browser.ui.detail_panel import DetailPanel
            for w in QApplication.allWidgets():
                if isinstance(w, DetailPanel):
                    try:
                        w.detail_refresh_requested.emit()
                    except Exception:
                        pass
        except Exception:
            log.debug("Global detail refresh failed")

    # ── Thumbnail actions ─────────────────────────────────

    def _select_thumbnail(self, asset_id: str) -> None:
        """Pick an image off disk and write it to the asset's
        ``thumbnail.png`` sidecar."""
        try:
            from PySide6.QtWidgets import QFileDialog
            from PySide6.QtGui import QImage
            import hou

            out_path = self._thumbnail_path(asset_id)
            if out_path is None:
                return

            parent = hou.qt.mainWindow()
            src, _ = QFileDialog.getOpenFileName(
                parent,
                "Select Thumbnail",
                "",
                "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)",
            )
            if not src:
                return

            img = QImage(src)
            if img.isNull():
                hou.ui.setStatusMessage(
                    f"Could not load image: {src}",
                    severity=hou.severityType.Warning,
                )
                return

            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not img.save(str(out_path), "PNG"):
                hou.ui.setStatusMessage(
                    f"Failed to write thumbnail to {out_path}",
                    severity=hou.severityType.Warning,
                )
                return

            log.info("Set thumbnail for %s -> %s", asset_id, out_path)
            hou.ui.setStatusMessage(
                f"Thumbnail set for {asset_id}",
                severity=hou.severityType.Message,
            )
            self._request_thumbnail_refresh(asset_id)
        except Exception:
            log.exception("Select thumbnail failed for %s", asset_id)

    def _capture_thumbnail(self, asset_id: str) -> None:
        """Capture the current frame from the active Scene Viewer
        and write it to the asset's ``thumbnail.png`` sidecar."""
        try:
            import hou

            out_path = self._thumbnail_path(asset_id)
            if out_path is None:
                return

            sv = self._find_active_scene_viewer()
            if sv is None:
                hou.ui.setStatusMessage(
                    "Capture: no active Scene Viewer.",
                    severity=hou.severityType.Warning,
                )
                return

            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Wipe any stale file so the post-capture mtime check works.
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                log.debug("Could not remove old thumbnail at %s", out_path)

            try:
                settings = sv.flipbookSettings().stash()
                settings.output(str(out_path))
                settings.outputToMPlay(False)
                cur = int(hou.frame())
                settings.frameRange((cur, cur))
                settings.useResolution(True)
                settings.resolution((512, 512))
                sv.flipbook(sv.curViewport(), settings)
            except Exception:
                log.exception("flipbook capture failed for %s", asset_id)
                return

            # Houdini sometimes inserts a frame-number into the
            # output filename even for a single-frame range
            # (e.g. ``thumbnail.0042.png``). If the literal path is
            # missing, glob the parent for any ``thumbnail*.png`` and
            # rename the newest match to the canonical sidecar path.
            if not out_path.exists():
                cands = sorted(
                    out_path.parent.glob("thumbnail*.png"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if cands:
                    try:
                        cands[0].replace(out_path)
                    except Exception:
                        log.exception(
                            "Could not rename %s -> %s",
                            cands[0], out_path,
                        )

            if not out_path.exists():
                hou.ui.setStatusMessage(
                    f"Capture: nothing was written to {out_path}",
                    severity=hou.severityType.Warning,
                )
                return

            log.info("Captured thumbnail for %s -> %s", asset_id, out_path)
            hou.ui.setStatusMessage(
                f"Captured thumbnail for {asset_id}",
                severity=hou.severityType.Message,
            )
            self._request_thumbnail_refresh(asset_id)
        except Exception:
            log.exception("Capture thumbnail failed for %s", asset_id)

    def _find_active_scene_viewer(self):
        """Return the currently active ``hou.SceneViewer``, or any
        Scene Viewer if none is currently focused, or ``None``."""
        try:
            import hou
            scene_viewers = []
            for tab in hou.ui.paneTabs():
                if isinstance(tab, hou.SceneViewer):
                    scene_viewers.append(tab)
            if not scene_viewers:
                return None
            for sv in scene_viewers:
                try:
                    if sv.isCurrentTab():
                        return sv
                except Exception:
                    continue
            return scene_viewers[0]
        except Exception:
            log.debug("Failed to find SceneViewer")
            return None

    def _request_thumbnail_refresh(self, asset_id: str) -> None:
        """Drop the asset's thumbnail caches and trigger a re-load.

        Walks ``QApplication.allWidgets()`` for any open
        :class:`AssetBrowserWidget`, finds the matching card in the
        grid, and asks the browser's :class:`ThumbnailLoader` to
        invalidate it. The loader's ``on_ready`` callback will repaint
        the card automatically once the new pixmap arrives.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from asset_browser.ui.browser import AssetBrowserWidget
            for w in QApplication.allWidgets():
                if not isinstance(w, AssetBrowserWidget):
                    continue
                loader = getattr(w, "_thumb_loader", None)
                if loader is None:
                    continue
                grid = getattr(w, "_grid", None)
                if grid is None:
                    continue
                for card in getattr(grid, "_cards", []):
                    a = getattr(card, "asset", None)
                    if a is not None and a.id == asset_id:
                        loader.invalidate(a)
        except Exception:
            log.debug("Thumbnail refresh failed for %s", asset_id)
        # Also re-fetch the detail panel so its preview area picks up
        # the new image immediately.
        self._request_global_detail_refresh()

    # ── Detail panel layout ───────────────────────────────

    def get_detail_sections(
        self, detail: AssetDetail,
    ) -> list[DetailSection] | None:
        # Container details (Groups / Scenes) get a slim section list
        # — no scene-actions row, no todos. Groups also get a
        # Departments tab where the user can toggle which depts the
        # group covers; scenes don't (no editable depts field).
        kind = detail.metadata.get("kind")
        if kind == "group":
            return [
                DetailSection(
                    key="info",
                    title="Info",
                    icon="info",
                    widget_factory=self._build_container_info_section,
                ),
                # Multis share the rich asset/shot Departments section
                # so version dropdowns + meta rows render uniformly.
                # The per-row coverage toggle is injected when
                # ``is_multi`` is detected.
                DetailSection(
                    key="departments",
                    title="Departments",
                    icon="layers-3",
                    widget_factory=self._build_combined_departments_section,
                ),
            ]
        if kind == "scene":
            return [
                DetailSection(
                    key="info",
                    title="Info",
                    icon="info",
                    widget_factory=self._build_container_info_section,
                ),
            ]

        # Asset actions live at the TOP of the detail panel so their
        # placement is consistent across assets — the user always knows
        # where Save/Publish/Refresh are regardless of selection.
        # Section title shows the loaded scene context
        # ('PROP/TemplateTest / lookdev / v0024'), or 'Scene Actions'
        # when there's no pipeline scene loaded.
        scene_ctx = self._get_loaded_scene_context()
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
                widget_factory=self._build_combined_info_section,
            ),
            DetailSection(
                key="departments",
                title="Departments",
                icon="layers-3",
                widget_factory=self._build_combined_departments_section,
            ),
            DetailSection(
                key="todos",
                title="Tasks",
                icon="list-todo",
                widget_factory=self._build_todos_section,
            ),
        ]
        # Stash for use inside the combined info section (the actions
        # widget builds inline rather than as a tab now).
        self._actions_section_title = actions_title
        return sections

    def _build_combined_info_section(self, ctx: DetailContext):
        """Info tab content — identity breadcrumb, filesystem path,
        the per-kind info table, and the description paragraph.

        Action buttons live in the DetailPanel's sticky bottom bar now;
        this section is purely descriptive.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
        detail = ctx.detail

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Identity breadcrumb (URI) and filesystem path — uniform across
        # all asset kinds where the data is resolvable.
        breadcrumb = self._build_identity_breadcrumb(detail)
        if breadcrumb is not None:
            vbox.addWidget(breadcrumb)
        path_row = self._build_path_row(detail)
        if path_row is not None:
            vbox.addWidget(path_row)

        # Per-kind info table.
        if "type:asset" in detail.tags:
            info_w = self._build_asset_info_section(ctx)
        elif "type:shot" in detail.tags:
            info_w = self._build_shot_info_section(ctx)
        else:
            info_w = None
        if info_w is not None:
            vbox.addWidget(info_w)

        # Description (plain paragraph) — last so the structured
        # metadata above is the first thing the eye lands on.
        if detail.description:
            desc = QLabel(detail.description)
            desc.setWordWrap(True)
            desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
            vbox.addWidget(desc)

        vbox.addStretch(1)
        return holder

    def _build_identity_breadcrumb(self, detail):
        """Render the entity URI as a copyable monospaced breadcrumb,
        or ``None`` if no URI is resolvable for this detail kind.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QWidget,
        )
        from asset_browser.core.icons import icon as make_icon
        from asset_browser.core.theme import (
            BG_MID, BORDER, FONT_SMALL, TEXT_DIM, TEXT_SECONDARY, scaled,
        )

        segs = self._resolve_identity_segments(detail)
        if not segs:
            return None
        text = " / ".join(segs)

        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scaled(4))

        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {FONT_SMALL}px; color: {TEXT_SECONDARY}; "
            f"background: {BG_MID}; border: 1px solid {BORDER}; "
            f"border-radius: {scaled(3)}px; padding: 2px 6px;"
        )
        lbl.setWordWrap(True)
        row.addWidget(lbl, stretch=1)

        copy_btn = QPushButton()
        copy_btn.setFixedSize(scaled(20), scaled(20))
        copy_btn.setIcon(make_icon("copy", scaled(12), TEXT_DIM))
        copy_btn.setToolTip("Copy URI to clipboard")
        copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {BG_MID}; "
            f"border-radius: {scaled(3)}px; }}"
        )
        copy_btn.clicked.connect(
            lambda _checked=False, t=text: self._copy_to_clipboard(t)
        )
        row.addWidget(copy_btn)
        return w

    def _build_path_row(self, detail):
        """Render the export-folder path as a click-to-copy
        monospaced row, or ``None`` if there is no resolvable path.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QWidget,
        )
        from asset_browser.core.icons import icon as make_icon
        from asset_browser.core.theme import (
            BG_MID, BORDER, FONT_TINY, TEXT_DIM, scaled,
        )

        try:
            path = self._resolve_export_path(detail.id if detail else "")
        except Exception:
            log.debug("export-path resolution failed", exc_info=True)
            return None
        if not path:
            return None

        text = str(path)
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scaled(4))

        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {FONT_TINY}px; color: {TEXT_DIM}; "
            f"background: transparent; border: none;"
        )
        lbl.setWordWrap(True)
        lbl.setToolTip(text)
        row.addWidget(lbl, stretch=1)

        copy_btn = QPushButton()
        copy_btn.setFixedSize(scaled(20), scaled(20))
        copy_btn.setIcon(make_icon("copy", scaled(12), TEXT_DIM))
        copy_btn.setToolTip("Copy path to clipboard")
        copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {BG_MID}; "
            f"border-radius: {scaled(3)}px; }}"
        )
        copy_btn.clicked.connect(
            lambda _checked=False, t=text: self._copy_to_clipboard(t)
        )
        row.addWidget(copy_btn)
        return w

    def _resolve_identity_segments(self, detail):
        """Return the breadcrumb segments for an asset's URI, or
        ``None`` when no identity is resolvable.

        Splits ``detail.id`` (``project/CHAR/Baby`` form) and appends
        variants if present.
        """
        meta = detail.metadata or {}
        parts = (detail.id or "").split("/")
        parts = [p for p in parts if p]
        if not parts:
            return None
        variants = meta.get("variants") or []
        if "type:asset" in detail.tags and variants:
            return parts + ["/".join(variants)] if len(variants) > 1 \
                else parts + [str(variants[0])]
        return parts

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            log.debug("clipboard copy failed", exc_info=True)

    def _build_container_info_section(self, ctx: DetailContext):
        """Info tab content for a Group/Scene detail.

        Shows identity (project / context / path) and member count.
        For groups, the editable departments toggle lives on its own
        Departments tab (see :meth:`_build_group_departments_section`).
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QGridLayout,
            QLabel,
            QVBoxLayout,
            QWidget,
        )

        detail = ctx.detail
        meta = detail.metadata or {}
        kind = meta.get("kind", "")
        proj_name = meta.get("project", "")
        path = meta.get("path", "")
        member_count = int(meta.get("member_count") or 0)
        ctx_label = meta.get("context", "")

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        # Identity grid — project / context / path / members
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        row = 0

        def _row(label: str, value: str) -> None:
            nonlocal row
            k = QLabel(label)
            k.setStyleSheet("color: #888;")
            v = QLabel(value)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(k, row, 0, Qt.AlignTop)
            grid.addWidget(v, row, 1)
            row += 1

        # Display label decouples from internal ``kind`` slug — the
        # data model still calls these "groups", but the UI surfaces
        # them as "Multi" since the term reads more concretely.
        _KIND_DISPLAY = {"group": "Multi", "scene": "Root"}
        if proj_name:
            _row("Project", proj_name)
        if kind:
            _row("Type", _KIND_DISPLAY.get(kind, kind.capitalize()))
        if ctx_label and kind == "group":
            _row("Context", ctx_label.capitalize())
        if path:
            _row("Path", path)
        unit = "asset" if kind == "scene" else "member"
        _row("Members", f"{member_count} {unit}{'' if member_count == 1 else 's'}")

        vbox.addLayout(grid)

        # Per-dept "Open" buttons — groups only. The deck on the
        # group card surfaces the same actions, but the buttons here
        # are more discoverable for users who haven't found the deck
        # expand affordance yet.
        group_depts = list(meta.get("departments") or ())
        if kind == "group" and group_depts:
            from PySide6.QtWidgets import (
                QHBoxLayout,
                QLabel,
                QPushButton,
            )
            heading = QLabel("Work scenes")
            heading.setStyleSheet("color: #aaa; font-weight: bold;")
            vbox.addWidget(heading)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            for dept_name in group_depts:
                short = _DEPT_SHORT_NAMES.get(
                    dept_name, dept_name.title(),
                )
                btn = QPushButton(f"Open {short}")
                btn.setToolTip(
                    f"Open the latest {dept_name} workfile for this Multi",
                )
                btn.clicked.connect(
                    lambda checked=False, d=dept_name:
                        self.execute_action(
                            f"open_workfile:{d}", detail,
                        )
                )
                btn_row.addWidget(btn)
            btn_row.addStretch(1)
            vbox.addLayout(btn_row)

        vbox.addStretch(1)
        return holder

    def _build_group_departments_section(self, ctx: DetailContext):
        """Departments tab content for a Group detail.

        Inline checkbox list mirroring the dialog-based Edit flow's
        ``departments`` multi-select. Commits via :meth:`edit_collection`.
        """
        from PySide6.QtWidgets import (
            QCheckBox,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        detail = ctx.detail
        meta = detail.metadata or {}
        depts_current = list(meta.get("departments") or ())
        known_depts = list(meta.get("known_departments") or ())

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        if not known_depts:
            empty = QLabel("(no departments defined for this context)")
            empty.setStyleSheet("color: #666;")
            vbox.addWidget(empty)
            vbox.addStretch(1)
            return holder

        boxes_holder = QWidget()
        boxes_lyt = QVBoxLayout(boxes_holder)
        boxes_lyt.setContentsMargins(0, 0, 0, 0)
        boxes_lyt.setSpacing(2)
        current_set = {str(d) for d in depts_current}
        boxes: list[QCheckBox] = []
        for d in known_depts:
            cb = QCheckBox(d)
            cb.setChecked(d in current_set)
            boxes_lyt.addWidget(cb)
            boxes.append(cb)
        vbox.addWidget(boxes_holder)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        apply_btn = QPushButton("Apply")
        btn_row.addWidget(apply_btn)
        vbox.addLayout(btn_row)

        cid = detail.id  # container id == tag

        def _apply() -> None:
            selected = tuple(
                cb.text() for cb in boxes if cb.isChecked()
            )
            try:
                ok = bool(self.edit_collection(
                    cid, {"departments": selected},
                ))
            except Exception:
                log.exception("edit_collection failed for %s", cid)
                ok = False
            if ok and ctx.refresh_detail is not None:
                try:
                    ctx.refresh_detail()
                except Exception:
                    log.exception("refresh_detail failed")

        apply_btn.clicked.connect(_apply)
        vbox.addStretch(1)
        return holder

    def _build_combined_departments_section(self, ctx: DetailContext):
        """Departments tab content — Multi / Root membership pills on
        top (a shot's Root-ref is conceptually the root layer of its
        department stack), then the departments grid below.

        Wrapped defensively: if either sub-builder throws, we still
        return a non-empty widget so the detail panel keeps the
        Departments tab visible.
        """
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        try:
            mem_w = self._build_membership_section(ctx)
        except Exception:
            log.exception("Membership section build failed")
            mem_w = None
        if mem_w is not None:
            vbox.addWidget(mem_w)

        try:
            depts_w = self._build_departments_section(ctx)
        except Exception:
            log.exception(
                "Departments section build failed for %s", ctx.detail.id,
            )
            depts_w = None
        if depts_w is not None:
            vbox.addWidget(depts_w)
        else:
            err = QLabel(
                "(failed to load departments — see log for traceback)"
            )
            err.setStyleSheet("color: #f06060;")
            err.setWordWrap(True)
            vbox.addWidget(err)

        vbox.addStretch(1)
        return holder

    def _build_asset_info_section(self, ctx: DetailContext):
        detail = ctx.detail
        meta = detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        if meta.get("category"):
            rows.append(("Category", str(meta["category"])))
        variants = meta.get("variants") or []
        if variants:
            rows.append(("Variants", ", ".join(variants)))
        if detail.versions:
            latest = detail.versions[-1]
            rows.append(("Latest", getattr(latest, "version", "") or ""))
        return self._build_info_table(rows)

    def _build_shot_info_section(self, ctx: DetailContext):
        meta = ctx.detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        if meta.get("sequence"):
            rows.append(("Sequence", str(meta["sequence"])))
        fs = meta.get("frame_start")
        fe = meta.get("frame_end")
        ft = meta.get("frame_total")
        if fs is not None:
            rows.append(("Frame Start", str(fs)))
        if fe is not None:
            rows.append(("Frame End", str(fe)))
        if ft is not None:
            rows.append(("Frame Total", str(ft)))
        if meta.get("fps") is not None:
            rows.append(("FPS", str(meta["fps"])))

        # Scene reference — resolved (including sequence inheritance).
        scene_label, inherited = self._shot_scene_label(ctx.detail.id)
        if scene_label:
            suffix = " (inherited)" if inherited else ""
            rows.append(("Root", f"{scene_label}{suffix}"))
        return self._build_info_table(rows)

    def _shot_scene_label(self, asset_id: str) -> tuple[str, bool]:
        """Return ``(label, inherited)`` for the scene currently
        attached to a shot, or ``("", False)`` if none.

        ``inherited`` is True when the scene_ref is inherited from the
        parent sequence rather than set on the shot itself.
        """
        try:
            parts = asset_id.split("/", 1)
            if len(parts) != 2:
                return ("", False)
            project_name = parts[0]
            proj = self._registry.get(project_name)
            if proj is None or not self._is_ready(project_name):
                return ("", False)
            self._activate_project(proj)
            from tumblepipe.config import scene as scene_mod
            shot_uri = self._uri_for_asset_id(asset_id)
            if shot_uri is None:
                return ("", False)
            direct = scene_mod.get_scene_ref(shot_uri)
            if direct is not None:
                scene_uri = direct
                inherited = False
            else:
                resolved, _src = scene_mod.get_inherited_scene_ref(shot_uri)
                if resolved is None:
                    return ("", False)
                scene_uri = resolved
                inherited = True
            segs = getattr(scene_uri, "segments", None)
            label = (
                "/".join(segs) if segs else str(scene_uri)
            )
            return (label, inherited)
        except Exception:
            log.debug(
                "Failed to read scene_ref for %s", asset_id, exc_info=True,
            )
            return ("", False)

    def _build_info_table(self, rows: list[tuple[str, str]]):
        """Build a label/value grid section box from a list of rows.
        Returns ``None`` when there are no rows."""
        from PySide6.QtWidgets import QGridLayout, QLabel
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            FONT_FAMILY, FONT_SMALL, TEXT_DIM, TEXT_SECONDARY, scaled,
        )

        if not rows:
            return None

        w, lay = make_section_box()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(scaled(8))
        grid.setVerticalSpacing(scaled(2))
        label_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        value_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; border: none; background: transparent;"
        )
        for row, (label, value) in enumerate(rows):
            key_lbl = QLabel(label)
            key_lbl.setStyleSheet(label_style)
            key_lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
            grid.addWidget(key_lbl, row, 0)
            val_lbl = QLabel(value)
            val_lbl.setStyleSheet(value_style)
            val_lbl.setWordWrap(True)
            grid.addWidget(val_lbl, row, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        return w

    def _build_membership_section(self, ctx: DetailContext):
        """Show pills for every group and scene this asset belongs to."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            ACCENT, BG_MID, BORDER, FONT_FAMILY, FONT_SMALL,
            TEXT_DIM, TEXT_SECONDARY,
        )

        memberships = self.get_asset_membership(ctx.detail.id)
        if not memberships:
            return None

        w, lay = make_section_box()
        flow = QHBoxLayout()
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(4)

        pill_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; background-color: {BG_MID}; "
            f"border: 1px solid {BORDER}; border-radius: 10px; "
            f"padding: 2px 8px;"
        )
        for _cid, label, kind in memberships:
            prefix = "M" if kind == "group" else "R"
            pill = QLabel(f"{prefix}  {label}")
            pill.setStyleSheet(pill_style)
            pill.setToolTip(f"{'Multi' if kind == 'group' else 'Root'}: {label}")
            flow.addWidget(pill)
        flow.addStretch(1)
        lay.addLayout(flow)
        return w

    def _build_todos_section(self, ctx: DetailContext):
        """List the asset's todos with toggleable checkboxes + an inline
        add row and a "clear all" button.
        """
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QLineEdit, QPushButton,
        )
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.ui.lucide_checkbox import LucideCheckBox
        from asset_browser.core.icons import icon
        from asset_browser.core.theme import (
            ACCENT, BG_DARK, BORDER, FONT_BODY, FONT_FAMILY,
            FONT_SMALL, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )

        try:
            import asset_browser
            mgr = asset_browser.get_todos()
        except Exception:
            mgr = None
        if mgr is None:
            return None
        self._hook_todo_refresh(mgr)

        asset_id = ctx.detail.id
        todos = mgr.todos(self.id, asset_id)
        w, lay = make_section_box()

        _flat_btn = (
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { background-color: rgba(255,255,255,24);"
            "                    border-radius: 3px; }"
        )

        if not todos:
            empty = QLabel("No tasks.")
            empty.setStyleSheet(
                f'color: {TEXT_DIM}; font-family: "{FONT_FAMILY}"; '
                f"font-size: {FONT_SMALL}px; background: transparent;"
            )
            lay.addWidget(empty)
        else:
            for idx, todo in enumerate(todos):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                cb = LucideCheckBox(todo.get("text", ""))
                cb.setChecked(bool(todo.get("done")))
                cb.setTextColor(TEXT_DIM if todo.get("done") else TEXT_PRIMARY)
                cb.toggled.connect(
                    lambda done, i=idx: mgr.set_done(
                        self.id, asset_id, i, done,
                    )
                )
                row.addWidget(cb, stretch=1)
                rm = QPushButton()
                rm.setIcon(icon("x", 12, TEXT_DIM))
                rm.setFlat(True)
                rm.setFixedSize(20, 20)
                rm.setCursor(Qt.PointingHandCursor)
                rm.setStyleSheet(_flat_btn)
                rm.clicked.connect(
                    lambda checked=False, i=idx: mgr.remove(
                        self.id, asset_id, i,
                    )
                )
                row.addWidget(rm)
                lay.addLayout(row)

        # Footer: add-new input + clear-all button
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 6, 0, 0)
        footer.setSpacing(4)
        add_input = QLineEdit()
        add_input.setPlaceholderText("Add task…")
        add_input.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {BG_DARK};"
            f"  color: {TEXT_PRIMARY};"
            f"  border: 1px solid {BORDER};"
            f"  border-radius: 3px;"
            f"  padding: 2px 6px;"
            f'  font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px;'
            f"}}"
            f"QLineEdit:focus {{ border-color: {ACCENT}; }}"
        )

        def _do_add():
            text = add_input.text().strip()
            if not text:
                return
            add_input.clear()
            mgr.add(self.id, asset_id, text)

        add_input.returnPressed.connect(_do_add)
        footer.addWidget(add_input, stretch=1)

        add_btn = QPushButton()
        add_btn.setIcon(icon("plus", 14, TEXT_SECONDARY))
        add_btn.setFlat(True)
        add_btn.setFixedSize(22, 22)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setToolTip("Add task")
        add_btn.setStyleSheet(_flat_btn)
        add_btn.clicked.connect(_do_add)
        footer.addWidget(add_btn)

        if todos:
            from PySide6.QtWidgets import QMenu
            from asset_browser.core.theme import MENU_STYLE
            clear_btn = QPushButton()
            clear_btn.setIcon(icon("brush-cleaning", 14, TEXT_SECONDARY))
            clear_btn.setFlat(True)
            clear_btn.setFixedSize(22, 22)
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setToolTip("Clear tasks")
            clear_btn.setStyleSheet(_flat_btn)

            def _show_clear_menu():
                m = QMenu(clear_btn)
                m.setStyleSheet(MENU_STYLE)
                a_done = m.addAction("Clear completed")
                a_all = m.addAction("Clear all")
                chosen = m.exec(
                    clear_btn.mapToGlobal(
                        clear_btn.rect().bottomLeft()
                    )
                )
                if chosen is a_done:
                    mgr.clear_completed(self.id, asset_id)
                elif chosen is a_all:
                    mgr.clear(self.id, asset_id)

            clear_btn.clicked.connect(_show_clear_menu)
            footer.addWidget(clear_btn)

        lay.addLayout(footer)
        return w

    def _build_departments_section(self, ctx: DetailContext):
        from PySide6.QtWidgets import (
            QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
            QVBoxLayout,
        )
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
        from PySide6.QtGui import QFont, QFontMetrics
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            ACCENT, BG_DARK, BG_MID, BORDER, BUTTON_GHOST_STYLE, COMBO_STYLE,
            FONT_FAMILY, FONT_SMALL, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
            scaled,
        )
        # Reach into the theme module's private chevron-PNG renderer so
        # the per-row stylesheet can include the same chevron without
        # depending on QSS rule-merge semantics (which silently drop the
        # chevron's ``image`` property on some Qt builds when a separate
        # rule for ``QComboBox::down-arrow`` is concatenated). The
        # helper is private to tumbletrove and has been removed/renamed
        # in newer releases — fall back to ``None`` (the row stylesheet
        # already handles that gracefully via ``image: none``).
        from asset_browser.core import theme as _theme_mod
        _render_chevron = getattr(
            _theme_mod, "_render_combo_arrow_png", None,
        )
        _arrow_url = (
            _render_chevron(TEXT_SECONDARY)
            if callable(_render_chevron)
            else None
        )

        # Reusable QFont matching the QSS-applied combo font. Needed because
        # ``QWidget.font()``/``fontMetrics()`` don't reflect stylesheet-set
        # fonts, so QFontMetrics(widget.font()) under-measures combo text.
        _qss_font = QFont(FONT_FAMILY)
        _qss_font.setPixelSize(FONT_SMALL)

        # Pre-render the meta-row Lucide icons (clock for "edited", package
        # for "exported") to base64 PNG data URIs so QLabel rich text can
        # render them inline via <img> without writing cache files.
        def _icon_html(name: str, size: int = 11) -> str:
            try:
                import base64
                from asset_browser.core.icons import icon_pixmap
                pix = icon_pixmap(name, scaled(size), TEXT_DIM)
                arr = QByteArray()
                buf = QBuffer(arr)
                buf.open(QIODevice.WriteOnly)
                pix.save(buf, "PNG")
                b64 = base64.b64encode(bytes(arr)).decode("ascii")
                return (
                    f'<img src="data:image/png;base64,{b64}" '
                    f'width="{size}" height="{size}"/>'
                )
            except Exception:
                return ""
        _user_html = _icon_html("user-round")
        _edited_html = _icon_html("clock")
        _exported_html = _icon_html("upload")
        # Approximate width of an inline icon (icon px + leading space) used
        # for measurement when deciding which candidate to render.
        _icon_w = scaled(13)

        class _TightVersionCombo(QComboBox):
            """QComboBox sized tight to its widest item + minimal chrome.

            Bypasses QStyle's generous default sizeHint (which doubles
            the visible width on Windows) and uses an explicit QFont
            matching the QSS so the measurement isn't off-by-DPI when
            the stylesheet font hasn't propagated to ``self.font()``.
            """

            def sizeHint(self):
                fm = QFontMetrics(_qss_font)
                widest = 0
                for i in range(self.count()):
                    widest = max(widest, fm.horizontalAdvance(self.itemText(i)))
                h = super().sizeHint().height()
                # text width + 6px left padding + 14px chevron + 2px borders
                # + 6px breathing room.
                return QSize(widest + scaled(28), h)

            def minimumSizeHint(self):
                return self.sizeHint()

        class _DeptNameLabel(QLabel):
            """Three-stage shrinking name label:

            * full name when the column has room
            * the optional short/abbreviated label when full doesn't fit
            * "…"-elided short (or full, if no short) when even the short
              doesn't fit

            sizeHint is based on the full name so the layout grants room
            when available; minimumSizeHint matches the short label width
            (or a small floor) so the column yields cleanly on narrow
            panels.
            """

            def __init__(self, parent=None):
                super().__init__(parent)
                self._full = ""
                self._short = ""

            def setFullText(self, text: str, short: str = "") -> None:
                self._full = text or ""
                self._short = short or ""
                self.setToolTip(self._full)
                self.updateGeometry()
                self._refresh()

            def resizeEvent(self, e):
                super().resizeEvent(e)
                self._refresh()

            def sizeHint(self):
                fm = QFontMetrics(self.font())
                return QSize(
                    fm.horizontalAdvance(self._full) + scaled(2),
                    fm.height(),
                )

            def minimumSizeHint(self):
                fm = QFontMetrics(self.font())
                base = (
                    fm.horizontalAdvance(self._short)
                    if self._short else scaled(28)
                )
                return QSize(base + scaled(4), fm.height())

            def _refresh(self) -> None:
                fm = QFontMetrics(self.font())
                avail = max(0, self.width())
                if fm.horizontalAdvance(self._full) <= avail:
                    target = self._full
                elif self._short and fm.horizontalAdvance(self._short) <= avail:
                    target = self._short
                else:
                    candidate = self._short or self._full
                    target = fm.elidedText(candidate, Qt.ElideRight, avail)
                if target != QLabel.text(self):
                    QLabel.setText(self, target)

        class _DeptMetaLabel(QLabel):
            """Compact rich-text label with inline icons:

                user · 🕐 Nw ago · 📦 Md ago

            Drops pieces in priority order as the column shrinks:

                1. user + edited + exported   (full)
                2. edited + exported          (user dropped)
                3. edited                     (exported dropped)
                4. ""                         (nothing fits)

            Candidates are stored with ``{E}``/``{X}`` placeholder tokens;
            measurement substitutes them with an approximate icon-pixel
            cost, while rendering swaps them for the precomputed icon HTML.
            """

            def __init__(self, parent=None):
                super().__init__(parent)
                self._user = ""
                self._when = ""
                self._exported = ""
                self.setTextFormat(Qt.RichText)

            def set_parts(
                self, user: str, when: str, exported: str = "",
            ) -> None:
                self._user = user or ""
                self._when = when or ""
                self._exported = exported or ""
                self.updateGeometry()
                self._refresh()

            def resizeEvent(self, e):
                super().resizeEvent(e)
                self._refresh()

            def _candidates(self) -> list[str]:
                user = f"{{U}} {self._user}" if self._user else ""
                edited = f"{{E}} {self._when}" if self._when else ""
                exported = f"{{X}} {self._exported}" if self._exported else ""
                items = [s for s in (edited, exported) if s]
                full = " · ".join(items)
                out: list[str] = []
                if user and full:
                    out.append(f"{user} · {full}")
                if full:
                    out.append(full)
                if edited:
                    out.append(edited)
                elif exported:
                    out.append(exported)
                return out

            def _measure(self, tokenized: str) -> int:
                fm = QFontMetrics(self.font())
                n = (
                    tokenized.count("{U}")
                    + tokenized.count("{E}")
                    + tokenized.count("{X}")
                )
                plain = (
                    tokenized
                    .replace("{U}", "")
                    .replace("{E}", "")
                    .replace("{X}", "")
                )
                return fm.horizontalAdvance(plain) + n * _icon_w

            def _to_html(self, tokenized: str) -> str:
                return (
                    tokenized
                    .replace("{U}", _user_html)
                    .replace("{E}", _edited_html)
                    .replace("{X}", _exported_html)
                )

            def sizeHint(self):
                fm = QFontMetrics(self.font())
                cands = self._candidates()
                widest = cands[0] if cands else ""
                return QSize(self._measure(widest) + scaled(2), fm.height())

            def minimumSizeHint(self):
                fm = QFontMetrics(self.font())
                return QSize(0, fm.height())

            def _refresh(self) -> None:
                avail = max(0, self.width())
                target = ""
                for cand in self._candidates():
                    if self._measure(cand) <= avail:
                        target = self._to_html(cand)
                        break
                if target != QLabel.text(self):
                    QLabel.setText(self, target)

        asset_id = ctx.detail.id
        dept_info: dict = (ctx.detail.metadata or {}).get("departments", {})
        is_shot = "type:shot" in ctx.detail.tags
        is_multi = "type:group" in ctx.detail.tags
        # Multis carry their context (``shots`` / ``assets``) on the
        # detail metadata; uncovered depts still render as "missing"
        # rows so the user can toggle them on.
        if is_multi:
            ent_ctx = (
                (ctx.detail.metadata or {}).get("context") or "shots"
            )
        else:
            ent_ctx = "shots" if is_shot else "assets"
        all_depts = self._list_entity_departments(ent_ctx)
        dept_shorts = self._list_entity_dept_shorts(ent_ctx)
        overrides = self._dept_version_overrides.get(asset_id, {})
        scene_dv = self._get_scene_dept_version(asset_id)
        active_dept = scene_dv[0] if scene_dv else None
        active_version = scene_dv[1] if scene_dv else None
        # Coverage toggle state for Multis: depts the user has flagged
        # this Multi to override. Uncovered depts render as missing
        # rows with an unchecked toggle.
        covered_set: set[str] = set(
            (ctx.detail.metadata or {}).get("covered_departments", [])
        ) if is_multi else set()

        w, lay = make_section_box()

        if not all_depts:
            empty = QLabel("No departments found.")
            empty.setStyleSheet(
                f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
                f"color: {TEXT_DIM}; border: none; background: transparent;"
            )
            lay.addWidget(empty)
            return w

        rows_box = QVBoxLayout()
        rows_box.setContentsMargins(0, 0, 0, 0)
        rows_box.setSpacing(0)

        # Row background colors:
        #   - active row: solid accent tint
        #   - even rows : slightly lighter than the box background
        #   - odd rows  : transparent (matches the box background)
        even_bg = BG_DARK
        odd_bg = "transparent"
        active_bg = self._mix_hex(BG_MID, ACCENT, 0.35)

        name_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; border: none; background: transparent;"
        )
        active_name_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {ACCENT}; font-weight: bold; "
            f"border: none; background: transparent;"
        )
        missing_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent; "
            f"font-style: italic;"
        )
        user_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )

        for row, dept_name in enumerate(all_depts):
            versions = dept_info.get(dept_name) or []
            available = bool(versions)
            is_active = dept_name == active_dept

            row_bg = active_bg if is_active else (
                even_bg if (row % 2 == 0) else odd_bg
            )

            row_frame = QFrame()
            row_frame.setObjectName("deptRow")
            row_frame.setStyleSheet(
                f"QFrame#deptRow {{ background: {row_bg}; "
                f"border: none; border-radius: 3px; }}"
            )
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(scaled(4), scaled(2), scaled(4), scaled(2))
            row_layout.setSpacing(scaled(8))

            # Multi-only column: per-dept override toggle. Checked
            # means the Multi covers this dept (its workfile takes
            # precedence over individual member workfiles); unchecked
            # means the dept falls through to per-member workfiles.
            if is_multi:
                from PySide6.QtWidgets import QCheckBox
                toggle = QCheckBox()
                toggle.setChecked(dept_name in covered_set)
                toggle.setToolTip(
                    f"Override {dept_name} for all members of this Multi"
                )
                toggle.toggled.connect(
                    lambda checked, gid=asset_id, dn=dept_name:
                        self._toggle_group_dept_coverage(
                            gid, dn, bool(checked),
                        )
                )
                row_layout.addWidget(toggle)

            # Col 0: dept name (full → short → ellided as the column shrinks)
            name_lbl = _DeptNameLabel()
            if is_active:
                name_lbl.setStyleSheet(active_name_style)
            else:
                name_lbl.setStyleSheet(name_style if available else missing_style)
            short_name = dept_shorts.get(dept_name, "")
            name_lbl.setFullText(
                dept_name.title(),
                short=short_name.title() if short_name else "",
            )
            row_layout.addWidget(name_lbl)

            # Col 1: user · time-ago — right-aligned, drops user then date
            # as the row narrows (see _DeptMetaLabel above).
            user_lbl = _DeptMetaLabel()
            user_lbl.setStyleSheet(user_style)
            user_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_layout.addWidget(user_lbl, stretch=1)

            combo: QComboBox | None = None
            if available:
                ordered = list(reversed(versions))  # newest first
                # Default selection priority:
                #   1. previously chosen override
                #   2. version of the loaded scene (if this row is active)
                #   3. newest version
                if dept_name in overrides:
                    current = overrides[dept_name]
                elif is_active and active_version and active_version in versions:
                    current = active_version
                else:
                    current = ordered[0]

                # Col 2: version dropdown.
                #
                # We write a SINGLE stylesheet here (not COMBO_STYLE +
                # overrides) because Qt's QSS engine doesn't reliably
                # merge ``image:`` from a base ::down-arrow rule with a
                # later one — the chevron disappears. Inlining the
                # chevron PNG keeps it visible.
                #
                # Width is hardcoded based on font metrics for "v0001"
                # plus a stable chrome budget. _TightVersionCombo's
                # overridden sizeHint isn't used here (Qt's pre-paint
                # font metrics weren't reliable), but kept around for
                # widgets that need to expose an honest sizeHint.
                arrow_decl = (
                    f"image: url({_arrow_url});" if _arrow_url else "image: none;"
                )
                combo = QComboBox()
                combo.setStyleSheet(f"""
                    QComboBox {{
                        background-color: {BG_DARK};
                        border: 1px solid {BORDER};
                        border-radius: 4px;
                        padding: 4px 16px 4px 6px;
                        color: {TEXT_PRIMARY};
                        font-family: "{FONT_FAMILY}";
                        font-size: {FONT_SMALL}px;
                    }}
                    QComboBox:hover {{
                        border-color: {ACCENT};
                    }}
                    QComboBox::drop-down {{
                        subcontrol-origin: padding;
                        subcontrol-position: right center;
                        border: none;
                        width: 14px;
                    }}
                    QComboBox::down-arrow {{
                        {arrow_decl}
                        width: 10px;
                        height: 10px;
                        margin-right: 2px;
                    }}
                    QComboBox QAbstractItemView {{
                        background-color: {BG_DARK};
                        border: 1px solid {BORDER};
                        color: {TEXT_PRIMARY};
                        selection-background-color: {ACCENT};
                        selection-color: white;
                    }}
                """)
                for ver in ordered:
                    combo.addItem(ver, ver)
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                # Hardcoded fixed width that comfortably fits a "v0001"-
                # style label plus the 14px chevron + padding + borders at
                # FONT_SMALL = 12px. We avoid measurement-based sizing
                # because Qt's pre-paint QFontMetrics gives unreliable
                # results that have been collapsing this combo to ~25px.
                combo.setFixedWidth(scaled(72))
                combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                row_layout.addWidget(combo)

                # Col 3: open icon button — saves space vs the "Open" label
                # so narrow rows don't force the section box past the panel.
                from asset_browser.core.icons import icon as make_icon
                open_btn = QPushButton()
                open_btn.setIcon(make_icon("play", scaled(14), ACCENT))
                open_btn.setIconSize(QSize(scaled(14), scaled(14)))
                open_btn.setFixedSize(scaled(28), scaled(24))
                open_btn.setStyleSheet(BUTTON_GHOST_STYLE)
                open_btn.setToolTip("Open this version")
                open_btn.clicked.connect(
                    lambda _checked=False, c=combo, dn=dept_name,
                    aid=asset_id, rd=ctx.refresh_detail:
                        self._open_version_now(
                            aid, dn, c.currentData(), rd,
                        )
                )
                row_layout.addWidget(open_btn)

                # Compute the latest export age once per row — it doesn't
                # depend on which version is selected (the export folder
                # tracks the latest export for the dept).
                exported_age = self._format_relative_time(
                    self._get_latest_export_mtime(asset_id, dept_name)
                )

                # Initial fill + live update on combo change
                user_lbl.set_parts(
                    self._get_user_for_version(asset_id, dept_name, current) or "",
                    self._format_relative_time(
                        self._get_mtime_for_version(asset_id, dept_name, current)
                    ),
                    exported=exported_age,
                )

                def _on_change(
                    _idx,
                    c=combo, dn=dept_name, ulbl=user_lbl, aid=asset_id,
                    exp=exported_age,
                ):
                    v = c.currentData()
                    self._on_dept_version_picked(aid, dn, v)
                    ulbl.set_parts(
                        self._get_user_for_version(aid, dn, v) or "",
                        self._format_relative_time(
                            self._get_mtime_for_version(aid, dn, v)
                        ),
                        exported=exp,
                    )

                combo.currentIndexChanged.connect(_on_change)
            else:
                dash = QLabel("—")
                dash.setStyleSheet(missing_style)
                dash.setMinimumWidth(scaled(56))
                dash.setAlignment(Qt.AlignCenter)
                row_layout.addWidget(dash)

                # Match the Open button's icon style — clicking creates
                # v0001 from the dept template, which is intuitive enough
                # that the green play icon doubles for "create + open".
                from asset_browser.core.icons import icon as make_icon
                create_btn = QPushButton()
                create_btn.setIcon(make_icon("play", scaled(14), ACCENT))
                create_btn.setIconSize(QSize(scaled(14), scaled(14)))
                create_btn.setFixedSize(scaled(28), scaled(24))
                create_btn.setStyleSheet(BUTTON_GHOST_STYLE)
                create_btn.setToolTip(
                    f"Create {dept_name}/v0001 from the dept template."
                )
                create_btn.clicked.connect(
                    lambda _checked=False, dn=dept_name,
                    aid=asset_id, rd=ctx.refresh_detail:
                        self._new_from_template(aid, dn, rd)
                )
                row_layout.addWidget(create_btn)

            # ── Row-wide right-click context menu ──
            # Connect on the frame and the two labels (combo + button
            # have their own click semantics and stay exempt).
            menu_targets = [row_frame, name_lbl, user_lbl]
            for target in menu_targets:
                target.setContextMenuPolicy(Qt.CustomContextMenu)
                target.customContextMenuRequested.connect(
                    lambda pos, src=target, dn=dept_name, av=available,
                    ia=is_active, c=combo, aid=asset_id,
                    rd=ctx.refresh_detail:
                        self._show_dept_context_menu(
                            aid, dn, av, ia, c, src, rd, pos,
                        )
                )

            rows_box.addWidget(row_frame)

        lay.addLayout(rows_box)
        return w

    @staticmethod
    def _mix_hex(base_hex: str, accent_hex: str, ratio: float) -> str:
        """Linearly blend two ``#rrggbb`` colors. ``ratio`` is the share
        of ``accent_hex`` in the result (0..1)."""
        try:
            def _hx(s: str) -> tuple[int, int, int]:
                s = s.lstrip("#")
                return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            br, bg, bb = _hx(base_hex)
            ar, ag, ab = _hx(accent_hex)
            r = int(br + (ar - br) * ratio)
            g = int(bg + (ag - bg) * ratio)
            b = int(bb + (ab - bb) * ratio)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return base_hex

    def _build_asset_actions_section(self, ctx: DetailContext):
        """Save / Publish / Refresh — always operate on the loaded scene.

        The header (entity / dept / version) lives outside this box —
        it's set as the section ``title`` in :meth:`get_detail_sections`.
        """
        from PySide6.QtWidgets import QHBoxLayout, QPushButton
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE, scaled,
        )

        scene_ctx = self._get_loaded_scene_context()
        has_ctx = scene_ctx is not None

        w, lay = make_section_box()

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(scaled(4))

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        save_btn.setEnabled(has_ctx)
        if not has_ctx:
            save_btn.setToolTip(
                "The loaded scene has no pipeline context."
            )
        save_btn.clicked.connect(
            lambda _checked=False, rd=ctx.refresh_detail:
                self._save_current_scene(rd)
        )
        btn_row.addWidget(save_btn)

        pub_btn = QPushButton("Publish")
        pub_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        pub_btn.setEnabled(has_ctx)
        if not has_ctx:
            pub_btn.setToolTip(
                "The loaded scene has no pipeline context."
            )
        pub_btn.clicked.connect(
            lambda _checked=False, rd=ctx.refresh_detail:
                self._publish_current_scene(rd)
        )
        btn_row.addWidget(pub_btn)
        # Note: this detail-panel section is currently dead code (the
        # asset browser renders the Save/Publish buttons via the
        # quick-actions toolbar instead — see get_quick_actions +
        # get_quick_action_hover). The hover-info wiring for those
        # lives in the shared asset_browser/core/hover_info.py path.

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        refresh_btn.setToolTip(
            "Re-scan the selected asset and reload the details panel."
        )
        refresh_btn.clicked.connect(
            lambda _checked=False, aid=ctx.detail.id, rd=ctx.refresh_detail:
                self._refresh_asset(aid, rd)
        )
        btn_row.addWidget(refresh_btn)

        lay.addLayout(btn_row)
        return w

    def _get_loaded_scene_context(self):
        """Return the current scene's ``Context`` if it has pipeline metadata.

        Falls back to parsing the context from the hip file path for
        migrated projects that don't have ``context.json``.
        """
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_str = hou.hipFile.path()
            if not hip_str:
                return None
            hip_path = Path(hip_str)
            ctx = get_workfile_context(hip_path)
            if ctx is None:
                ctx = self._context_from_hip_path(hip_path)
            return ctx
        except Exception:
            return None

    def _on_dept_version_picked(
        self, asset_id: str, dept: str, version: str | None,
    ) -> None:
        if not version:
            return
        self._dept_version_overrides.setdefault(asset_id, {})[dept] = version
        log.debug(
            "Pipeline: dept version override %s/%s -> %s",
            asset_id, dept, version,
        )

    def _show_dept_context_menu(
        self, asset_id: str, dept: str, available: bool, is_active: bool,
        combo, source_widget, refresh_cb, pos=None,
    ) -> None:
        """Right-click menu for a dept row in the details panel.

        ``pos`` is the click position in ``source_widget`` coordinates;
        the menu pops at the cursor (mapped to global). Falls back to
        the widget's bottom-left when ``pos`` is None.
        """
        from PySide6.QtWidgets import QMenu
        from asset_browser.core.theme import MENU_STYLE

        menu = QMenu(source_widget)
        menu.setStyleSheet(MENU_STYLE)

        if available and combo is not None:
            ver = combo.currentData()
            open_act = menu.addAction(f"Open {ver}" if ver else "Open")
            open_act.triggered.connect(
                lambda _checked=False:
                    self._open_version_now(asset_id, dept, ver, refresh_cb)
            )

        loc_act = menu.addAction("Open Location")
        loc_act.triggered.connect(
            lambda _checked=False: self._open_dept_work_dir(asset_id, dept)
        )

        export_act = menu.addAction("View Latest Export")
        export_act.triggered.connect(
            lambda _checked=False: self._open_latest_export(asset_id, dept)
        )

        if available:
            new_inst_act = menu.addAction("Open in New Houdini")
            new_inst_act.triggered.connect(
                lambda _checked=False:
                    self._open_in_new_instance(asset_id, dept)
            )

        if is_active:
            menu.addSeparator()
            reload_act = menu.addAction("Reload Scene")
            reload_act.triggered.connect(
                lambda _checked=False: self._reload_current_scene(refresh_cb)
            )

        menu.addSeparator()
        new_cur_act = menu.addAction("New: Current")
        new_cur_act.triggered.connect(
            lambda _checked=False:
                self._new_from_current(asset_id, dept, refresh_cb)
        )
        new_tmpl_act = menu.addAction("New: Template")
        new_tmpl_act.triggered.connect(
            lambda _checked=False:
                self._new_from_template(asset_id, dept, refresh_cb)
        )

        if pos is not None:
            global_pos = source_widget.mapToGlobal(pos)
        else:
            global_pos = source_widget.mapToGlobal(
                source_widget.rect().bottomLeft()
            )
        menu.exec(global_pos)

    def _open_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open the dept's workfile directory in the OS file browser."""
        if not asset_id:
            return
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
        if root is None:
            return
        try:
            cats = self._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            base = root / kind / second / third / dept
            target = base if base.exists() else base.parent
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception("Open Location failed for %s/%s", asset_id, dept)

    def _open_latest_export(self, asset_id: str, dept: str) -> None:
        """Open the dept's latest export folder in the OS file browser."""
        if not asset_id:
            return
        # Activate the asset's project so latest_export_path resolves
        # against its config.
        proj = self._project_for_asset_id(asset_id)
        if proj is not None:
            self._activate_project(proj)
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
            if path is None:
                import hou
                hou.ui.setStatusMessage(
                    f"No export found for {dept}.",
                    severity=hou.severityType.Warning,
                )
                return
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(path)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "View Latest Export failed for %s/%s", asset_id, dept,
            )

    def _reload_current_scene(self, refresh_cb=None) -> None:
        """Reload the currently-loaded .hip from disk.

        Like :meth:`_open_version_now`, this defers the load to the
        next event-loop tick so the QAction click handler can return
        before Houdini tears down scene Qt state. The loaded scene's
        project is re-activated before the load so the env is
        consistent.
        """
        def _settle():
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after reload failed")

        try:
            import hou
            hip = hou.hipFile.path()
        except Exception:
            log.exception("Reload Scene: failed to read current hip path")
            _settle()
            return
        if not hip:
            _settle()
            return

        target_proj = self._project_for_hip_path(Path(hip))
        from asset_browser.core.thumbnail import _gui_singleshot

        def _do_reload(proj=target_proj):
            try:
                import hou
                if proj is not None:
                    self._activate_project(proj)
                hou.hipFile.load(hip)
                log.info("Reloaded scene: %s", hip)
                self._request_global_detail_refresh()
            except Exception:
                log.exception("Reload Scene failed")
            finally:
                _settle()

        _gui_singleshot(_do_reload)

    # ── Pipeline-context helpers ──────────────────────────

    def _asset_id_to_uri(self, asset_id: str):
        """Resolve a 3-segment asset_id ``"PROJECT/CAT/Name"`` (or
        ``"PROJECT/SEQ/Shot"``) to a tumblehead entity URI.

        Backwards-compat thin wrapper around :meth:`_uri_for_asset_id`
        kept so the older callers don't need a rename.
        """
        return self._uri_for_asset_id(asset_id)

    def _workfile_path_for(
        self, asset_id: str, dept: str, version: str,
    ) -> Path | None:
        """Return the .hip workfile :class:`Path` for an asset/dept/version.

        Uses direct filesystem math via the asset's project root —
        does NOT call ``tumblehead.pipe.paths.get_hip_file_path``,
        because that function caches ``TH_PROJECT_PATH`` at module
        import time and returns paths under the wrong project for any
        non-launch project.
        """
        if not asset_id or not version:
            return None
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
        if root is None:
            return None
        try:
            cats = self._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            dept_dir = root / kind / second / third / dept
            if not dept_dir.exists():
                return None
            # Match the convention used by _get_department_workfile_info:
            # filename ends with _{version}.hip[nc|lc].
            for hip in dept_dir.glob("*.hip*"):
                stem = hip.stem
                tail = stem.rsplit("_", 1)
                if len(tail) == 2 and tail[1] == version:
                    return hip
            return None
        except Exception:
            return None

    def _get_user_for_version(self, asset_id: str, dept: str, version: str):
        """Read the user attribution for a specific dept/version.

        Reads ``{dept_dir}/_context/{version}.json`` directly instead
        of going through ``tumblehead.pipe.houdini.ui.project_browser.helpers
        .get_user_from_context``, which would re-resolve the path
        through the cached single-project ``tumblehead.pipe.paths``
        functions and miss cross-project switches.
        """
        if not asset_id or not version:
            return None
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
        if root is None:
            return None
        try:
            cats = self._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            ctx_file = (
                root / kind / second / third / dept
                / "_context" / f"{version}.json"
            )
            if not ctx_file.exists():
                return None
            import json
            data = json.loads(ctx_file.read_text(encoding="utf-8"))
            user = data.get("user")
            return str(user) if user else None
        except Exception:
            log.debug(
                "Failed to read user for %s/%s/%s",
                asset_id, dept, version,
            )
            return None

    def _get_mtime_for_version(self, asset_id: str, dept: str, version: str):
        """Return the .hip file's modification time as a datetime, or None."""
        p = self._workfile_path_for(asset_id, dept, version)
        if p is None or not p.exists():
            return None
        try:
            import datetime as dt
            return dt.datetime.fromtimestamp(p.stat().st_mtime)
        except Exception:
            return None

    def _get_latest_export_mtime(self, asset_id: str, dept: str):
        """Return the latest export folder's mtime for ``dept`` as a
        datetime, or ``None`` if no export exists."""
        if not asset_id:
            return None
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return None
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
        except Exception:
            return None
        if path is None or not path.exists():
            return None
        try:
            import datetime as dt
            return dt.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    def _latest_update_timestamp(
        self, asset_id: str, depts: dict[str, str],
    ) -> float:
        """POSIX timestamp of the most recent workfile across departments.

        ``depts`` maps ``dept -> latest_version``. Returns 0.0 when no
        workfile can be stat'd — callers store this in ``metadata`` and
        the sort surfaces it last under descending order.
        """
        latest = 0.0
        for dept, version in depts.items():
            p = self._workfile_path_for(asset_id, dept, version)
            if p is None:
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt > latest:
                latest = mt
        return latest

    @staticmethod
    def _format_relative_time(timestamp) -> str:
        """Format a datetime as 'Ns/m/h/d/w/mo/y ago'. Cloned from
        ``tumblehead.pipe.houdini.ui.project_browser.helpers``."""
        if timestamp is None:
            return ""
        import datetime as dt
        diff = dt.datetime.now() - timestamp
        if diff.total_seconds() < 0:
            return "in the future"
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        weeks = days // 7
        if weeks < 4:
            return f"{weeks}w ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"

    def _user_mtime_label(
        self, asset_id: str, dept: str, version: str,
    ) -> str:
        """Combined 'user · 2h ago' label for a given dept/version."""
        user = self._get_user_for_version(asset_id, dept, version) or ""
        when = self._format_relative_time(
            self._get_mtime_for_version(asset_id, dept, version)
        )
        if user and when:
            return f"{user} · {when}"
        return user or when or "—"

    def _open_version_now(
        self, asset_id: str, dept: str, version: str | None,
        refresh_cb=None,
    ) -> None:
        """Open the .hip workfile for a specific (asset, dept, version).

        For cross-project opens, launches a new Houdini instance with
        the target project's environment so the USD resolver and
        pipeline context are clean. Same-project opens load in-process
        via deferred ``hou.hipFile.load``.
        """
        if not asset_id or not version:
            return
        proj = self._project_for_asset_id(asset_id)
        if proj is None:
            return

        # Cross-project: launch a new Houdini instance instead of
        # loading into the current session (resolver + env mismatch).
        try:
            import hou
            scene_proj = self._project_for_hip_path(
                Path(hou.hipFile.path()),
            )
            if (
                scene_proj is not None
                and proj is not None
                and scene_proj.name != proj.name
            ):
                hou.ui.setStatusMessage(
                    f"Opening {proj.name} scene in a new Houdini "
                    f"instance...",
                    severity=hou.severityType.Message,
                )
                self._open_in_new_instance(asset_id, dept)
                return
        except Exception:
            pass

        path = self._workfile_path_for(asset_id, dept, version)
        if path is None or not path.exists():
            try:
                import hou
                hou.ui.setStatusMessage(
                    f"Workfile not found: {asset_id} / {dept} / {version}",
                    severity=hou.severityType.Warning,
                )
            except Exception:
                pass
            return

        path_str = str(path)
        from asset_browser.core.thumbnail import _gui_singleshot

        def _do_load(target_proj=proj):
            try:
                import hou
                self._activate_project(target_proj)
                hou.hipFile.load(path_str)
                log.info("Opened workfile: %s", path_str)
            except Exception:
                log.exception("Failed to load %s", path_str)
                return
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after open failed")
            self._request_global_detail_refresh()

        _gui_singleshot(_do_load)

    def _get_scene_asset_id(self) -> str | None:
        """Return the asset_id (``PROJECT/CATEGORY/Name``) for the
        currently loaded .hip file, or ``None`` if it can't be determined.
        """
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_path = Path(hou.hipFile.path())
            scene_ctx = get_workfile_context(hip_path)
            if scene_ctx is None:
                return None
            proj = self._project_for_hip_path(hip_path)
            if proj is None:
                return None
            # Build the asset_id from entity URI segments:
            # entity:/assets/CATEGORY/NAME → PROJECT/CATEGORY/NAME
            uri = scene_ctx.entity_uri
            if uri is not None and len(uri.segments) >= 3:
                return f"{proj.name}/{uri.segments[1]}/{uri.segments[2]}"
        except Exception:
            pass
        return None

    def _scene_matches_asset(self, asset_id: str) -> bool:
        """True iff the currently loaded .hip belongs to ``asset_id``."""
        return self._get_scene_dept_version(asset_id) is not None

    def _get_scene_dept_version(
        self, asset_id: str,
    ) -> tuple[str, str] | None:
        """Return ``(dept, version)`` of the loaded scene if it belongs
        to ``asset_id``'s project + entity, else ``None``."""
        if not asset_id:
            return None
        target_uri = self._uri_for_asset_id(asset_id)
        if target_uri is None:
            return None
        target_proj = self._project_for_asset_id(asset_id)
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_path = Path(hou.hipFile.path())
            scene_ctx = get_workfile_context(hip_path)
            if scene_ctx is None:
                return None
            if str(scene_ctx.entity_uri) != str(target_uri):
                return None
            # Also confirm the loaded scene actually lives under this
            # project's project_path — otherwise two projects with the
            # same entity URI shape would falsely match.
            scene_proj = self._project_for_hip_path(hip_path)
            if scene_proj is None or (
                target_proj is not None
                and scene_proj.name != target_proj.name
            ):
                return None
            return (scene_ctx.department_name, scene_ctx.version_name or "")
        except Exception:
            return None

    def _new_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for ``asset_id`` from
        the department template.

        Mirrors Project Browser's 'New: Template' action: activate the
        target project's env, clear the scene, save an empty hip at the
        next versioned path, then run the matching
        ``config:/templates/{context}/{dept}/template.py`` to populate
        ``/stage`` before saving again. Works for both empty depts
        (next = v0001) and populated depts (next = v(N+1)).
        """
        if not asset_id:
            return
        proj = self._project_for_asset_id(asset_id)
        if proj is None:
            return
        # Activate before resolving anything so next_hip_file_path /
        # storage.resolve / template loading all see the right project.
        self._activate_project(proj)
        client = self._client_for_asset_id(asset_id)
        if client is None:
            return
        entity_uri = self._uri_for_asset_id(asset_id)
        if entity_uri is None:
            return

        try:
            from tumblepipe.pipe.paths import (
                next_hip_file_path, Context,
            )
            from tumblepipe.util.uri import Uri
        except Exception:
            log.exception("Create: tumblehead imports failed")
            return

        from asset_browser.core.thumbnail import _gui_singleshot

        def _do_create(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client between activate
                # and the path resolution that depends on it.
                self._activate_project(target_proj)

                try:
                    next_path = next_hip_file_path(
                        entity_uri, dept, nc_type=None,
                    )
                except Exception:
                    log.exception(
                        "Create: next_hip_file_path failed for %s/%s",
                        asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"Create: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Resolve the template module path.
                segs = entity_uri.segments
                entity_context = segs[0] if len(segs) > 0 else "assets"
                try:
                    template_uri = Uri.parse_unsafe(
                        f"config:/templates/{entity_context}/{dept}/template.py"
                    )
                    template_path = client.storage.resolve(template_uri)
                except Exception:
                    log.exception(
                        "Create: failed to resolve template URI for %s/%s",
                        entity_context, dept,
                    )
                    template_path = None

                next_path.parent.mkdir(parents=True, exist_ok=True)

                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=entity_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, None, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # Run the department template against /stage
                template_applied = False
                if template_path is not None and Path(template_path).exists():
                    try:
                        from tumblepipe.pipe.houdini.ui.project_browser.helpers import (
                            load_module,
                        )
                        stage = hou.node("/stage")
                        if stage is not None:
                            module_name = (
                                f"{'_'.join(entity_uri.segments[1:])}"
                                f"_{dept}_template"
                            )
                            template = load_module(
                                Path(template_path), module_name,
                            )
                            template.create(stage, entity_uri, dept)
                            stage.layoutChildren()
                            template_applied = True
                    except Exception:
                        log.exception(
                            "Create: template.create() failed for %s/%s",
                            asset_id, dept,
                        )
                else:
                    log.warning(
                        "Create: no template found at %s — saving empty scene",
                        template_path,
                    )

                hou.hipFile.save(str(next_path))

                log.info(
                    "Created %s (template_applied=%s)",
                    next_path, template_applied,
                )
                msg = f"Created {Path(next_path).name}"
                if not template_applied:
                    msg += " (no template)"
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Create failed for %s/%s", asset_id, dept)
                return

            self._cached_assets = None
            self._cached_shots = None
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after create failed")

        _gui_singleshot(_do_create)

    def _new_from_current(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the *currently loaded* scene as the next ``dept/vNNNN``
        workfile for ``asset_id``.

        Mirrors Project Browser's 'New: Current' action — copies the
        in-memory scene state into a fresh workfile slot for the target
        dept, regardless of which entity the scene was originally
        loaded from. The previous scene's context (if any) becomes
        ``from_version`` in the new ``_context/<vNNNN>.json`` so the
        provenance is preserved.
        """
        if not asset_id:
            return
        proj = self._project_for_asset_id(asset_id)
        if proj is None:
            return
        self._activate_project(proj)
        entity_uri = self._uri_for_asset_id(asset_id)
        if entity_uri is None:
            return

        from asset_browser.core.thumbnail import _gui_singleshot

        def _do_save(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import (
                    next_hip_file_path, get_workfile_context, Context,
                )
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client.
                self._activate_project(target_proj)

                try:
                    next_path = next_hip_file_path(
                        entity_uri, dept, nc_type=None,
                    )
                except Exception:
                    log.exception(
                        "New from Current: next_hip_file_path failed for %s/%s",
                        asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"New from Current: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Capture the current scene's context (if any) so we
                # can record from_version provenance metadata.
                prev_ctx = None
                try:
                    hip_str = hou.hipFile.path()
                    if hip_str:
                        prev_ctx = get_workfile_context(Path(hip_str))
                except Exception:
                    prev_ctx = None

                next_path.parent.mkdir(parents=True, exist_ok=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=entity_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, prev_ctx, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                log.info("New from Current: saved %s", next_path)
                hou.ui.setStatusMessage(
                    f"Saved {Path(next_path).name}",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception(
                    "New from Current failed for %s/%s",
                    asset_id, dept,
                )
                return

            self._cached_assets = None
            self._cached_shots = None
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception(
                        "Detail refresh after New from Current failed",
                    )

        _gui_singleshot(_do_save)

    def _context_from_hip_path(self, hip_path: Path):
        """Derive a pipeline Context from the hip file's path when
        ``get_workfile_context`` returns ``None`` (e.g. migrated
        projects without ``context.json``).

        Path convention:
        ``{PROJECT}/shots/{seq}/{shot}/{dept}/{prefix}_{version}.hip``
        ``{PROJECT}/assets/{cat}/{name}/{dept}/{prefix}_{version}.hip``
        """
        try:
            from tumblepipe.pipe.paths import Context
            from tumblepipe.util.uri import Uri

            parts = hip_path.parts
            # Find 'assets' or 'shots' in the path to anchor parsing.
            for i, seg in enumerate(parts):
                if seg in ("assets", "shots") and i + 3 < len(parts):
                    kind = seg                # "assets" or "shots"
                    cat_or_seq = parts[i + 1]  # category or sequence
                    name = parts[i + 2]        # asset/shot name
                    dept = parts[i + 3]        # department
                    break
            else:
                return None

            uri = Uri.parse_unsafe(
                f"entity:/{kind}/{cat_or_seq}/{name}"
            )
            # Extract version from filename
            stem = hip_path.stem
            tail = stem.rsplit("_", 1)
            version = tail[1] if len(tail) == 2 else None

            return Context(
                entity_uri=uri,
                department_name=dept,
                version_name=version,
            )
        except Exception:
            log.debug(
                "Failed to parse context from path %s", hip_path,
            )
            return None

    def _save_current_scene(self, refresh_cb=None) -> None:
        """Save the loaded scene as the next workfile version of its own context."""
        try:
            import hou
            from tumblepipe.pipe.paths import (
                next_hip_file_path, get_workfile_context, Context,
            )
            from tumblepipe.pipe.context import save_context, save_entity_context

            hip_path = Path(hou.hipFile.path())
            prev_ctx = get_workfile_context(hip_path)
            # Fallback for migrated projects without context.json
            if prev_ctx is None:
                prev_ctx = self._context_from_hip_path(hip_path)
            if prev_ctx is None:
                hou.ui.setStatusMessage(
                    "Save: current scene has no pipeline context.",
                    severity=hou.severityType.Warning,
                )
                return

            # Make sure the loaded scene's project is active before
            # we resolve the next path / save / write context json.
            scene_proj = self._project_for_hip_path(hip_path)
            if scene_proj is not None:
                self._activate_project(scene_proj)

            next_path = next_hip_file_path(
                prev_ctx.entity_uri, prev_ctx.department_name, nc_type=None,
            )
            hou.hipFile.save(str(next_path))

            next_ctx = get_workfile_context(next_path) or Context(
                entity_uri=prev_ctx.entity_uri,
                department_name=prev_ctx.department_name,
                version_name=Path(next_path).stem.rsplit("_", 1)[-1],
            )
            save_context(
                Path(next_path).parent, prev_ctx, next_ctx,
                file_extension=Path(next_path).suffix.lstrip("."),
            )
            save_entity_context(Path(next_path).parent, next_ctx)

            log.info("Saved next version: %s", next_path)
            hou.ui.setStatusMessage(
                f"Saved {Path(next_path).name}",
                severity=hou.severityType.Message,
            )
            # Drop scan caches so the next browse query re-scans.
            self._cached_assets = None
            self._cached_shots = None
        except Exception:
            log.exception("Save failed")
        finally:
            # Always notify the browser so the detail panel and quick-
            # action label re-sync — even on partial-success saves where
            # hipFile.save() succeeded but context.json writing raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after save failed")

    def _publish_current_scene(self, refresh_cb=None) -> None:
        """Execute matching ExportLayer / ExportRig nodes for the loaded scene."""
        try:
            self._publish_current_scene_impl()
        finally:
            # Always notify the browser so the spinner/detail can settle
            # even if publish bailed early or raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after publish failed")

    def _publish_current_scene_impl(self) -> None:
        scene_ctx = self._get_loaded_scene_context()
        if scene_ctx is None:
            try:
                import hou
                hou.ui.setStatusMessage(
                    "Publish: the loaded scene has no pipeline context.",
                    severity=hou.severityType.Warning,
                )
            except Exception:
                pass
            return
        # Activate the loaded scene's project so the export node
        # wrappers + storage resolve against the correct config.
        try:
            import hou
            scene_proj = self._project_for_hip_path(Path(hou.hipFile.path()))
        except Exception:
            scene_proj = None
        if scene_proj is not None:
            self._activate_project(scene_proj)
        entity_uri = scene_ctx.entity_uri
        try:
            import hou
            published = 0
            scanned = 0
            mismatched: list[tuple[str, str]] = []  # (path, entity_uri)
            target = str(entity_uri)

            try:
                from tumblepipe.pipe.houdini.lops.export_layer import ExportLayer
                import tumblepipe.pipe.houdini.nodes as ns
                for node in ns.list_by_node_type("export_layer", "Lop"):
                    scanned += 1
                    try:
                        wrapper = ExportLayer(node)
                        ent = str(wrapper.get_entity_uri())
                        if ent == target:
                            wrapper.execute()
                            published += 1
                            log.info("Published ExportLayer %s", node.path())
                        else:
                            mismatched.append((node.path(), ent))
                    except Exception:
                        log.exception(
                            "ExportLayer wrap/execute failed: %s", node.path(),
                        )
            except Exception:
                log.exception("Failed scanning export_layer LOPs")

            try:
                from tumblepipe.pipe.houdini.sops.export_rig import ExportRig
                import tumblepipe.pipe.houdini.nodes as ns
                for node in ns.list_by_node_type("export_rig", "Sop"):
                    scanned += 1
                    try:
                        wrapper = ExportRig(node)
                        ent = str(wrapper.get_asset_uri())
                        if ent == target:
                            wrapper.execute()
                            published += 1
                            log.info("Published ExportRig %s", node.path())
                        else:
                            mismatched.append((node.path(), ent))
                    except Exception:
                        log.exception(
                            "ExportRig wrap/execute failed: %s", node.path(),
                        )
            except Exception:
                log.exception("Failed scanning export_rig SOPs")

            if published == 0:
                if scanned == 0:
                    msg = (
                        f"Publish: no export_layer / export_rig nodes "
                        f"in scene."
                    )
                else:
                    msg = (
                        f"Publish: {scanned} export node(s) in scene "
                        f"but none match {entity_uri}."
                    )
                    log.warning(
                        "Publish mismatch for %s. Scanned nodes: %s",
                        target, mismatched,
                    )
                hou.ui.setStatusMessage(msg, severity=hou.severityType.Warning)
                return

            log.info("Published %d export node(s) for %s", published, entity_uri)
            hou.ui.setStatusMessage(
                f"Published {published} export node(s).",
                severity=hou.severityType.Message,
            )
            # refresh_cb is dispatched by the outer wrapper's finally;
            # only drop caches here so the next browse re-scans.
            self._refresh_asset(None, None)
        except Exception:
            log.exception("Publish failed")

    def _refresh_asset(self, asset_id, refresh_cb) -> None:
        """Drop catalog caches for this asset and trigger a re-fetch."""
        if asset_id is not None:
            self._dept_version_overrides.pop(asset_id, None)
        # Drop the discovery cache so the next browse query re-scans.
        self._cached_assets = None
        self._cached_shots = None
        if callable(refresh_cb):
            try:
                refresh_cb()
            except Exception:
                log.exception("Detail refresh callback failed")

    # ── Settings ──────────────────────────────────────────
    # The legacy ``get_settings`` / ``set_setting`` form was replaced
    # by :meth:`get_settings_widget` so the gear-icon dialog renders
    # a richer project list with Add / Remove buttons instead of
    # three text fields.

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
            self._client_slots.pop(stale, None)

        # Add / replace each entry.
        for proj in new_projects:
            existing = self._registry.get(proj.name)
            self._registry.add(proj, save=False)
            # If the entry's paths changed, force re-init on next browse.
            if existing is None or (
                existing.project_path != proj.project_path
                or existing.config_path != proj.config_path
            ):
                self._client_slots.pop(proj.name, None)

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
            if not self._is_ready(proj.name):
                self._try_get_client(proj.name)

    # ── Actions ───────────────────────────────────────────

    @staticmethod
    def _entity_uri_for(asset_or_detail) -> str | None:
        """Build an entity URI string from an Asset/AssetDetail's id and tags.

        Asset IDs are 3-segment ``"PROJECT/CATEGORY/Name"`` (or
        ``"PROJECT/SEQ/Shot"``); the project segment is dropped from
        the URI since URIs themselves don't carry project info.
        """
        parts = asset_or_detail.id.split("/")
        if len(parts) < 3:
            return None
        # parts[0] is the project name; URIs use parts[1] / parts[2].
        a, b = parts[1], parts[2]
        if "type:asset" in asset_or_detail.tags:
            return f"entity:/assets/{a}/{b}"
        if "type:shot" in asset_or_detail.tags:
            return f"entity:/shots/{a}/{b}"
        return None

    def get_ghost_data(self, asset):
        from asset_browser.core.ghost_overlay import GhostData, GhostNode
        if "type:shot" in asset.tags:
            return GhostData(nodes=[GhostNode("th::import_shot::1.0", 0.0, 0.0)])
        return GhostData(nodes=[GhostNode("th::import_asset::1.0", 0.0, 0.0)])

    @staticmethod
    def _is_import_assets_node(node) -> bool:
        if node is None:
            return False
        try:
            return node.type().name().startswith("th::import_assets::")
        except Exception:
            return False

    @staticmethod
    def _is_import_asset_node(node) -> bool:
        """Detect the *singular* import_asset HDA (one asset per node)."""
        if node is None:
            return False
        try:
            return node.type().name().startswith("th::import_asset::")
        except Exception:
            return False

    def _upgrade_singular_to_plural(self, target, new_uri):
        """Replace a singular ``th::import_asset`` node with a new
        ``th::import_assets`` node containing both the existing asset
        and ``new_uri``.

        Preserves the target's position, name, wiring, exclude-departments,
        and include-layerbreak flag. Returns the new raw node, or ``None``
        if the existing asset URI couldn't be read (the caller should fall
        back to the regular drop path in that case).
        """
        import hou
        from tumblepipe.util.uri import Uri
        from tumblepipe.pipe.houdini.lops import import_asset, import_assets

        # Read state from the singular node before we destroy it.
        try:
            old = import_asset.ImportAsset(target)
            old_uri = old.get_asset_uri()
            old_variant = old.get_variant_name()
            old_version = old.get_version_name()
            old_excl = old.get_exclude_department_names()
            old_layerbreak = old.get_include_layerbreak()
        except Exception:
            log.exception(
                "Failed to read state from singular import_asset %s",
                target.path(),
            )
            return None
        if old_uri is None:
            return None

        old_position = target.position()
        old_color = None
        try:
            old_color = target.color()
        except Exception:
            pass
        old_name = target.name()
        input_conns = list(target.inputConnections())
        output_conns = list(target.outputConnections())
        network = target.parent()

        # Free the name so the plural node can take it cleanly. Suffix
        # the old node so unique_name=False would still work; we destroy
        # it shortly anyway.
        try:
            target.setName(f"{old_name}__obsolete", unique_name=True)
        except Exception:
            log.debug(
                "Could not rename %s before upgrade", target.path(),
                exc_info=True,
            )

        plural = import_assets.create(network, old_name)
        raw = plural.native()

        # Reset the default entry count so the two add_asset_entry calls
        # below produce indices 1 and 2 instead of stacking on top of an
        # empty pre-allocated entry from the HDA's default state.
        try:
            plural.parm('asset_imports').set(0)
        except Exception:
            log.debug("Could not reset asset_imports count", exc_info=True)

        try:
            plural.set_exclude_department_names(old_excl)
            plural.set_include_layerbreak(old_layerbreak)
        except Exception:
            log.debug(
                "Could not transfer node-level params to plural",
                exc_info=True,
            )

        plural.add_asset_entry(
            old_uri, variant=old_variant, version=old_version, instances=1,
        )
        plural.add_asset_entry(new_uri)

        # Re-wire: inputs going into target now go into the new raw,
        # outputs consuming target now consume the new raw.
        for ic in input_conns:
            try:
                src_node = ic.inputNode()
                src_idx = ic.outputIndex()
                dst_idx = ic.inputIndex()
                raw.setInput(dst_idx, src_node, src_idx)
            except Exception:
                log.debug("Failed to rewire input", exc_info=True)
        for oc in output_conns:
            try:
                consumer = oc.inputNode()
                src_idx = oc.outputIndex()
                dst_idx = oc.inputIndex()
                consumer.setInput(dst_idx, raw, src_idx)
            except Exception:
                log.debug("Failed to rewire output", exc_info=True)

        # Destroy the old singular AFTER wiring so we don't disconnect
        # anything by accident.
        try:
            target.destroy()
        except Exception:
            log.exception("Failed to destroy old singular %s", old_name)

        # Position + color/style the new node where the old one was.
        try:
            raw.setPosition(old_position)
            if old_color is not None:
                raw.setColor(old_color)
        except Exception:
            log.debug("Position transfer failed", exc_info=True)

        try:
            plural.execute()
        except Exception:
            log.exception(
                "execute() failed on upgraded plural import_assets %s",
                old_name,
            )
        return raw

    def attach_network_thumbnail(self, asset_id, raw_node, drop) -> None:
        """Attach the asset/shot's ``thumbnail.png`` sidecar as a
        ``hou.NetworkImage`` next to the given import node.

        Public so the asset browser host can call it for non-catalog-
        owned drops too (e.g. ``import_layer`` nodes created by the
        sub-card drop path). No-op when the sidecar isn't on disk or
        the drop didn't land on a network editor.
        """
        import hou
        try:
            thumb = self._thumbnail_path(asset_id)
            if thumb is None:
                log.debug(
                    "network thumbnail: no path resolvable for %s",
                    asset_id,
                )
                return
            if not thumb.exists():
                log.debug(
                    "network thumbnail: sidecar missing on disk for %s "
                    "(expected at %s)",
                    asset_id, thumb,
                )
                return
            from tumblepipe.pipe.houdini import network_thumbnail
            editor = (
                drop.pane
                if isinstance(drop.pane, hou.NetworkEditor)
                else None
            )
            log.debug(
                "network thumbnail: attaching %s to %s (editor=%s)",
                thumb, raw_node.path(), editor,
            )
            network_thumbnail.attach(raw_node, thumb, editor=editor)
        except Exception:
            log.exception(
                "Failed to attach network thumbnail for %s", asset_id,
            )

    def on_drop(self, detail, drop) -> bool:
        """Import a single pipeline asset or shot into the scene.

        Activates the asset's project before constructing the import
        node so the LOP looks at the right pipeline config.

        Asset → ``th::import_asset::1.0`` with the entity URI.
        Shot  → ``th::import_shot::1.0``  with the entity URI.
        Root  → stock ``sublayer`` LOP referencing the Root's USD
                via the ``entity:/scenes/...`` URI so the asset
                resolver maps to the latest exported version.
        If the drop lands on an existing ``th::import_assets::2.0``
        node, the asset is appended as a new multiparm entry instead
        of creating a new node.
        """
        import hou

        # Roots (scenes) drop as a sublayer node — they aren't asset/
        # shot entities, so they bypass the import_* HDA path.
        if detail.metadata.get("kind") == "scene":
            return self._drop_root_as_sublayer(detail, drop)

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Pipeline assets can only be imported into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        entity_uri = self._entity_uri_for(detail)
        if entity_uri is None:
            hou.ui.setStatusMessage(
                f"Cannot derive entity URI for {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True  # Always handled — never fall through to fallback menu

        # Activate the asset's project so import_asset / import_shot
        # resolve against the right pipeline config.
        proj = self._project_for_asset_id(detail.id)
        if proj is not None:
            self._activate_project(proj)

        # Shots cannot be appended to an import_assets node — fall
        # through to the normal single-shot path below.
        target = getattr(drop, "target_node", None)
        if (
            "type:shot" not in detail.tags
            and self._is_import_assets_node(target)
        ):
            try:
                from tumblepipe.util.uri import Uri
                from tumblepipe.pipe.houdini.lops import import_assets
                wrapper = import_assets.ImportAssets(target)
                wrapper.add_asset_entry(Uri.parse_unsafe(entity_uri))
                wrapper.execute()
            except Exception:
                log.exception(
                    "Failed to append %s to %s",
                    detail.id, target.path(),
                )
                hou.ui.setStatusMessage(
                    f"Failed to add {detail.name} (see console)",
                    severity=hou.severityType.Error,
                )
                return True
            hou.ui.setStatusMessage(
                f"Added {detail.name} to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        # Drop onto a singular import_asset → upgrade it to a plural
        # import_assets node carrying both the original asset and the
        # newly dropped one, preserving wiring and position.
        if (
            "type:shot" not in detail.tags
            and self._is_import_asset_node(target)
        ):
            try:
                from tumblepipe.util.uri import Uri
                raw = self._upgrade_singular_to_plural(
                    target, Uri.parse_unsafe(entity_uri),
                )
            except Exception:
                log.exception(
                    "Failed to upgrade %s to plural import_assets",
                    target.path(),
                )
                raw = None
            if raw is not None:
                raw.setSelected(True, clear_all_selected=True)
                self.attach_network_thumbnail(detail.id, raw, drop)
                hou.ui.setStatusMessage(
                    f"Combined {detail.name} into {raw.name()}",
                    severity=hou.severityType.Message,
                )
                return True
            # Fall through to the normal single-asset path on failure.

        network = drop.network
        try:
            from tumblepipe.util.uri import Uri
            if "type:shot" in detail.tags:
                from tumblepipe.pipe.houdini.lops import import_shot
                # Prefix with sequence so the node name matches the
                # displayed Asset.name and avoids leading-digit shot
                # names (e.g. "010") that Houdini rejects.
                seq = detail.metadata.get("sequence", "")
                node_name = f"{seq}_{detail.name}" if seq else detail.name
                node = import_shot.create(network, node_name.replace(" ", "_"))
                node.set_shot_uri(Uri.parse_unsafe(entity_uri))
                node.execute()
            else:
                from tumblepipe.pipe.houdini.lops import import_asset
                node = import_asset.create(network, detail.name.replace(" ", "_"))
                node.set_asset_uri(Uri.parse_unsafe(entity_uri))
                node.execute()
            raw = node.native()
            if drop.position is not None:
                raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                raw.moveToGoodPosition()
            raw.setSelected(True, clear_all_selected=True)
            try:
                raw.setDisplayFlag(True)
                raw.setRenderFlag(True)
            except AttributeError:
                pass
            self.attach_network_thumbnail(detail.id, raw, drop)
        except Exception:
            log.exception("Failed to drop %s", detail.id)
            hou.ui.setStatusMessage(
                f"Failed to import {detail.name} (see console)",
                severity=hou.severityType.Error,
            )
            return True  # Always handled — error logged + status message

        hou.ui.setStatusMessage(
            f"Imported {detail.name}", severity=hou.severityType.Message,
        )
        return True

    def _drop_root_as_sublayer(self, detail, drop) -> bool:
        """Drop a Root card into a LOP network as a stock ``sublayer``
        node pointing at the Root's ``entity:/scenes/...`` URI.

        The asset resolver maps that URI to the latest exported
        scene USD at evaluation time, so the sublayer stays in sync
        with re-exports automatically. Returns ``True`` always
        (drop is considered handled even on failure — failures emit
        a status message rather than fall through to a fallback
        menu).
        """
        import hou

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Roots can only be sublayered into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        parsed = self._parse_collection_id(detail.id)
        if parsed is None or parsed[0] != "scene":
            return True
        _, proj_name, path = parsed

        proj = self._registry.get(proj_name)
        if proj is not None:
            try:
                self._activate_project(proj)
            except Exception:
                log.exception(
                    "activate_project failed for Root drop %s", detail.id,
                )

        try:
            from tumblepipe.pipe.usd import generate_scene_sublayer_uri
            scene_uri = self._scene_uri(path)
            layer_uri = generate_scene_sublayer_uri(scene_uri)
        except Exception:
            log.exception(
                "Failed to build sublayer URI for Root %s", detail.id,
            )
            hou.ui.setStatusMessage(
                f"Failed to sublayer Root {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True

        network = drop.network
        name = (detail.name or "root").replace(" ", "_")
        try:
            node = network.createNode("sublayer", name)
            # ``num_files`` controls the multiparm count for layer
            # paths; defaults to 1 on a fresh node but set explicitly
            # in case of future schema changes.
            try:
                node.parm("num_files").set(1)
            except Exception:
                pass
            node.parm("filepath1").set(layer_uri)
            if drop.position is not None:
                node.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                node.moveToGoodPosition()
            node.setSelected(True, clear_all_selected=True)
            try:
                node.setDisplayFlag(True)
            except AttributeError:
                pass
        except Exception:
            log.exception(
                "Failed to create sublayer for Root %s", detail.id,
            )
            hou.ui.setStatusMessage(
                f"Failed to import Root {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True

        hou.ui.setStatusMessage(
            f"Sublayered Root: {detail.name}",
            severity=hou.severityType.Message,
        )
        return True

    def on_sub_drop(self, asset, sub_keys, drop) -> bool:
        """Asset/shot dept sub-cards fall through to the browser's
        default ``import_layer`` LOP path.
        """
        return False

    def on_multi_drop(self, assets, drop) -> bool:
        """Import multiple pipeline assets into the scene.

        Multiple assets → one ``th::import_assets::2.0`` with multiparm
        entries populated. Shots are skipped (multi-shot drops are not
        supported yet).
        """
        import hou

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Pipeline assets can only be imported into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        # Filter to assets only — multi-shot drops are not supported.
        asset_items = [a for a in assets if "type:asset" in a.tags]
        if not asset_items:
            return False

        # A single asset should fall back to the single-drop path so
        # we get an `import_asset` node instead of an `import_assets`.
        if len(asset_items) == 1:
            try:
                detail = self.get_detail(asset_items[0].id)
                object.__setattr__(detail, "catalog_id", self.id)
            except Exception:
                log.exception("Failed to fetch detail for single drop")
                return False
            return self.on_drop(detail, drop)

        # If the drop lands on an existing import_assets node,
        # append each dragged asset as a new entry.
        target = getattr(drop, "target_node", None)
        if self._is_import_assets_node(target):
            try:
                from tumblepipe.util.uri import Uri
                from tumblepipe.pipe.houdini.lops import import_assets
                wrapper = import_assets.ImportAssets(target)
                added = 0
                for a in asset_items:
                    uri_str = self._entity_uri_for(a)
                    if uri_str is None:
                        continue
                    wrapper.add_asset_entry(Uri.parse_unsafe(uri_str))
                    added += 1
                wrapper.execute()
            except Exception:
                log.exception(
                    "Failed to append %d assets to %s",
                    len(asset_items), target.path(),
                )
                return False
            hou.ui.setStatusMessage(
                f"Added {added} assets to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        network = drop.network
        try:
            from tumblepipe.util.uri import Uri
            from tumblepipe.pipe.houdini.lops import import_assets

            node = import_assets.create(network, "import_assets")
            multiparm = node.parm("asset_imports")
            multiparm.set(len(asset_items))

            for i, a in enumerate(asset_items, start=1):
                entity_uri = self._entity_uri_for(a)
                if entity_uri is None:
                    continue
                node.parm(f"entity{i}").set(entity_uri)

            node.execute()
            raw = node.native()
            if drop.position is not None:
                raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                raw.moveToGoodPosition()
            raw.setSelected(True, clear_all_selected=True)
            try:
                raw.setDisplayFlag(True)
                raw.setRenderFlag(True)
            except AttributeError:
                pass
        except Exception:
            log.exception("Failed multi-drop of %d assets", len(asset_items))
            return False

        hou.ui.setStatusMessage(
            f"Imported {len(asset_items)} assets",
            severity=hou.severityType.Message,
        )
        return True

    # ── Quick Actions (top bar) ────────────────────────────

    def get_quick_actions(self):
        from asset_browser.api.catalog import QuickAction
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
            self._save_current_scene(done_cb)
        elif action_id == "publish":
            self._publish_current_scene(done_cb)
        elif action_id == "reload":
            self._reload_current_scene(done_cb)

    def get_quick_action_hover(self, action_id: str) -> str | None:
        """Return rich-text HTML for the hover popup above a quick action.

        - ``save``: last mtime of the currently-loaded hip file.
        - ``publish``: mtime of the latest published version for the
          current scene's (entity, dept, variant) tuple.

        Returns ``None`` for actions we don't track (e.g. ``reload``) so
        the button falls back to its regular QToolTip.
        """
        from asset_browser.core.hover_info import format_age_html

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
            scene_ctx = self._get_loaded_scene_context()
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

    # ── Entity Creation ────────────────────────────────

    def get_creation_options(self, tags=frozenset()):
        from asset_browser.api.catalog import CreationOption
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
        from asset_browser.api.catalog import CreationField
        projects = [p.name for p in self._registry.all()]
        default_proj = self._resolve_project_from_tags(tags)

        if option_id == "new_asset":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            # Category dropdown
            cats = (
                self._list_categories_for_project(default_proj)
                if default_proj else self._list_categories()
            )
            # Pre-select from tags
            default_cat = ""
            for t in tags:
                if t.startswith("category:"):
                    default_cat = t.split(":", 1)[1]
                    break
            fields.append(CreationField(
                "category", "Category",
                field_type="dropdown",
                choices=tuple(cats),
                default=default_cat,
                allow_new=True,
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
                allow_new=True,
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
        from tumblepipe.api import default_client, reset_default_client
        from tumblepipe.util.uri import Uri

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
            client = self._get_or_build_client(proj_name)
        except CatalogInitError as err:
            hou.ui.displayMessage(str(err))
            return None

        name = fields.get("name", "").strip()
        if not name:
            hou.ui.displayMessage("Name is required.")
            return None

        if option_id == "new_asset":
            category = fields.get("category", "").strip()
            if not category:
                hou.ui.displayMessage("Category is required.")
                return None
            entity_uri = Uri.parse_unsafe(f"entity:/assets/{category}/{name}")
            schema_uri = Uri.parse_unsafe("schemas:/entity/assets/category/asset")
            if client.config.get_properties(entity_uri) is not None:
                hou.ui.displayMessage(
                    f"Asset '{name}' already exists in '{category}'."
                )
                return None
            client.config.add_entity(entity_uri, {"name": name}, schema_uri)
            self._cached_assets = None
            return f"{proj_name}/{category}/{name}"

        if option_id == "new_shot":
            sequence = fields.get("sequence", "").strip()
            if not sequence:
                hou.ui.displayMessage("Sequence is required.")
                return None
            frame_start = int(fields.get("frame_start", "1001"))
            frame_end = int(fields.get("frame_end", "1100"))
            entity_uri = Uri.parse_unsafe(f"entity:/shots/{sequence}/{name}")
            schema_uri = Uri.parse_unsafe("schemas:/entity/shots/sequence/shot")
            if client.config.get_properties(entity_uri) is not None:
                hou.ui.displayMessage(
                    f"Shot '{name}' already exists in '{sequence}'."
                )
                return None
            client.config.add_entity(entity_uri, {
                "frame_start": frame_start,
                "frame_end": frame_end,
            }, schema_uri)
            self._cached_shots = None
            return f"{proj_name}/{sequence}/{name}"

        if option_id == "new_group":
            context = fields.get("context", "shots").strip()
            try:
                from tumblepipe.config import groups as grp_mod
                group_uri = Uri.parse_unsafe(f"groups:/{context}/{name}")
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
                scene_uri = Uri.parse_unsafe(f"scenes:/{name}")
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
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return []
        project_name, second, third = parts
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return []
        client = self._get_or_build_client(project_name)
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
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return False
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return False
        project_name = parts[0]
        client = self._get_or_build_client(project_name)
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
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return False
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return False
        project_name = parts[0]
        client = self._get_or_build_client(project_name)
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

    # ── Group / Scene lifecycle ──────────────────────────

    @staticmethod
    def _parse_collection_id(collection_id: str) -> tuple[str, str, str] | None:
        """Return (kind, project_name, path) for ``group:...`` / ``scene:...`` ids."""
        if not collection_id:
            return None
        if collection_id.startswith("group:"):
            rest = collection_id[len("group:"):]
            if ":" not in rest:
                return None
            proj_name, path = rest.split(":", 1)
            return ("group", proj_name, path)
        if collection_id.startswith("scene:"):
            rest = collection_id[len("scene:"):]
            if ":" not in rest:
                return None
            proj_name, path = rest.split(":", 1)
            return ("scene", proj_name, path)
        return None

    def _group_uri(self, path: str):
        from tumblepipe.util.uri import Uri
        return Uri.parse_unsafe(f"groups:/{path}")

    def _scene_uri(self, path: str):
        from tumblepipe.util.uri import Uri
        return Uri.parse_unsafe(f"scenes:/{path}")

    def delete_collection(self, collection_id: str) -> bool:
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return False
        kind, proj_name, path = parsed
        proj = self._registry.get(proj_name)
        if proj is None:
            return False
        try:
            self._activate_project(proj)
        except Exception:
            log.exception("activate_project failed")
            return False
        try:
            if kind == "group":
                from tumblepipe.config import groups as grp_mod
                grp_mod.remove_group(self._group_uri(path))
            elif kind == "scene":
                from tumblepipe.config import scenes as scn_mod
                scn_mod.remove_scene(self._scene_uri(path))
            else:
                return False
        except Exception:
            log.exception("delete_collection failed for %s", collection_id)
            return False
        self.invalidate_cache()
        return True

    def get_collection_edit_fields(
        self, collection_id: str,
    ) -> list[CreationField]:
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return []
        kind, proj_name, path = parsed
        if kind != "group":
            return []
        proj = self._registry.get(proj_name)
        if proj is None:
            return []
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.config import department as dept_mod
        except Exception:
            return []
        try:
            grp = grp_mod.get_group(self._group_uri(path))
        except Exception:
            log.exception("get_group failed")
            return []
        if grp is None:
            return []
        ctx = path.split("/", 1)[0] if "/" in path else "assets"
        try:
            known_depts = tuple(
                d.name for d in
                dept_mod.list_departments(ctx, include_generated=False)
            )
        except Exception:
            known_depts = ()
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
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return False
        kind, proj_name, path = parsed
        if kind != "group":
            return False
        proj = self._registry.get(proj_name)
        if proj is None:
            return False
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
        except Exception:
            return False
        group_uri = self._group_uri(path)
        try:
            grp = grp_mod.get_group(group_uri)
        except Exception:
            log.exception("get_group failed")
            return False
        if grp is None:
            return False
        current = {str(d) for d in getattr(grp, "departments", ())}
        new_set = set(fields.get("departments", ()) or ())
        to_add = new_set - current
        to_remove = current - new_set
        changed = False
        for d in to_add:
            try:
                grp_mod.add_department(group_uri, d)
                changed = True
            except Exception:
                log.exception("add_department failed: %s", d)
        for d in to_remove:
            try:
                grp_mod.remove_department(group_uri, d)
                changed = True
            except Exception:
                log.exception("remove_department failed: %s", d)
        if changed:
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
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return (0, 0, "")
        kind, proj_name, path = parsed
        proj = self._registry.get(proj_name)
        if proj is None:
            return (0, 0, "")
        try:
            self._activate_project(proj)
        except Exception:
            return (0, 0, "")
        added = 0
        skipped = 0
        skip_reasons: set[str] = set()
        if kind == "group":
            try:
                from tumblepipe.config import groups as grp_mod
            except Exception:
                return (0, 0, "")
            group_uri = self._group_uri(path)
            expected_ctx = path.split("/", 1)[0] if "/" in path else ""
            for aid in asset_ids:
                uri = self._uri_for_asset_id(aid)
                if uri is None:
                    skipped += 1
                    skip_reasons.add("unknown asset")
                    continue
                uri_str = str(uri)
                is_shot = uri_str.startswith("entity:/shots/")
                is_asset = uri_str.startswith("entity:/assets/")
                if expected_ctx == "shots" and not is_shot:
                    skipped += 1
                    skip_reasons.add("group accepts shots only")
                    continue
                if expected_ctx == "assets" and not is_asset:
                    skipped += 1
                    skip_reasons.add("group accepts assets only")
                    continue
                try:
                    grp_mod.add_member(group_uri, uri)
                    added += 1
                except Exception:
                    log.exception("add_member failed")
                    skipped += 1
                    skip_reasons.add("add_member failed")
        elif kind == "scene":
            try:
                from tumblepipe.config import scenes as scn_mod
                from tumblepipe.config import scene as scene_mod
            except Exception:
                return (0, 0, "")
            scene_uri = self._scene_uri(path)
            try:
                scene = scn_mod.get_scene(scene_uri)
            except Exception:
                log.exception("get_scene failed")
                return (0, 0, "")
            if scene is None:
                return (0, 0, "")
            shot_ref_changed_uris: list = []
            existing = list(getattr(scene, "assets", ()))
            # ``AssetEntry.asset`` is typed as ``str`` (URI string); the
            # tumblepipe JSON layer doesn't serialise raw ``Uri`` objects,
            # so passing one in silently produced unreadable entries
            # that vanished on the next ``get_scene`` (bug observed:
            # "Root members disappear after restart"). Force ``str(uri)``
            # for every comparison and AssetEntry write below.
            existing_uris = {
                str(getattr(e, "asset", getattr(e, "uri", e)))
                for e in existing
            }
            new_entries = list(existing)
            for aid in asset_ids:
                uri = self._uri_for_asset_id(aid)
                if uri is None:
                    skipped += 1
                    skip_reasons.add("unknown asset")
                    continue
                uri_str = str(uri)
                # Shots dropped on a Root set the shot's scene_ref
                # (the shot "uses" this Root as its root-layer source).
                if uri_str.startswith("entity:/shots/"):
                    try:
                        scene_mod.set_scene_ref(uri, scene_uri)
                        added += 1
                        shot_ref_changed_uris.append(uri)
                    except Exception:
                        log.exception("set_scene_ref failed for %s", aid)
                        skipped += 1
                        skip_reasons.add("set_scene_ref failed")
                    continue
                if not uri_str.startswith("entity:/assets/"):
                    skipped += 1
                    skip_reasons.add("Root accepts assets or shots")
                    continue
                if uri_str in existing_uris:
                    skipped += 1
                    skip_reasons.add("already in Root")
                    continue
                existing_uris.add(uri_str)
                try:
                    AssetEntry = scn_mod.AssetEntry  # type: ignore[attr-defined]
                    new_entries.append(
                        AssetEntry(asset=uri_str, instances=1)
                    )
                    added += 1
                except Exception:
                    log.exception("AssetEntry create failed")
                    skipped += 1
                    skip_reasons.add("entry create failed")
            if shot_ref_changed_uris:
                # Shot detail panels show the assigned Root — refresh
                # so the user sees the change without re-selecting.
                try:
                    self._request_global_detail_refresh()
                except Exception:
                    pass
            assets_changed = new_entries != list(existing)
            if assets_changed:
                try:
                    scn_mod.set_scene_assets(scene_uri, new_entries)
                except Exception:
                    log.exception("set_scene_assets failed")
                    return (0, len(asset_ids), "set_scene_assets failed")
            # Staging the Root (export_scene_version,
            # generate_root_version, build) used to happen here
            # automatically, but heavy USD operations on every
            # drag-drop turned out to be too punishing. They're now
            # explicit context-menu actions on the Root card (see
            # ``_export_root_usd``). The membership write above
            # still happens unconditionally so data is never lost.
        else:
            return (0, 0, "")

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
        parsed = self._parse_collection_id(collection_id)
        if parsed is None:
            return (0, 0, "")
        kind, proj_name, path = parsed
        proj = self._registry.get(proj_name)
        if proj is None:
            return (0, 0, "")
        try:
            self._activate_project(proj)
        except Exception:
            return (0, 0, "")
        removed = 0
        skipped = 0
        if kind == "group":
            try:
                from tumblepipe.config import groups as grp_mod
            except Exception:
                return (0, 0, "")
            group_uri = self._group_uri(path)
            for aid in asset_ids:
                uri = self._uri_for_asset_id(aid)
                if uri is None:
                    skipped += 1
                    continue
                try:
                    grp_mod.remove_member(group_uri, uri)
                    removed += 1
                except Exception:
                    log.exception("remove_member failed")
                    skipped += 1
        elif kind == "scene":
            try:
                from tumblepipe.config import scenes as scn_mod
                from tumblepipe.config import scene as scene_mod
            except Exception:
                return (0, 0, "")
            scene_uri = self._scene_uri(path)
            try:
                scene = scn_mod.get_scene(scene_uri)
            except Exception:
                log.exception("get_scene failed")
                return (0, 0, "")
            if scene is None:
                return (0, 0, "")
            drop_set: set[str] = set()
            shot_uris_to_clear: list = []
            for aid in asset_ids:
                uri = self._uri_for_asset_id(aid)
                if uri is None:
                    continue
                uri_str = str(uri)
                # Shots: membership is via ``scene_ref`` on the shot,
                # not via the Root's ``assets`` list. Clear the ref
                # (if it points at this Root) to "remove" the shot.
                if uri_str.startswith("entity:/shots/"):
                    shot_uris_to_clear.append(uri)
                else:
                    drop_set.add(uri_str)
            for shot_uri in shot_uris_to_clear:
                try:
                    current_ref = scene_mod.get_scene_ref(shot_uri)
                    if (
                        current_ref is not None
                        and str(current_ref) == str(scene_uri)
                    ):
                        scene_mod.set_scene_ref(shot_uri, None)
                        removed += 1
                    else:
                        skipped += 1
                except Exception:
                    log.exception(
                        "set_scene_ref(None) failed for %s", shot_uri,
                    )
                    skipped += 1
            kept: list = []
            for entry in getattr(scene, "assets", ()):
                entry_uri = str(
                    getattr(entry, "asset", getattr(entry, "uri", entry))
                )
                if entry_uri in drop_set:
                    removed += 1
                else:
                    kept.append(entry)
            asset_list_changed = (
                drop_set
                and len(kept) != len(list(getattr(scene, "assets", ())))
            )
            if asset_list_changed:
                try:
                    scn_mod.set_scene_assets(scene_uri, kept)
                except Exception:
                    log.exception("set_scene_assets failed")
                    return (0, len(asset_ids), "set_scene_assets failed")
                # Staging (export_scene_version) is a manual context-
                # menu action on the Root card; see ``_export_root_usd``.
                # We keep the membership write strictly automatic so
                # users never lose data.
            if shot_uris_to_clear:
                # Shot detail panels show the assigned Root — refresh
                # so the user sees the change without re-selecting.
                try:
                    self._request_global_detail_refresh()
                except Exception:
                    pass
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
                self._open_group_workfile(target_id, dept)
            else:
                self._open_workfile(target_id, dept)

    # ── Sub-cards (departments) ───────────────────────────

    def get_sub_cards(self, asset: Asset) -> list[SubCard]:
        # Group container cards: one sub-card per dept the group
        # covers. "missing" status (no action_id) for covered depts
        # that don't have a workfile yet — right-click → "New:
        # Template" creates one. Active-version tracking against the
        # currently-loaded scene is deferred (groups don't have a
        # ``_get_scene_dept_version`` equivalent yet).
        if "type:group" in asset.tags:
            depts_dict = asset.metadata.get("departments") or {}
            # ``departments`` is a dict[str, str] on group cards
            # ({dept: latest_version or ""}). Older code paths may
            # still produce a list — handle both shapes so a stale
            # synthesizer doesn't crash the deck.
            if isinstance(depts_dict, dict):
                covered = list(depts_dict.keys())
            else:
                covered = list(depts_dict)
                depts_dict = {d: "" for d in covered}
            ctx = (
                asset.metadata.get("context")
                or self._group_context_from_tag(asset.id)
                or "shots"
            )
            canonical = self._list_entity_departments(ctx)
            order = {name: i for i, name in enumerate(canonical)}
            sorted_depts = sorted(
                covered,
                key=lambda n: (order.get(n, len(order)), n),
            )
            cards: list[SubCard] = []
            for dept_name in sorted_depts:
                short = _DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
                latest = depts_dict.get(dept_name) or ""
                if latest:
                    cards.append(SubCard(
                        key=dept_name,
                        label=short,
                        status="available",
                        detail=latest,
                        icon=_DEPT_ICONS.get(dept_name, "package"),
                        action_id=f"open_workfile:{dept_name}",
                    ))
                else:
                    cards.append(SubCard(
                        key=dept_name,
                        label=short,
                        status="missing",
                        icon=_DEPT_ICONS.get(dept_name, "package"),
                    ))
            return cards

        depts = asset.metadata.get("departments", {})

        cards = []
        # Get all possible departments for this entity type
        is_shot = "type:shot" in asset.tags
        all_depts = self._list_entity_departments("shots" if is_shot else "assets")

        # Detect the loaded scene's dept so we can mark it "active".
        scene_dv = self._get_scene_dept_version(asset.id)
        active_dept = scene_dv[0] if scene_dv else None

        # Group coverage: depts where this member's workfile is
        # superseded by a group's multi-shot workfile. The sub-card
        # detail line shows "ⓖ GroupLabel" instead of a version, and
        # the click action route through ``open_workfile:<dept>`` —
        # which now resolves via ``latest_hip_file_path_with_context``
        # and lands on the group's hip automatically.
        dept_groups = self._dept_groups_for_member(asset.id)

        def _icon_for(dept: str) -> str:
            if is_shot and dept in _SHOT_DEPT_ICONS:
                return _SHOT_DEPT_ICONS[dept]
            return _DEPT_ICONS.get(dept, "package")

        for dept_name in all_depts:
            short = _DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
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
                cards.append(SubCard(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail="ⓜ",
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
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
                cards.append(SubCard(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail=version,
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
                ))
            else:
                cards.append(SubCard(
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
        """Drop discovered asset/shot caches; next query re-fetches."""
        self._cached_assets = None
        self._cached_shots = None
        self._member_groups_cache = None

    def _invalidate_membership_cache(self) -> None:
        """Drop only the member-coverage cache.

        Membership changes don't alter ``_cached_assets`` /
        ``_cached_shots`` — those enumerate which entities *exist*, not
        who they belong to. Skipping their bust spares a costly
        filesystem rediscovery pass on the next sidebar / grid query
        (which dominates the perceived latency of add/remove ops).
        """
        self._member_groups_cache = None

    def _reset_project_clients(self) -> None:
        """Drop per-project API client state (Shift+Click full reset).

        Wipes both READY clients and any FAILED slots, so subsequent
        browses retry from scratch. Also clears the activation guard
        so the next ``_activate_project`` rebuilds the tumblehead
        Client even when called with the previously-active project.
        """
        self._client_slots.clear()
        self._active_project_path = None
        self.invalidate_cache()

    # ── Internal helpers ──────────────────────────────────

    def _get_all_items(self) -> list[Asset]:
        """List assets + shots (across projects)."""
        if (
            self._cached_assets is not None
            and self._cached_shots is not None
        ):
            return self._cached_assets + self._cached_shots
        assets = self._discover_assets()
        shots = self._discover_shots()
        # Don't cache an empty result when no clients were ready yet —
        # a transient "no projects loaded" state would otherwise stick
        # forever and require a manual refresh to recover.
        has_projects = any(True for _ in self._registry.all())
        no_clients_ready = not self._ready_clients
        if (
            not assets and not shots
            and has_projects and no_clients_ready
        ):
            return []
        self._cached_assets = assets
        self._cached_shots = shots
        return self._cached_assets + self._cached_shots

    def _discover_assets(self) -> list[Asset]:
        """Aggregate assets from every registered project's Client.

        Per-project failures are recorded on ``self._discovery_errors``
        (cleared at the start of each browse) so the consumer can
        surface them via :class:`AssetPage.errors`.
        """
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client, init_err = self._try_get_client(proj.name)
            if init_err is not None:
                self._discovery_errors.append(init_err)
                continue
            assert client is not None
            # Activate this project's env so latest_export_path
            # resolves against the right config.
            self._activate_project(proj)
            try:
                for a in self._discover_assets_for(proj, client):
                    by_id.setdefault(a.id, a)
            except CatalogError as err:
                self._discovery_errors.append(err)
            except Exception as exc:
                self._discovery_errors.append(AssetDiscoveryError(
                    self.id,
                    f"asset discovery failed for {proj.name}: {exc}",
                    cause=exc,
                ))
        return list(by_id.values())

    def _discover_assets_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        try:
            all_entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise AssetDiscoveryError(
                self.id,
                f"list_entities failed for {proj.name}: {exc}",
                cause=exc,
            ) from exc

        out: list[Asset] = []
        for entity in all_entities:
            segs = entity.uri.segments
            if len(segs) < 3 or segs[0] != "assets":
                continue
            category = segs[1]
            name = segs[2]
            asset_id = f"{proj.name}/{category}/{name}"

            # Use our own filesystem-based scan instead of
            # ``latest_export_path`` — that helper caches
            # ``TH_PROJECT_PATH`` at import time and returns paths
            # under the wrong project for non-launch projects.
            # ``_get_department_workfile_info`` returns a
            # ``{dept: [versions]}`` mapping; collapse to
            # ``{dept: latest_version}`` for the deck popup label.
            workfile_info = self._get_department_workfile_info(asset_id)
            depts = {
                dept: versions[-1]
                for dept, versions in workfile_info.items()
                if versions
            }

            tags = {
                "source:pipeline",
                "type:asset",
                f"category:{category.lower()}",
                f"project:{proj.name}",
            }

            out.append(Asset(
                id=asset_id,
                name=name,
                thumbnail_url="",
                tags=frozenset(tags),
                metadata={
                    "departments": depts,
                    "category": category,
                    "project": proj.name,
                    "dept_count": len(depts),
                    "has_sub_cards": True,
                    "latest_update": self._latest_update_timestamp(
                        asset_id, depts,
                    ),
                },
            ))
        return out

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

        parts = self._split_asset_id(asset_id)
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

        from asset_browser.api.catalog import Asset
        return Asset(
            id=asset_id,
            name=third,
            thumbnail_url="",
            tags=frozenset(tags),
            metadata={
                "departments": depts,
                **({"category": second, "project": project_name} if is_asset
                   else {"sequence": second, "project": project_name}),
                "dept_count": len(depts),
                "has_sub_cards": True,
                "latest_update": self._latest_update_timestamp(
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
        if proj is None or not self._is_ready(proj_name):
            return None
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
        except Exception:
            log.debug("group refresh imports failed", exc_info=True)
            return None
        try:
            grp = grp_mod.get_group(self._group_uri(path))
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
        self._member_groups_cache = None

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
        asset = self._container_collection_to_asset(proxy, proj_name, "group")
        return Asset(
            id=asset.id,
            name=asset.name,
            thumbnail_url=asset.thumbnail_url,
            tags=asset.tags,
            metadata=asset.metadata,
            catalog_id=self.id,
        )

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
        if proj is None or not self._is_ready(proj_name):
            return None
        try:
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
        except Exception:
            return None
        try:
            scn = scn_mod.get_scene(self._scene_uri(path))
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
        asset = self._container_collection_to_asset(proxy, proj_name, "scene")
        return Asset(
            id=asset.id,
            name=asset.name,
            thumbnail_url=asset.thumbnail_url,
            tags=asset.tags,
            metadata=asset.metadata,
            catalog_id=self.id,
        )

    def _discover_shots(self) -> list[Asset]:
        """Aggregate shots from every registered project's Client.

        Per-project failures are recorded on ``self._discovery_errors``
        for consumer surfacing via :class:`AssetPage.errors`.
        """
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client, init_err = self._try_get_client(proj.name)
            if init_err is not None:
                # Init errors are already recorded by _discover_assets
                # in a typical browse — avoid duplicate reporting.
                if init_err not in self._discovery_errors:
                    self._discovery_errors.append(init_err)
                continue
            assert client is not None
            self._activate_project(proj)
            try:
                for a in self._discover_shots_for(proj, client):
                    by_id.setdefault(a.id, a)
            except CatalogError as err:
                self._discovery_errors.append(err)
            except Exception as exc:
                self._discovery_errors.append(AssetDiscoveryError(
                    self.id,
                    f"shot discovery failed for {proj.name}: {exc}",
                    cause=exc,
                ))
        return list(by_id.values())

    def _discover_shots_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        try:
            all_entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise AssetDiscoveryError(
                self.id,
                f"list_entities failed for {proj.name}: {exc}",
                cause=exc,
            ) from exc

        out: list[Asset] = []
        for entity in all_entities:
            segs = entity.uri.segments
            if len(segs) < 3 or segs[0] != "shots":
                continue
            sequence = segs[1]
            shot_name = segs[2]
            asset_id = f"{proj.name}/{sequence}/{shot_name}"

            # See _discover_assets_for: use our own filesystem scan
            # instead of latest_export_path to dodge the cross-project
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
                # Skip frame range for this entity rather than tanking
                # the whole shot discovery — but log so the failure
                # is visible.
                log.warning(
                    "frame range lookup failed for %s: %s", entity.uri, exc,
                )
                fr = None
            if fr is not None:
                start = fr.start_frame
                end = fr.end_frame
                if start is not None and end is not None:
                    frame_range = f"{start}-{end}"

            tags = {
                "source:pipeline",
                "type:shot",
                f"sequence:{sequence}",
                f"project:{proj.name}",
            }

            out.append(Asset(
                id=asset_id,
                name=f"{sequence}_{shot_name}",
                thumbnail_url="",
                tags=frozenset(tags),
                metadata={
                    "departments": depts,
                    "category": sequence,
                    "sequence": sequence,
                    "project": proj.name,
                    "frame_range": frame_range,
                    "dept_count": len(depts),
                    "has_sub_cards": True,
                    "latest_update": self._latest_update_timestamp(
                        asset_id, depts,
                    ),
                },
            ))
        return out

    def _list_categories_for_project(self, project_name: str) -> list[str]:
        """Return the asset categories registered for ``project_name``.

        Returns empty list when the Client isn't yet READY (so the
        sidebar can render before background init finishes). Raises
        :class:`ConfigError` if the underlying enumeration fails.
        """
        if not self._is_ready(project_name):
            return []
        client = self._client_slots[project_name].client
        try:
            entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to list categories for {project_name}: {exc}",
                cause=exc,
            ) from exc
        return sorted({
            e.uri.segments[1]
            for e in entities
            if len(e.uri.segments) >= 3 and e.uri.segments[0] == "assets"
        })

    def _list_sequences_for_project(self, project_name: str) -> list[str]:
        """Return the shot sequences registered for ``project_name``.

        See :meth:`_list_categories_for_project` for the contract.
        """
        if not self._is_ready(project_name):
            return []
        client = self._client_slots[project_name].client
        try:
            entities = client.config.list_entities(None, closure=True)
        except Exception as exc:
            raise ConfigError(
                self.id,
                f"failed to list sequences for {project_name}: {exc}",
                cause=exc,
            ) from exc
        return sorted({
            e.uri.segments[1]
            for e in entities
            if len(e.uri.segments) >= 3 and e.uri.segments[0] == "shots"
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

    # Default short labels for common department names. Used as a
    # fallback when a department doesn't declare its own ``short`` via
    # the API. Lookup is case-insensitive on the dept name.
    _DEFAULT_DEPT_SHORTS = {
        "model": "mdl",
        "blendshape": "blndsp",
        "blendshapes": "blndsp",
        "lookdev": "lkd",
        "lighting": "lgt",
        "rig": "rig",
        "layout": "lay",
        "environment": "env",
        "animation": "ani",
        "crowd": "crwd",
        "fx": "fx",
        "cfx": "cfx",
        "render": "rnd",
        "comp": "cmp",
    }

    def _list_entity_dept_shorts(self, context: str) -> dict[str, str]:
        """Return ``{dept_name: short}`` for every department, preferring
        the API-declared short when present and falling back to the
        ``_DEFAULT_DEPT_SHORTS`` map (matched case-insensitively).
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
            fallback = self._DEFAULT_DEPT_SHORTS.get(d.name.lower())
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
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return {}
        project_name, second, third = parsed
        root = self._project_root_for_asset_id(asset_id)
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
                dept_dir = base / dept
                if not dept_dir.exists():
                    continue
                versions: set[str] = set()
                for hip in dept_dir.glob("*.hip*"):
                    stem = hip.stem  # e.g. "PROP_TemplateTest_model_v0037"
                    tail = stem.rsplit("_", 1)
                    if (
                        len(tail) == 2
                        and tail[1].startswith("v")
                        and tail[1][1:].isdigit()
                    ):
                        versions.add(tail[1])
                if versions:
                    result[dept] = sorted(versions)
            return result
        except CatalogError:
            raise
        except Exception as exc:
            raise WorkfileScanError(
                self.id,
                f"failed to scan workfile versions for {asset_id}: {exc}",
                cause=exc,
            ) from exc

    def _get_department_info(self, asset_id: str) -> dict[str, list[str]]:
        """Get {dept_name: [versions]} for an asset/shot (publish versions)."""
        parsed = self._split_asset_id(asset_id)
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
        uri = self._uri_for_asset_id(asset_id)
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
        client = self._client_for_asset_id(asset_id)
        if client is None:
            return None
        try:
            from tumblepipe.util.uri import Uri
        except Exception:
            return None
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return None
        try:
            export_uri = Uri.parse_unsafe(
                f"export:/{'/'.join(uri.segments)}"
            )
            return client.storage.resolve(export_uri)
        except Exception:
            return None

    def _import_asset_to_scene(self, detail: AssetDetail) -> None:
        """Create a th::import_asset::1.0 LOP in /stage for the asset.

        ``execute_action`` is invoked on a worker thread, but ``hou.node``
        / ``createNode`` are not thread-safe — dispatch to the GUI loop.
        """
        from asset_browser.core.thumbnail import _gui_singleshot

        name = detail.name.replace(" ", "_")
        asset_id = detail.id
        entity_uri = self._entity_uri_for(detail)

        def _do_import():
            try:
                import hou
                stage = hou.node("/stage")
                if stage is None or entity_uri is None:
                    return
                from tumblepipe.util.uri import Uri
                from tumblepipe.pipe.houdini.lops import import_asset
                node = import_asset.create(stage, name)
                node.set_asset_uri(Uri.parse_unsafe(entity_uri))
                node.execute()
                raw = node.native()
                raw.setDisplayFlag(True)
                raw.setSelected(True, clear_all_selected=True)
            except Exception:
                log.exception("Failed to import asset %s", asset_id)

        _gui_singleshot(_do_import)

    def _open_in_new_instance(self, asset_id: str, dept: str) -> None:
        """Launch a new Houdini process with the latest workfile for a dept.

        Inherits the current process's full environment (TH_*, HOUDINI_PATH,
        etc.) so the new instance has identical pipeline context.
        """
        if not asset_id:
            return
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        proj = self._registry.get(project_name)
        root = self._project_root_for_asset_id(asset_id)
        if proj is None or root is None:
            return
        try:
            cats = self._list_categories_for_project(project_name)
            if second in cats:
                work_dir = root / "assets" / second / third / dept
            else:
                work_dir = root / "shots" / second / third / dept

            hip_path = None
            if work_dir.exists():
                hip_files = sorted(
                    work_dir.glob("*.hip*"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if hip_files:
                    hip_path = hip_files[0]

            if hip_path is None:
                import hou
                hou.ui.setStatusMessage(
                    f"No workfile found for {dept}.",
                    severity=hou.severityType.Warning,
                )
                return

            # Build env: current process env + target project's TH_* vars.
            # TH_PIPELINE_PATH is hpm-owned and inherited from the parent
            # process; never override it per-project.
            import subprocess
            env = dict(os.environ)
            env["TH_PROJECT_PATH"] = proj.project_path
            env["TH_CONFIG_PATH"] = proj.config_path

            # Use the same executable as the current Houdini process
            houdini_bin = Path(sys.executable)
            if not houdini_bin.exists():
                hfs = os.environ.get("HFS", "")
                houdini_bin = Path(hfs) / "bin" / "houdinifx.exe" if hfs else None

            # CREATE_NEW_PROCESS_GROUP detaches the child so it
            # survives after the calling callback returns.
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if houdini_bin and houdini_bin.exists():
                subprocess.Popen(
                    [str(houdini_bin), str(hip_path)],
                    env=env,
                    creationflags=flags,
                )
                log.info("Launched new Houdini instance: %s", hip_path)
            else:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(hip_path)],
                    env=env,
                    creationflags=flags,
                )
                log.info("Launched hip via shell: %s", hip_path)
        except Exception:
            log.exception("Failed to open in new instance: %s/%s", asset_id, dept)

    def _autosave_before_scene_swap(self) -> None:
        """Version-up the current scene if the user opted in and there are
        unsaved changes. Called from the scene-load paths to bypass
        Houdini's "save changes?" prompt without losing the user's WIP.

        No-op when the pref is off, when the hip is clean, or when the
        current scene has no pipeline context (untitled, off-pipeline,
        etc. — falling back to Houdini's native prompt is safer than
        silently writing to an unknown location).
        """
        if not self._prefs.autosave_on_scene_change:
            return
        try:
            import hou
            if not hou.hipFile.hasUnsavedChanges():
                return
        except Exception:
            return
        # _save_current_scene already guards on context + activates the
        # correct project, and is safe to call on the GUI thread.
        try:
            self._save_current_scene()
        except Exception:
            log.exception("Autosave on scene change failed")

    def _open_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a department.

        File-system inspection happens on the worker thread; the actual
        ``hou.hipFile.load`` must run on the GUI thread or Houdini crashes.

        Resolution is delegated to ``latest_hip_file_path_with_context``,
        which auto-routes to a covering group's workfile when one
        exists — so clicking a member shot's covered dept opens the
        group's hip, not a local shot hip.
        """
        if not asset_id:
            return
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        proj = self._registry.get(project_name)
        root = self._project_root_for_asset_id(asset_id)
        if proj is None or root is None:
            return
        try:
            cats = self._list_categories_for_project(project_name)
            if second in cats:
                work_dir = root / "assets" / second / third / dept
            else:
                work_dir = root / "shots" / second / third / dept

            hip_path: Path | None = None
            try:
                self._activate_project(proj)
                from tumblepipe.pipe import paths as paths_mod
                entity_uri = self._uri_for_asset_id(asset_id)
                if entity_uri is not None:
                    resolved = paths_mod.latest_hip_file_path_with_context(
                        entity_uri, dept,
                    )
                    if resolved is not None and resolved.exists():
                        hip_path = resolved
            except Exception:
                log.debug(
                    "latest_hip_file_path_with_context failed for %s/%s",
                    asset_id, dept, exc_info=True,
                )

            # Last-resort fallback: legacy direct glob over the
            # member's own dept folder. Kept so that a tumblepipe
            # path-resolver hiccup doesn't break opens entirely.
            if hip_path is None and work_dir.exists():
                hip_files = sorted(
                    work_dir.glob("*.hip*"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if hip_files:
                    hip_path = hip_files[0]

            if hip_path is not None:
                from asset_browser.core.thumbnail import _gui_singleshot

                def _do_load(p=hip_path, target_proj=proj):
                    try:
                        import hou
                        self._autosave_before_scene_swap()
                        self._activate_project(target_proj)
                        hou.hipFile.load(str(p))
                        log.info("Opened workfile: %s", p)
                    except Exception:
                        log.exception("Failed to load workfile %s", p)
                        return
                    self._request_global_detail_refresh()

                _gui_singleshot(_do_load)
                return

            # Fallback: open the directory in Explorer (safe off-thread)
            target = work_dir if work_dir.exists() else root
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception("Failed to open workfile for %s/%s", asset_id, dept)

    def _open_group_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a group's department.

        Mirrors :meth:`_open_workfile` but routes through tumblepipe's
        group-aware path resolver. The ``hou.hipFile.load`` call is
        dispatched on the GUI thread; if no workfile exists, the
        group's work directory is opened in Explorer.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._activate_project(proj)
            from tumblepipe.pipe import paths as paths_mod
            from tumblepipe import api as tp_api
            from tumblepipe.util.uri import Uri
            group_uri = self._group_uri(path)
            hip_path = paths_mod.latest_hip_file_path_with_context(
                group_uri, dept,
            )
            if hip_path is not None and hip_path.exists():
                from asset_browser.core.thumbnail import _gui_singleshot

                def _do_load(p=hip_path, target_proj=proj):
                    try:
                        import hou
                        self._autosave_before_scene_swap()
                        self._activate_project(target_proj)
                        hou.hipFile.load(str(p))
                        log.info("Opened group workfile: %s", p)
                    except Exception:
                        log.exception(
                            "Failed to load group workfile %s", p,
                        )
                        return
                    self._request_global_detail_refresh()

                _gui_singleshot(_do_load)
                return

            # Fallback: open the resolved work directory in Explorer.
            # If the dept folder doesn't exist yet, walk up to the
            # group root so the user still lands somewhere sensible.
            workspace_uri = (
                Uri.parse_unsafe("groups:/") / group_uri.segments / dept
            )
            target = tp_api.storage.resolve(workspace_uri)
            if not target.exists():
                group_root_uri = (
                    Uri.parse_unsafe("groups:/") / group_uri.segments
                )
                target = tp_api.storage.resolve(group_root_uri)
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "Failed to open group workfile for %s/%s", asset_id, dept,
            )

    def _open_group_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open a group dept's workfile directory in Explorer.

        Mirrors :meth:`_open_dept_work_dir` but resolves the path via
        the group's ``groups:/`` workspace URI. Walks up to the group
        root if the dept folder doesn't exist yet.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._activate_project(proj)
            from tumblepipe import api as tp_api
            from tumblepipe.util.uri import Uri
            group_uri = self._group_uri(path)
            workspace_uri = (
                Uri.parse_unsafe("groups:/") / group_uri.segments / dept
            )
            target = tp_api.storage.resolve(workspace_uri)
            if not target.exists():
                target = tp_api.storage.resolve(
                    Uri.parse_unsafe("groups:/") / group_uri.segments,
                )
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "Failed to open group work dir for %s/%s", asset_id, dept,
            )

    def _new_group_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for a group from the
        department template.

        Mirrors :meth:`_new_from_template` but routes through the
        group's ``groups:/ctx/name`` URI, which
        :func:`next_hip_file_path` and the template loader already
        handle. Falls back to a no-op if the project or URI can't be
        resolved.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._registry.get(proj_name)
        if proj is None:
            return
        self._activate_project(proj)
        client, _err = self._try_get_client(proj_name)
        if client is None:
            return

        try:
            from tumblepipe.pipe.paths import (
                next_hip_file_path, Context,
            )
            from tumblepipe.util.uri import Uri
        except Exception:
            log.exception("New: Template (group): tumblepipe imports failed")
            return

        group_uri = self._group_uri(path)
        # Context for groups is the first segment ("shots" or "assets")
        ctx = (
            self._group_context_from_tag(asset_id) or "shots"
        )

        from asset_browser.core.thumbnail import _gui_singleshot

        def _do_create(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                self._activate_project(target_proj)

                try:
                    next_path = next_hip_file_path(
                        group_uri, dept, nc_type=None,
                    )
                except Exception:
                    log.exception(
                        "New: Template (group): next_hip_file_path "
                        "failed for %s/%s", asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"Create: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Resolve the template module path — groups share the
                # same per-context dept template as shots/assets.
                try:
                    template_uri = Uri.parse_unsafe(
                        f"config:/templates/{ctx}/{dept}/template.py"
                    )
                    template_path = client.storage.resolve(template_uri)
                except Exception:
                    log.exception(
                        "New: Template (group): failed to resolve "
                        "template URI for %s/%s", ctx, dept,
                    )
                    template_path = None

                next_path.parent.mkdir(parents=True, exist_ok=True)

                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=group_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, None, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # Run the department template against /stage
                if template_path is not None and Path(template_path).exists():
                    try:
                        from tumblepipe.pipe.houdini.ui.project_browser.helpers import (
                            load_module,
                        )
                        stage = hou.node("/stage")
                        if stage is not None:
                            module_name = (
                                f"{'_'.join(group_uri.segments[1:])}"
                                f"_{dept}_template_group"
                            )
                            template = load_module(
                                Path(template_path), module_name,
                            )
                            template.create(stage, group_uri, dept)
                            stage.layoutChildren()
                            hou.hipFile.save(str(next_path))
                    except Exception:
                        log.exception(
                            "New: Template (group): template apply "
                            "failed for %s/%s", ctx, dept,
                        )

                log.info("Created group workfile: %s", next_path)
                # The detail-panel-driven refresh path only fires for
                # the currently-displayed card. After New: Template
                # from a sub-card right-click we need the Multi card
                # itself to swap out its "missing" sub-card for the
                # new "v0001"-bearing one — regardless of whether the
                # group's detail is open.
                try:
                    self._request_card_refresh_for_id(asset_id)
                except Exception:
                    log.exception(
                        "card refresh failed for new group workfile %s",
                        asset_id,
                    )
                if refresh_cb is not None:
                    try:
                        refresh_cb()
                    except Exception:
                        log.exception("refresh_cb failed after group create")
            except Exception:
                log.exception(
                    "New: Template (group): unexpected failure for %s/%s",
                    asset_id, dept,
                )

        _gui_singleshot(_do_create)
