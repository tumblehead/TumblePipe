"""TumblePipe as TumbleTrove's workfile-context provider.

NodePilot and Radial follow the department the artist is in, and the asset
or shot. They read it through ``tumbletrove.context`` and never import
TumblePipe (tumbletrove/TumblePipe#10, tumbletrove/asset-browser#24). TumblePipe is
the one that knows the workfile, so it answers for it:

- **context**: the ``context.json`` beside the open .hip
  (:func:`tumblepipe.pipe.paths.workspace.get_workfile_context`), as
  department, entity path and version. A plain scene answers ``None``.
- **departments**: the project's own, assets then shots, in pool order.
- **project_dir**: ``$TH_CONFIG_PATH/<kind>``, so a lead's "save for this
  project" lands in the project config beside ``db/``.
- **stock_dir**: ``<package>/stock/<kind>``, the department radials
  TumblePipe ships. They are the lowest layer, below every package, so
  TumbleRig's rig radial replaces the stock one. They are handed over
  here, and not declared as a package contribution
  (``package.register(..., contributions=...)``), because contributions
  are the *package* layer, where the stock rig menu would tie with
  TumbleRig's and the package names' order would pick.

Everything is best-effort. Without ``tumbletrove.context`` (TumbleTrove
older than the release carrying #24) nothing registers, and a provider
function that fails answers "don't know" instead of raising into
TumbleTrove's scene callbacks.
"""

from __future__ import annotations

import inspect
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: The provider name TumbleTrove reports as ``Context.source``.
PROVIDER_NAME = "tumblepipe"

#: Department contexts, in the order a picker lists them.
_DEPARTMENT_CONTEXTS = ("assets", "shots")


def package_root() -> Path:
    """The package root (the directory holding ``hpm.toml``)."""
    return Path(__file__).resolve().parents[2]


def entity_path(entity_uri) -> str:
    """The entity as TumbleTrove's ``when.entity`` globs see it.

    ``entity:/assets/char/hero`` -> ``assets/char/hero``. Any other purpose
    keeps it as the first segment (``groups:/shots/g1`` -> ``groups/shots/g1``)
    so a group workfile never matches an ``assets/*`` or ``shots/*`` glob
    meant for a single entity.
    """
    segments = list(entity_uri.segments)
    if entity_uri.purpose != "entity":
        segments.insert(0, entity_uri.purpose)
    return "/".join(segments)


def workfile_context(hip_path) -> dict | None:
    """The open workfile's department, entity and version, or ``None``."""
    if not hip_path:
        return None
    try:
        from tumblepipe.pipe.paths.workspace import get_workfile_context
        ctx = get_workfile_context(Path(hip_path))
    except Exception:
        logger.debug("no workfile context for %s", hip_path, exc_info=True)
        return None
    if ctx is None:
        return None
    return {
        "department": ctx.department_name,
        "entity": entity_path(ctx.entity_uri),
        "version": ctx.version_name,
    }


def project_departments() -> list[str] | None:
    """The project's enabled departments, assets then shots, or ``None``.

    ``None`` (no project, unreadable config) lets TumbleTrove fall back to
    its standard list rather than offer an empty picker.
    """
    try:
        from tumblepipe.config.department import list_department_names
        names: list[str] = []
        for context in _DEPARTMENT_CONTEXTS:
            for name in list_department_names(context, include_disabled=False):
                if name not in names:
                    names.append(name)
    except Exception:
        logger.debug("could not list the project's departments", exc_info=True)
        return None
    return names or None


def project_dir(kind: str) -> Path | None:
    """Where project-level files of *kind* live: the project config."""
    config = os.environ.get("TH_CONFIG_PATH", "").strip()
    if not config:
        return None
    return Path(config) / kind


def stock_dir(kind: str) -> Path | None:
    """The files of *kind* TumblePipe ships as stock, if it ships any."""
    folder = package_root() / "stock" / kind
    return folder if folder.is_dir() else None


def register() -> bool:
    """Register with ``tumbletrove.context``. ``False`` when it isn't there.

    ``stock_dir`` is passed only to a TumbleTrove that takes it, so an
    earlier build of the context API still gets the context, departments
    and project folder.
    """
    try:
        from tumbletrove import context
    except ImportError:
        return False
    if not hasattr(context, "register_provider"):
        return False

    kwargs = {
        "context": workfile_context,
        "departments": project_departments,
        "project_dir": project_dir,
    }
    try:
        accepted = inspect.signature(context.register_provider).parameters
    except (TypeError, ValueError):
        accepted = {}
    if "stock_dir" in accepted:
        kwargs["stock_dir"] = stock_dir
    context.register_provider(PROVIDER_NAME, **kwargs)
    return True
