"""Pipeline catalog — factory entry point.

Browses production assets and shots from any number of registered
Tumblehead projects. Projects are stored in
``~/.config/asset_browser/projects.json`` (see
:class:`asset_browser.core.projects.PipelineProjectRegistry`). Each entry
holds ``project_path`` and ``config_path``; the registry's
``pipeline_path`` field is ignored at runtime. The active TumblePipe
install is read from ``$TH_PIPELINE_PATH`` (set globally by hpm via
the package's ``[env]`` block) so it tracks hpm upgrades
automatically.

The ``TH_*`` env vars are authoritative for the launch session: every
:func:`create_catalog` call passes them through
:meth:`PipelineProjectRegistry.bootstrap_from_env`, which adds the env-driven
project on first run and refreshes its paths on subsequent runs if
they've changed (e.g. config dir renamed ``_config`` → ``_config2``).
Env paths that don't exist on disk are ignored with a warning rather
than registered — a mistyped ``TH_PROJECT_PATH`` must not become a
persisted project that fails Client init on every later launch.
``projects.json`` is just an off-session cache so non-env-launched
sessions can browse the same projects.

TumblePipe declares this factory to TumbleTrove in
``tumblepipe.startup.register_package``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from tumbletrove.asset_browser.core.projects import PipelineProjectRegistry

from .catalog import PipelineCatalog
from .types import projects_json_path

log = logging.getLogger(__name__)


class ProjectNotConfigured(RuntimeError):
    """TumblePipe is installed on the launched project, but the project has no
    pipeline configuration (or the path it names does not exist).

    Raised from :func:`create_catalog` instead of returning ``None`` because
    ``None`` means "no pipeline here" and TumbleTrove treats it as a clean
    opt-out: the catalog just isn't there, and the artist reads an onboarding
    gap as a broken asset browser. A raising factory becomes a visible
    FailedCatalog whose message reaches the browser's status bar.
    """


def looks_like_project(project_path, config_path=None) -> bool:
    """True when *project_path* carries a TumblePipe config database.

    The marker is ``<config>/db/entity.json`` — the same one the native
    ``tt_setup`` / ``tt_prepare`` hooks use (``th_project_core::looks_like_project``),
    so the launch hook and the catalog agree on what "configured" means.
    """
    config = Path(config_path) if config_path else Path(project_path) / "_config"
    return (config / "db" / "entity.json").is_file()


def _unconfigured_error(project_path: str) -> ProjectNotConfigured:
    p = Path(project_path)
    if not p.is_dir():
        return ProjectNotConfigured(
            f"TumblePipe cannot find this project: TH_PROJECT_PATH points at "
            f"{project_path}, which does not exist. Check the path in the project's "
            f"settings in TumbleTrove Desktop, or that the share is reachable."
        )
    return ProjectNotConfigured(
        f"TumblePipe is installed, but this project has not been configured yet: "
        f"{project_path} has no _config/db/entity.json. Use Configure… on the "
        f"TumblePipe card in TumbleTrove Desktop to set the project up."
    )


def create_catalog():
    """Factory — named by TumblePipe's package registration.

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

    Returning ``None`` means "no pipeline configured here" — a normal
    outcome, not a failure. An actual failure is left to raise: TumbleTrove
    turns it into a visible FailedCatalog with the error attached, which is
    more useful than the swallow-and-log this used to do, and it can no longer
    stall a Houdini launch because the factory runs when the panel opens.
    """
    registry = PipelineProjectRegistry(projects_json_path())
    registry.load()
    # Add the env-driven project on first run, and refresh its
    # paths on subsequent runs if TH_* env vars have changed since
    # they were last cached. The env vars are authoritative for the
    # launch session — projects.json is just the off-session cache.
    registry.bootstrap_from_env()

    env_proj = os.environ.get("TH_PROJECT_PATH", "").strip()
    if env_proj:
        # Houdini was launched from a project that has TumblePipe installed.
        # If that project is not set up, say so — this is the case an artist
        # reads as "the asset browser is broken", not "no pipeline here".
        env_config = os.environ.get("TH_CONFIG_PATH", "").strip() or None
        if not looks_like_project(env_proj, env_config):
            raise _unconfigured_error(env_proj)
        env_name = Path(env_proj).name or "default"
        if env_name in registry.names:
            # Scope this session to the launch-project only.
            scoped = PipelineProjectRegistry(projects_json_path())
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
