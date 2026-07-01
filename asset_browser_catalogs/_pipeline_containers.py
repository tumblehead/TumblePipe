"""Group / Scene container abstraction.

Replaces the ``if kind == "group" ... elif kind == "scene"`` branching
that used to be sprinkled across :class:`PipelineCatalog`. A
``collection_id`` of the form ``"group:<project>:<path>"`` or
``"scene:<project>:<path>"`` parses into a :class:`GroupContainer`
or :class:`SceneContainer` via :meth:`ContainerRef.parse`. Catalog
callsites then dispatch on type instead of re-stringifying.

The parsed refs carry exactly the URI helpers and identifying fields
the catalog used to compute inline:

- ``ref.uri`` — the ``groups:/<path>`` / ``scenes:/<path>`` URI.
- ``ref.context`` (group only) — the leading segment (``"shots"`` /
  ``"assets"``) used to scope member entity URIs.
- ``ref.collection_id`` — the canonical string form, round-tripping
  through :meth:`ContainerRef.parse`.

Container behaviour lives here too, on :class:`ContainerManager`:
collection discovery, container asset/detail synthesis, membership and
per-context department coverage, member add/remove, and the Root
context-menu actions (open location, rebuild assigned shots, export
USD). The catalog instantiates one ``ContainerManager`` and dispatches
to it; a few public names (the Root actions + ``get_asset_membership``)
keep thin delegators on the catalog because the asset browser invokes
them by name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.errors import TagQueryError
from tumbletrove.asset_browser.api.types import Asset, AssetDetail, AssetPage, Collection

import _pipeline_uris as uris
from _pipeline_types import version_code

if TYPE_CHECKING:
    from tumblepipe.util.uri import Uri
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupContainer:
    """A Multi (group). JSON-backed members list + per-context
    department coverage. Always belongs to one project."""

    project_name: str
    path: str

    kind: str = "group"

    @property
    def collection_id(self) -> str:
        return f"group:{self.project_name}:{self.path}"

    @property
    def uri(self) -> "Uri":
        return uris.group(self.path)

    @property
    def context(self) -> str:
        """``"shots"`` or ``"assets"`` — the first path segment. The
        single-segment form (no slash) is treated as ``"assets"``; the
        legacy ``_get_member_groups_for_project`` did the same thing
        and we preserve it so existing JSON keeps round-tripping."""
        return self.path.split("/", 1)[0] if "/" in self.path else "assets"


@dataclass(frozen=True)
class SceneContainer:
    """A Root (scene). Owns an asset list and acts as a shot's
    ``scene_ref`` destination."""

    project_name: str
    path: str

    kind: str = "scene"

    @property
    def collection_id(self) -> str:
        return f"scene:{self.project_name}:{self.path}"

    @property
    def uri(self) -> "Uri":
        return uris.scene(self.path)


ContainerRef = GroupContainer | SceneContainer


def parse(collection_id: str) -> ContainerRef | None:
    """Parse ``"<kind>:<project>:<path>"`` into the typed container.

    Returns ``None`` for empty input, missing prefix, or missing
    ``project:path`` split. Unknown kinds also return ``None`` —
    callers can use this as a single test for "is this a recognised
    container id at all".
    """
    if not collection_id:
        return None
    if collection_id.startswith("group:"):
        rest = collection_id[len("group:"):]
        if ":" not in rest:
            return None
        proj_name, path = rest.split(":", 1)
        return GroupContainer(proj_name, path)
    if collection_id.startswith("scene:"):
        rest = collection_id[len("scene:"):]
        if ":" not in rest:
            return None
        proj_name, path = rest.split(":", 1)
        return SceneContainer(proj_name, path)
    return None


class ContainerManager:
    """Group / Scene (Multi / Root) operations for the Pipeline catalog.

    Owns the behaviour the :class:`GroupContainer` / :class:`SceneContainer`
    typed boundary used to dispatch back into the catalog for: collection
    discovery and asset/detail synthesis, membership and per-context
    department coverage, member add/remove, and the Root context-menu
    actions (open location, rebuild assigned shots, export USD).

    Same back-reference pattern as :class:`WorkfileManager` /
    :class:`SceneManager` / :class:`DetailSectionBuilder`: every method
    reaches several catalog services (resolver, client pool, project
    activator, cache invalidation, card / detail refresh hooks), so the
    catalog is passed in whole rather than threading a dozen callables
    through the constructor.
    """

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog
        # Member-coverage cache: maps project name -> the parsed
        # ``groups.json`` coverage used by ``get_asset_membership`` and
        # the dept-pill builders. Lazily populated by
        # ``_get_member_groups_for_project``; dropped by
        # ``invalidate_membership_cache``. Membership changes don't alter
        # which entities *exist*, so this is busted independently of the
        # catalog's ``_cached_assets`` / ``_cached_shots``.
        self._member_groups_cache = None

    def invalidate_membership_cache(self) -> None:
        """Drop only the member-coverage cache.

        Membership changes don't alter ``_cached_assets`` /
        ``_cached_shots`` — those enumerate which entities *exist*, not
        who they belong to. Skipping their bust spares a costly
        filesystem rediscovery pass on the next sidebar / grid query
        (which dominates the perceived latency of add/remove ops).
        """
        self._member_groups_cache = None

    # ── Filtering ─────────────────────────────────────────

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
        proj = self._catalog._registry.get(proj_name)
        if proj is None or not self._catalog._clients.is_ready(proj_name):
            return items
        self._catalog._activate_project(proj)
        try:
            from tumblepipe.config import groups as grp_mod
            group_uri = uris.group(group_path)
            group = grp_mod.get_group(group_uri)
        except Exception as exc:
            raise TagQueryError(
                self._catalog.id,
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
        proj = self._catalog._registry.get(proj_name)
        if proj is None or not self._catalog._clients.is_ready(proj_name):
            return items
        self._catalog._activate_project(proj)
        try:
            from tumblepipe.config import scene as scn_mod
            scene_uri = uris.scene(scene_path)
            scene = scn_mod.get_scene_by_uri(scene_uri)
        except Exception as exc:
            raise TagQueryError(
                self._catalog.id,
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

    # ── Collection discovery ──────────────────────────────

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
        if not self._catalog._clients.is_ready(proj.name):
            return result
        try:
            self._catalog._activate_project(proj)
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
        if not self._catalog._clients.is_ready(proj.name):
            return children
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import scene as scn_mod
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
        for proj in self._catalog._registry.all():
            leaves = self._list_group_leaves_by_context(proj)
            for ctx in ("shots", "assets"):
                for grp in leaves[ctx]:
                    yield grp, proj.name

    def _iter_scene_collections(self):
        """Yield ``(scene_collection, project_name)`` for every scene
        in every registered project."""
        for proj in self._catalog._registry.all():
            for scn in self._build_scenes_for_project(proj):
                yield scn, proj.name

    def _container_collection_to_asset(
        self, collection: "Collection", project_name: str, kind: str,
    ) -> Asset:
        """Wrap a Group/Scene Collection in an Asset so the grid can
        render it as a card. The ``drill_tag`` field signals to
        the browser's card-click handler that the card represents a
        container — clicking it should drill into its members rather
        than open a detail panel.

        For groups, the asset also advertises ``has_sub_cards=True``
        and a flat ``departments`` list so the deck popup can render
        one sub-card per dept (mirroring the shot/asset open-workfile
        UX).
        """
        metadata: dict = {}
        dirty = False
        ctx = ""
        has_sub_cards = False
        if kind == "scene":
            # Surface a dirty flag so the renderer can paint an
            # "unsaved" indicator on Roots whose JSON has drifted
            # from the latest exported USD.
            try:
                dirty = self._root_is_dirty(collection.tag)
            except Exception:
                dirty = False
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
                has_sub_cards = True
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
            kind=kind,
            drill_tag=collection.tag,
            member_count=collection.count,
            dirty=dirty,
            context=ctx,
            has_sub_cards=has_sub_cards,
            metadata=metadata,
        )

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
        ref = parse(scene_id)
        if not isinstance(ref, SceneContainer):
            return False
        proj = self._catalog._registry.get(ref.project_name)
        if proj is None or not self._catalog._clients.is_ready(ref.project_name):
            return False
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import scene as scn_mod
            scene = scn_mod.get_scene_by_uri(ref.uri)
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
            export_root = get_scene_staged_path(ref.uri)
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
                (
                    p for p in export_root.iterdir()
                    if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit()
                ),
                key=lambda p: version_code(p.name),
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
        """Return the member-scoping context for a ``group:`` id, or
        ``None`` if it isn't a group id. Delegates to the single canonical
        classifier (GroupContainer.context) so this can't disagree with it —
        previously this returned ``None`` for a single-segment path while
        GroupContainer.context returned ``"assets"``, splitting member
        scoping between the two.
        """
        ref = parse(group_tag)
        if not isinstance(ref, GroupContainer):
            return None
        return ref.context

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
        proj = self._catalog._registry.get(project_name)
        if proj is None or not self._catalog._clients.is_ready(project_name):
            return []
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            grp = grp_mod.get_group(uris.group(path))
        except Exception:
            log.debug("Failed to resolve group %s", group_tag, exc_info=True)
            return []
        if grp is None:
            return []
        return [str(d) for d in getattr(grp, "departments", ())]

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
        proj = self._catalog._registry.get(proj_name)
        if proj is None or not self._catalog._clients.is_ready(proj_name):
            return {}
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.pipe import paths as paths_mod
            group_uri = uris.group(path)
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

    def _get_container_assets(
        self, kind: str, query: str, tags: frozenset[str],
        cursor: str | None, page_size: int,
    ) -> AssetPage:
        """Return synthetic Group/Scene cards for the grid.

        Called from :meth:`get_assets` when the active filter is
        ``type:group`` / ``type:scene``. Respects the ``project:``
        pill, the search box, and standard pagination.
        """
        self._catalog._discovery_errors = []
        for err in self._catalog._clients.ensure_all():
            self._catalog._discovery_errors.append(err)

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
        # Multi cards carry a non-empty ``context`` field today, so a
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
                    if a.context in multi_ctx_tags
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
            errors=tuple(self._catalog._discovery_errors),
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

        proj = self._catalog._registry.get(proj_name)
        if proj is not None:
            try:
                self._catalog._activate_project(proj)
            except Exception:
                log.exception("activate_project failed in container detail")

        metadata: dict = {
            "project": proj_name,
            "path": path,
        }
        member_count = 0
        ctx = ""

        if kind == "group":
            try:
                from tumblepipe.config import groups as grp_mod
                from tumblepipe.config import department as dept_mod
                grp = grp_mod.get_group(uris.group(path))
                covered: list[str] = []
                if grp is not None:
                    member_count = len(grp.members)
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
                from tumblepipe.config import scene as scn_mod
                scn = scn_mod.get_scene_by_uri(uris.scene(path))
                if scn is not None:
                    member_count = len(scn.assets)
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
            kind=kind,
            drill_tag=asset_id,
            member_count=member_count,
            context=ctx,
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
            proj = self._catalog._registry.get(proj_name)
            if proj is None or not self._catalog._clients.is_ready(proj_name):
                return result
            self._catalog._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.config import scene as scn_mod

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
                    shot_uri = uris.entity_from_suffix(entity_suffix)
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
                if (
                    not matched
                    and assigned_scene_uri is not None
                    and str(s.uri) == str(assigned_scene_uri)
                ):
                    matched = True
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
        proj = self._catalog._registry.get(proj_name)
        if proj is None or not self._catalog._clients.is_ready(proj_name):
            cache[proj_name] = coverage
            return coverage

        try:
            self._catalog._activate_project(proj)
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
        proj = self._catalog._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import groups as grp_mod
        except Exception:
            log.exception("toggle dept coverage: import failed")
            return
        group_uri = uris.group(path)
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
        self._catalog._invalidate_membership_cache()
        try:
            self._catalog._request_card_refresh_for_id(group_id)
        except Exception:
            log.exception("card refresh after dept toggle failed")
        try:
            self._catalog._request_global_detail_refresh()
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
            removed, skipped, msg = self._catalog.remove_assets_from_collection(
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
            self._catalog._invalidate_membership_cache()
            self._catalog._request_global_detail_refresh()
            self._catalog._request_global_grid_refresh()

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
            removed, skipped, msg = self._catalog.remove_assets_from_collection(
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
            self._catalog._invalidate_membership_cache()
            self._catalog._request_global_detail_refresh()
            # Re-query the grid so the removed asset disappears from
            # the current view immediately (otherwise the user sees a
            # phantom card until the next refresh button click).
            self._catalog._request_global_grid_refresh()

    def _add_assets_to_group(
        self, ref: GroupContainer, asset_ids: list[str],
    ) -> tuple[int, int, set[str]]:
        try:
            from tumblepipe.config import groups as grp_mod
        except Exception:
            return (0, 0, set())
        added = 0
        skipped = 0
        skip_reasons: set[str] = set()
        for aid in asset_ids:
            uri = self._catalog._resolver.uri_for(aid)
            if uri is None:
                skipped += 1
                skip_reasons.add("unknown asset")
                continue
            uri_str = str(uri)
            is_shot = uri_str.startswith("entity:/shots/")
            is_asset = uri_str.startswith("entity:/assets/")
            if ref.context == "shots" and not is_shot:
                skipped += 1
                skip_reasons.add("group accepts shots only")
                continue
            if ref.context == "assets" and not is_asset:
                skipped += 1
                skip_reasons.add("group accepts assets only")
                continue
            try:
                grp_mod.add_member(ref.uri, uri)
                added += 1
            except Exception:
                log.exception("add_member failed")
                skipped += 1
                skip_reasons.add("add_member failed")
        return (added, skipped, skip_reasons)

    def _add_assets_to_scene(
        self, ref: SceneContainer, asset_ids: list[str],
    ) -> tuple[int, int, set[str]]:
        try:
            from tumblepipe.config import scene as scn_mod
            from tumblepipe.config import scene as scene_mod
        except Exception:
            return (0, 0, set())
        try:
            scene = scn_mod.get_scene_by_uri(ref.uri)
        except Exception:
            log.exception("get_scene failed")
            return (0, 0, set())
        if scene is None:
            return (0, 0, set())
        added = 0
        skipped = 0
        skip_reasons: set[str] = set()
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
            uri = self._catalog._resolver.uri_for(aid)
            if uri is None:
                skipped += 1
                skip_reasons.add("unknown asset")
                continue
            uri_str = str(uri)
            # Shots dropped on a Root set the shot's scene_ref (the
            # shot "uses" this Root as its root-layer source).
            if uri_str.startswith("entity:/shots/"):
                try:
                    scene_mod.set_scene_ref(uri, ref.uri)
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
            # Shot detail panels show the assigned Root — refresh so the
            # user sees the change without re-selecting.
            self._catalog._request_global_detail_refresh()
        if new_entries != list(existing):
            try:
                scn_mod.set_scene_assets(ref.uri, new_entries)
            except Exception:
                log.exception("set_scene_assets failed")
                return (0, len(asset_ids), {"set_scene_assets failed"})
        # Staging the Root (export_scene_version, generate_root_version,
        # build) used to happen here automatically, but heavy USD
        # operations on every drag-drop turned out to be too punishing.
        # They're now explicit context-menu actions on the Root card
        # (see ``export_root_usd``). The membership write above still
        # happens unconditionally so data is never lost.
        return (added, skipped, skip_reasons)

    def _remove_assets_from_group(
        self, ref: GroupContainer, asset_ids: list[str],
    ) -> tuple[int, int]:
        try:
            from tumblepipe.config import groups as grp_mod
        except Exception:
            return (0, 0)
        removed = 0
        skipped = 0
        for aid in asset_ids:
            uri = self._catalog._resolver.uri_for(aid)
            if uri is None:
                skipped += 1
                continue
            try:
                grp_mod.remove_member(ref.uri, uri)
                removed += 1
            except Exception:
                log.exception("remove_member failed")
                skipped += 1
        return (removed, skipped)

    def _remove_assets_from_scene(
        self, ref: SceneContainer, asset_ids: list[str],
    ) -> tuple[int, int] | None:
        """Returns ``None`` to signal a fatal set_scene_assets failure;
        the caller surfaces a distinct error in that case."""
        try:
            from tumblepipe.config import scene as scn_mod
            from tumblepipe.config import scene as scene_mod
        except Exception:
            return (0, 0)
        try:
            scene = scn_mod.get_scene_by_uri(ref.uri)
        except Exception:
            log.exception("get_scene failed")
            return (0, 0)
        if scene is None:
            return (0, 0)
        removed = 0
        skipped = 0
        drop_set: set[str] = set()
        shot_uris_to_clear: list = []
        for aid in asset_ids:
            uri = self._catalog._resolver.uri_for(aid)
            if uri is None:
                continue
            uri_str = str(uri)
            # Shots: membership is via ``scene_ref`` on the shot, not
            # via the Root's ``assets`` list. Clear the ref (if it
            # points at this Root) to "remove" the shot.
            if uri_str.startswith("entity:/shots/"):
                shot_uris_to_clear.append(uri)
            else:
                drop_set.add(uri_str)
        for shot_uri in shot_uris_to_clear:
            try:
                current_ref = scene_mod.get_scene_ref(shot_uri)
                if (
                    current_ref is not None
                    and str(current_ref) == str(ref.uri)
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
        if drop_set and len(kept) != len(list(getattr(scene, "assets", ()))):
            try:
                scn_mod.set_scene_assets(ref.uri, kept)
            except Exception:
                log.exception("set_scene_assets failed")
                return None
            # Staging (export_scene_version) is a manual context-menu
            # action on the Root card; see ``export_root_usd``. We
            # keep the membership write strictly automatic so users
            # never lose data.
        if shot_uris_to_clear:
            # Shot detail panels show the assigned Root — refresh so
            # the user sees the change without re-selecting.
            self._catalog._request_global_detail_refresh()
        return (removed, skipped)

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
            print(
                f"[asset_browser] open_container_location failed for "
                f"{collection_id}: {msg}",
                file=sys.stderr,
            )
            return False

        ref = parse(collection_id)
        if ref is None:
            return _bail("could not parse collection id")
        proj = self._catalog._registry.get(ref.project_name)
        if proj is None:
            return _bail(f"unknown project {ref.project_name!r}")
        try:
            self._catalog._activate_project(proj)
            from tumblepipe import api as tp_api
        except Exception:
            log.exception("open_container_location: imports failed")
            return _bail("tumblepipe imports failed")

        candidate_uris: list = []
        try:
            if isinstance(ref, GroupContainer):
                # Multis live at two possible roots — the config side
                # (``groups:/``) holds the JSON definition, and the
                # workfile side mirrors the entity layout. Try both;
                # the first one that resolves to an existing folder
                # wins. Walk up either if needed.
                candidate_uris.append(
                    uris.groups_root() / ref.uri.segments
                )
                candidate_uris.append(
                    uris.project_root() / ref.uri.segments
                )
            else:
                # Roots export to ``export:/scenes/<path>/_staged`` —
                # that's where the .usda layer versions land. Plain
                # ``export:/scenes/<path>`` is the parent.
                candidate_uris.append(
                    uris.export_scenes_root()
                    / ref.uri.segments / "_staged"
                )
                candidate_uris.append(
                    uris.export_scenes_root() / ref.uri.segments
                )
                # Fall back to the scene's config folder if no export
                # has happened yet so the user lands somewhere
                # meaningful.
                candidate_uris.append(
                    uris.scenes_root() / ref.uri.segments
                )
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
            if p.exists():
                target = p
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
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return []
        proj = self._catalog._registry.get(ref.project_name)
        if proj is None:
            return []
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.config import scene as scn_mod
        except Exception:
            log.exception("list_root_assigned_shots: imports failed")
            return []
        try:
            shots = list(scn_mod.find_shots_with_scene_ref(ref.uri))
        except Exception:
            log.exception(
                "find_shots_with_scene_ref failed for %s", ref.uri,
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
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return (0, [])
        proj = self._catalog._registry.get(ref.project_name)
        if proj is None:
            return (0, [])
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.pipe.scene_build import generate_root_version
        except Exception:
            log.exception("rebuild_root_assigned_shots: imports failed")
            return (0, list(shots))
        ok = 0
        failed: list = []
        for shot_uri in shots:
            try:
                generate_root_version(shot_uri)
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
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return False
        proj = self._catalog._registry.get(ref.project_name)
        if proj is None:
            return False
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.pipe.scene_build import export_scene_version
        except Exception:
            log.exception("export_root_usd: imports failed")
            return False
        try:
            export_scene_version(ref.uri)
        except Exception:
            log.exception(
                "export_scene_version failed for %s", ref.uri,
            )
            return False
        # Re-fetch the Root card so the dirty indicator clears now
        # that scene JSON matches the freshly-written context.json.
        try:
            self._catalog._request_card_refresh_for_id(collection_id)
        except Exception:
            log.exception(
                "card refresh after export failed for %s", collection_id,
            )
        return True
