"""Discovering the assets and shots of every registered project: cards,
details and the sidebar's counts.

- :class:`ProjectBuckets` — a project's categories and sequences, straight
  from its config (no card building, so safe on the GUI thread).
- :class:`EntityIndex` — the card / detail builders, the discovery pass
  over every project and its cache, the department workfile scan behind a
  card, and the per-bucket counts the sidebar shows.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.errors import (
    AssetDiscoveryError,
    CatalogError,
    ConfigError,
    WorkfileScanError,
)
from tumbletrove.asset_browser.api.types import Asset, AssetDetail

from . import uris as uris
from .types import latest_by_dept, scan_workfiles

if TYPE_CHECKING:
    from .projects import (
        PipelineProjectRegistry, ProjectConfig,
    )
    from .clients import ClientPool
    from .dept_scope import DepartmentScope
    from .houdini import ProjectActivator
    from .resolver import AssetResolver
    from .workfiles import WorkfileManager


class ProjectBuckets:
    """The categories and sequences each registered project holds.

    Both include a bucket with no entities yet (``entity:/assets/<cat>`` on
    its own), which the pipeline supports. ``[]`` while the project's Client
    is not READY, so the sidebar can render before background init finishes;
    a config that cannot be enumerated raises :class:`ConfigError`.
    """

    def __init__(
        self, catalog_id: str, registry: "PipelineProjectRegistry",
        clients: "ClientPool",
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._clients = clients

    def _buckets(self, project_name: str, kind: str) -> list[str]:
        if not self._clients.is_ready(project_name):
            return []
        client = self._clients.get(project_name)
        try:
            entity_uris = client.config.list_entity_uris(None, closure=True)
        except Exception as exc:
            raise ConfigError(
                self._catalog_id,
                f"failed to list the {kind} of {project_name}: {exc}",
                cause=exc,
            ) from exc
        return sorted({
            uri.segments[1] for uri in entity_uris
            if len(uri.segments) >= 2 and uri.segments[0] == kind
        })

    def categories(self, project_name: str) -> list[str]:
        return self._buckets(project_name, "assets")

    def sequences(self, project_name: str) -> list[str]:
        return self._buckets(project_name, "shots")

    def all_categories(self) -> list[str]:
        """Every initialized project's categories, merged."""
        out: set[str] = set()
        for proj in self._registry.all():
            out.update(self.categories(proj.name))
        return sorted(out)

    def all_sequences(self) -> list[str]:
        out: set[str] = set()
        for proj in self._registry.all():
            out.update(self.sequences(proj.name))
        return sorted(out)

    def count_entities(self, project_name: str, kind: str, bucket: str) -> int:
        """``entity:/{kind}/{bucket}/...`` leaves, from the uri listing
        alone — no card building, safe on the GUI thread."""
        if not self._clients.is_ready(project_name):
            return 0
        client = self._clients.get(project_name)
        return sum(
            1 for uri in client.config.list_entity_uris(None, closure=True)
            if len(uri.segments) >= 3
            and uri.segments[0] == kind
            and uri.segments[1] == bucket
        )


# ── Entity facts a detail (and the scene's timeline) reads ────────────


