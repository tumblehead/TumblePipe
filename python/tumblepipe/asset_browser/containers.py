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
collection discovery, container card/detail synthesis, membership and
per-context department coverage, the collection lifecycle the host
routes through the catalog (delete, edit, add / remove members), and
the Root context-menu actions (open location, rebuild assigned shots,
export USD). The catalog keeps the host-facing names as one-line
delegations.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from tumbletrove.asset_browser.api.errors import ConfigError, TagQueryError
from tumbletrove.asset_browser.api.types import (
    Asset, AssetDetail, AssetPage, Collection, CreationField,
)

from . import uris as uris
from tumblepipe.naming import get_version_code

from .types import latest_by_dept, workfile_versions

if TYPE_CHECKING:
    from .projects import PipelineProjectRegistry
    from tumblepipe.util.uri import Uri
    from .clients import ClientPool
    from .houdini import ProjectActivator
    from .refresh import BrowserRefresh
    from .resolver import AssetResolver

log = logging.getLogger(__name__)

#: The ``type:`` tag value of the cards a Multi of each context takes.
_CONTEXT_TYPE = {"assets": "asset", "shots": "shot"}

#: A shots Multi's Timeline in the Edit dialog, and the (shared timeline,
#: bookmarks) settings each choice stands for (``config.multi_timeline``).
#: Bookmarks come only with a shared timeline: on separate timelines every
#: shot's bookmark would cover the same frames.
_TIMELINE_SETTINGS = {
    "Per shot": (False, False),
    "Shared": (True, False),
    "Shared, with bookmarks": (True, True),
}
TIMELINE_CHOICES = tuple(_TIMELINE_SETTINGS)


def timeline_choice(shared: bool, bookmarks: bool) -> str:
    """The Timeline choice for a Multi's settings."""
    if not shared:
        return "Per shot"
    return "Shared, with bookmarks" if bookmarks else "Shared"


def timeline_settings(choice: str) -> tuple[bool, bool]:
    """``(shared timeline, bookmarks)`` for a Timeline choice."""
    return _TIMELINE_SETTINGS[choice]


