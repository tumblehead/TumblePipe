"""Parse asset ids into the project, client, entity URI and folder they name.

Replaces the half-dozen separate ``_split_asset_id`` /
``_project_for_asset_id`` / ``_client_for_asset_id`` /
``_uri_for_asset_id`` / ``_project_root_for_asset_id`` helpers that used
to live on :class:`PipelineCatalog`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tumbletrove.asset_browser.api.errors import CatalogError
from .projects import ProjectConfig, PipelineProjectRegistry

from .clients import ClientPool
from . import uris as uris

if TYPE_CHECKING:
    from tumblepipe.util.uri import Uri


class AssetResolutionError(CatalogError):
    """An ``asset_id`` does not lead anywhere sensible.

    Distinct from :class:`CatalogInitError` (Client construction failure).
    """


class EntityNotRegistered(AssetResolutionError):
    """The id parses to an entity URI the project's config does not hold.

    :meth:`AssetResolver.uri_for` is string math: any ``PROJECT/X/Y`` becomes
    ``entity:/shots/X/Y`` (or ``assets``) whether or not that entity exists.
    Nothing downstream checks either — reserving a workfile version and
    writing an entity's own properties both create whatever is missing. So a
    create or an edit reached with an id that names no real entity quietly
    manufactured one: a layout department's multi-shot scene lived for a week
    as workfiles of ``entity:/shots/010/Multishots``, a shot that was never in
    the config, and every export from it collected nothing.
    """


class AssetResolver:
    """``asset_id`` → its project, client, entity URI and project root.

    Takes the catalog's ID for error provenance, the project registry
    for project lookup, the :class:`ClientPool` for Client access, and
    a callable that returns the project's asset categories (used to
    disambiguate ``"PROJECT/cat/Name"`` vs ``"PROJECT/seq/Shot"``).
    """

    def __init__(
        self,
        catalog_id: str,
        registry: PipelineProjectRegistry,
        clients: ClientPool,
        categories_for_project: Callable[[str], list[str]],
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._clients = clients
        self._categories_for_project = categories_for_project

    # ── Lightweight accessors (no client build) ──────────

    def split(self, asset_id: str) -> tuple[str, str, str] | None:
        """Split ``"PROJECT/SECOND/THIRD"`` or return ``None`` for any
        other shape, an empty segment included."""
        if not asset_id:
            return None
        # split() with no maxsplit: a 4-segment id must be rejected, not
        # glued into third="c/d".
        parts = asset_id.split("/")
        if len(parts) != 3 or not all(parts):
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

    def project_for_path(self, path: Path | str | None) -> ProjectConfig | None:
        """The registered project whose folder holds ``path`` (a ``.hip``),
        or None — an untitled scene, or a file outside every project."""
        if not path:
            return None
        resolved = Path(path).resolve()
        for proj in self._registry.all():
            if resolved.is_relative_to(Path(proj.project_path).resolve()):
                return proj
        return None

    def uri_for(self, asset_id: str) -> "Uri | None":
        """Build the entity URI. ``None`` for a malformed id. Takes the
        shot URI shape when categories are unknown."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        project_name, second, third = parts
        cats = self._categories_for_project(project_name)
        kind = "assets" if second in cats else "shots"
        return uris.entity(kind, second, third)

    def uri_for_ready(self, asset_id: str) -> "Uri | None":
        """:meth:`uri_for` with the project's Client guaranteed READY first.

        ``uri_for`` guesses the *shot* shape while the project's categories
        are unknown, which is right for painting a sidebar before background
        init finishes and wrong for anything that then resolves a workspace
        from the URI: a New from Current or Open Latest clicked during a slow
        (SMB) client warm-up would reserve, save or look for the workfile
        under ``shots/<cat>/<asset>/``. Paths that read or write on disk must
        use this. It blocks on Client construction, so it belongs on the
        worker thread the file-system handlers already run on, never on the
        GUI thread. ``None`` when the id is malformed; a Client that cannot
        be built raises :class:`CatalogInitError`.
        """
        parts = self.split(asset_id)
        if parts is None:
            return None
        self._clients.get(parts[0])
        return self.uri_for(asset_id)

    def registered_uri_for(self, asset_id: str) -> "Uri | None":
        """:meth:`uri_for_ready`, refusing an entity the config does not hold.

        For paths that *write* against an entity — creating a workfile,
        saving the open scene as one, editing properties. Those would
        otherwise create the entity (or its workspace) as a side effect; see
        :class:`EntityNotRegistered`. Reading paths keep ``uri_for_ready``: an
        open or a listing of something missing already comes back empty.

        Returns ``None`` exactly where ``uri_for_ready`` does (malformed id).

        Raises:
            CatalogInitError: the project's Client cannot be built.
            EntityNotRegistered: the id is well formed, but no such entity
                exists in the project's config.
        """
        uri = self.uri_for_ready(asset_id)
        if uri is None:
            return None
        client = self._clients.get(asset_id.split("/", 1)[0])
        if client.config.get_properties(uri) is None:
            raise EntityNotRegistered(
                self._catalog_id,
                f"{uri} is not in this project's configuration. Nothing was "
                f"created or changed. If this is a shot or an asset, create "
                f"it first; if it is meant to be a Multi, use the Multi's own "
                f"row instead.",
            )
        return uri

    def client_for(self, asset_id: str):
        """The project's Client — ``None`` for a malformed id; a Client
        that cannot be built raises :class:`CatalogInitError`."""
        parts = self.split(asset_id)
        if parts is None:
            return None
        return self._clients.get(parts[0])

    def entity_dir(self, asset_id: str) -> Path | None:
        """The entity's own folder (``project:/<segments>``), resolved by its
        project's own storage table — no project activation needed, so it is
        safe for the per-card thumbnail lookups. Where its sidecars
        (``description.txt``, ``thumbnail.png``) live. ``None`` for a
        malformed id; a Client that cannot be built raises
        :class:`CatalogInitError`."""
        uri = self.uri_for_ready(asset_id)
        if uri is None:
            return None
        client = self._clients.get(asset_id.split("/", 1)[0])
        return Path(client.storage.resolve(uris.project_root() / uri.segments))
