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
``projects.json`` is just an off-session cache so non-env-launched
sessions can browse the same projects.

tumbletrove's catalog registry discovers this file via
``importlib.util.spec_from_file_location`` — it globs ``*.py`` files
only and skips underscore-prefixed names, so the catalog must live as
a single top-level ``pipeline.py``. The actual :class:`PipelineCatalog`
implementation lives in :mod:`_pipeline_catalog`; this file stays a
small factory plus the ``sys.path`` tweak that lets the underscore-
prefixed companion modules import each other absolutely.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Prepend this directory to sys.path so the underscore-prefixed
# companion modules (``_pipeline_catalog`` and friends) can be
# imported absolutely. The discovery loader doesn't attach this module
# to a parent package, so relative imports fail with ImportError.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tumbletrove.asset_browser.core.projects import PipelineProjectRegistry  # noqa: E402

from _pipeline_catalog import PipelineCatalog  # noqa: E402
from _pipeline_types import projects_json_path  # noqa: E402

log = logging.getLogger(__name__)


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

    Fails closed: any exception in registry construction is logged and
    causes the catalog to be skipped. The asset browser invokes this on
    Houdini's main thread during startup, so a raised exception here
    would otherwise propagate up and stall Houdini load.
    """
    try:
        registry = PipelineProjectRegistry(projects_json_path())
        registry.load()
        # Add the env-driven project on first run, and refresh its
        # paths on subsequent runs if TH_* env vars have changed since
        # they were last cached. The env vars are authoritative for the
        # launch session — projects.json is just the off-session cache.
        registry.bootstrap_from_env()

        env_proj = os.environ.get("TH_PROJECT_PATH", "").strip()
        if env_proj:
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
    except Exception:
        log.exception("Pipeline catalog skipped — registry load failed")
        return None