def _lookup(catalog_id: str, what: str, asset_id: str, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        raise ConfigError(
            catalog_id, f"{what} lookup failed for {asset_id}: {exc}",
            cause=exc,
        ) from exc


def entity_channels(catalog_id: str, asset_id: str, uri) -> list[str]:
    """The entity's publish channel names."""
    from tumblepipe.config.channels import list_channels
    return list(_lookup(catalog_id, "channels", asset_id, list_channels, uri))


def entity_frame_range(catalog_id: str, asset_id: str, uri):
    """The entity's ``FrameRange``, or None when it has none. A Multi's id
    resolves no entity ``uri`` and has none: its scene keeps the range it
    has, and the FPS comes from the project."""
    from tumblepipe.config.timeline import get_frame_range
    if uri is None:
        return None
    return _lookup(catalog_id, "frame range", asset_id, get_frame_range, uri)


def entity_fps(catalog_id: str, asset_id: str, uri):
    """The entity's FPS, or the project's when the entity sets none."""
    from tumblepipe.config.timeline import get_fps
    fps = _lookup(catalog_id, "fps", asset_id, get_fps, uri)
    if fps is not None:
        return fps
    return _lookup(catalog_id, "fps", asset_id, get_fps)


def entity_animatable(catalog_id: str, asset_id: str, uri) -> bool:
    """Whether the entity is time-dependent (assets set ``animatable:
    false``; shots and unmarked entities are, and so is an id that
    resolves no entity ``uri``)."""
    from tumblepipe.config.timeline import is_animatable
    if uri is None:
        return True
    return _lookup(catalog_id, "animatable", asset_id, is_animatable, uri)


class EntityIndex:
    """Asset and shot cards, details, and the discovery cache."""

    def __init__(
        self,
        catalog_id: str,
        *,
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        resolver: "AssetResolver",
        activator: "ProjectActivator",
        buckets: ProjectBuckets,
        dept_scope: "DepartmentScope",
        workfiles: "WorkfileManager",
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._clients = clients
        self._resolver = resolver
        self._activator = activator
        self._buckets = buckets
        self._dept_scope = dept_scope
        self._workfiles = workfiles
        # Discovery cache (merged across projects); None until a pass that
        # hit no errors completes.
        self._assets: list[Asset] | None = None
        self._shots: list[Asset] | None = None
        #: Tag filter of the most recent grid query. A single-card refresh
        #: has no tags of its own, so it replays these to keep the
        #: contextual category prefix (see :meth:`apply_category_prefix`).
        self.last_query_tags: frozenset[str] = frozenset()

    def invalidate(self, *, assets: bool = True, shots: bool = True) -> None:
        """Drop the cached cards; the next query rediscovers."""
        if assets:
            self._assets = None
        if shots:
            self._shots = None

    # ── Discovery ────────────────────────────────────────

    def cached(self) -> list[Asset] | None:
        """The cached cards if a pass has completed, else None.

        The sidebar's counts run on the GUI thread, where a discovery pass
        (a workfile scandir per department per card, possibly over SMB) is
        a hard freeze: they read this or count uris instead.
        """
        if self._assets is None or self._shots is None:
            return None
        return self._assets + self._shots

    def items(self, errors: list[CatalogError]) -> list[Asset]:
        """Every project's asset and shot cards; failures go on ``errors``.

        Cached, except for a pass that hit errors (a cache hit skips
        discovery, so the next browse would show the partial result with
        the errors gone) or found nothing while no Client was ready yet (a
        transient "nothing loaded" must not stick).
        """
        cached = self.cached()
        if cached is not None:
            return cached
        before = len(errors)
        assets, shots = self._discover(errors)
        has_projects = any(True for _ in self._registry.all())
        if not assets and not shots and has_projects and not self._clients.ready:
            return []
        if len(errors) == before:
            self._assets, self._shots = assets, shots
        return assets + shots

    def _discover(self, errors: list) -> tuple[list[Asset], list[Asset]]:
        """One pass over each project's entity list: ``(assets, shots)``."""
        assets_by_id: dict[str, Asset] = {}
        shots_by_id: dict[str, Asset] = {}
        for proj in self._registry.all():
            client, init_err = self._clients.try_get(proj.name)
            if init_err is not None:
                errors.append(init_err)
                continue
            # The workspace scans resolve through the active project.
            self._activator.activate(proj)
            # coherent(): one config coherency check for the whole project
            # pass — the per-card department lists and frame ranges would
            # otherwise each re-stamp the db files.
            with client.config.coherent():
                try:
                    entity_uris = client.config.list_entity_uris(None, closure=True)
                except Exception as exc:
                    errors.append(AssetDiscoveryError(
                        self._catalog_id,
                        f"list_entities failed for {proj.name}: {exc}",
                        cause=exc,
                    ))
                    continue
                # The pools, once per project rather than once per card.
                pools = {
                    kind: self._dept_scope.pool(kind) for kind in uris.ENTITY_KINDS
                }
                for uri in entity_uris:
                    segments = uri.segments
                    if len(segments) != 3 or segments[0] not in uris.ENTITY_KINDS:
                        continue
                    # One entity whose card cannot be built (a broken frame
                    # range, an unreadable share folder) is surfaced on the
                    # page's errors, beside the cards that could be.
                    try:
                        card = self.build_card(proj, uri, pools[segments[0]])
                    except Exception as exc:
                        errors.append(AssetDiscoveryError(
                            self._catalog_id, f"{uri}: {exc}", cause=exc,
                        ))
                        continue
                    by_id = assets_by_id if segments[0] == "assets" else shots_by_id
                    by_id.setdefault(card.id, card)
        return list(assets_by_id.values()), list(shots_by_id.values())

    # ── Cards ────────────────────────────────────────────

    def build_card(
        self, proj: "ProjectConfig", uri, departments: list[str] | None = None,
    ) -> Asset:
        """The card of the asset or shot at ``uri``. ``departments`` is the
        context's pool when the caller already has it (one per project in
        a discovery pass).

        A department folder that can't be read is left off the card and
        named in its Note instead of failing the whole card: one such folder
        (left delete-pending on a network share by an interrupted Delete)
        otherwise took the entity out of the browser, where it could not be
        deleted again, and put a catalog-wide error over the page."""
        kind, bucket, name = uri.segments
        asset_id = uris.asset_id_for(proj.name, uri)
        unreadable: list[tuple[str, OSError]] = []
        scanned = self.scan_departments(asset_id, departments, unreadable)
        depts = {dept: versions[-1] for dept, (versions, _) in scanned.items()}
        latest_update, last_user, last_note = self._latest_update_user_note(
            asset_id, scanned,
        )
        if unreadable:
            last_note = unreadable_note(unreadable)
        metadata: dict = {
            "departments": depts,
            "project": proj.name,
            "dept_count": len(depts),
            "latest_update": latest_update,
            "last_user": last_user,
            "last_note": last_note,
        }
        if kind == "assets":
            tags = {"type:asset", f"category:{bucket}"}
            metadata["category"] = bucket
        else:
            tags = {"type:shot", f"sequence:{bucket}"}
            metadata["sequence"] = bucket
            fr = entity_frame_range(self._catalog_id, asset_id, uri)
            metadata["frame_range"] = (
                f"{fr.start_frame}-{fr.end_frame}"
                if fr is not None
                and fr.start_frame is not None and fr.end_frame is not None
                else ""
            )
        return Asset(
            id=asset_id,
            name=name,
            thumbnail_url="",
            tags=frozenset({"source:pipeline", f"project:{proj.name}", *tags}),
            has_deck_items=True,
            metadata=metadata,
            catalog_id=self._catalog_id,
        )

    def card(self, asset_id: str) -> Asset | None:
        """A fresh card for one asset or shot (a save, a create), with the
        grid's current category prefix; None for an id naming none."""
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return None
        self._activator.activate(proj)
        uri = self._resolver.uri_for_ready(asset_id)
        card = self.build_card(proj, uri)
        return self.apply_category_prefix([card], self.last_query_tags)[0]

    @staticmethod
    def apply_category_prefix(
        items: list[Asset], tags: frozenset[str],
    ) -> list[Asset]:
        """Prefix names with their category or sequence when the view
        mixes them.

        Sibling assets in different categories can share a bare name
        ("SET" exists in both Clash and PoP), and every sequence can have
        its own SH0010, so the Assets-root, Shots-root, project-root,
        Multi-member and All views render ambiguous rows. Inside a single
        category or sequence node (a ``category:`` / ``sequence:`` tag is
        in the filter) the bare name stays: there the prefix only repeats
        the node's name and pushes the shot out of the Name column.
        """
        in_category = any(t.startswith("category:") for t in tags)
        in_sequence = any(t.startswith("sequence:") for t in tags)
        out: list[Asset] = []
        for a in items:
            meta = a.metadata or {}
            if "type:asset" in a.tags and not in_category:
                parent = meta.get("category", "")
            elif "type:shot" in a.tags and not in_sequence:
                parent = meta.get("sequence", "")
            else:
                parent = ""
            if parent:
                out.append(dataclasses.replace(a, name=f"{parent}/{a.name}"))
            else:
                out.append(a)
        return out

    # ── Detail ───────────────────────────────────────────

    def detail(self, asset_id: str, description: str) -> AssetDetail:
        """The detail of an asset or shot (``PROJECT/<bucket>/<name>``)."""
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return AssetDetail(
                id=asset_id, name=asset_id, thumbnail_url="",
                tags=frozenset({"source:pipeline"}),
            )
        # Config reads below (channels, frame range, fps) resolve through
        # the active project.
        self._activator.activate(proj)
        uri = self._resolver.uri_for_ready(asset_id)
        kind, bucket, name = uri.segments
        # Workfile versions (the detail's version dropdowns), not exports.
        dept_info = self.department_versions(asset_id)
        tags = {"source:pipeline", f"project:{proj.name}"}
        metadata: dict = {
            # An AssetDetail becomes a grid card verbatim when the browser
            # resolves a favourite / shelf / collection ref, so
            # ``departments`` must carry the card shape (dept -> latest)
            # exactly like the discovery builders. The full per-dept
            # version list lives under its own key for the dropdowns.
            "departments": latest_by_dept(dept_info),
            "department_versions": dept_info,
            "project": proj.name,
        }
        if kind == "assets":
            tags |= {"type:asset", f"category:{bucket}"}
            metadata["category"] = bucket
            metadata["channels"] = entity_channels(self._catalog_id, asset_id, uri)
        else:
            tags |= {"type:shot", f"sequence:{bucket}"}
            metadata["sequence"] = bucket
            fr = entity_frame_range(self._catalog_id, asset_id, uri)
            if fr is not None:
                if fr.start_frame is not None:
                    metadata["frame_start"] = fr.start_frame
                if fr.end_frame is not None:
                    metadata["frame_end"] = fr.end_frame
                if fr.start_frame is not None and fr.end_frame is not None:
                    metadata["frame_total"] = fr.end_frame - fr.start_frame + 1
            fps = entity_fps(self._catalog_id, asset_id, uri)
            if fps is not None:
                metadata["fps"] = fps
        return AssetDetail(
            id=asset_id,
            name=name,
            thumbnail_url="",
            tags=frozenset(tags),
            description=description,
            metadata=metadata,
        )

    # ── Department workfiles ─────────────────────────────

    def department_versions(self, asset_id: str) -> dict[str, list[str]]:
        """``{dept: [workfile versions]}`` for every department with one."""
        return {
            dept: versions
            for dept, (versions, _) in self.scan_departments(asset_id).items()
        }

    def scan_departments(
        self, asset_id: str, departments: list[str] | None = None,
        unreadable: list[tuple[str, OSError]] | None = None,
    ) -> dict[str, tuple[list[str], float]]:
        """One scandir per dept workspace: ``{dept: (versions, newest_mtime)}``,
        departments with no workfile left out.

        The workspace is the one Open uses (``pipe.paths.department_workspace``):
        a department a Multi covers lists the Multi's workfiles.
        ``departments`` is the context's pool when the caller has it; when
        omitted it is read here — one project enumeration, so per-card
        loops must always pass it.

        A workspace that can't be listed raises :class:`WorkfileScanError`,
        unless ``unreadable`` is given: then the department is skipped and
        ``(dept, error)`` appended to it.
        """
        from tumblepipe.pipe.paths import department_workspace
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return {}
        self._activator.activate(proj)
        uri = self._resolver.uri_for_ready(asset_id)
        if departments is None:
            departments = self._dept_scope.pool(uri.segments[0])
        result: dict[str, tuple[list[str], float]] = {}
        for dept in departments:
            _workfile_uri, workspace = department_workspace(uri, dept)
            try:
                versions, newest = scan_workfiles(workspace)
            except OSError as exc:
                if unreadable is None:
                    raise WorkfileScanError(
                        self._catalog_id,
                        f"failed to scan workfile versions for {asset_id}: {exc}",
                        cause=exc,
                    ) from exc
                unreadable.append((dept, exc))
                continue
            if versions:
                result[dept] = (versions, newest)
        return result

    def _latest_update_user_note(
        self, asset_id: str, scanned: dict[str, tuple[list[str], float]],
    ) -> tuple[float, str, str]:
        """``(newest_mtime_across_depts, last_author, last_note)``.

        The mtime comes free with the scan. The author and note cost
        exactly ONE ``_context/{version}.json`` read between them — for the
        newest department only — so a large list rebuild stays cheap. That
        is why this asks ``get_user_and_note_for_version`` and not
        ``get_dept_row_meta``, which also globs and stats the workfile for
        an mtime this already has.
        """
        latest_update = 0.0
        best_dept = ""
        best_versions: list[str] | None = None
        for dept, (versions, mtime) in scanned.items():
            if mtime > latest_update:
                latest_update, best_dept, best_versions = mtime, dept, versions
        if not best_dept:
            return latest_update, "", ""
        user, note = self._workfiles.get_user_and_note_for_version(
            asset_id, best_dept, best_versions[-1],
        )
        return latest_update, user, note

    # ── Sidebar counts ───────────────────────────────────

    def count(self, project_name: str, kind: str, bucket: str) -> int:
        """Entities in one category (``kind="assets"``) or sequence
        (``"shots"``): from the cache when a pass has completed, else from
        the uri listing — never by building cards."""
        cached = self.cached()
        if cached is None:
            return self._buckets.count_entities(project_name, kind, bucket)
        bucket_tag = f"{'category' if kind == 'assets' else 'sequence'}:{bucket}"
        type_tag = f"type:{'asset' if kind == 'assets' else 'shot'}"
        project_tag = f"project:{project_name}"
        return sum(
            1 for a in cached
            if bucket_tag in a.tags and project_tag in a.tags and type_tag in a.tags
        )

    # ── Export folder ────────────────────────────────────

    def export_path(self, asset_id: str) -> Path | None:
        """The entity's export folder; None for a malformed id."""
        client = self._resolver.client_for(asset_id)
        if client is None:
            return None
        uri = self._resolver.uri_for_ready(asset_id)
        return client.storage.resolve(uris.export_for_entity(uri))


def unreadable_note(unreadable: list[tuple[str, OSError]]) -> str:
    """The Note of a card whose department folders could not be read."""
    depts = ", ".join(dept for dept, _exc in unreadable)
    _dept, first = unreadable[0]
    reason = first.strerror or str(first)
    folders = "folder" if len(unreadable) == 1 else "folders"
    return (
        f"Can't read the {depts} {folders} ({reason}). If it was just deleted, "
        "close Houdini and any Explorer window showing it, then refresh."
    )
