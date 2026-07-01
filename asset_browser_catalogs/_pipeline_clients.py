"""Per-project tumblepipe Client lifecycle.

Owns the slot dict, the construction lock, and the FAILED/READY state
transitions. Catalog-side code goes through this rather than touching
``ClientSlot`` directly.

Construction is serialised because tumblepipe's ``Client`` reads
``TH_*`` env vars while building its ``JsonConfigStore`` — two
concurrent builds race on the env and cross-wire each other's
config. The lock is catalog-scoped (one per :class:`ClientPool`).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from tumbletrove.asset_browser.api.errors import CatalogInitError
from tumbletrove.asset_browser.core.projects import ProjectConfig, PipelineProjectRegistry

from _pipeline_types import (
    PERMANENT_INIT_ERRORS,
    ClientSlot,
    ClientState,
)

log = logging.getLogger(__name__)


class ClientPool:
    """Lifecycle manager for per-project ``tumblepipe.api.Client`` objects.

    Builds Clients lazily and serialises construction with a per-pool
    lock. Each project gets a :class:`ClientSlot` that tracks UNTRIED /
    READY / FAILED_TRANSIENT / FAILED_PERMANENT, so transient failures
    retry while permanent ones stick.
    """

    def __init__(self, catalog_id: str, registry: PipelineProjectRegistry) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._slots: dict[str, ClientSlot] = {}
        self._lock = threading.Lock()

    # ── Slot access ───────────────────────────────────────

    def _slot(self, project_name: str) -> ClientSlot:
        """Return (creating if missing) the slot for ``project_name``."""
        slot = self._slots.get(project_name)
        if slot is None:
            slot = ClientSlot()
            self._slots[project_name] = slot
        return slot

    def is_ready(self, project_name: str) -> bool:
        slot = self._slots.get(project_name)
        return slot is not None and slot.state is ClientState.READY

    @property
    def ready(self) -> dict[str, object]:
        """Read-only view of currently-READY clients keyed by name."""
        return {
            name: slot.client
            for name, slot in self._slots.items()
            if slot.state is ClientState.READY and slot.client is not None
        }

    # ── Construction ──────────────────────────────────────

    def get(self, project_name: str):
        """Return the Client for ``project_name``, building on demand.

        Raises :class:`CatalogInitError` if the project isn't registered
        or the underlying Client construction fails (transient OR
        permanent — callers use :meth:`try_get` if they need to skip
        failures).
        """
        slot = self._slots.get(project_name)
        if slot is not None and slot.state is ClientState.READY:
            return slot.client
        proj = self._registry.get(project_name)
        if proj is None:
            raise CatalogInitError(
                self._catalog_id,
                f"unknown project {project_name!r}",
            )
        return self._build_blocking(proj)

    def try_get(
        self, project_name: str,
    ) -> tuple[object | None, CatalogInitError | None]:
        """Return ``(client, error)`` without raising.

        For aggregators that skip projects that fail to init while
        collecting errors for surfacing via ``AssetPage.errors``.
        """
        try:
            return (self.get(project_name), None)
        except CatalogInitError as err:
            return (None, err)

    def ensure_all(self) -> list[CatalogInitError]:
        """Attempt to build Clients for every registered project.

        Returns the list of init errors that occurred. Does not raise —
        per-project failures are collected for aggregator surfacing.
        """
        errors: list[CatalogInitError] = []
        for proj in self._registry.all():
            _, err = self.try_get(proj.name)
            if err is not None:
                errors.append(err)
        return errors

    # ── Slot maintenance ──────────────────────────────────

    def remove(self, project_name: str) -> None:
        """Drop a single project's slot — used when a project is
        removed from the registry, or when the user forces a full
        rebuild via Shift+Click."""
        self._slots.pop(project_name, None)

    def clear(self) -> None:
        """Drop every slot. Subsequent ``get`` calls retry from scratch."""
        self._slots.clear()

    # ── Lock-held construction ────────────────────────────

    def _build_blocking(self, proj: ProjectConfig):
        """Build one project's Client synchronously, holding the lock.

        Idempotent for READY slots. FAILED_PERMANENT slots re-raise the
        stored error. FAILED_TRANSIENT and UNTRIED slots attempt
        (re-)construction.
        """
        slot = self._slot(proj.name)
        if slot.state is ClientState.READY:
            return slot.client
        if slot.state is ClientState.FAILED_PERMANENT:
            assert slot.error is not None
            raise slot.error

        with self._lock:
            slot = self._slot(proj.name)
            if slot.state is ClientState.READY:
                return slot.client
            if slot.state is ClientState.FAILED_PERMANENT:
                assert slot.error is not None
                raise slot.error
            return self._build_locked(proj, slot)

    def _build_locked(self, proj: ProjectConfig, slot: ClientSlot):
        """Lock-held construction. Updates ``slot`` and returns the
        Client on success; raises :class:`CatalogInitError` on failure.
        """
        slot.last_attempt = time.time()
        try:
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
            # file is open. That signal is owned by ProjectActivator;
            # constructing per-project Clients here is a passive lookup
            # and feeds Client via explicit args below.
            from tumblepipe.api import Client
            client = Client(
                Path(proj.project_path),
                Path(pipeline_path),
                Path(proj.config_path),
            )
        except Exception as exc:
            permanent = isinstance(exc, PERMANENT_INIT_ERRORS)
            err = CatalogInitError(
                self._catalog_id,
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
                "Pipeline API init failed for project %s (state=%s)",
                proj.name, slot.state.name,
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