def _hip_context_user_note(hip_path) -> tuple[str, str]:
    """``(user, note)`` for a workfile, from the ``_context/{version}.json``
    sidecar next to it (the same file the entity save flow writes and
    ``WorkfileHelper.get_dept_row_meta`` reads for assets/shots — here the
    dept dir comes straight from the hip path, so it also works for group
    workfiles the entity resolver can't split). Both come out of one read
    because they live in one file. ``("", "")`` for a version saved before
    sidecars existed; the note is also blank for versions saved before
    notes existed. A sidecar that will not parse raises, as does a hip
    whose name is not a version (the workfile lookups only return those)."""
    from tumblepipe.pipe.paths.naming import version_of
    ctx_file = hip_path.parent / "_context" / f"{version_of(hip_path)}.json"
    if not ctx_file.exists():
        return "", ""
    import json
    data = json.loads(ctx_file.read_text(encoding="utf-8"))
    return str(data.get("user") or ""), str(data.get("note") or "")


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

    Its dependencies are handed in: the project registry, client pool,
    resolver and activator, the browser refresh, and the catalog's whole
    cache invalidation (a deleted container changes the sidebar).
    """

    def __init__(
        self,
        catalog_id: str,
        *,
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        resolver: "AssetResolver",
        activator: "ProjectActivator",
        refresh: "BrowserRefresh",
        invalidate_cache: Callable[[], None],
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._clients = clients
        self._resolver = resolver
        self._activator = activator
        self._refresh = refresh
        self._invalidate_cache = invalidate_cache
        # Member-coverage cache: maps project name -> the parsed
        # ``groups.json`` coverage used by ``get_asset_membership`` and
        # the dept-pill builders. Lazily populated by
        # ``_get_member_groups_for_project``; dropped by
        # ``invalidate_membership_cache``. Membership changes don't alter
        # which entities *exist*, so this is busted independently of the
        # discovery cache (``EntityIndex``).
        self._member_groups_cache = None

    def invalidate_membership_cache(self) -> None:
        """Drop only the member-coverage cache.

        Membership changes don't alter the discovered cards —
        those enumerate which entities *exist*, not
        who they belong to. Skipping their bust spares a costly
        filesystem rediscovery pass on the next sidebar / grid query
        (which dominates the perceived latency of add/remove ops).
        """
        self._member_groups_cache = None

    # ── Filtering ─────────────────────────────────────────

    def filter_by_group(self, items: list, tag: str) -> list:
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
        if proj is None or not self._clients.is_ready(proj_name):
            return items
        self._activator.activate(proj)
        try:
            from tumblepipe.config import groups as grp_mod
            group_uri = uris.group(group_path)
            group = grp_mod.get_group(group_uri)
        except Exception as exc:
            raise TagQueryError(
                self._catalog_id,
                f"failed to resolve group {group_path!r} in {proj_name}: {exc}",
                cause=exc,
            ) from exc
        if group is None:
            return []
        member_ids = {
            uris.asset_id_for(proj_name, member) for member in group.members
        }
        return [a for a in items if a.id in member_ids]

    def filter_by_scene(self, items: list, tag: str) -> list:
        """Filter assets to those in a pipeline scene.

        Tag format: ``scene:PROJECT:PATH``. See :meth:`filter_by_group`
        for the contract — raises :class:`TagQueryError` on lookup
        failure.
        """
        _, rest = tag.split(":", 1)
        proj_name, scene_path = rest.split(":", 1)
        proj = self._registry.get(proj_name)
        if proj is None or not self._clients.is_ready(proj_name):
            return items
        self._activator.activate(proj)
        try:
            from tumblepipe.config import scene as scn_mod
            scene_uri = uris.scene(scene_path)
            scene = scn_mod.get_scene_by_uri(scene_uri)
        except Exception as exc:
            raise TagQueryError(
                self._catalog_id,
                f"failed to resolve scene {scene_path!r} in {proj_name}: {exc}",
                cause=exc,
            ) from exc
        if scene is None:
            return []
        asset_ids = {
            uris.asset_id_for(proj_name, uris.parse(entry.asset))
            for entry in scene.assets
        }
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
        if not self._clients.is_ready(proj.name):
            return result
        self._activator.activate(proj)
        from tumblepipe.config import groups as grp_mod
        for ctx in ("shots", "assets"):
            for g in grp_mod.list_groups(ctx):
                name = g.uri.segments[-1] if g.uri.segments else str(g.uri)
                group_id = f"group:{proj.name}:{ctx}/{name}"
                # The leaf lands on the Multi ITSELF — its card, with
                # a department row per covered department, in list
                # view like a shot — not on its members. A leaf whose
                # tag was the bare member filter made a Multi read
                # as a category of shots. Members are one
                # double-click away (the card's drill tag).
                result[ctx].append(Collection(
                    id=group_id,
                    label=name,
                    count=len(g.members),
                    tag=(
                        f"project:{proj.name}+type:group"
                        f"+multi_context:{ctx}+{group_id}"
                    ),
                    kind="group",
                    default_view="list",
                    # A Multi takes members of its own context only;
                    # the sidebar dims it while other cards are dragged.
                    accepts_types=(_CONTEXT_TYPE[ctx],),
                ))
        return result

    def multis_sections(self, proj):
        """Return ``(asset_multis_subheader, shot_multis_subheader)``
        for one project.

        Each subheader is a single ``Multis`` Collection whose
        children are the actual Multi leaves; the subheader's tag
        filters the grid to that context's Multi cards via
        ``type:group+multi_context:<assets|shots>``. Both headers are
        always returned (never ``None``), even for an empty context;
        the caller leaves a childless one out of the sidebar.

        Callers inject these into the Assets / Shots sections rather
        than rendering a separate top-level Multis section, so the
        sidebar mirrors the inherent context split without nesting
        twice.
        """
        project_tag = f"project:{proj.name}"
        leaves = self._list_group_leaves_by_context(proj)
        # default_view="list": a Multi is an entity with department rows,
        # and those browse as list rows for the same reason shots do.
        asset_sub = Collection(
            id=f"{proj.name}:multis_section:assets",
            label="Multis",
            icon="group",
            tag=f"{project_tag}+type:group+multi_context:assets",
            count=len(leaves["assets"]),
            children=tuple(leaves["assets"]),
            default_view="list",
            section=True,
        )
        shot_sub = Collection(
            id=f"{proj.name}:multis_section:shots",
            label="Multis",
            icon="group",
            tag=f"{project_tag}+type:group+multi_context:shots",
            count=len(leaves["shots"]),
            children=tuple(leaves["shots"]),
            default_view="list",
            section=True,
        )
        return asset_sub, shot_sub

    def root_collections(self, proj) -> list[Collection]:
        """Build Scene collections for a single project."""
        children: list[Collection] = []
        if not self._clients.is_ready(proj.name):
            return children
        self._activator.activate(proj)
        from tumblepipe.config import scene as scn_mod
        for s in scn_mod.list_scenes():
            path = "/".join(s.uri.segments) if s.uri.segments else str(s.uri)
            name = s.uri.segments[-1] if s.uri.segments else path
            children.append(Collection(
                id=f"scene:{proj.name}:{path}",
                label=name,
                count=len(s.assets),
                tag=f"scene:{proj.name}:{path}",
                kind="scene",
            ))
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
            for scn in self.root_collections(proj):
                yield scn, proj.name

    def collection_card(
        self, collection: "Collection", project_name: str, kind: str,
    ) -> Asset:
        """Wrap a Group/Scene Collection in an Asset so the grid can
        render it as a card. The ``deck_drill_tag`` field tells the
        browser the card represents a container: a **double-click**
        drills into its members (a single click selects it like any
        other card, and the deck expands its department rows).

        For groups, the asset also advertises ``has_deck_items=True``
        and a ``departments`` map (``{dept: latest_version}``, the same
        card shape shots/assets use) so the deck popup can render one
        deck item per covered dept (mirroring the shot/asset
        open-workfile UX). A Multi is an entity with department rows
        first and a container of members second.
        """
        metadata: dict = {}
        dirty = False
        ctx = ""
        has_deck_items = False
        # The container's identity is the collection ID (``group:P:ctx/name``
        # / ``scene:P:name``). Its sidebar ``tag`` is what clicking the leaf
        # filters by, and for a Multi that is now a compound tag that lands
        # on the card rather than the bare id — so nothing here may parse
        # the tag as an id.
        container_id = collection.id
        if kind == "scene":
            # Surface a dirty flag so the renderer can paint an
            # "unsaved" indicator on Roots whose JSON has drifted
            # from the latest exported USD.
            dirty = self._root_is_dirty(container_id)
        if kind == "group":
            # Stash the Multi's context (``assets`` / ``shots``) so
            # ``page`` can filter by it when the
            # sidebar's nested **Multis** subheader is active. The
            # context comes from the second URI segment, i.e. the
            # ``ctx`` in ``group:PROJECT:ctx/name``.
            _, _, group_path = container_id.split(":", 2)
            ctx = group_path.split("/", 1)[0] if "/" in group_path else ""
            covered = self._group_departments_from_tag(
                container_id, project_name,
            )
            if covered:
                from tumblepipe.pipe.paths.naming import version_of
                hips = self._group_dept_latest_hips(container_id)
                # Same shape as shot/asset metadata: {dept: latest_version
                # or ""}. Dept-name iteration order (for deck item render)
                # comes from get_deck_items, which sorts by the project's
                # canonical dept order.
                depts_dict = {}
                latest_update = 0.0
                latest_hip = None
                for dept in covered:
                    hip = hips.get(dept)
                    depts_dict[dept] = version_of(hip) if hip else ""
                    if hip is None:
                        continue
                    try:
                        mtime = hip.stat().st_mtime
                    except FileNotFoundError:
                        # Deleted since the lookup that found it.
                        continue
                    if mtime > latest_update:
                        latest_update = mtime
                        latest_hip = hip
                metadata["departments"] = depts_dict
                has_deck_items = True
                # User/Edited/Note list cells, mirroring what
                # ``_latest_update_user_note`` does for asset/shot
                # cards: newest dept's mtime, plus ONE _context
                # sidecar read for that dept only.
                if latest_hip is not None:
                    metadata["latest_update"] = latest_update
                    user, note = _hip_context_user_note(latest_hip)
                    metadata["last_user"] = user
                    metadata["last_note"] = note
        return Asset(
            id=container_id,
            name=collection.label,
            thumbnail_url="",
            tags=frozenset({
                f"type:{kind}",
                f"project:{project_name}",
                "source:pipeline",
                container_id,
            }),
            kind=kind,
            # Double-click drills into the members: the bare id is the
            # member filter tag.
            deck_drill_tag=container_id,
            member_count=collection.count,
            dirty=dirty,
            context=ctx,
            has_deck_items=has_deck_items,
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
        from tumblepipe.pipe.staged_build import build_staged

        output_path = next_staged_file_path(shot_uri)
        frame_range = get_frame_range(shot_uri)
        render_range = (
            frame_range.full_range() if frame_range else None
        )
        if render_range is None:
            raise RuntimeError(
                f"No frame range found for shot: {shot_uri}"
            )
        build_staged(shot_uri, output_path, render_range)

    def _root_is_dirty(self, scene_id: str) -> bool:
        """Return ``True`` when a Root has unsaved changes vs the last
        exported USD.

        Compares the Root's current ``assets`` list against the asset
        list recorded in the latest exported version's ``context.json``.
        If they differ — or no export exists yet and the Root has any
        members — the Root is dirty. Root edits stage themselves, so a
        dot that stays on the card means staging failed (the Python Shell
        says why) or the Root was edited outside the browser; Export Root
        USD clears it by hand.
        """
        ref = parse(scene_id)
        if not isinstance(ref, SceneContainer):
            return False
        proj = self._registry.get(ref.project_name)
        if proj is None or not self._clients.is_ready(ref.project_name):
            return False
        self._activator.activate(proj)
        from tumblepipe.pipe.root_staging import root_export_is_stale
        return root_export_is_stale(ref.uri)

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
        ``group:PROJECT:ctx/name``. ``[]`` while the project's Client is
        not READY, or for a Multi that no longer exists.
        """
        _, _, path = group_tag.split(":", 2)
        proj = self._registry.get(project_name)
        if proj is None or not self._clients.is_ready(project_name):
            return []
        self._activator.activate(proj)
        from tumblepipe.config import groups as grp_mod
        grp = grp_mod.get_group(uris.group(path))
        if grp is None:
            return []
        return list(grp.departments)

    def _group_dept_latest_hips(self, group_tag: str) -> dict:
        """Return ``{dept: latest_hip_Path}`` for a group's workfile
        tree.

        Resolution goes through ``latest_hip_file_path_with_context``
        (the same helper :meth:`_open_group_workfile` and
        :meth:`_new_group_from_template` use), so paths line up with
        the open/create flows even if the on-disk layout doesn't match
        a naive ``<root>/groups/<ctx>/<name>/<dept>`` walk. ``{}`` while
        the project's Client is not READY, or for a Multi that no longer
        exists.
        """
        _, proj_name, path = group_tag.split(":", 2)
        proj = self._registry.get(proj_name)
        if proj is None or not self._clients.is_ready(proj_name):
            return {}
        self._activator.activate(proj)
        from tumblepipe.config import groups as grp_mod
        from tumblepipe.pipe import paths as paths_mod
        group_uri = uris.group(path)
        grp = grp_mod.get_group(group_uri)
        if grp is None:
            return {}
        result: dict = {}
        for dept_name in grp.departments:
            hip_path = paths_mod.latest_hip_file_path_with_context(
                group_uri, dept_name,
            )
            if hip_path is None or not hip_path.exists():
                continue
            result[dept_name] = hip_path
        return result

    def _get_group_department_workfile_info(
        self, group_tag: str,
    ) -> dict[str, list[str]]:
        """Return ``{dept: [versions]}`` (ascending) for a group's
        workfile tree — every version in the dept folder, so the detail
        panel's dropdown can pick an older one. The folder is the latest
        hip's parent; the latest itself is always included, since the
        context.json pointer can see a save a stale SMB listing misses."""
        from tumblepipe.pipe.paths.naming import version_of
        result: dict[str, list[str]] = {}
        for dept_name, hip in self._group_dept_latest_hips(
                group_tag).items():
            versions = set(workfile_versions(hip.parent))
            versions.add(version_of(hip))
            result[dept_name] = sorted(versions, key=get_version_code)
        return result

    def group_attribution(self, group_tag: str) -> dict:
        """Return ``{dept: (user, mtime_epoch, note)}`` for a group's latest
        workfiles.

        The mtime is the hip file's stat; the user and note come from the
        ``_context`` sidecar (see :func:`_hip_context_user_note`). These
        feed the list view's User/Edited/Note columns for a group's deck
        rows (the entity-side equivalent is
        ``WorkfileManager.get_dept_row_meta``, which returns the same
        triple). One sidecar read per covered dept — callers hit this on
        deck expand, never during grid rebuilds.
        """
        result: dict = {}
        for dept_name, hip in self._group_dept_latest_hips(
                group_tag).items():
            try:
                mtime = hip.stat().st_mtime
            except FileNotFoundError:
                # Deleted since the lookup that found it.
                continue
            user, note = _hip_context_user_note(hip)
            result[dept_name] = (user, mtime, note)
        return result

    def member_coverage(
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

    def page(
        self, kind: str, query: str, tags: frozenset[str],
        cursor: str | None, page_size: int,
    ) -> AssetPage:
        """Return synthetic Group/Scene cards for the grid.

        Called from :meth:`get_assets` when the active filter is
        ``type:group`` / ``type:scene``. Respects the ``project:``
        filter tag, the search box, and standard pagination.
        """
        errors = list(self._clients.ensure_all())

        if kind == "group":
            items = [
                self.collection_card(g, p, "group")
                for g, p in self._iter_group_collections()
            ]
        else:
            items = [
                self.collection_card(s, p, "scene")
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
            # A sidebar Multi leaf names its own Multi (``group:...``
            # alongside ``type:group``): show that one card, so the leaf
            # lands on the Multi rather than the whole Multis grid. The
            # bare ``group:`` tag without ``type:group`` is the member
            # filter and never reaches this branch.
            wanted = {t for t in tags if t.startswith("group:")}
            if wanted:
                items = [a for a in items if a.id in wanted]

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
            errors=tuple(errors),
        )

    def detail(self, asset_id: str) -> AssetDetail:
        """Build an AssetDetail for a Group/Scene id.

        ``asset_id`` is the container's tag — same format the sidebar
        uses (``group:PROJECT:ctx/name`` / ``scene:PROJECT:path``). The
        detail carries enough metadata for the panel to render the
        Info tab + departments toggle (groups only).
        """
        # Container ids are minted by the catalog; one that does not
        # split is a bug and raises.
        kind, proj_name, path = asset_id.split(":", 2)

        proj = self._registry.get(proj_name)
        if proj is not None:
            self._activator.activate(proj)

        metadata: dict = {
            "project": proj_name,
            "path": path,
        }
        member_count = 0
        ctx = ""

        if kind == "group":
            from tumblepipe.config import groups as grp_mod
            from tumblepipe.config import department as dept_mod
            grp = grp_mod.get_group(uris.group(path))
            covered: list[str] = []
            if grp is not None:
                member_count = len(grp.members)
                covered = list(grp.departments)
            # Shape both dept keys like shots/assets so the rich
            # dept section renders version dropdowns + active
            # highlights uniformly: ``department_versions`` carries
            # the full {dept: [versions]} the dropdowns need, while
            # ``departments`` stays card-shaped ({dept: latest}) —
            # a detail is turned into a grid card verbatim for
            # favourite / shelf refs. The covered/uncovered toggle
            # state lives separately in ``covered_departments``.
            group_dept_versions = (
                self._get_group_department_workfile_info(asset_id)
            )
            metadata["departments"] = latest_by_dept(group_dept_versions)
            metadata["department_versions"] = group_dept_versions
            metadata["covered_departments"] = covered
            ctx = GroupContainer(proj_name, path).context
            metadata["known_departments"] = [
                d.name for d in
                dept_mod.list_departments(ctx, include_generated=False)
            ]
        elif kind == "scene":
            from tumblepipe.config import scene as scn_mod
            scn = scn_mod.get_scene_by_uri(uris.scene(path))
            if scn is not None:
                member_count = len(scn.assets)

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
            deck_drill_tag=asset_id,
            member_count=member_count,
            context=ctx,
            metadata=metadata,
        )

    def get_asset_membership(
        self, asset_id: str,
    ) -> list[tuple[str, str, str]]:
        """Return ``(collection_id, label, kind)`` for every group and
        scene that contains *asset_id*.

        Used by the detail panel's "belongs to" pills and the card menu.
        ``[]`` for an id that names no project, or while the project's
        Client is not READY (the id cannot be told asset from shot yet).
        """
        proj = self._resolver.project_for(asset_id)
        if proj is None or not self._clients.is_ready(proj.name):
            return []
        self._activator.activate(proj)
        entity_uri = self._resolver.uri_for(asset_id)
        from tumblepipe.config import groups as grp_mod
        from tumblepipe.config import scene as scn_mod

        result: list[tuple[str, str, str]] = []
        context = entity_uri.segments[0]
        for g in grp_mod.list_groups(context):
            if entity_uri in g.members:
                path = "/".join(g.uri.segments)
                result.append((
                    GroupContainer(proj.name, path).collection_id,
                    g.name, "group",
                ))

        # A Root holds an asset two ways: listed in the Root's ``assets``
        # (an asset), or named by the shot's own or inherited
        # ``scene_ref`` (a shot).
        assigned = None
        if context == "shots":
            assigned = scn_mod.get_scene_ref(entity_uri)
            if assigned is None:
                assigned, _source = scn_mod.get_inherited_scene_ref(entity_uri)
        for s in scn_mod.list_scenes():
            held = any(uris.parse(e.asset) == entity_uri for e in s.assets)
            if held or (assigned is not None and s.uri == assigned):
                path = "/".join(s.uri.segments)
                result.append((
                    SceneContainer(proj.name, path).collection_id,
                    s.name, "scene",
                ))
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
        cache = self._member_groups_cache
        if cache is None:
            cache = {}
            self._member_groups_cache = cache
        if proj_name in cache:
            return cache[proj_name]

        coverage: dict[str, dict[str, tuple[str, str]]] = {}
        proj = self._registry.get(proj_name)
        if proj is None or not self._clients.is_ready(proj_name):
            cache[proj_name] = coverage
            return coverage

        self._activator.activate(proj)
        from tumblepipe.config import groups as grp_mod
        for ctx in ("shots", "assets"):
            for g in grp_mod.list_groups(ctx):
                # The collection id, so the remove action plugs straight
                # into ``remove_assets_from_collection``.
                group_id = GroupContainer(
                    proj_name, "/".join(g.uri.segments),
                ).collection_id
                for member_uri in g.members:
                    bucket = coverage.setdefault(
                        uris.asset_id_for(proj_name, member_uri), {},
                    )
                    for dept in g.departments:
                        bucket.setdefault(dept, (group_id, g.name))

        cache[proj_name] = coverage
        return coverage

    def set_coverage(
        self, group_id: str, dept: str, enabled: bool,
    ) -> None:
        """Add or remove ``dept`` from the Multi's covered list.

        Wraps ``add_department`` / ``remove_department`` with cache
        invalidation + card and detail-panel refresh so the user sees
        the deck and the Departments tab catch up on the next paint.
        """
        if not group_id.startswith("group:"):
            return
        _, proj_name, path = group_id.split(":", 2)
        proj = self._registry.get(proj_name)
        if proj is None:
            return
        self._activator.activate(proj)
        from tumblepipe.config import groups as grp_mod
        group_uri = uris.group(path)
        grp = grp_mod.get_group(group_uri)
        if grp is None:
            raise ValueError(f"Multi {group_id} no longer exists")
        current = set(grp.departments)
        if enabled and dept not in current:
            grp_mod.add_department(group_uri, dept)
        elif (not enabled) and dept in current:
            grp_mod.remove_department(group_uri, dept)
        else:
            return  # already in target state, no-op
        self.invalidate_membership_cache()
        self._refresh.card(group_id)
        self._refresh.detail()

    def remove_member(
        self, asset_id: str, collection_id: str,
    ) -> None:
        """Remove one asset or shot from a Multi or a Root, from a menu.

        :meth:`remove_assets` with a status line, and the detail panels
        and grid re-queried so the removed card leaves the container's
        view at once. Raises; the menu entry is the boundary.
        """
        if not asset_id or not collection_id:
            return
        removed, _skipped, msg = self.remove_assets(collection_id, [asset_id])
        if msg:
            import hou
            hou.ui.setStatusMessage(msg, severity=hou.severityType.Message)
        if removed:
            self._refresh.detail()
            self._refresh.grid()

    def _add_assets_to_group(
        self, ref: GroupContainer, asset_ids: list[str],
    ) -> tuple[int, int, set[str]]:
        from tumblepipe.config import groups as grp_mod
        added = 0
        skipped = 0
        skip_reasons: set[str] = set()
        for aid in asset_ids:
            uri = self._resolver.uri_for(aid)
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
            grp_mod.add_member(ref.uri, uri)
            added += 1
        return (added, skipped, skip_reasons)

    def _add_assets_to_scene(
        self, ref: SceneContainer, asset_ids: list[str],
    ) -> tuple[int, int, set[str]]:
        from tumblepipe.config import scene as scn_mod
        scene = scn_mod.get_scene_by_uri(ref.uri)
        if scene is None:
            raise ValueError(f"Root {ref.path!r} does not exist")
        added = 0
        skipped = 0
        skip_reasons: set[str] = set()
        shot_ref_changed_uris: list = []
        existing = list(scene.assets)
        # ``AssetEntry.asset`` is typed as ``str`` (URI string); the
        # tumblepipe JSON layer doesn't serialise raw ``Uri`` objects,
        # so passing one in silently produced unreadable entries
        # that vanished on the next ``get_scene`` (bug observed:
        # "Root members disappear after restart"). Force ``str(uri)``
        # for every comparison and AssetEntry write below.
        existing_uris = {e.asset for e in existing}
        new_entries = list(existing)
        for aid in asset_ids:
            uri = self._resolver.uri_for(aid)
            if uri is None:
                skipped += 1
                skip_reasons.add("unknown asset")
                continue
            uri_str = str(uri)
            # Shots dropped on a Root set the shot's scene_ref (the
            # shot "uses" this Root as its root-layer source).
            if uri.segments[0] == "shots":
                scn_mod.set_scene_ref(uri, ref.uri)
                added += 1
                shot_ref_changed_uris.append(uri)
                continue
            if uri_str in existing_uris:
                skipped += 1
                skip_reasons.add("already in Root")
                continue
            existing_uris.add(uri_str)
            new_entries.append(scn_mod.AssetEntry(asset=uri_str, instances=1))
            added += 1
        if shot_ref_changed_uris:
            # Shot detail panels show the assigned Root — refresh so the
            # user sees the change without re-selecting.
            self._refresh.detail()
        if new_entries != list(existing):
            scn_mod.set_scene_assets(ref.uri, new_entries)
        return (added, skipped, skip_reasons)

    def _remove_assets_from_group(
        self, ref: GroupContainer, asset_ids: list[str],
    ) -> tuple[int, int]:
        from tumblepipe.config import groups as grp_mod
        removed = 0
        skipped = 0
        for aid in asset_ids:
            uri = self._resolver.uri_for(aid)
            if uri is None:
                skipped += 1
                continue
            grp_mod.remove_member(ref.uri, uri)
            removed += 1
        return (removed, skipped)

    # ── Root edit: the asset list ─────────────────────────

    @staticmethod
    def _scene_asset_label(uri_or_str) -> str:
        """``entity:/assets/CAT/NAME`` → ``CAT/NAME``, the label the edit
        dialog shows and hands back."""
        text = str(uri_or_str)
        return text.replace("entity:/assets/", "", 1)

    def scene_edit_fields(self, ref: SceneContainer) -> list:
        """The Root's edit dialog: one multi-select of the project's
        assets, ticked where the Root already holds them."""
        from tumbletrove.asset_browser.api.types import CreationField
        from tumblepipe.config import scene as scn_mod
        scene = scn_mod.get_scene_by_uri(ref.uri)
        if scene is None:
            return []
        choices = tuple(
            self._scene_asset_label(u) for u in scn_mod.list_available_assets()
        )
        current = tuple(
            dict.fromkeys(self._scene_asset_label(e.asset) for e in scene.assets)
        )
        return [
            CreationField(
                key="assets",
                label="Assets",
                field_type="multi_select",
                choices=choices,
                initial=current,
                required=False,
            ),
        ]

    def edit_scene_assets(self, ref: SceneContainer, labels) -> bool:
        """Apply the edit dialog's ticked asset labels to the Root.

        Entries the Root already holds keep their instance count and
        channel; a newly ticked asset joins with one instance on the
        default channel; an unticked one leaves. Returns True when the
        list changed.
        """
        from tumblepipe.config import scene as scn_mod
        scene = scn_mod.get_scene_by_uri(ref.uri)
        if scene is None:
            raise ValueError(f"Root {ref.path!r} does not exist")
        wanted = list(dict.fromkeys(str(x) for x in labels))
        kept = [
            e for e in scene.assets
            if self._scene_asset_label(e.asset) in wanted
        ]
        held = {self._scene_asset_label(e.asset) for e in scene.assets}
        added = [
            scn_mod.AssetEntry(asset=f"entity:/assets/{label}", instances=1)
            for label in wanted if label not in held
        ]
        new_assets = kept + added
        if [(e.asset, e.instances, e.channel) for e in new_assets] == [
            (e.asset, e.instances, e.channel) for e in scene.assets
        ]:
            return False
        scn_mod.set_scene_assets(ref.uri, new_assets)
        return True

    def _remove_assets_from_scene(
        self, ref: SceneContainer, asset_ids: list[str],
    ) -> tuple[int, int]:
        """``(removed, skipped)``."""
        from tumblepipe.config import scene as scn_mod
        scene = scn_mod.get_scene_by_uri(ref.uri)
        if scene is None:
            raise ValueError(f"Root {ref.path!r} does not exist")
        removed = 0
        skipped = 0
        drop_set: set[str] = set()
        shot_uris_to_clear: list = []
        for aid in asset_ids:
            uri = self._resolver.uri_for(aid)
            if uri is None:
                continue
            # Shots: membership is via ``scene_ref`` on the shot, not
            # via the Root's ``assets`` list. Clear the ref (if it
            # points at this Root) to "remove" the shot.
            if uri.segments[0] == "shots":
                shot_uris_to_clear.append(uri)
            else:
                drop_set.add(str(uri))
        for shot_uri in shot_uris_to_clear:
            if scn_mod.get_scene_ref(shot_uri) == ref.uri:
                scn_mod.set_scene_ref(shot_uri, None)
                removed += 1
            else:
                skipped += 1
        kept: list = []
        for entry in scene.assets:
            if entry.asset in drop_set:
                removed += 1
            else:
                kept.append(entry)
        if len(kept) != len(scene.assets):
            scn_mod.set_scene_assets(ref.uri, kept)
        if shot_uris_to_clear:
            # Shot detail panels show the assigned Root — refresh so
            # the user sees the change without re-selecting.
            self._refresh.detail()
        return (removed, skipped)

    def open_container_location(self, collection_id: str) -> bool:
        """Open the Multi or Root's on-disk storage folder in Explorer.

        Mirrors :meth:`_open_dept_work_dir` for shots/assets: resolves
        the container's URI to a filesystem path via tumblepipe's
        storage layer and shells out to ``explorer``. Walks up to the
        nearest existing ancestor if the leaf folder hasn't been
        created yet (e.g. a brand-new Multi with no workfiles). Raises
        when none of them exists; the host reports it.
        """

        ref = parse(collection_id)
        if ref is None:
            raise ValueError(f"{collection_id!r} is not a Multi or Root id")
        proj = self._registry.get(ref.project_name)
        if proj is None:
            raise ValueError(f"unknown project {ref.project_name!r}")
        self._activator.activate(proj)
        from tumblepipe.api import api as tp_api

        if isinstance(ref, GroupContainer):
            # Multis live at two possible roots — the config side
            # (``groups:/``) holds the JSON definition, and the
            # workfile side mirrors the entity layout. Try both;
            # the first one that resolves to an existing folder wins.
            candidate_uris = [
                uris.groups_root() / ref.uri.segments,
                uris.project_root() / ref.uri.segments,
            ]
        else:
            # Roots export to ``export:/scenes/<path>/_staged`` —
            # that's where the .usda layer versions land. Plain
            # ``export:/scenes/<path>`` is the parent. A never-exported
            # Root has no folder: its definition is a ``scenes:/`` entry
            # in the config database, which is not a storage purpose.
            candidate_uris = [
                uris.export_scenes_root() / ref.uri.segments / "_staged",
                uris.export_scenes_root() / ref.uri.segments,
            ]

        paths = [tp_api.storage.resolve(uri) for uri in candidate_uris]
        target = next((p for p in paths if p.exists()), None)
        if target is None:
            raise FileNotFoundError(
                f"none of {collection_id}'s folders exists yet: "
                + ", ".join(str(p) for p in paths)
            )
        from tumblepipe.util import desktop
        desktop.open_in_file_browser(target)
        return True

    def list_root_assigned_shots(self, collection_id: str) -> list:
        """Return a list of shot URIs whose ``scene_ref`` points at
        this Root.
        """
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return []
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return []
        self._activator.activate(proj)
        from tumblepipe.config import scene as scn_mod
        return list(scn_mod.find_shots_with_scene_ref(ref.uri))

    def rebuild_root_assigned_shots(
        self, collection_id: str,
    ) -> tuple[int, list]:
        """Regenerate root + build staged USD for every shot whose
        ``scene_ref`` points at this Root. Returns ``(ok_count,
        failed_uris)``: one shot that fails to build must not cost the
        rest, so each failure is logged and handed back for the host to
        report.
        """
        shots = self.list_root_assigned_shots(collection_id)
        if not shots:
            return (0, [])
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return (0, [])
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return (0, [])
        self._activator.activate(proj)
        from tumblepipe.pipe.scene_build import generate_root_version
        ok = 0
        failed: list = []
        for shot_uri in shots:
            try:
                generate_root_version(shot_uri)
                self._build_shot_staged(shot_uri)
            except Exception:
                # Per-shot boundary: reported through ``failed``.
                log.exception("Rebuilding %s failed", shot_uri)
                failed.append(shot_uri)
                continue
            ok += 1
        return (ok, failed)

    def export_root_usd(self, collection_id: str) -> bool:
        """Re-export a Root's scene USD via ``export_scene_version``.

        Root edits stage themselves (``pipe.root_staging.sync_roots``);
        this is the by-hand fallback. Returns ``True`` on success; a failed
        export raises, and the host reports it.
        """
        ref = parse(collection_id)
        if not isinstance(ref, SceneContainer):
            return False
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return False
        self._activator.activate(proj)
        from tumblepipe.pipe.scene_build import export_scene_version
        export_scene_version(ref.uri)
        # Re-fetch the Root card so the dirty indicator clears now
        # that scene JSON matches the freshly-written context.json.
        self._refresh.card(collection_id)
        return True


    # ── Collection lifecycle (the host routes these by the clicked row) ──

    def _activated(self, collection_id: str) -> ContainerRef | None:
        """The parsed id with its project active; None when it names no
        container of a registered project."""
        ref = parse(collection_id)
        if ref is None:
            return None
        proj = self._registry.get(ref.project_name)
        if proj is None:
            return None
        self._activator.activate(proj)
        return ref

    def delete(self, collection_id: str) -> bool:
        ref = self._activated(collection_id)
        if ref is None:
            return False
        try:
            if isinstance(ref, GroupContainer):
                from tumblepipe.config import groups as grp_mod
                grp_mod.remove_group(ref.uri)
            else:
                from tumblepipe.config import scene as scn_mod
                scn_mod.remove_scene(ref.uri)
        except Exception as exc:
            raise ConfigError(
                self._catalog_id,
                f"failed to delete {ref.kind} {collection_id}: {exc}",
                cause=exc,
            ) from exc
        self._invalidate_cache()
        return True

    def edit_fields(self, collection_id: str) -> list[CreationField]:
        """The Edit dialog: a Root's asset list, a Multi's departments."""
        ref = self._activated(collection_id)
        if ref is None:
            return []
        if isinstance(ref, SceneContainer):
            return self.scene_edit_fields(ref)
        from tumblepipe.config import groups as grp_mod
        from tumblepipe.config import department as dept_mod
        grp = grp_mod.get_group(ref.uri)
        if grp is None:
            return []
        known_depts = tuple(
            d.name for d in
            dept_mod.list_departments(ref.context, include_generated=False)
        )
        fields = [
            CreationField(
                key="departments",
                label="Departments",
                field_type="multi_select",
                choices=known_depts,
                initial=tuple(grp.departments),
                required=False,
            ),
        ]
        # Only shots have frames to lay end to end.
        if ref.context == "shots":
            fields.append(CreationField(
                key="timeline",
                label="Timeline",
                field_type="dropdown",
                choices=TIMELINE_CHOICES,
                initial=timeline_choice(
                    grp.shared_timeline, grp.timeline_bookmarks,
                ),
                required=False,
            ))
        return fields

    def _edit_timeline(self, ref: GroupContainer, grp, choice) -> bool:
        """Apply the Edit dialog's Timeline choice; whether it changed."""
        if choice not in TIMELINE_CHOICES:
            return False
        shared, bookmarks = timeline_settings(choice)
        if (shared, bookmarks) == (grp.shared_timeline, grp.timeline_bookmarks):
            return False
        from tumblepipe.config import multi_timeline
        multi_timeline.set_timeline(ref.uri, shared, bookmarks)
        # The Multi's workfile may be the open scene: lay it out now, not
        # on its next open.
        try:
            from tumblepipe.pipe.houdini import multi_timeline as session
            session.apply_if_open(ref.uri)
        except ImportError:
            pass  # no Houdini session
        return True

    def edit(self, collection_id: str, fields: dict) -> bool:
        ref = self._activated(collection_id)
        if ref is None:
            return False
        if isinstance(ref, SceneContainer):
            try:
                changed = self.edit_scene_assets(
                    ref, fields.get("assets", ()) or (),
                )
            except Exception as exc:
                raise ConfigError(
                    self._catalog_id,
                    f"failed to edit root {collection_id}: {exc}",
                    cause=exc,
                ) from exc
            if changed:
                self.invalidate_membership_cache()
                self._sync_roots(collection_id)
            return True
        from tumblepipe.config import groups as grp_mod
        grp = grp_mod.get_group(ref.uri)
        if grp is None:
            return False
        current = set(grp.departments)
        new_set = set(fields.get("departments", ()) or ())
        to_add = new_set - current
        to_remove = current - new_set
        try:
            for d in to_add:
                grp_mod.add_department(ref.uri, d)
            for d in to_remove:
                grp_mod.remove_department(ref.uri, d)
            self._edit_timeline(ref, grp, fields.get("timeline"))
        except Exception as exc:
            raise ConfigError(
                self._catalog_id,
                f"failed to edit group {collection_id}: {exc}",
                cause=exc,
            ) from exc
        if to_add or to_remove:
            self.invalidate_membership_cache()
            # The Multi's card shows a deck row per covered department; the
            # collection id is the card's id.
            self._refresh.card(collection_id)
        return True

    def add_assets(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        """``(added, skipped, status message)``."""
        ref = self._activated(collection_id)
        if ref is None:
            return (0, 0, "")
        if isinstance(ref, GroupContainer):
            added, skipped, reasons = self._add_assets_to_group(ref, asset_ids)
        else:
            added, skipped, reasons = self._add_assets_to_scene(ref, asset_ids)
        if added:
            self.invalidate_membership_cache()
        why = ", ".join(sorted(reasons))
        if skipped and added:
            msg = f"Added {added} of {added + skipped} — {skipped} skipped ({why})"
        elif skipped:
            msg = f"Skipped {skipped} ({why})"
        else:
            msg = f"Added {added}"
        if added and isinstance(ref, SceneContainer):
            msg = self._with_sync(msg, self._sync_roots(collection_id))
        return (added, skipped, msg)

    def remove_assets(
        self, collection_id: str, asset_ids: list[str],
    ) -> tuple[int, int, str]:
        """``(removed, skipped, status message)``."""
        ref = self._activated(collection_id)
        if ref is None:
            return (0, 0, "")
        if isinstance(ref, GroupContainer):
            removed, skipped = self._remove_assets_from_group(ref, asset_ids)
        else:
            removed, skipped = self._remove_assets_from_scene(ref, asset_ids)
        if removed:
            self.invalidate_membership_cache()
        if removed and skipped:
            msg = f"Removed {removed}, skipped {skipped}"
        elif skipped:
            msg = f"Skipped {skipped}"
        else:
            msg = f"Removed {removed}"
        if removed and isinstance(ref, SceneContainer):
            msg = self._with_sync(msg, self._sync_roots(collection_id))
        return (removed, skipped, msg)

    def _sync_roots(self, collection_id: str) -> str:
        """Bring the shots up to date after a Root edit; the summary.

        A Root edit reaches import_shot only through files (the Root's
        export, the shot's root layer, its staged build); without them a
        layout workfile opened next imports none of it. ``sync_roots``
        writes only what changed, and the Root's card drops its drift dot.
        """
        from tumblepipe.pipe.root_staging import sync_roots
        result = sync_roots()
        self._refresh.card(collection_id)
        return result.summary()

    @staticmethod
    def _with_sync(msg: str, summary: str) -> str:
        return f"{msg}; {summary}" if summary else msg

    def card(self, collection_id: str) -> Asset | None:
        """A fresh card for a Multi or Root (after a create or an edit); None
        while its project's Client is not READY or when it is gone."""
        ref = parse(collection_id)
        if ref is None or not self._clients.is_ready(ref.project_name):
            return None
        if self._activated(collection_id) is None:
            return None
        if isinstance(ref, GroupContainer):
            from tumblepipe.config import groups as grp_mod
            container = grp_mod.get_group(ref.uri)
            count = len(container.members) if container is not None else 0
            # Other members' decks read the coverage too.
            self.invalidate_membership_cache()
        else:
            from tumblepipe.config import scene as scn_mod
            container = scn_mod.get_scene_by_uri(ref.uri)
            count = len(container.assets) if container is not None else 0
        if container is None:
            return None
        proxy = Collection(
            id=collection_id,
            label=ref.path.rsplit("/", 1)[-1],
            tag=collection_id,
            count=count,
            kind=ref.kind,
        )
        asset = self.collection_card(
            proxy, ref.project_name, ref.kind,
        )
        return dataclasses.replace(asset, catalog_id=self._catalog_id)
