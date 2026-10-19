"""Houdini-side bridging for the Pipeline catalog.

- :class:`ProjectActivator` — switch ``TH_*`` env + the tumblepipe
  ``default_client`` singleton to a given project, with a fast path
  for the no-op case and a one-shot warning when the activated project
  diverges from Houdini's launch project.
- :func:`session_nc_type` — the license-driven workfile extension.

Reporting a failure to the artist and hopping to the main thread live in
:mod:`tumblepipe.pipe.houdini.ui.report`, shared with the nodes.

Lives in its own module so the catalog file isn't carrying 70+ lines
of env/singleton plumbing inline.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .projects import ProjectConfig

from tumblepipe.pipe.houdini.ui.report import run_on_main_thread

log = logging.getLogger(__name__)


def session_nc_type() -> str | None:
    """The license-driven workfile extension type for this Houdini session.

    ``'nc'`` for Apprentice/ApprenticeHD/Education (.hipnc), ``'lc'`` for Indie
    (.hiplc), ``None`` for commercial (.hip). Pass to ``next_hip_file_path`` so
    the saved file matches Ctrl+S — hardcoding ``None`` makes Houdini rewrite the
    extension on a non-commercial license, landing the file where the pipeline
    didn't record it.

    Education is ``.hipnc`` by observation (a Houdini Education session saves a
    requested ``.hip`` as ``.hipnc``), even though SideFX's own Python helper
    (``houpythonportion.sceneFileExtension``) files it under ``.hip`` in 22.0 —
    so this is not delegated to that helper. This is only a *prediction*
    either way; :func:`tumblepipe.pipe.houdini.workfile_save.save_hip_file` records whatever
    Houdini actually wrote, so a category missed here degrades to a log
    warning rather than a sidecar that points at a file that does not exist.
    """
    import hou
    category = hou.licenseCategory()
    if category in (
        hou.licenseCategoryType.Apprentice,
        hou.licenseCategoryType.ApprenticeHD,
        hou.licenseCategoryType.Education,
    ):
        return "nc"
    if category == hou.licenseCategoryType.Indie:
        return "lc"
    return None


class ProjectActivator:
    """Tracks and switches the user-active pipeline project.

    A single instance lives on :class:`PipelineCatalog`. Calling
    :meth:`activate` is idempotent on the fast path (env vars already
    match the cached active project); the slow path mutates
    ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` / ``TH_EXPORT_PATH`` and
    resets tumblepipe's ``default_client`` singleton. Pipeline modules
    reach the client through the ``tumblepipe.api.api`` proxy, which
    forwards to the current singleton, so nothing per-module needs
    patching on a switch.

    The launch project (env at catalog construction) is captured so
    the first activation that switches away can surface a one-shot
    "switched pipeline context" status message — emitted via
    :func:`run_on_main_thread` so it's safe to call from any thread.
    """

    def __init__(self) -> None:
        # The project_path of the project whose env is currently bound.
        # ``None`` means "no activation has happened yet" — the first
        # call always runs the slow path even if the project matches the
        # launch env, to bind the client singleton to the active project.
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
        from tumblepipe.api import reset_default_client, default_client
        reset_default_client()
        # Warm the new project's Client now so the first downstream op
        # doesn't eat the ~100ms construction - and so a project whose
        # config cannot load fails here, at the switch, instead of the
        # action carrying on against the previous project's client.
        # Modules read the client via the tumblepipe.api.api proxy, which
        # always forwards to the current singleton, so a switch needs no
        # per-module patching.
        default_client()
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
