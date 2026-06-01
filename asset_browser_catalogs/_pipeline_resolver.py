"""Parse asset ids into typed (ref, project, client, uri, root) tuples.

Replaces the half-dozen separate ``_split_asset_id`` /
``_parse_entity_ref`` / ``_project_for_asset_id`` /
``_client_for_asset_id`` / ``_uri_for_asset_id`` /
``_project_root_for_asset_id`` helpers that used to live on
:class:`PipelineCatalog`. Each callsite previously opened with two or
three None-checked partial lookups; an :class:`AssetCtx` returned by
:meth:`AssetResolver.resolve` gives you everything in one go and
fails loudly when the id is bad.

Lightweight accessors (``project_for``, ``parse_ref``, ``uri_for``,
``client_for``, ``root_for``) survive for callers that genuinely
need a partial view — e.g. building a project-scoped collection
tree before any client is READY.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from asset_browser.api.errors import CatalogError
from asset_browser.core.projects import ProjectConfig, ProjectRegistry

from _pipeline_clients import ClientPool
from _pipeline_types import AssetRef, EntityRef, parse_entity_ref
import _pipeline_uris as uris

if TYPE_CHECKING:
    from tumblepipe.util.uri import Uri


class AssetResolutionError(CatalogError):
    """Raised when an ``asset_id`` cannot be fully resolved.

    Distinct from :class:`CatalogInitError` (Client construction
    failure) — this is the higher-level "id doesn't lead anywhere
    sensible" error. The cause is one of:

    - malformed id (not three slash-separated segments),
    - unknown project (first segment doesn't match the registry),
    - entity not parseable against the project's category list.

    Client construction failures bubble up as ``CatalogInitError``
    unchanged.
    """


@dataclass(frozen=True)
class AssetCtx:
    """Fully-resolved view of an asset id.

    Every field is non-None. Built by :meth:`AssetResolver.resolve`;
    callers can use any field without re-checking.
    """

    asset_id: str
    ref: EntityRef
    project: ProjectConfig
    client: object  # ``tumblepipe.api.Client`` when READY
    uri: "Uri"
    root: Path


class AssetResolver:
    """``asset_id`` → typed context, with both eager and best-effort entry points.

    Takes the catalog's ID for error provenance, the project registry
    for project lookup, the :class:`ClientPool` for Client access, and
    a callable that returns the project's asset categories (used to
    disambiguate ``"PROJECT/cat/Name"`` vs ``"PROJECT/seq/Shot"``).
    """

    def __init__(
        self,
        catalog_id: str,
        registry: ProjectRegistry,
        clients: ClientPool,
        categories_for_project: Callable[[str], list[str]],
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._clients = clients
        self._categories_for_project = categories_for_project

    # ── Lightweight accessors (no client build) ──────────

    def split(self, asset_id: str) -> tuple[str, str, str] | None:
        """Split ``"PROJECT/SECOND/THIRD"`` or return ``None``."""
        if not asset_id:
            return None
        parts = asset_id.split("/", 2)
        if len(parts) != 3:
            return None
        return (parts[0], parts[1], parts[2])

    def project_for(self, asset_id: str) -> ProjectConfig | None:
        """Look up the project by name from ``asset_id``'s first
        segment. No client build, no error if the project doesn't
        exist — None is returned for both."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        return self._registry.get(parts[0])

    def parse_ref(self, asset_id: str) -> EntityRef | None:
        """Return :class:`AssetRef` / :class:`ShotRef` using the
        project's category list to disambiguate. Returns ``None`` on
        parse failure. Treats the entity as a shot when categories
        are unknown (Client not READY) — same fallback as the legacy
        ``_parse_entity_ref``."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        cats = self._categories_for_project(parts[0])
        return parse_entity_ref(asset_id, cats)

    def uri_for(self, asset_id: str) -> "Uri | None":
        """Build the entity URI. ``None`` on parse failure. Falls back
        to the shot URI shape when categories are unknown."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        project_name, second, third = parts
        cats = self._categories_for_project(project_name)
        kind = "assets" if second in cats else "shots"
        try:
            return uris.entity(kind, second, third)
        except Exception:
            return None

    def client_for(self, asset_id: str):
        """Best-effort client lookup — ``None`` on parse failure or
        Client init failure (errors are silently swallowed; use
        :meth:`resolve` if you need the typed error)."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        client, _err = self._clients.try_get(parts[0])
        return client

    def root_for(self, asset_id: str) -> Path | None:
        """Project root on disk. Prefers the live Client's
        ``PROJECT_PATH`` (so any bootstrap-time fixups apply); falls
        back to the registry entry when the Client isn't READY."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        client, _err = self._clients.try_get(parts[0])
        if client is not None:
            return Path(client.PROJECT_PATH)
        proj = self._registry.get(parts[0])
        if proj is not None:
            return Path(proj.project_path)
        return None

    # ── Full resolution (builds client) ──────────────────

    def resolve(self, asset_id: str) -> AssetCtx:
        """Return the full :class:`AssetCtx`.

        Raises:
            AssetResolutionError: id is malformed / project unknown /
                entity unparseable against the project's categories.
            CatalogInitError: Client construction failed (propagated
                from :meth:`ClientPool.get`).
        """
        parts = self.split(asset_id)
        if parts is None:
            raise AssetResolutionError(
                self._catalog_id,
                f"malformed asset_id {asset_id!r}",
            )
        project_name, second, third = parts
        proj = self._registry.get(project_name)
        if proj is None:
            raise AssetResolutionError(
                self._catalog_id,
                f"unknown project {project_name!r} in {asset_id!r}",
            )
        # Client first — categories require a READY client.
        client = self._clients.get(project_name)
        cats = self._categories_for_project(project_name)
        ref = parse_entity_ref(asset_id, cats)
        if ref is None:
            raise AssetResolutionError(
                self._catalog_id,
                f"unparseable entity in {asset_id!r}",
            )
        kind = "assets" if isinstance(ref, AssetRef) else "shots"
        uri = uris.entity(kind, second, third)
        return AssetCtx(
            asset_id=asset_id,
            ref=ref,
            project=proj,
            client=client,
            uri=uri,
            root=Path(client.PROJECT_PATH),
        )

    def try_resolve(self, asset_id: str) -> AssetCtx | None:
        """As :meth:`resolve` but returns ``None`` instead of raising.

        Use for aggregator paths that must skip bad entries (e.g.
        discovery merging across projects, where one project's
        broken client shouldn't stop the others)."""
        try:
            return self.resolve(asset_id)
        except Exception:
            return None
