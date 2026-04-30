"""Pipeline catalog — browse production assets and shots from any
number of registered Tumblehead projects.

Projects are stored in
``~/.config/asset_browser/projects.json`` (see
:class:`asset_browser.core.projects.ProjectRegistry`). Each entry holds
``project_path`` and ``config_path``; the registry's ``pipeline_path``
field is ignored at runtime. The active TumblePipe install is read from
``$TH_PIPELINE_PATH`` (set globally by hpm via the package's ``[env]``
block) so it tracks hpm upgrades automatically.

On first run with no registry file, a single entry is bootstrapped from
``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` so existing single-project
users get the same behavior with no manual setup.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from asset_browser.api.catalog import (
    Asset,
    AssetAction,
    AssetDetail,
    AssetPage,
    Catalog,
    Collection,
    CreationField,
    DetailContext,
    DetailSection,
    ListColumn,
    SortOption,
    SubCard,
)
from asset_browser.core.projects import ProjectConfig, ProjectRegistry

log = logging.getLogger(__name__)

# Render / playblast assets carry sentinel prefixes so their asset
# ids don't collide with the 3-segment ``PROJECT/CAT/Name`` ids used
# by regular assets/shots. Both encode (project, entity_kind, segs)
# only — per-source detail (dept/layer/version) lives in metadata.
_RENDER_ID_PREFIX = "@render:"
_PLAYBLAST_ID_PREFIX = "@playblast:"

# Storage URI purposes scanned by render discovery. ``render`` is
# walked for EXR layers under ``<purpose>:/render/<kind>/<seq>/<shot>/``
# and ``daily`` is walked for the flat ``<purpose>:/daily/<kind>/<dept>/<seq>_<shot>.mp4``
# layout. Both feed into a single per-shot card.
_RENDER_PURPOSES = ("render", "daily")

# Lucide icon names per department — shared between Shot/Asset
# department sub-cards and Playblast dept sub-cards. Add new depts
# here and they pick up consistent iconography across both surfaces.
_DEPT_ICONS = {
    "model": "shapes",          # 3D shapes
    "lookdev": "palette",        # color palette for shading
    "rig": "bone",              # skeleton/rigging
    "animation": "move-3d",     # 3D movement
    "layout": "grid-3x3",       # scene layout
    "render": "camera",         # camera/render
    "light": "lamp",            # lighting
    "cfx": "sparkles",          # cloth/hair FX
    "composite": "layers",      # compositing layers
    "effects": "zap",           # VFX
    "blendshape": "blend",      # blending shapes
    "environment": "mountain",  # environment/landscape
    "crowd": "users",           # crowd of people
}
_DEPT_SHORT_NAMES = {
    "animation": "Anim",
    "blendshape": "Blend",
    "composite": "Comp",
    "environment": "Enviro",
}


def _cascade_counts(col: Collection) -> Collection:
    """Return a new Collection whose count is the sum of all descendant counts."""
    import dataclasses
    if not col.children:
        return col
    cascaded_children = tuple(_cascade_counts(c) for c in col.children)
    total = sum(c.count for c in cascaded_children)
    return dataclasses.replace(col, children=cascaded_children, count=total)


def _projects_json_path() -> Path:
    """Resolve the on-disk location of ``projects.json`` using the
    same conventions as :func:`asset_browser._config_dir`."""
    houdini_pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if houdini_pref:
        return Path(houdini_pref) / "asset_browser" / "projects.json"
    return Path.home() / ".config" / "asset_browser" / "projects.json"


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
    """
    registry = ProjectRegistry(_projects_json_path())
    registry.load()
    registry.bootstrap_from_env()  # add env-project if missing (no-op otherwise)

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

    @property
    def default_project_tag(self) -> str:
        """Return the tag for the project Houdini was launched with.

        The browser can use this to auto-activate the matching project
        pill on first load so the grid defaults to the current project.
        """
        if self._launch_project_name:
            return f"project:{self._launch_project_name}"
        return ""

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

        # Per-project Client instances. Built eagerly on the calling
        # thread (typically the GUI thread during pypanel creation).
        # Building Clients on background threads while Houdini's
        # pypanel construction is also running races against HDA
        # loading and crashes the process — see the long history
        # of attempts in this file's git log.
        import threading
        self._clients: dict[str, "_AnyClient"] = {}
        # Serializes _build_client_now — tumblepipe's Client constructor
        # reads TH_* env vars from its config_convention.py, so two
        # threads building different projects race on the env and
        # stamp each other's paths into the wrong config.
        self._build_client_lock = threading.Lock()
        # Tracks projects whose init has been attempted (and failed
        # or succeeded) so we don't retry unrecoverable failures on
        # every browse.
        self._init_attempted: set[str] = set()

        # Discovery cache (merged across projects). Invalidated by
        # project add / remove / refresh.
        self._cached_assets: list[Asset] | None = None
        self._cached_shots: list[Asset] | None = None
        # Renders are scanned independently — same disk shapes (EXR
        # sequences, daily MP4s) but resolved through
        # ``tumblepipe.pipe.paths`` rather than the workfile scanner.
        self._cached_renders: list[Asset] | None = None
        # Playblasts: per-shot card aggregating dept-keyed MP4 previews.
        # Disk-driven, parallel to renders.
        self._cached_playblasts: list[Asset] | None = None

        # Per-asset, per-department version override.
        # Shape: {asset_id: {dept_name: version_label}}
        self._dept_version_overrides: dict[str, dict[str, str]] = {}

        # Render thumbnail generation runs on background daemon
        # threads, throttled to avoid spawning 50 ffmpeg processes
        # at once when the user opens a panel full of fresh cards.
        # ``_render_thumb_in_progress`` deduplicates so the same
        # card isn't queued twice while one job is mid-flight.
        import threading
        self._render_thumb_in_progress: set[str] = set()
        self._render_thumb_lock = threading.Lock()
        self._render_thumb_sem = threading.Semaphore(2)

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

        # Client construction is deferred — ``_ensure_all_clients()``
        # is called from ``get_assets()`` and ``initialize()`` which
        # both run on worker threads.

    # ── Per-project init ──────────────────────────────────

    def _build_client_now(self, proj: "ProjectConfig"):
        """Construct one project's :class:`tumblehead.api.Client`
        synchronously on the calling thread.

        Idempotent — returns the cached Client on subsequent calls.
        Records the attempt so a transient failure isn't retried on
        every browse.

        The construction is serialised against a catalog-wide lock
        because tumblepipe's ``Client`` reads ``TH_*`` env vars inside
        its ``ProjectConfigConvention`` __init__, so two concurrent
        builds (worker + main thread) race on the env and cross-wire
        each other's ``config`` object.
        """
        if proj.name in self._clients:
            return self._clients[proj.name]
        if proj.name in self._init_attempted:
            return None
        with self._build_client_lock:
            # Re-check inside the lock — another waiter may have built it.
            if proj.name in self._clients:
                return self._clients[proj.name]
            if proj.name in self._init_attempted:
                return None
            return self._build_client_locked(proj)

    def _build_client_locked(self, proj: "ProjectConfig"):
        try:
            import sys
            # TH_PIPELINE_PATH is owned by hpm — set in the package's
            # [env] block to the active install. Never per-project.
            pipeline_path = os.environ["TH_PIPELINE_PATH"]
            py_path = str(
                Path(pipeline_path) / "houdini" / "TumblePipe" / "python" / "1x"
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
            self._clients[proj.name] = client
            self._init_attempted.add(proj.name)
            log.info(
                "Pipeline API initialized for %s: %s",
                proj.name, client.PROJECT_PATH,
            )
            return client
        except Exception:
            self._init_attempted.add(proj.name)
            log.exception(
                "Failed to initialize pipeline API for project %s",
                proj.name,
            )
            return None

    def _ensure_client(self, project_name: str, timeout: float = 30.0):
        """Return the :class:`tumblehead.api.Client` for ``project_name``,
        building it on the calling thread if it hasn't been built yet."""
        if project_name in self._clients:
            return self._clients[project_name]
        proj = self._registry.get(project_name)
        if proj is None:
            return None
        return self._build_client_now(proj)

    def _ensure_all_clients(self, timeout: float = 30.0) -> None:
        """Build any not-yet-built Clients for every registered project."""
        for proj in self._registry.all():
            self._ensure_client(proj.name, timeout=timeout)

    def initialize(self) -> None:
        """Called on a worker thread — safe to do slow I/O here."""
        self._ensure_all_clients()

    # ── Project / asset_id helpers ────────────────────────

    def _split_asset_id(
        self, asset_id: str,
    ) -> tuple[str, str, str] | None:
        """Parse a 3-segment asset_id ``"PROJECT/CAT/Name"`` (or
        ``"PROJECT/SEQ/Shot"``). Returns ``(project, second, third)``
        or ``None`` if the id is malformed.

        Render / playblast ids (``@render:...`` / ``@playblast:...``)
        intentionally return ``None`` so helpers that assume the
        workfile shape (``_uri_for_asset_id``,
        ``_get_department_workfile_info``, etc.) don't accidentally
        process them.
        """
        if not asset_id:
            return None
        if (
            asset_id.startswith(_RENDER_ID_PREFIX)
            or asset_id.startswith(_PLAYBLAST_ID_PREFIX)
        ):
            return None
        parts = asset_id.split("/", 2)
        if len(parts) != 3:
            return None
        return parts[0], parts[1], parts[2]

    def _is_render_asset_id(self, asset_id: str) -> bool:
        return bool(asset_id) and asset_id.startswith(_RENDER_ID_PREFIX)

    def _split_render_asset_id(
        self, asset_id: str,
    ) -> dict | None:
        """Parse a render asset id of the form
        ``@render:<proj>:<entity_kind>:<seg1>:<seg2>``.

        One card represents one entity (typically a shot). All
        layers / depts / dailies for that entity are stored as
        ``metadata["sources"]`` rather than encoded in the id.

        Returns ``{"project", "entity_kind", "entity_segs"}`` or
        ``None`` for malformed ids.
        """
        if not self._is_render_asset_id(asset_id):
            return None
        body = asset_id[len(_RENDER_ID_PREFIX):]
        parts = body.split(":")
        if len(parts) != 4:
            return None
        project, entity_kind, seg1, seg2 = parts
        return {
            "project": project,
            "entity_kind": entity_kind,
            "entity_segs": (seg1, seg2),
        }

    def _make_render_asset_id(
        self,
        project: str,
        entity_kind: str,
        entity_segs: tuple[str, str],
    ) -> str:
        seg1, seg2 = entity_segs
        body = ":".join([project, entity_kind, seg1, seg2])
        return f"{_RENDER_ID_PREFIX}{body}"

    def _is_playblast_asset_id(self, asset_id: str) -> bool:
        return bool(asset_id) and asset_id.startswith(_PLAYBLAST_ID_PREFIX)

    def _split_playblast_asset_id(
        self, asset_id: str,
    ) -> dict | None:
        """Parse ``@playblast:<proj>:<entity_kind>:<seg1>:<seg2>``.

        One card per entity (typically a shot). All depts that ship a
        playblast for that entity are stored as ``metadata["depts"]``
        and aren't encoded in the id.
        """
        if not self._is_playblast_asset_id(asset_id):
            return None
        body = asset_id[len(_PLAYBLAST_ID_PREFIX):]
        parts = body.split(":")
        if len(parts) != 4:
            return None
        project, entity_kind, seg1, seg2 = parts
        return {
            "project": project,
            "entity_kind": entity_kind,
            "entity_segs": (seg1, seg2),
        }

    def _make_playblast_asset_id(
        self,
        project: str,
        entity_kind: str,
        entity_segs: tuple[str, str],
    ) -> str:
        seg1, seg2 = entity_segs
        body = ":".join([project, entity_kind, seg1, seg2])
        return f"{_PLAYBLAST_ID_PREFIX}{body}"

    def _project_for_asset_id(self, asset_id: str) -> ProjectConfig | None:
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return None
        return self._registry.get(parts[0])

    def _client_for_asset_id(self, asset_id: str):
        parts = self._split_asset_id(asset_id)
        if parts is None:
            return None
        return self._ensure_client(parts[0])

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
        """
        if project is None:
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
        # Don't call _ensure_client — runs on GUI thread
        cats = self._list_categories()
        seqs = self._list_sequences()
        return {
            "source": ["pipeline"],
            "type": ["asset", "shot", "render", "playblast"],
            "category": [c.lower() for c in cats],
            "sequence": seqs,
            "project": [p.name for p in self._registry.all()],
            "has": ["exr", "daily"],
            "kind": ["media"],
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
        """Return [Assets, Shots, Groups, Scenes] sections for one
        project, omitting any that are empty."""
        project_tag = f"project:{proj.name}"
        sections: list[Collection] = []

        cats = self._list_categories_for_project(proj.name)
        if cats:
            cat_children = []
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
        if seqs:
            seq_children = []
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

        # Media section — Renders (per shot, with raw/denoise/slapcomp/
        # daily as detail-panel sources) + Playblasts (per shot, with
        # one sub-card per dept). Only added when at least one media
        # asset exists. Parent is navigation-only; children carry the
        # tag filters that scope the grid.
        render_total = self._count_for_project_renders(proj.name)
        playblast_total = self._count_for_project_playblasts(proj.name)
        media_children: list[Collection] = []
        if render_total:
            media_children.append(Collection(
                id=f"{proj.name}:renders_section",
                label="Renders",
                count=render_total,
                tag=f"{project_tag}+type:render",
                icon="film",
            ))
        if playblast_total:
            media_children.append(Collection(
                id=f"{proj.name}:playblasts_section",
                label="Playblasts",
                count=playblast_total,
                tag=f"{project_tag}+type:playblast",
                icon="circle-play",
            ))
        if media_children:
            sections.append(Collection(
                id=f"{proj.name}:media_section",
                label="Media",
                count=render_total + playblast_total,
                tag=f"{project_tag}+kind:media",
                icon="image-play",
                children=tuple(media_children),
            ))

        group_children = self._build_groups_for_project(proj)
        sections.append(Collection(
            id=f"{proj.name}:groups_section",
            label="Groups",
            icon="group",
            children=tuple(group_children),
        ))

        scene_children = self._build_scenes_for_project(proj)
        sections.append(Collection(
            id=f"{proj.name}:scenes_section",
            label="Scenes",
            icon="layers",
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

    def _count_for_project_renders(self, project_name: str) -> int:
        items = self._get_all_items()
        proj_tag = f"project:{project_name}"
        return sum(
            1 for a in items
            if "type:render" in a.tags and proj_tag in a.tags
        )

    def _count_for_project_playblasts(self, project_name: str) -> int:
        items = self._get_all_items()
        proj_tag = f"project:{project_name}"
        return sum(
            1 for a in items
            if "type:playblast" in a.tags and proj_tag in a.tags
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

        Tag format: ``group:PROJECT:CONTEXT/NAME``
        """
        try:
            _, rest = tag.split(":", 1)
            proj_name, group_path = rest.split(":", 1)
            proj = self._registry.get(proj_name)
            if proj is None or proj_name not in self._clients:
                return items
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.util.uri import Uri
            group_uri = Uri.parse_unsafe(f"groups:/{group_path}")
            group = grp_mod.get_group(group_uri)
            if group is None:
                return items
            member_ids = set()
            for member_uri in group.members:
                segs = member_uri.segments if hasattr(member_uri, 'segments') else str(member_uri).replace("entity:/", "").split("/")
                if len(segs) >= 3:
                    member_ids.add(f"{proj_name}/{segs[1]}/{segs[2]}")
                elif len(segs) >= 2:
                    member_ids.add(f"{proj_name}/{segs[0]}/{segs[1]}")
            return [a for a in items if a.id in member_ids]
        except Exception:
            log.debug("Failed to filter by group %s", tag, exc_info=True)
            return items

    def _filter_by_scene(self, items: list, tag: str) -> list:
        """Filter assets to those in a pipeline scene.

        Tag format: ``scene:PROJECT:PATH``
        """
        try:
            _, rest = tag.split(":", 1)
            proj_name, scene_path = rest.split(":", 1)
            proj = self._registry.get(proj_name)
            if proj is None or proj_name not in self._clients:
                return items
            self._activate_project(proj)
            from tumblepipe.config import scenes as scn_mod
            from tumblepipe.util.uri import Uri
            scene_uri = Uri.parse_unsafe(f"scenes:/{scene_path}")
            scene = scn_mod.get_scene(scene_uri)
            if scene is None:
                return items
            asset_ids = set()
            for entry in scene.assets:
                uri = entry.asset
                segs = uri.segments if hasattr(uri, 'segments') else str(uri).replace("entity:/", "").split("/")
                if len(segs) >= 3:
                    asset_ids.add(f"{proj_name}/{segs[1]}/{segs[2]}")
                elif len(segs) >= 2:
                    asset_ids.add(f"{proj_name}/{segs[0]}/{segs[1]}")
            return [a for a in items if a.id in asset_ids]
        except Exception:
            log.debug("Failed to filter by scene %s", tag, exc_info=True)
            return items

    def _build_groups_for_project(self, proj) -> list[Collection]:
        """Build Group sub-sections (Assets / Shots) for one project.

        Asset-groups and shot-groups can't mix departments so they're
        presented under separate headers. Empty sub-sections are
        omitted.
        """
        if proj.name not in self._clients:
            return []
        sub_by_ctx: dict[str, list[Collection]] = {"assets": [], "shots": []}
        try:
            self._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            for ctx in ("shots", "assets"):
                ctx_groups = grp_mod.list_groups(ctx)
                for g in ctx_groups:
                    name = g.uri.segments[-1] if g.uri.segments else str(g.uri)
                    member_count = len(g.members)
                    sub_by_ctx[ctx].append(Collection(
                        id=f"group:{proj.name}:{ctx}/{name}",
                        label=name,
                        count=member_count,
                        tag=f"group:{proj.name}:{ctx}/{name}",
                        kind="group",
                    ))
        except Exception:
            log.debug("Failed to list groups for %s", proj.name, exc_info=True)

        sections: list[Collection] = []
        if sub_by_ctx["assets"]:
            sections.append(Collection(
                id=f"{proj.name}:groups_section:assets",
                label="Assets",
                icon="box",
                children=tuple(sub_by_ctx["assets"]),
            ))
        if sub_by_ctx["shots"]:
            sections.append(Collection(
                id=f"{proj.name}:groups_section:shots",
                label="Shots",
                icon="clapperboard",
                children=tuple(sub_by_ctx["shots"]),
            ))
        return sections

    def _build_scenes_for_project(self, proj) -> list[Collection]:
        """Build Scene collections for a single project."""
        children: list[Collection] = []
        if proj.name not in self._clients:
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
            if proj is None or proj_name not in self._clients:
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

            # Scenes
            for s in scn_mod.list_scenes():
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
                        break
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
        # Block until every registered project's bg init has finished
        # (success or fail) before aggregating discovery.
        self._ensure_all_clients()
        all_items = self._get_all_items()

        # Handle group/scene tags (special filtering)
        remaining_tags = set()
        for t in tags:
            if t.startswith("group:"):
                all_items = self._filter_by_group(all_items, t)
            elif t.startswith("scene:"):
                all_items = self._filter_by_scene(all_items, t)
            else:
                remaining_tags.add(t)

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
        )

    # ── Detail ────────────────────────────────────────────

    def get_detail(self, asset_id: str, version: str | None = None) -> AssetDetail:
        # asset_id format: "PROJECT/CATEGORY/AssetName" or "PROJECT/SEQ/Shot",
        # or a render id (``@render:...``) — handled separately.
        if self._is_render_asset_id(asset_id):
            return self._get_render_detail(asset_id)

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
                start = getattr(fr, "start_frame", None)
                end = getattr(fr, "end_frame", None)
                if start is None or end is None:
                    start = getattr(fr, "first_frame", None)
                    end = getattr(fr, "last_frame", None)
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

    def _get_render_detail(self, asset_id: str) -> AssetDetail:
        """Build an ``AssetDetail`` for a render asset.

        Mirrors the source ``Asset`` (tags + metadata) and adds a
        small render-specific summary as the description so the Info
        tab has something useful to show.
        """
        parsed = self._split_render_asset_id(asset_id)
        if parsed is None:
            return AssetDetail(
                id=asset_id, name=asset_id, thumbnail_url="",
                tags=frozenset({"source:pipeline", "type:render"}),
            )

        # Look up the cached Asset so we re-use the discovery work
        # rather than re-scanning the disk.
        source = next(
            (a for a in self._get_all_items() if a.id == asset_id),
            None,
        )
        tags = set(source.tags) if source else {
            "source:pipeline",
            "type:render",
            f"project:{parsed['project']}",
        }
        metadata: dict = dict(source.metadata) if source else {}

        # Frame range from the primary source's context.json. Skipped
        # when the primary is a daily MP4 (no context file).
        frame_range = ""
        primary_key = metadata.get("primary_source", "")
        sources = metadata.get("sources") or {}
        primary_info = sources.get(primary_key) if primary_key else None
        if primary_info and primary_info.get("kind") == "exr":
            layer = self._resolve_render_layer(asset_id)
            if layer is not None:
                try:
                    fr = layer.get_frame_range()
                    if fr is not None:
                        first = getattr(fr, "first_frame", None)
                        last = getattr(fr, "last_frame", None)
                        if first is not None and last is not None:
                            frame_range = f"{first}-{last}"
                except Exception:
                    pass
        if frame_range:
            metadata["frame_range"] = frame_range

        seg1, seg2 = parsed["entity_segs"]
        entity_label = (
            f"{seg1}_{seg2}" if parsed["entity_kind"] == "shots" else seg2
        )
        # Description summary — primary source + frame range + AOVs.
        bits = [entity_label]
        if primary_info is not None:
            bits.append(
                f"Primary: {self._render_source_label(primary_key, primary_info)}",
            )
        if frame_range:
            bits.append(f"Frames: {frame_range}")
        versions = metadata.get("versions") or []
        if versions:
            bits.append(f"Versions: {', '.join(versions)}")
        aovs = metadata.get("aovs") or []
        if aovs:
            bits.append(f"AOVs: {', '.join(aovs)}")
        description = "\n".join(bits)

        name = source.name if source else entity_label

        return AssetDetail(
            id=asset_id,
            name=name,
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
        """Return variant names for an asset/shot, [] on error."""
        try:
            from tumblepipe.config.variants import list_variants
        except Exception:
            return []
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return []
        try:
            return list(list_variants(uri))
        except Exception:
            log.debug("variants lookup failed for %s", asset_id)
            return []

    def _get_frame_range_obj(self, asset_id: str):
        """Return the FrameRange dataclass for a shot, or None."""
        try:
            from tumblepipe.config.timeline import get_frame_range
        except Exception:
            return None
        uri = self._uri_for_asset_id(asset_id)
        if uri is None:
            return None
        try:
            return get_frame_range(uri)
        except Exception:
            return None

    def _get_fps(self, asset_id: str):
        """Return FPS for an entity (or project default), or None."""
        try:
            from tumblepipe.config.timeline import get_fps
        except Exception:
            return None
        uri = self._uri_for_asset_id(asset_id)
        try:
            if uri is not None:
                fps = get_fps(uri)
                if fps is not None:
                    return fps
            return get_fps()
        except Exception:
            return None

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
        if self._is_render_asset_id(asset_id):
            return self._render_thumbnail_path(asset_id)
        if self._is_playblast_asset_id(asset_id):
            return self._playblast_thumbnail_path(asset_id)

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

    def _render_thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the on-disk thumbnail path for a render card.

        Sidecar lives next to the *primary source's* media:
        - EXR primary:   ``<latest_version_dir>/thumbnail.png``
        - Daily primary: ``<mp4>.thumbnail.png``

        Returns ``None`` when the source isn't on disk yet.
        """
        info = self._resolve_render_source(asset_id, source=None)
        if info is None:
            return None
        src = info["info"]
        if src.get("kind") == "daily":
            path_str = src.get("path")
            if not path_str:
                return None
            mp4 = Path(path_str)
            return mp4.with_suffix(mp4.suffix + ".thumbnail.png")
        # EXR — the cached path is the latest version dir.
        path_str = src.get("path")
        if not path_str:
            return None
        return Path(path_str) / "thumbnail.png"

    def _playblast_thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the sidecar thumbnail path for a playblast card —
        ``<latest_mp4>.thumbnail.png`` for the primary dept's latest
        version. Returns ``None`` when nothing is on disk.
        """
        mp4 = self._resolve_playblast_path(asset_id, dept=None)
        if mp4 is None:
            return None
        return mp4.with_suffix(mp4.suffix + ".thumbnail.png")

    def get_thumbnail(self, asset: Asset):
        """Return the path to the asset's sidecar thumbnail if present.

        Falls back to an empty string (placeholder icon) when there
        isn't one yet. For render / playblast assets, schedules a
        background thumbnail generation from the primary source's
        latest media so the grid stays responsive — the new thumbnail
        appears on the next refresh once iconvert/ffmpeg finishes.
        """
        p = self._thumbnail_path(asset.id)
        if p is not None and p.exists():
            return p
        if (
            self._is_render_asset_id(asset.id)
            or self._is_playblast_asset_id(asset.id)
        ):
            self._schedule_media_thumbnail(asset)
        return ""

    def _schedule_media_thumbnail(self, asset: Asset) -> None:
        """Kick off a background thumbnail generation for a render or
        playblast asset. No-op if one is already in progress for this
        id.

        Daemon thread bounded by a Semaphore (2 concurrent). Reads
        source paths straight from ``asset.metadata`` so the worker
        doesn't touch project env vars. Refresh dispatched on the
        GUI thread when complete.
        """
        aid = asset.id
        with self._render_thumb_lock:
            if aid in self._render_thumb_in_progress:
                return
            self._render_thumb_in_progress.add(aid)
        import threading
        threading.Thread(
            target=self._media_thumb_worker,
            args=(asset,),
            daemon=True,
            name=f"MediaThumb-{aid[:32]}",
        ).start()

    def _media_thumb_worker(self, asset: Asset) -> None:
        try:
            with self._render_thumb_sem:
                if self._is_playblast_asset_id(asset.id):
                    generated = self._playblast_generate_thumbnail_from_metadata(
                        asset,
                    )
                else:
                    generated = self._render_generate_thumbnail_from_metadata(
                        asset,
                    )
            if generated is not None:
                # Refresh on the GUI thread — _request_thumbnail_refresh
                # walks Qt widgets which is GUI-thread-only.
                try:
                    from asset_browser.core.thumbnail import _gui_singleshot
                    _gui_singleshot(
                        lambda aid=asset.id:
                            self._request_thumbnail_refresh(aid),
                    )
                except Exception:
                    pass
        except Exception:
            log.debug(
                "Background media thumbnail failed for %s",
                asset.id, exc_info=True,
            )
        finally:
            with self._render_thumb_lock:
                self._render_thumb_in_progress.discard(asset.id)

    # ── Render thumbnail generation ───────────────────────

    def _render_generate_thumbnail_from_metadata(
        self, asset: Asset,
    ) -> Path | None:
        """Build a first-frame thumbnail PNG from the primary source's
        cached on-disk path. Reads only ``asset.metadata`` so the
        worker doesn't touch project env vars.
        """
        meta = asset.metadata or {}
        sources = meta.get("sources") or {}
        primary_key = (
            meta.get("primary_source")
            or (
                self._pick_primary_source(sources) if sources else None
            )
        )
        if not primary_key or primary_key not in sources:
            return None
        src = sources[primary_key]

        if src.get("kind") == "daily":
            mp4_str = src.get("path")
            if not mp4_str:
                return None
            mp4 = Path(mp4_str)
            if not mp4.exists():
                return None
            out = mp4.with_suffix(mp4.suffix + ".thumbnail.png")
            if out.exists():
                return out
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                return None
            return self._render_thumb_from_mp4(mp4, out)

        # EXR primary
        layer_str = src.get("path")
        if not layer_str:
            return None
        layer_path = Path(layer_str)
        if not layer_path.is_dir():
            return None
        out = layer_path / "thumbnail.png"
        if out.exists():
            return out
        first_frame = self._render_first_frame_in_dir(layer_path)
        if first_frame is None:
            return None
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        return self._render_thumb_from_exr(first_frame, out)

    def _playblast_generate_thumbnail_from_metadata(
        self, asset: Asset,
    ) -> Path | None:
        """Build a first-frame thumbnail PNG for a playblast card from
        the primary dept's latest MP4. Reads only ``asset.metadata``
        so the worker doesn't touch project env vars.
        """
        meta = asset.metadata or {}
        depts = meta.get("depts") or {}
        primary = meta.get("primary_dept")
        if not primary or primary not in depts:
            return None
        path_str = depts[primary].get("path")
        if not path_str:
            return None
        mp4 = Path(path_str)
        if not mp4.exists():
            return None
        out = mp4.with_suffix(mp4.suffix + ".thumbnail.png")
        if out.exists():
            return out
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        return self._render_thumb_from_mp4(mp4, out)

    @staticmethod
    def _render_first_frame_in_dir(version_dir: Path):
        """Return the lowest-numbered EXR/JPG actually on disk.

        Looks at flat layer files first, then drops into the first
        AOV subdir. Mirrors ``_render_first_frame_path`` but takes
        a raw Path so it works without a Layer dataclass.
        """
        try:
            flat = sorted(
                f for f in version_dir.iterdir()
                if f.is_file() and f.suffix.lower() in (".exr", ".jpg", ".png", ".tif")
            )
            if flat:
                return flat[0]
        except Exception:
            pass
        try:
            for sub in sorted(version_dir.iterdir()):
                if not sub.is_dir() or sub.suffix != "":
                    continue
                aov_files = sorted(
                    f for f in sub.iterdir()
                    if f.is_file() and f.suffix.lower() in (".exr", ".jpg", ".png", ".tif")
                )
                if aov_files:
                    return aov_files[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _render_first_frame_path(layer):
        """Return the lowest-numbered EXR (or sidecar suffix) actually
        on disk for ``layer``. Prefers the layer's own frames; falls
        back to the first AOV's beauty-equivalent when the layer is
        AOV-only.
        """
        try:
            frame_glob = layer.get_frame_path("*")
            candidates = sorted(
                frame_glob.parent.glob(frame_glob.name),
                key=lambda p: p.name,
            )
            if candidates:
                return candidates[0]
        except Exception:
            pass
        # Layer has no flat frames — try the first AOV directory
        try:
            for aov in layer.aovs.values():
                aov_glob = aov.get_aov_frame_path("*")
                aov_candidates = sorted(
                    aov_glob.parent.glob(aov_glob.name),
                    key=lambda p: p.name,
                )
                if aov_candidates:
                    return aov_candidates[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _render_thumb_from_exr(src: Path, out: Path) -> Path | None:
        """Convert an EXR to a 512px PNG via Houdini's ``iconvert``.

        Returns the output path on success or ``None`` on failure.
        ``iconvert`` ships with Houdini and lives on PATH inside the
        Houdini environment.
        """
        import shutil
        import subprocess
        exe = shutil.which("iconvert")
        if exe is None:
            log.debug("iconvert not on PATH — skipping EXR thumbnail")
            return None
        try:
            subprocess.run(
                [exe, str(src), str(out)],
                check=True,
                creationflags=0x08000000,
                capture_output=True,
                timeout=30,
            )
        except Exception:
            log.debug("iconvert failed for %s", src, exc_info=True)
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return out if out.exists() else None

    @staticmethod
    def _render_thumb_from_mp4(src: Path, out: Path) -> Path | None:
        """Extract the first frame of ``src`` as a 512px PNG via ffmpeg."""
        import shutil
        import subprocess
        exe = shutil.which("ffmpeg")
        if exe is None:
            log.debug("ffmpeg not on PATH — skipping MP4 thumbnail")
            return None
        try:
            subprocess.run(
                [
                    exe, "-y", "-loglevel", "error",
                    "-i", str(src),
                    "-vf", "scale=512:-1",
                    "-frames:v", "1",
                    str(out),
                ],
                check=True,
                creationflags=0x08000000,
                capture_output=True,
                timeout=30,
            )
        except Exception:
            log.debug("ffmpeg thumbnail failed for %s", src, exc_info=True)
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return out if out.exists() else None

    def get_card_menu_items(self, asset: Asset):
        """Catalog-contributed card right-click items."""
        asset_id = asset.id

        # Playblast cards: small viewer menu mirroring renders.
        # mplay omitted because mplay can't play MP4.
        if "type:playblast" in asset.tags:
            return [
                (
                    "Open Latest in DJV",
                    lambda aid=asset_id:
                        self._execute_playblast_action(
                            "playblast_open_djv", aid,
                        ),
                ),
                (
                    "Open Folder",
                    lambda aid=asset_id:
                        self._execute_playblast_action(
                            "playblast_open_folder", aid,
                        ),
                ),
            ]

        # Render assets get a small viewer-focused menu — none of the
        # workfile/database items make sense for a frame sequence.
        if "type:render" in asset.tags:
            # mplay only when the primary source is EXR. Daily-only
            # cards still get a DJV button (mplay can't play MP4).
            sources = (asset.metadata or {}).get("sources") or {}
            primary_key = (
                (asset.metadata or {}).get("primary_source")
                or (
                    self._pick_primary_source(sources)
                    if sources else ""
                )
            )
            primary_is_daily = (
                bool(primary_key)
                and sources.get(primary_key, {}).get("kind") == "daily"
            )
            menu: list = []
            if not primary_is_daily:
                menu.append((
                    "Open in MPlay (latest)",
                    lambda aid=asset_id:
                        self._execute_render_action(
                            "render_open_mplay", aid,
                        ),
                ))
            menu.append((
                "Open in DJV (latest)",
                lambda aid=asset_id:
                    self._execute_render_action(
                        "render_open_djv", aid,
                    ),
            ))
            menu.append((
                "Open Folder",
                lambda aid=asset_id:
                    self._execute_render_action(
                        "render_open_folder", aid,
                    ),
            ))
            return menu

        items = [
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
                    "Clear scene",
                    lambda aid=asset_id: self._clear_shot_scene_ref(aid),
                )
            )
        return items

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
        consistent across both surfaces. Both branches expose
        ``New: Current`` and ``New: Template`` so the user can spawn
        a fresh version from either the loaded scene or a template
        regardless of whether the dept already has versions.
        """
        # Playblast dept sub-cards → DJV / open folder for that dept's
        # latest version. ``sub_card_key`` is the dept name.
        if "type:playblast" in asset.tags:
            asset_id = asset.id
            dept = sub_card_key
            return [
                (
                    f"Open in DJV ({dept})",
                    lambda aid=asset_id, d=dept:
                        self._execute_playblast_action(
                            "playblast_open_djv", aid, dept=d,
                        ),
                ),
                (
                    "Open Folder",
                    lambda aid=asset_id, d=dept:
                        self._execute_playblast_action(
                            "playblast_open_folder", aid, dept=d,
                        ),
                ),
            ]

        # Render AOV sub-cards → mplay / djv / open folder scoped to
        # that pass within the primary source's latest version.
        # ``sub_card_key`` is the AOV name (e.g. "beauty", "normal").
        if "type:render" in asset.tags:
            asset_id = asset.id
            aov = sub_card_key
            return [
                (
                    f"Open in MPlay ({aov})",
                    lambda aid=asset_id, a=aov:
                        self._execute_render_action(
                            "render_open_mplay", aid, aov=a,
                        ),
                ),
                (
                    f"Open in DJV ({aov})",
                    lambda aid=asset_id, a=aov:
                        self._execute_render_action(
                            "render_open_djv", aid, aov=a,
                        ),
                ),
                (
                    "Open Folder",
                    lambda aid=asset_id, a=aov:
                        self._execute_render_action(
                            "render_open_folder", aid, aov=a,
                        ),
                ),
            ]

        dept = sub_card_key
        asset_id = asset.id
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
        # Render / Playblast cards get a simpler Info-only panel —
        # workfile Departments / Tasks tabs don't apply.
        if "type:render" in detail.tags or "type:playblast" in detail.tags:
            return [
                DetailSection(
                    key="info",
                    title="Info",
                    icon="info",
                    widget_factory=self._build_combined_info_section,
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
        """Info tab content — description, asset/shot info table,
        Open-in-Database-Editor, and the catalog's per-asset action
        buttons (Import to Scene, Open Export Folder, Edit, Delete).
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QVBoxLayout, QWidget,
        )
        from asset_browser.ui.detail_panel import ElidingPushButton
        from asset_browser.core.theme import (
            BUTTON_GHOST_STYLE, DANGER,
        )
        detail = ctx.detail

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Description (plain paragraph)
        if detail.description:
            desc = QLabel(detail.description)
            desc.setWordWrap(True)
            desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
            vbox.addWidget(desc)

        # Asset / Shot / Render / Playblast info table
        is_render = "type:render" in detail.tags
        is_playblast = "type:playblast" in detail.tags
        if "type:asset" in detail.tags:
            info_w = self._build_asset_info_section(ctx)
            if info_w is not None:
                vbox.addWidget(info_w)
        elif "type:shot" in detail.tags:
            info_w = self._build_shot_info_section(ctx)
            if info_w is not None:
                vbox.addWidget(info_w)
        elif is_render:
            info_w = self._build_render_info_section(ctx)
            if info_w is not None:
                vbox.addWidget(info_w)
        elif is_playblast:
            info_w = self._build_playblast_info_section(ctx)
            if info_w is not None:
                vbox.addWidget(info_w)

        # Open in Database Editor — only for entity-backed assets;
        # renders + playblasts are filesystem-only so the button
        # would be a dead link.
        if not (is_render or is_playblast):
            db_btn = ElidingPushButton("Open in Database Editor…")
            db_btn.setStyleSheet(BUTTON_GHOST_STYLE)
            db_btn.clicked.connect(
                lambda _checked=False, aid=detail.id: self._open_database_editor(aid)
            )
            vbox.addWidget(db_btn)

        # Per-asset action buttons (formerly the standalone Actions tab)
        try:
            actions = self.get_actions(detail)
        except Exception:
            log.exception("get_actions failed for %s", detail.id)
            actions = []
        for action in actions:
            if getattr(action, "file_id", None) is not None:
                continue  # file-bound download actions are not surfaced here
            btn = ElidingPushButton(action.label)
            if getattr(action, "destructive", False):
                btn.setStyleSheet(
                    BUTTON_GHOST_STYLE
                    + f"\nQPushButton {{ color: {DANGER}; }}"
                    + f"\nQPushButton:hover {{ background-color: {DANGER};"
                    f" color: white; }}"
                )
            else:
                btn.setStyleSheet(BUTTON_GHOST_STYLE)
            btn.setEnabled(getattr(action, "enabled", True))
            if getattr(action, "tooltip", ""):
                btn.setToolTip(action.tooltip)
            btn.clicked.connect(
                lambda _checked=False, a=action, d=detail:
                    self.execute_action(a.id, d)
            )
            vbox.addWidget(btn)

        vbox.addStretch(1)
        return holder

    def _build_combined_departments_section(self, ctx: DetailContext):
        """Departments tab content — the existing departments grid plus
        the Groups & Scenes membership pills below.
        """
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        depts_w = self._build_departments_section(ctx)
        if depts_w is not None:
            vbox.addWidget(depts_w)

        try:
            mem_w = self._build_membership_section(ctx)
        except Exception:
            mem_w = None
        if mem_w is not None:
            vbox.addWidget(mem_w)

        vbox.addStretch(1)
        return holder

    def _build_asset_info_section(self, ctx: DetailContext):
        meta = ctx.detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        if meta.get("category"):
            rows.append(("Category", str(meta["category"])))
        variants = meta.get("variants") or []
        if variants:
            rows.append(("Variants", ", ".join(variants)))
        return self._build_info_table(rows)

    def _build_playblast_info_section(self, ctx: DetailContext):
        meta = ctx.detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        entity_segs = meta.get("entity_segs") or []
        entity_kind = meta.get("entity_kind") or ""
        if len(entity_segs) >= 2:
            rows.append((
                entity_kind.title().rstrip("s") or "Entity",
                f"{entity_segs[0]}/{entity_segs[1]}",
            ))
        depts = meta.get("depts") or {}
        primary = meta.get("primary_dept")
        if depts:
            # ``light* (v0005, 3 versions)`` per dept; primary marked.
            labels = []
            ordered = (
                [primary] + sorted(d for d in depts if d != primary)
            ) if primary in depts else sorted(depts.keys())
            for d in ordered:
                summary = depts[d]
                latest = summary.get("latest", "?")
                vcount = len(summary.get("versions", []) or [])
                marker = "*" if d == primary else ""
                bits = f"{latest}"
                if vcount > 1:
                    bits += f" ({vcount} versions)"
                labels.append(f"{d}{marker}: {bits}")
            rows.append(("Depts", "; ".join(labels)))
        if meta.get("latest_update"):
            try:
                import datetime
                ts = datetime.datetime.fromtimestamp(
                    meta["latest_update"],
                ).strftime("%Y-%m-%d %H:%M")
                rows.append(("Updated", ts))
            except Exception:
                pass
        return self._build_info_table(rows)

    def _build_render_info_section(self, ctx: DetailContext):
        meta = ctx.detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        entity_segs = meta.get("entity_segs") or []
        entity_kind = meta.get("entity_kind") or ""
        if len(entity_segs) >= 2:
            rows.append((
                entity_kind.title().rstrip("s") or "Entity",
                f"{entity_segs[0]}/{entity_segs[1]}",
            ))
        sources = meta.get("sources") or {}
        primary = meta.get("primary_source")
        if sources:
            # Friendly source list with the primary marked. Daily
            # entries get a (daily) suffix to disambiguate from
            # an EXR layer of the same dept name.
            labels = []
            for k in self._sorted_source_keys(sources):
                label = self._render_source_label(k, sources[k])
                if k == primary:
                    label = f"{label}*"
                labels.append(label)
            rows.append(("Sources", ", ".join(labels)))
        if meta.get("frame_range"):
            rows.append(("Frame Range", str(meta["frame_range"])))
        versions = meta.get("versions") or []
        if versions:
            rows.append(("Versions", ", ".join(versions)))
        aovs = meta.get("aovs") or []
        if aovs:
            rows.append(("AOVs", ", ".join(aovs)))
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
            rows.append(("Scene", f"{scene_label}{suffix}"))
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
            if proj is None or project_name not in self._clients:
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
            prefix = "G" if kind == "group" else "S"
            pill = QLabel(f"{prefix}  {label}")
            pill.setStyleSheet(pill_style)
            pill.setToolTip(f"{'Group' if kind == 'group' else 'Scene'}: {label}")
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
        # rule for ``QComboBox::down-arrow`` is concatenated).
        from asset_browser.core import theme as _theme_mod
        _arrow_url = _theme_mod._render_combo_arrow_png(TEXT_SECONDARY)

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
        ent_ctx = "shots" if is_shot else "assets"
        all_depts = self._list_entity_departments(ent_ctx)
        dept_shorts = self._list_entity_dept_shorts(ent_ctx)
        overrides = self._dept_version_overrides.get(asset_id, {})
        scene_dv = self._get_scene_dept_version(asset_id)
        active_dept = scene_dv[0] if scene_dv else None
        active_version = scene_dv[1] if scene_dv else None

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

                create_btn = QPushButton("Create")
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
        try:
            import hou
            hip = hou.hipFile.path()
        except Exception:
            log.exception("Reload Scene: failed to read current hip path")
            return
        if not hip:
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
            except Exception:
                log.exception("Reload Scene failed")
                return
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after reload failed")
            self._request_global_detail_refresh()

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
            # Drop scan caches and refresh the open detail so the new
            # version appears in any matching dropdown.
            self._cached_assets = None
            self._cached_shots = None
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after save failed")
        except Exception:
            log.exception("Save failed")

    def _publish_current_scene(self, refresh_cb=None) -> None:
        """Execute matching ExportLayer / ExportRig nodes for the loaded scene."""
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
            self._refresh_asset(None, refresh_cb)
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
        import threading
        existing_names = set(self._registry.names)
        new_names = {p.name for p in new_projects}

        # Remove projects that disappeared.
        for stale in existing_names - new_names:
            self._registry.remove(stale, save=False)
            self._clients.pop(stale, None)
            self._init_attempted.discard(stale)

        # Add / replace each entry.
        for proj in new_projects:
            existing = self._registry.get(proj.name)
            self._registry.add(proj, save=False)
            # If the entry's paths changed, force re-init on next browse.
            if existing is None or (
                existing.project_path != proj.project_path
                or existing.config_path != proj.config_path
            ):
                self._clients.pop(proj.name, None)
                self._init_attempted.discard(proj.name)

        try:
            self._registry.save()
        except Exception:
            log.exception("Failed to save projects.json")

        # Drop discovery cache so the next browse re-aggregates.
        self._cached_assets = None
        self._cached_shots = None

        # Build any newly-added (or path-changed) Clients eagerly on
        # the calling thread (Apply runs from the GUI-thread settings
        # widget). This is the only safe place to construct Clients.
        for proj in self._registry.all():
            if proj.name not in self._clients:
                self._build_client_now(proj)

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

    def on_drop(self, detail, drop) -> bool:
        """Import a single pipeline asset or shot into the scene.

        Activates the asset's project before constructing the import
        node so the LOP looks at the right pipeline config.

        Asset → ``th::import_asset::1.0`` with the entity URI.
        Shot  → ``th::import_shot::1.0``  with the entity URI.
        If the drop lands on an existing ``th::import_assets::2.0``
        node, the asset is appended as a new multiparm entry instead
        of creating a new node.
        """
        import hou

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
            QuickAction(id="save", label="Save", icon="save", tooltip="Save current scene"),
            QuickAction(id="publish", label="Publish", icon="upload", tooltip="Publish exports"),
            QuickAction(id="reload", label="Reload", icon="refresh-cw", tooltip="Reload current scene"),
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
        opts.append(CreationOption("new_group", "New Group...", "users"))
        opts.append(CreationOption("new_scene", "New Scene...", "layers"))
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
        client = self._clients.get(proj_name)
        if client is None:
            hou.ui.displayMessage(f"No client for project: {proj_name}")
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
                        f"Group '{name}' already exists in {context}."
                    )
                    return None
                grp_mod.add_group(context, name, [], [])
                hou.ui.setStatusMessage(
                    f"Created group: {name} ({context})",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Failed to create group %s", name)
                hou.ui.displayMessage(f"Failed to create group: {name}")
                return None
            return None  # Groups aren't assets — no card to select

        if option_id == "new_scene":
            try:
                from tumblepipe.config import scenes as scn_mod
                scene_uri = Uri.parse_unsafe(f"scenes:/{name}")
                existing = scn_mod.get_scene(scene_uri)
                if existing is not None:
                    hou.ui.displayMessage(
                        f"Scene '{name}' already exists."
                    )
                    return None
                scn_mod.add_scene(name)
                hou.ui.setStatusMessage(
                    f"Created scene: {name}",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Failed to create scene %s", name)
                hou.ui.displayMessage(f"Failed to create scene: {name}")
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
        client = self._ensure_client(project_name)
        if client is None:
            return []
        try:
            props = client.config.get_properties(uri) or {}
        except Exception:
            log.exception("get_properties failed for %s", asset_id)
            return []

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
        client = self._ensure_client(project_name)
        if client is None:
            return False
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
        except Exception:
            log.exception("set_properties failed for %s", asset_id)
            return False
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
        client = self._ensure_client(project_name)
        if client is None:
            return False
        try:
            client.config.remove_entity(uri)
        except Exception:
            log.exception("remove_entity failed for %s", asset_id)
            return False
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
            self.invalidate_cache()
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
            shot_ref_changed = False
            existing = list(getattr(scene, "assets", ()))
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
                # Shots dropped on a scene set the shot's scene_ref
                # (the shot "uses" this scene as its root-layer source).
                if uri_str.startswith("entity:/shots/"):
                    try:
                        scene_mod.set_scene_ref(uri, scene_uri)
                        added += 1
                        shot_ref_changed = True
                    except Exception:
                        log.exception("set_scene_ref failed for %s", aid)
                        skipped += 1
                        skip_reasons.add("set_scene_ref failed")
                    continue
                if not uri_str.startswith("entity:/assets/"):
                    skipped += 1
                    skip_reasons.add("scene accepts assets or shots")
                    continue
                if uri_str in existing_uris:
                    skipped += 1
                    skip_reasons.add("already in scene")
                    continue
                existing_uris.add(uri_str)
                try:
                    AssetEntry = scn_mod.AssetEntry  # type: ignore[attr-defined]
                    new_entries.append(
                        AssetEntry(asset=uri, instances=1)
                    )
                    added += 1
                except Exception:
                    log.exception("AssetEntry create failed")
                    skipped += 1
                    skip_reasons.add("entry create failed")
            if shot_ref_changed:
                # Shot detail panels show the assigned scene — refresh
                # so the user sees the change without re-selecting.
                try:
                    self._request_global_detail_refresh()
                except Exception:
                    pass
            if new_entries != list(existing):
                try:
                    scn_mod.set_scene_assets(scene_uri, new_entries)
                except Exception:
                    log.exception("set_scene_assets failed")
                    return (0, len(asset_ids), "set_scene_assets failed")
        else:
            return (0, 0, "")

        if added:
            self.invalidate_cache()
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
            drop_set = set()
            for aid in asset_ids:
                uri = self._uri_for_asset_id(aid)
                if uri is not None:
                    drop_set.add(str(uri))
            kept: list = []
            for entry in getattr(scene, "assets", ()):
                entry_uri = str(
                    getattr(entry, "asset", getattr(entry, "uri", entry))
                )
                if entry_uri in drop_set:
                    removed += 1
                else:
                    kept.append(entry)
            if removed:
                try:
                    scn_mod.set_scene_assets(scene_uri, kept)
                except Exception:
                    log.exception("set_scene_assets failed")
                    return (0, len(asset_ids), "set_scene_assets failed")
        if removed:
            self.invalidate_cache()
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
        is_render = "type:render" in detail.tags
        is_playblast = "type:playblast" in detail.tags

        if is_playblast:
            # Primary actions target the primary dept's latest mp4;
            # then one DJV button per other dept.
            meta = detail.metadata or {}
            depts: dict = meta.get("depts") or {}
            primary = meta.get("primary_dept")
            actions.append(AssetAction(
                id="playblast_open_djv",
                label="Open in DJV",
                icon="eye",
            ))
            actions.append(AssetAction(
                id="playblast_open_folder",
                label="Open Folder",
                icon="folder-open",
            ))
            for dept_name in sorted(depts.keys()):
                if dept_name == primary:
                    continue
                actions.append(AssetAction(
                    id=f"playblast_open_djv:dept={dept_name}",
                    label=f"Open {dept_name.title()} in DJV",
                    icon="eye",
                ))
            return actions

        if is_render:
            # Render cards are read-only viewers — no
            # import/edit/delete buttons. Primary action targets the
            # card's primary source (denoise/main → render/main → …);
            # then one mplay button per *other* source so the user can
            # jump between raw render, denoise, slapcomp, daily, etc.
            # without leaving the detail panel.
            meta = detail.metadata or {}
            sources: dict = meta.get("sources") or {}
            primary_key = (
                meta.get("primary_source")
                or (self._pick_primary_source(sources) if sources else None)
            )
            primary_info = sources.get(primary_key) if primary_key else None
            primary_is_daily = (
                primary_info is not None
                and primary_info.get("kind") == "daily"
            )

            # Primary actions
            if not primary_is_daily:
                actions.append(AssetAction(
                    id="render_open_mplay",
                    label="Open in MPlay",
                    icon="play",
                ))
            actions.append(AssetAction(
                id="render_open_djv",
                label="Open in DJV",
                icon="eye",
            ))
            actions.append(AssetAction(
                id="render_open_folder",
                label="Open Folder",
                icon="folder-open",
            ))

            # One row per other source. Daily entries label as
            # "Daily (light)"; EXR entries label as "Slapcomp" (when
            # layer is "slapcomp") or "<dept> (<layer>)" otherwise —
            # e.g. "Render (main)", "Composite (fx)".
            for src_key in self._sorted_source_keys(sources):
                if src_key == primary_key:
                    continue
                src_info = sources[src_key]
                label = self._render_source_label(src_key, src_info)
                viewer = (
                    "DJV" if src_info.get("kind") == "daily" else "MPlay"
                )
                action_id = (
                    "render_open_djv"
                    if src_info.get("kind") == "daily"
                    else "render_open_mplay"
                )
                actions.append(AssetAction(
                    id=f"{action_id}:source={src_key}",
                    label=f"Open {label} in {viewer}",
                    icon=(
                        "eye" if src_info.get("kind") == "daily" else "play"
                    ),
                ))
            return actions

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

        elif action_id == "import_asset" and detail:
            self._import_asset_to_scene(detail)

        elif action_id.startswith("open_workfile:"):
            dept = action_id.split(":", 1)[1]
            self._open_workfile(detail.id if detail else "", dept)

        elif action_id.startswith("render_open_") and detail:
            # Action id format:
            #   render_open_<viewer>[:source=<name>][:aov=<name>]
            # Plain ``render_open_X`` → primary source, layer frames.
            base, params = self._parse_render_action_id(action_id)
            if base is not None:
                self._execute_render_action(
                    base,
                    detail.id,
                    source=params.get("source"),
                    aov=params.get("aov"),
                )

        elif action_id.startswith("playblast_open_") and detail:
            # Action id format: ``playblast_open_<viewer>[:dept=<name>]``.
            # Plain ``playblast_open_X`` → primary dept, latest mp4.
            base, params = self._parse_playblast_action_id(action_id)
            if base is not None:
                self._execute_playblast_action(
                    base,
                    detail.id,
                    dept=params.get("dept"),
                )

    # ── Render: viewer launching ──────────────────────────

    def _resolve_render_source(
        self, asset_id: str, source: str | None,
    ) -> dict | None:
        """Look up the source dict for ``source`` on a render card.

        ``source`` is a key into ``metadata["sources"]`` of the form
        ``"<layer>/<dept>"`` (EXR) or ``"daily/<dept>"`` (daily MP4).
        ``None`` returns the card's primary source. Returns ``None``
        if the card / source can't be found.
        """
        if not self._is_render_asset_id(asset_id):
            return None
        asset = next(
            (a for a in self._get_all_items() if a.id == asset_id), None,
        )
        if asset is None:
            return None
        sources = asset.metadata.get("sources") or {}
        if not sources:
            return None
        primary = (
            asset.metadata.get("primary_source")
            or self._pick_primary_source(sources)
        )
        chosen = source or primary
        if chosen not in sources:
            chosen = primary
        return {"key": chosen, "info": sources[chosen]}

    def _resolve_render_layer(
        self, asset_id: str, source: str | None = None,
    ):
        """Resolve a render asset id + source key to a ``Layer``
        (the dataclass from ``tumblepipe.pipe.paths``) by scanning
        the on-disk version dir directly.

        ``source`` selects an EXR source (``"main/denoise"`` etc.).
        Returns ``None`` for daily sources or unresolvable paths.
        """
        info = self._resolve_render_source(asset_id, source)
        if info is None:
            return None
        src = info["info"]
        if src.get("kind") != "exr":
            return None
        try:
            from tumblepipe.pipe.paths import AOV, Layer
        except Exception:
            return None

        version_path = Path(src["path"])
        try:
            if not version_path.is_dir():
                return None
        except Exception:
            return None

        layer_name = src["layer"]
        suffix = "exr"
        aovs: dict = {}
        try:
            for child in version_path.iterdir():
                if not child.is_dir() or child.suffix != "":
                    continue
                aov_files = [
                    f for f in child.iterdir()
                    if f.is_file() and f.suffix == f".{suffix}"
                ]
                if not aov_files:
                    continue
                aov_name = child.name
                first = sorted(aov_files)[0]
                aov_basename = first.name.split(".")[0]
                aovs[aov_name] = AOV(
                    path=child,
                    label=aov_name,
                    name=aov_basename,
                    suffix=suffix,
                )
        except Exception:
            pass

        layer_basename = layer_name
        try:
            flat_files = sorted(
                f for f in version_path.iterdir()
                if f.is_file() and f.suffix.lower() in (
                    ".exr", ".jpg", ".png", ".tif",
                )
            )
            if flat_files:
                layer_basename = flat_files[0].name.split(".")[0]
                suffix = flat_files[0].suffix.lstrip(".") or suffix
        except Exception:
            pass

        return Layer(
            path=version_path,
            label=layer_name,
            version=version_path.name,
            aovs=aovs,
            name=layer_basename,
            suffix=suffix,
        )

    def _resolve_daily_path(
        self, asset_id: str, source: str | None = None,
    ):
        """Return the on-disk MP4 for a daily source on a render card.

        ``source`` is a daily key like ``"daily/light"``; ``None``
        defaults to the card's primary source if it's a daily.
        Returns the cached MP4 path from ``metadata["sources"]``
        when present (no project activation needed). Returns ``None``
        when the source isn't a daily / file is missing.
        """
        info = self._resolve_render_source(asset_id, source)
        if info is None:
            return None
        src = info["info"]
        if src.get("kind") != "daily":
            return None
        path_str = src.get("path")
        if not path_str:
            return None
        mp4 = Path(path_str)
        try:
            return mp4 if mp4.exists() else None
        except Exception:
            return None

    @staticmethod
    def _parse_render_action_id(action_id: str) -> tuple[str | None, dict]:
        """Parse a render-action id of the form
        ``render_open_<viewer>[:key=value][:key=value]``.

        Returns ``(base_action, params_dict)`` or ``(None, {})`` if the
        id doesn't match a known action. Recognised viewers: ``mplay``,
        ``djv``, ``folder``. Recognised params: ``source``, ``aov``.
        """
        if not action_id.startswith("render_open_"):
            return (None, {})
        head, *tail = action_id.split(":")
        if head not in (
            "render_open_mplay", "render_open_djv", "render_open_folder",
        ):
            return (None, {})
        params: dict = {}
        for chunk in tail:
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            if k in ("source", "aov"):
                params[k] = v
        return (head, params)

    def _execute_render_action(
        self,
        action_id: str,
        asset_id: str,
        source: str | None = None,
        aov: str | None = None,
    ) -> None:
        """Dispatch one of ``render_open_mplay`` / ``render_open_djv`` /
        ``render_open_folder`` against a render card.

        ``source`` is a key into ``metadata["sources"]`` of the form
        ``"<layer>/<dept>"`` (EXR) or ``"daily/<dept>"`` (daily MP4).
        ``None`` uses the card's primary source. ``aov`` scopes the
        EXR action to a single render pass; daily sources ignore it.
        """
        info = self._resolve_render_source(asset_id, source)
        if info is None:
            self._render_status_warn(
                f"Render source not found for {asset_id}"
                + (f" / source={source}" if source else ""),
            )
            return
        src = info["info"]
        is_daily = src.get("kind") == "daily"

        if is_daily:
            mp4 = self._resolve_daily_path(asset_id, source=info["key"])
            if mp4 is None:
                self._render_status_warn(
                    f"Daily MP4 not found for {asset_id}",
                )
                return
            if action_id == "render_open_folder":
                self._render_open_folder(mp4.parent)
            else:
                # mplay can't play MP4; both mplay and djv requests
                # fall through to DJV here.
                self._render_launch_viewer("djv_view", str(mp4))
            return

        layer = self._resolve_render_layer(asset_id, source=info["key"])
        if layer is None:
            self._render_status_warn(
                f"Render layer not found for {asset_id} / {info['key']}",
            )
            return

        if aov:
            aov_obj = layer.get_aov(aov)
            if aov_obj is None:
                self._render_status_warn(
                    f"AOV {aov!r} not found in latest version of {asset_id}",
                )
                return
            target_dir = aov_obj.path
            mplay_frame = aov_obj.get_aov_frame_path("$F4")
            djv_frame = aov_obj.get_aov_frame_path("####")
        else:
            target_dir = layer.path
            mplay_frame = layer.get_frame_path("$F4")
            djv_frame = layer.get_frame_path("####")

        if action_id == "render_open_folder":
            self._render_open_folder(target_dir)
        elif action_id == "render_open_mplay":
            self._render_launch_viewer("mplay", str(mplay_frame))
        elif action_id == "render_open_djv":
            self._render_launch_viewer("djv_view", str(djv_frame))

    @staticmethod
    def _render_open_folder(path) -> None:
        try:
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(path)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception("Failed to open folder %s", path)

    def _render_launch_viewer(self, executable: str, *args: str) -> None:
        """Launch ``executable`` with ``args`` via ``subprocess.Popen``.

        Resolves the binary against ``PATH`` (Houdini's ``mplay`` ships
        on PATH inside Houdini's environment; ``djv_view`` requires a
        DJV install). Surfaces a status-bar warning when missing rather
        than raising, so a missing viewer doesn't crash the action.
        """
        import shutil
        import subprocess
        resolved = shutil.which(executable)
        if resolved is None:
            # Try common alt names (DJV ships as ``djv`` on some platforms,
            # ``djv_view`` on others).
            if executable == "djv_view":
                resolved = shutil.which("djv")
        if resolved is None:
            self._render_status_warn(
                f"{executable!r} not found on PATH — install it or "
                "add it to PATH and try again.",
            )
            return
        try:
            subprocess.Popen([resolved, *args])
        except Exception:
            log.exception(
                "Failed to launch %s with %s", resolved, args,
            )
            self._render_status_warn(
                f"Failed to launch {executable}; see Houdini console.",
            )

    @staticmethod
    def _render_status_warn(message: str) -> None:
        try:
            import hou
            hou.ui.setStatusMessage(
                message, severity=hou.severityType.Warning,
            )
        except Exception:
            log.warning(message)

    # ── Playblast: viewer launching ───────────────────────

    @staticmethod
    def _parse_playblast_action_id(
        action_id: str,
    ) -> tuple[str | None, dict]:
        """Parse ``playblast_open_<viewer>[:dept=<name>]``.

        Returns ``(base_action, params_dict)`` or ``(None, {})``.
        Recognised viewers: ``djv``, ``folder``. Recognised params:
        ``dept``.
        """
        if not action_id.startswith("playblast_open_"):
            return (None, {})
        head, *tail = action_id.split(":")
        if head not in ("playblast_open_djv", "playblast_open_folder"):
            return (None, {})
        params: dict = {}
        for chunk in tail:
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            if k == "dept":
                params[k] = v
        return (head, params)

    def _resolve_playblast_path(
        self, asset_id: str, dept: str | None = None,
    ):
        """Return the on-disk MP4 path for a playblast dept on a card.

        Reads ``metadata["depts"][dept]["path"]`` directly — no
        project activation, no storage walk. ``dept`` defaults to
        the card's primary dept.
        """
        if not self._is_playblast_asset_id(asset_id):
            return None
        asset = next(
            (a for a in self._get_all_items() if a.id == asset_id),
            None,
        )
        if asset is None:
            return None
        depts = asset.metadata.get("depts") or {}
        if not depts:
            return None
        chosen = dept or asset.metadata.get("primary_dept")
        if chosen not in depts:
            return None
        path_str = depts[chosen].get("path")
        if not path_str:
            return None
        mp4 = Path(path_str)
        try:
            return mp4 if mp4.exists() else None
        except Exception:
            return None

    def _execute_playblast_action(
        self,
        action_id: str,
        asset_id: str,
        dept: str | None = None,
    ) -> None:
        """Dispatch ``playblast_open_djv`` / ``playblast_open_folder``
        for a playblast card. ``dept`` selects which dept's latest
        MP4 to target; ``None`` uses the primary."""
        mp4 = self._resolve_playblast_path(asset_id, dept=dept)
        if mp4 is None:
            self._render_status_warn(
                f"Playblast not found for {asset_id}"
                + (f" / dept={dept}" if dept else ""),
            )
            return
        if action_id == "playblast_open_folder":
            self._render_open_folder(mp4.parent)
        else:
            # playblast_open_djv — DJV plays MP4 directly.
            self._render_launch_viewer("djv_view", str(mp4))

    # ── Sub-cards (departments) ───────────────────────────

    def get_sub_cards(self, asset: Asset) -> list[SubCard]:
        # Render assets — one sub-card per render pass (AOV) from the
        # latest version. Layers without AOV subdirs (e.g. slapcomp
        # JPG sequences) get no sub-cards; the main-card actions
        # cover them. Dailies have no AOVs either.
        if "type:render" in asset.tags:
            aovs = asset.metadata.get("aovs", []) or []
            if not aovs:
                return []
            _AOV_ICONS = {
                "beauty": "image",
                "albedo": "palette",
                "alpha": "circle-dashed",
                "normal": "compass",
                "depth": "ruler",
                "diffuse": "sun",
                "specular": "sparkle",
                "emission": "flame",
            }
            cards: list[SubCard] = []
            for aov in aovs:
                cards.append(SubCard(
                    key=aov,
                    label=aov.title(),
                    status="available",
                    icon=_AOV_ICONS.get(aov.lower(), "layers"),
                    action_id=f"render_open_mplay:aov={aov}",
                ))
            return cards

        # Playblast assets — one sub-card per dept with a playblast.
        # Version goes in the label (e.g. "Light v0005") so it's
        # immediately visible rather than buried in the dim detail row.
        if "type:playblast" in asset.tags:
            depts_meta: dict = asset.metadata.get("depts", {}) or {}
            primary = asset.metadata.get("primary_dept")
            cards: list[SubCard] = []
            ordered = (
                [primary] + sorted(d for d in depts_meta if d != primary)
            ) if primary in depts_meta else sorted(depts_meta.keys())
            for dept_name in ordered:
                summary = depts_meta.get(dept_name) or {}
                short = _DEPT_SHORT_NAMES.get(
                    dept_name, dept_name.title(),
                )
                latest = summary.get("latest", "")
                vcount = len(summary.get("versions", []) or [])
                label = f"{short} {latest}" if latest else short
                # When the dept has more than one version, surface the
                # count in the dim detail row (e.g. "3 versions").
                detail = (
                    f"{vcount} versions" if vcount > 1 else ""
                )
                cards.append(SubCard(
                    key=dept_name,
                    label=label,
                    status=(
                        "active" if dept_name == primary else "available"
                    ),
                    detail=detail,
                    icon=_DEPT_ICONS.get(dept_name, "video"),
                    action_id=f"playblast_open_djv:dept={dept_name}",
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

        for dept_name in all_depts:
            short = _DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
            version = depts.get(dept_name)
            if version:
                status = (
                    "active" if dept_name == active_dept else "available"
                )
                cards.append(SubCard(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail=version,
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
            ListColumn(key="status", label="", width=30),
            ListColumn(key="name", label="Name"),
            ListColumn(key="category", label="Category", width=100),
            ListColumn(key="dept_count", label="Depts", width=60, align="center"),
        ]

    # ── Cache invalidation ────────────────────────────────

    def invalidate_cache(self) -> None:
        """Drop discovered asset/shot caches; next query re-fetches."""
        self._cached_assets = None
        self._cached_shots = None
        self._cached_renders = None
        self._cached_playblasts = None

    def _reset_project_clients(self) -> None:
        """Drop per-project API clients (Shift+Click full reset)."""
        self._clients.clear()
        self.invalidate_cache()

    # ── Internal helpers ──────────────────────────────────

    def _get_all_items(self) -> list[Asset]:
        """List assets + shots + renders + playblasts (across projects)."""
        if (
            self._cached_assets is not None
            and self._cached_shots is not None
            and self._cached_renders is not None
            and self._cached_playblasts is not None
        ):
            return (
                self._cached_assets
                + self._cached_shots
                + self._cached_renders
                + self._cached_playblasts
            )
        assets = self._discover_assets()
        shots = self._discover_shots()
        renders = self._discover_renders()
        playblasts = self._discover_playblasts()
        # Don't cache an empty result when no clients were ready yet —
        # a transient "no projects loaded" state would otherwise stick
        # forever and require a manual refresh to recover.
        has_projects = any(True for _ in self._registry.all())
        no_clients_ready = not self._clients
        if (
            not assets and not shots and not renders and not playblasts
            and has_projects and no_clients_ready
        ):
            return []
        self._cached_assets = assets
        self._cached_shots = shots
        self._cached_renders = renders
        self._cached_playblasts = playblasts
        return (
            self._cached_assets
            + self._cached_shots
            + self._cached_renders
            + self._cached_playblasts
        )

    def _discover_assets(self) -> list[Asset]:
        """Aggregate assets from every registered project's Client."""
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client = self._ensure_client(proj.name)
            if client is None:
                log.warning(
                    "Skipping asset discovery for %s — Client not ready",
                    proj.name,
                )
                continue
            # Activate this project's env so latest_export_path
            # resolves against the right config.
            self._activate_project(proj)
            try:
                for a in self._discover_assets_for(proj, client):
                    by_id.setdefault(a.id, a)
            except Exception:
                log.exception(
                    "_discover_assets failed for project %s", proj.name,
                )
        return list(by_id.values())

    def _discover_assets_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        try:
            all_entities = client.config.list_entities(None, closure=True)
        except Exception:
            log.exception(
                "Failed to list entities for project %s", proj.name,
            )
            return []

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

    def _discover_shots(self) -> list[Asset]:
        """Aggregate shots from every registered project's Client."""
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client = self._ensure_client(proj.name)
            if client is None:
                log.warning(
                    "Skipping shot discovery for %s — Client not ready",
                    proj.name,
                )
                continue
            self._activate_project(proj)
            try:
                for a in self._discover_shots_for(proj, client):
                    by_id.setdefault(a.id, a)
            except Exception:
                log.exception(
                    "_discover_shots failed for project %s", proj.name,
                )
        return list(by_id.values())

    def _discover_shots_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        try:
            all_entities = client.config.list_entities(None, closure=True)
        except Exception:
            log.exception(
                "Failed to list shot entities for project %s", proj.name,
            )
            return []

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
                if fr:
                    # FrameRange uses start_frame / end_frame in newer
                    # tumblehead versions; fall back to first/last for
                    # older builds.
                    start = getattr(fr, "start_frame", None)
                    end = getattr(fr, "end_frame", None)
                    if start is None or end is None:
                        start = getattr(fr, "first_frame", None)
                        end = getattr(fr, "last_frame", None)
                    if start is not None and end is not None:
                        frame_range = f"{start}-{end}"
            except Exception:
                pass

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

    # ── Render discovery (EXR layers + daily MP4s) ────────

    def _discover_renders(self) -> list[Asset]:
        """Aggregate render layers + dailies from every project."""
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client = self._ensure_client(proj.name)
            if client is None:
                log.warning(
                    "Skipping render discovery for %s — Client not ready",
                    proj.name,
                )
                continue
            self._activate_project(proj)
            try:
                for a in self._discover_renders_for(proj, client):
                    by_id.setdefault(a.id, a)
            except Exception:
                log.exception(
                    "_discover_renders failed for project %s", proj.name,
                )
        return list(by_id.values())

    # Order of preference when picking the "primary" source on a
    # per-shot render card. Tried in (layer, dept) tuples — first
    # match wins. Then anything ``denoise/X``, then ``render/X``,
    # then any EXR source, finally daily.
    _PRIMARY_DEPT_PRIORITY = ("denoise", "render", "composite")
    _PRIMARY_LAYER_PRIORITY = ("main",)

    def _discover_renders_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        """Walk storage and emit one Asset per shot.

        Every layer × dept under ``render:/render/<kind>/<seg1>/<seg2>/``
        plus every daily MP4 under ``render:/daily/<kind>/<dept>/``
        is folded into the same card as a ``source`` in
        ``metadata["sources"]``. The card's primary action targets
        the best source — typically ``denoise/main``.

        Source key format:
        - ``"<layer>/<dept>"`` for EXR sequences
          (e.g. ``"main/denoise"``, ``"slapcomp/denoise"``)
        - ``"daily/<dept>"`` for daily MP4s
          (e.g. ``"daily/light"``)

        Disk-driven (not entity-driven). Skips legacy paths that
        don't match ``<kind>/<seq>/<shot>/<dept>/<layer>/<version>``.
        """
        try:
            from tumblepipe.api import default_client
        except Exception:
            log.debug("tumblepipe.api unavailable")
            return []
        try:
            api = default_client()
        except Exception:
            log.debug("default_client() failed for %s", proj.name)
            return []

        # Per-entity aggregation: ``{(kind, seg1, seg2): {source_key: info}}``
        per_entity: dict[
            tuple[str, str, str], dict[str, dict],
        ] = {}

        # ── EXR layers ───────────────────────────────────
        render_root = self._resolve_storage_root(api, "render:/render")
        if render_root is not None:
            for kind, seg1, seg2, dept, layer_name, versions in (
                self._iter_disk_render_layers(render_root)
            ):
                if not versions:
                    continue
                latest = versions[-1]
                key = f"{layer_name}/{dept}"
                per_entity.setdefault((kind, seg1, seg2), {})[key] = {
                    "kind": "exr",
                    "layer": layer_name,
                    "dept": dept,
                    "version": latest["version"],
                    "versions": [v["version"] for v in versions],
                    "aovs": latest["aovs"],
                    "path": str(latest["path"]),
                    "mtime": latest["mtime"],
                }

        # ── Daily MP4s ───────────────────────────────────
        daily_root = self._resolve_storage_root(api, "render:/daily")
        if daily_root is not None:
            for kind, seg1, seg2, dept, mp4_path, mtime in (
                self._iter_disk_dailies(daily_root)
            ):
                key = f"daily/{dept}"
                per_entity.setdefault((kind, seg1, seg2), {})[key] = {
                    "kind": "daily",
                    "layer": "",
                    "dept": dept,
                    "path": str(mp4_path),
                    "mtime": mtime,
                }

        # ── One card per entity ──────────────────────────
        out: list[Asset] = []
        for (kind, s1, s2), sources in per_entity.items():
            primary_key = self._pick_primary_source(sources)
            primary = sources[primary_key]
            extra = {
                "primary_source": primary_key,
                "sources": sources,
                "latest_update": max(
                    s["mtime"] for s in sources.values()
                ),
            }
            # Surface the primary's frame info at top level so the
            # background thumbnail worker + sub-cards can find it
            # without re-reading sources.
            if primary["kind"] == "exr":
                extra["layer_path"] = primary["path"]
                extra["aovs"] = primary["aovs"]
                extra["versions"] = primary["versions"]
            else:
                extra["daily_path"] = primary["path"]
            out.append(self._build_render_asset(
                proj=proj,
                entity_kind=kind,
                entity_segs=(s1, s2),
                extra_metadata=extra,
            ))

        log.info(
            "Discovered %d render assets for project %s",
            len(out), proj.name,
        )
        return out

    @classmethod
    def _sorted_source_keys(cls, sources: dict[str, dict]) -> list[str]:
        """Return source keys in display order: main first, then
        other layers alphabetically, then dailies last."""
        main = []
        other_exr = []
        daily = []
        for k, v in sources.items():
            if v.get("kind") == "daily":
                daily.append(k)
            elif v.get("layer") == "main":
                main.append(k)
            else:
                other_exr.append(k)
        # Within each group: denoise → render → composite → alpha
        def dept_sort(k):
            dept = k.rsplit("/", 1)[-1]
            try:
                return (
                    cls._PRIMARY_DEPT_PRIORITY.index(dept), dept,
                )
            except ValueError:
                return (len(cls._PRIMARY_DEPT_PRIORITY), dept)
        return (
            sorted(main, key=dept_sort)
            + sorted(other_exr)
            + sorted(daily)
        )

    @staticmethod
    def _render_source_label(key: str, info: dict) -> str:
        """Friendly label for a source key — e.g.
        ``"main/denoise"`` → ``"Denoise (main)"``,
        ``"slapcomp/denoise"`` → ``"Slapcomp"``,
        ``"daily/light"`` → ``"Daily (light)"``.
        """
        if info.get("kind") == "daily":
            dept = info.get("dept") or key.split("/", 1)[-1]
            return f"Daily ({dept})"
        layer = info.get("layer") or ""
        dept = info.get("dept") or ""
        if layer == "slapcomp":
            return "Slapcomp"
        if layer == "main":
            return dept.title() if dept else "Main"
        if layer and dept:
            return f"{dept.title()} ({layer})"
        return key.title()

    @classmethod
    def _pick_primary_source(cls, sources: dict[str, dict]) -> str:
        """Pick the best source key from a per-entity sources dict.

        Priority:
        1. ``main/denoise`` → ``main/render`` → ``main/composite``
        2. Other ``<layer>/denoise`` (sorted)
        3. Other ``<layer>/render`` (sorted)
        4. Any EXR source (sorted)
        5. Any daily source (sorted)
        """
        # Tier 1: preferred (layer, dept) combos
        for layer in cls._PRIMARY_LAYER_PRIORITY:
            for dept in cls._PRIMARY_DEPT_PRIORITY:
                key = f"{layer}/{dept}"
                if key in sources:
                    return key
        # Tier 2 + 3: any layer at preferred depts
        exr = [k for k, v in sources.items() if v["kind"] == "exr"]
        for dept in cls._PRIMARY_DEPT_PRIORITY:
            for k in sorted(exr):
                if k.endswith(f"/{dept}"):
                    return k
        # Tier 4: any EXR
        if exr:
            return sorted(exr)[0]
        # Tier 5: daily
        daily = sorted(
            k for k, v in sources.items() if v["kind"] == "daily"
        )
        return daily[0]

    @staticmethod
    def _resolve_storage_root(api, prefix: str):
        """Resolve a ``<purpose>:/<root>`` URI to an on-disk Path,
        or ``None`` when the storage doesn't resolve / doesn't exist."""
        try:
            from tumblepipe.util.uri import Uri
            root = api.storage.resolve(Uri.parse_unsafe(prefix))
        except Exception:
            return None
        if root is None:
            return None
        try:
            return root if root.exists() else None
        except Exception:
            return None

    def _iter_disk_render_layers(self, render_root):
        """Walk ``render_root`` and yield render-layer tuples.

        Layout: ``<root>/<kind>/<seg1>/<seg2>/<dept>/<layer>/<version>/...``
        where ``<kind>`` is ``shots`` or ``assets``. Anything outside
        this shape (legacy / pre-convention scratch dirs) is skipped.

        Yields ``(kind, seg1, seg2, dept, layer_name, versions_list)``
        where each entry in ``versions_list`` is a dict with
        ``version`` / ``path`` / ``aovs`` / ``mtime`` keys, sorted by
        version code.
        """
        try:
            from tumblepipe.pipe.paths import list_version_paths
        except Exception:
            list_version_paths = None

        for kind in ("shots", "assets"):
            kind_root = render_root / kind
            try:
                if not kind_root.is_dir():
                    continue
            except Exception:
                continue
            try:
                seg1_dirs = sorted(d for d in kind_root.iterdir() if d.is_dir())
            except Exception:
                continue
            for seg1_dir in seg1_dirs:
                try:
                    seg2_dirs = sorted(
                        d for d in seg1_dir.iterdir() if d.is_dir()
                    )
                except Exception:
                    continue
                for seg2_dir in seg2_dirs:
                    try:
                        dept_dirs = sorted(
                            d for d in seg2_dir.iterdir() if d.is_dir()
                        )
                    except Exception:
                        continue
                    for dept_dir in dept_dirs:
                        try:
                            layer_dirs = sorted(
                                d for d in dept_dir.iterdir() if d.is_dir()
                            )
                        except Exception:
                            continue
                        for layer_dir in layer_dirs:
                            versions = self._scan_layer_versions(
                                layer_dir, list_version_paths,
                            )
                            if not versions:
                                continue
                            yield (
                                kind,
                                seg1_dir.name,
                                seg2_dir.name,
                                dept_dir.name,
                                layer_dir.name,
                                versions,
                            )

    @staticmethod
    def _scan_layer_versions(layer_dir, list_version_paths) -> list[dict]:
        """Return version-info dicts for a layer dir, sorted by
        version code (oldest → newest).

        Each dict: ``{"version": str, "path": Path, "aovs": list[str],
        "mtime": float}``. Versions with no usable frame data are
        excluded.
        """
        # Use tumblepipe's helper when available — it filters by the
        # project's naming convention. Fall back to alphabetical sort
        # over directories that look like ``vNNNN`` if not.
        if list_version_paths is not None:
            try:
                version_paths = list_version_paths(layer_dir)
            except Exception:
                version_paths = []
        else:
            try:
                version_paths = sorted(
                    d for d in layer_dir.iterdir()
                    if d.is_dir()
                    and d.name.startswith("v")
                    and d.name[1:].isdigit()
                )
            except Exception:
                version_paths = []

        out: list[dict] = []
        for vp in version_paths:
            # AOV subdirs (anything without an extension that looks
            # like a directory of frames). Empty ones are skipped.
            aovs: list[str] = []
            try:
                for child in vp.iterdir():
                    if child.is_dir() and child.suffix == "":
                        aovs.append(child.name)
            except Exception:
                pass

            # Has any frames? Either flat .exr/.jpg in vp, or any AOV
            # has files. Otherwise skip — empty version dirs are
            # noise (e.g. a flipbook-failed run).
            has_frames = False
            try:
                for f in vp.iterdir():
                    if f.is_file() and f.suffix.lower() in (".exr", ".jpg", ".png", ".tif"):
                        has_frames = True
                        break
            except Exception:
                pass
            if not has_frames and aovs:
                # Confirm at least one AOV has files
                for a in aovs:
                    try:
                        for _ in (vp / a).iterdir():
                            has_frames = True
                            break
                    except Exception:
                        continue
                    if has_frames:
                        break
            if not has_frames:
                continue

            ctx = vp / "context.json"
            try:
                mtime = ctx.stat().st_mtime if ctx.exists() else vp.stat().st_mtime
            except Exception:
                mtime = 0.0

            out.append({
                "version": vp.name,
                "path": vp,
                "aovs": sorted(aovs),
                "mtime": mtime,
            })
        return out

    def _iter_disk_dailies(self, daily_root):
        """Walk ``daily_root`` and yield ``(kind, seg1, seg2, dept,
        mp4_path, mtime)`` tuples.

        Layout: ``<root>/<kind>/<dept>/<seg1>_<seg2>.mp4`` (no layer).
        Same convention as ``tumblepipe.pipe.paths.get_daily_path``.
        Files with no underscore in the stem are skipped.
        """
        for kind in ("shots", "assets"):
            kind_root = daily_root / kind
            try:
                if not kind_root.is_dir():
                    continue
            except Exception:
                continue
            try:
                dept_dirs = sorted(
                    d for d in kind_root.iterdir() if d.is_dir()
                )
            except Exception:
                continue
            for dept_dir in dept_dirs:
                try:
                    files = sorted(
                        f for f in dept_dir.iterdir()
                        if f.is_file() and f.suffix.lower() == ".mp4"
                    )
                except Exception:
                    continue
                for mp4 in files:
                    stem = mp4.stem
                    if "_" not in stem:
                        # Convention requires <seg1>_<seg2> — skip
                        # malformed names.
                        continue
                    seg1, seg2 = stem.split("_", 1)
                    try:
                        mtime = mp4.stat().st_mtime
                    except Exception:
                        mtime = 0.0
                    yield (kind, seg1, seg2, dept_dir.name, mp4, mtime)

    # ── Playblast discovery (per-shot, per-dept MP4s) ─────

    def _discover_playblasts(self) -> list[Asset]:
        """Aggregate per-shot playblast cards from every project."""
        by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client = self._ensure_client(proj.name)
            if client is None:
                log.warning(
                    "Skipping playblast discovery for %s — Client not ready",
                    proj.name,
                )
                continue
            self._activate_project(proj)
            try:
                for a in self._discover_playblasts_for(proj, client):
                    by_id.setdefault(a.id, a)
            except Exception:
                log.exception(
                    "_discover_playblasts failed for project %s",
                    proj.name,
                )
        return list(by_id.values())

    def _discover_playblasts_for(
        self, proj: ProjectConfig, client,
    ) -> list[Asset]:
        """Walk the playblast storage root and emit one Asset per shot.

        Layout: ``<purpose>:/playblast/<kind>/<seg1>/<seg2>/<dept>/v<NNNN>.mp4``

        All depts that have at least one playblast for the same entity
        fold into a single per-shot card. The dept whose latest version
        has the newest mtime becomes the primary (drives the card's
        main button + thumbnail).
        """
        try:
            from tumblepipe.api import default_client
        except Exception:
            log.debug("tumblepipe.api unavailable")
            return []
        try:
            api = default_client()
        except Exception:
            log.debug("default_client() failed for %s", proj.name)
            return []

        root = self._resolve_storage_root(api, "render:/playblast")
        if root is None:
            return []

        # ``per_entity[(kind, seg1, seg2)] = {dept: {versions, latest, path, mtime}}``
        per_entity: dict[
            tuple[str, str, str], dict[str, dict],
        ] = {}
        for kind, seg1, seg2, dept, dept_summary in (
            self._iter_disk_playblasts(root)
        ):
            per_entity.setdefault((kind, seg1, seg2), {})[dept] = (
                dept_summary
            )

        out: list[Asset] = []
        for (kind, s1, s2), depts in per_entity.items():
            if not depts:
                continue
            primary_dept = max(
                depts.keys(), key=lambda d: depts[d]["mtime"],
            )
            extra = {
                "depts": depts,
                "primary_dept": primary_dept,
                "latest_update": max(d["mtime"] for d in depts.values()),
            }
            out.append(self._build_playblast_asset(
                proj=proj,
                entity_kind=kind,
                entity_segs=(s1, s2),
                extra_metadata=extra,
            ))

        log.info(
            "Discovered %d playblast assets for project %s",
            len(out), proj.name,
        )
        return out

    def _iter_disk_playblasts(self, root):
        """Walk ``root`` and yield ``(kind, seg1, seg2, dept,
        dept_summary)`` tuples.

        Layout: ``<root>/<kind>/<seg1>/<seg2>/<dept>/v<NNNN>.mp4``.
        ``dept_summary`` is
        ``{"versions": [...], "latest": str, "path": str, "mtime": float}``
        for the latest version. Empty dept dirs are skipped.
        """
        try:
            from tumblepipe.pipe.paths import (
                list_version_paths as _unused,  # noqa: F401
            )
        except Exception:
            pass

        for kind in ("shots", "assets"):
            kind_root = root / kind
            try:
                if not kind_root.is_dir():
                    continue
            except Exception:
                continue
            try:
                seg1_dirs = sorted(
                    d for d in kind_root.iterdir() if d.is_dir()
                )
            except Exception:
                continue
            for seg1_dir in seg1_dirs:
                try:
                    seg2_dirs = sorted(
                        d for d in seg1_dir.iterdir() if d.is_dir()
                    )
                except Exception:
                    continue
                for seg2_dir in seg2_dirs:
                    try:
                        dept_dirs = sorted(
                            d for d in seg2_dir.iterdir() if d.is_dir()
                        )
                    except Exception:
                        continue
                    for dept_dir in dept_dirs:
                        summary = self._scan_playblast_dept_dir(dept_dir)
                        if summary is None:
                            continue
                        yield (
                            kind,
                            seg1_dir.name,
                            seg2_dir.name,
                            dept_dir.name,
                            summary,
                        )

    @staticmethod
    def _scan_playblast_dept_dir(dept_dir) -> dict | None:
        """List versioned MP4s in a playblast dept dir and return a
        summary of the latest, or ``None`` if empty.

        Files are expected to be named ``v<NNNN>.mp4`` (matches
        ``paths.get_next_playblast_path``). Anything else is ignored.
        """
        try:
            mp4s = [
                f for f in dept_dir.iterdir()
                if f.is_file()
                and f.suffix.lower() == ".mp4"
                and f.stem.startswith("v")
                and f.stem[1:].isdigit()
            ]
        except Exception:
            return None
        if not mp4s:
            return None
        # Sort by version code (numeric) so v0010 > v0009.
        mp4s.sort(key=lambda f: int(f.stem[1:]))
        latest = mp4s[-1]
        try:
            mtime = latest.stat().st_mtime
        except Exception:
            mtime = 0.0
        return {
            "versions": [f.stem for f in mp4s],
            "latest": latest.stem,
            "path": str(latest),
            "mtime": mtime,
        }

    @staticmethod
    def _render_layer_mtime(layer_path) -> float:
        """Return a representative mtime for a layer version dir.

        Prefers ``context.json`` (written when the version finishes),
        falls back to the directory mtime, falls back to 0.0.
        """
        try:
            ctx = layer_path / "context.json"
            if ctx.exists():
                return ctx.stat().st_mtime
            return layer_path.stat().st_mtime
        except Exception:
            return 0.0

    def _build_render_asset(
        self,
        *,
        proj: ProjectConfig,
        entity_kind: str,
        entity_segs: tuple[str, str],
        extra_metadata: dict,
    ) -> Asset:
        """Build the per-shot render Asset.

        ``extra_metadata`` is expected to carry ``primary_source``,
        ``sources``, plus thumbnail-friendly top-level keys
        (``layer_path``/``aovs``/``versions`` for EXR primaries or
        ``daily_path`` for daily primaries).
        """
        asset_id = self._make_render_asset_id(
            project=proj.name,
            entity_kind=entity_kind,
            entity_segs=entity_segs,
        )
        entity_label = (
            f"{entity_segs[0]}_{entity_segs[1]}"
            if entity_kind == "shots" else entity_segs[1]
        )

        tags = {
            "source:pipeline",
            "type:render",
            "kind:media",  # union tag for the Media sidebar parent
            f"project:{proj.name}",
            (
                f"entity:shot:{entity_segs[0]}:{entity_segs[1]}"
                if entity_kind == "shots"
                else f"entity:asset:{entity_segs[0]}:{entity_segs[1]}"
            ),
        }
        # ``has:daily`` and ``has:exr`` tags let sidebar collections
        # filter the grid by what variants the shot actually has.
        sources = extra_metadata.get("sources") or {}
        if any(v["kind"] == "daily" for v in sources.values()):
            tags.add("has:daily")
        if any(v["kind"] == "exr" for v in sources.values()):
            tags.add("has:exr")

        metadata: dict = {
            "project": proj.name,
            "entity_kind": entity_kind,
            "entity_segs": list(entity_segs),
            # Sub-cards (AOVs) only when the primary is EXR.
            "has_sub_cards": (
                extra_metadata.get("primary_source", "").startswith(
                    "daily/",
                ) is False
            ),
        }
        metadata.update(extra_metadata)

        return Asset(
            id=asset_id,
            name=entity_label,
            thumbnail_url="",
            tags=frozenset(tags),
            metadata=metadata,
        )

    def _build_playblast_asset(
        self,
        *,
        proj: ProjectConfig,
        entity_kind: str,
        entity_segs: tuple[str, str],
        extra_metadata: dict,
    ) -> Asset:
        """Build the per-shot playblast Asset.

        ``extra_metadata`` carries ``depts`` (the per-dept summaries),
        ``primary_dept`` and ``latest_update``.
        """
        asset_id = self._make_playblast_asset_id(
            project=proj.name,
            entity_kind=entity_kind,
            entity_segs=entity_segs,
        )
        entity_label = (
            f"{entity_segs[0]}_{entity_segs[1]}"
            if entity_kind == "shots" else entity_segs[1]
        )
        tags = {
            "source:pipeline",
            "type:playblast",
            "kind:media",  # union tag for the Media sidebar parent
            f"project:{proj.name}",
            (
                f"entity:shot:{entity_segs[0]}:{entity_segs[1]}"
                if entity_kind == "shots"
                else f"entity:asset:{entity_segs[0]}:{entity_segs[1]}"
            ),
        }
        # ``dept:<name>`` tag per contributing dept so sidebar
        # filters (or future dept sub-collections) can scope cleanly.
        for dept_name in (extra_metadata.get("depts") or {}).keys():
            tags.add(f"dept:{dept_name}")

        metadata: dict = {
            "project": proj.name,
            "entity_kind": entity_kind,
            "entity_segs": list(entity_segs),
            "has_sub_cards": True,
        }
        metadata.update(extra_metadata)

        return Asset(
            id=asset_id,
            name=entity_label,
            thumbnail_url="",
            tags=frozenset(tags),
            metadata=metadata,
        )

    def _list_categories_for_project(self, project_name: str) -> list[str]:
        client = self._clients.get(project_name)
        if client is None:
            return []
        try:
            entities = client.config.list_entities(None, closure=True)
            return sorted({
                e.uri.segments[1]
                for e in entities
                if len(e.uri.segments) >= 3 and e.uri.segments[0] == "assets"
            })
        except Exception:
            return []

    def _list_sequences_for_project(self, project_name: str) -> list[str]:
        client = self._clients.get(project_name)
        if client is None:
            return []
        try:
            entities = client.config.list_entities(None, closure=True)
            return sorted({
                e.uri.segments[1]
                for e in entities
                if len(e.uri.segments) >= 3 and e.uri.segments[0] == "shots"
            })
        except Exception:
            return []

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
        except Exception:
            log.exception("Failed to scan workfile versions for %s", asset_id)
            return {}

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

    def _open_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a department.

        File-system inspection happens on the worker thread; the actual
        ``hou.hipFile.load`` must run on the GUI thread or Houdini crashes.
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
            if work_dir.exists():
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
