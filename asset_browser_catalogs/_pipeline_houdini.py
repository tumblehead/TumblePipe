"""Houdini-side bridging for the Pipeline catalog.

Centralizes the two things every action handler needs from the host
process:

- :func:`run_on_main_thread` — schedule a callable on Houdini's main
  (GUI) thread without forcing each caller to re-import
  ``gui_dispatch`` and bake args into a default-arg closure.
- :class:`ProjectActivator` — switch ``TH_*`` env + the tumblepipe
  ``default_client`` singleton to a given project, with a fast path
  for the no-op case and a one-shot warning when the activated project
  diverges from Houdini's launch project.

Lives in its own module so the catalog file isn't carrying 70+ lines
of env/singleton plumbing inline.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

from tumbletrove.asset_browser.core.projects import ProjectConfig

log = logging.getLogger(__name__)


# tumblehead modules that cache ``api = default_client()`` at module
# load. When the active project changes, the cached reference must be
# patched so calls through these modules use the new project's Client.
# Order is not significant; new entries can be appended freely.
_API_BOUND_MODULES: tuple[str, ...] = (
    "tumblepipe.pipe.paths",
    "tumblepipe.config.timeline",
    "tumblepipe.config.variants",
    "tumblepipe.config.department",
    "tumblepipe.pipe.houdini.lops.import_layer",
)


def run_on_main_thread(func: Callable, *args, **kwargs) -> None:
    """Schedule ``func(*args, **kwargs)`` on Houdini's main thread.

    Thin wrapper around
    ``tumbletrove.common.gui.gui_dispatch`` that accepts args
    and kwargs explicitly instead of forcing callers to construct a
    closure with default-arg capture (``def _do(p=path): ...``).
    """
    from tumbletrove.common.gui import gui_dispatch
    if args or kwargs:
        gui_dispatch(lambda: func(*args, **kwargs))
    else:
        gui_dispatch(func)


class ProjectActivator:
    """Tracks and switches the user-active pipeline project.

    A single instance lives on :class:`PipelineCatalog`. Calling
    :meth:`activate` is idempotent on the fast path (env vars already
    match the cached active project); the slow path mutates
    ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` / ``TH_EXPORT_PATH``,
    resets tumblepipe's ``default_client`` singleton, and patches the
    cached ``api`` references on the modules in
    :data:`_API_BOUND_MODULES`.

    The launch project (env at catalog construction) is captured so
    the first activation that switches away can surface a one-shot
    "switched pipeline context" status message — emitted via
    :func:`run_on_main_thread` so it's safe to call from any thread.
    """

    def __init__(self) -> None:
        # The project_path of the project whose env is currently bound.
        # ``None`` means "no activation has happened yet" — the first
        # call must run the full patching pass even if the project
        # matches the launch env, because tumblepipe modules cached at
        # import time may still hold a stale ``api`` reference.
        self._active_project_path: str | None = None
        # Captured at construction so the "you've switched away from the
        # launch project" warning fires at most once per session.
        self._launch_project_path: str = os.environ.get(
            "TH_PROJECT_PATH", "",
        ).strip()
        self._launch_project_name: str = (
            Path(self._launch_project_path).name
            if self._launch_project_path else ""
        )
        self._warned: bool = False

    @property
    def launch_project_name(self) -> str:
        return self._launch_project_name

    @property
    def launch_project_path(self) -> str:
        return self._launch_project_path

    def reset(self) -> None:
        """Forget the cached active project so the next :meth:`activate`
        rebuilds the tumblepipe singleton even when called with the
        previously-active project. Used by the Shift+Click full client
        reset path.
        """
        self._active_project_path = None

    def activate(self, project: ProjectConfig | None) -> None:
        """Bind ``TH_*`` env vars + tumblepipe singleton to ``project``.

        Called before ``hou.hipFile.load`` / ``save`` / ``clear`` so
        downstream tumblehead operations resolve against the right
        project. Safe to call from any thread.

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
            new_client = default_client()
            for mod_path in _API_BOUND_MODULES:
                mod = sys.modules.get(mod_path)
                if mod is not None and hasattr(mod, "api"):
                    mod.api = new_client
        except Exception:
            log.debug("reset_default_client unavailable")
        self._active_project_path = project.project_path

        if (
            self._launch_project_path
            and project.project_path != self._launch_project_path
            and not self._warned
        ):
            self._warned = True
            self._dispatch_switch_warning(project.name)

    def _dispatch_switch_warning(self, proj_name: str) -> None:
        """Surface a one-shot status message on the main thread."""

        def _show_warning() -> None:
            import hou
            hou.ui.setStatusMessage(
                f"Switched pipeline context to {proj_name}. "
                "Some operations may require a Houdini restart.",
                severity=hou.severityType.Warning,
            )

        run_on_main_thread(_show_warning)
