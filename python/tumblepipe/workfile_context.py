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

A provider function that fails raises: ``tumbletrove.context`` is the
boundary that logs a failing provider and carries on with "don't know", so
swallowing here as well would only hide which provider broke and why.
"""

from __future__ import annotations

import os
from pathlib import Path


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
    from tumblepipe.pipe.paths.workspace import get_workfile_context
    ctx = get_workfile_context(Path(hip_path))
    if ctx is None:
        return None
    return {
        "department": ctx.department_name,
        "entity": entity_path(ctx.entity_uri),
        "version": ctx.version_name,
    }


def project_departments() -> list[str] | None:
    """The project's enabled departments, assets then shots, or ``None``.

    ``None`` (a project with no enabled department) lets TumbleTrove fall
    back to its standard list rather than offer an empty picker.
    """
    from tumblepipe.config.department import list_department_names
    names: list[str] = []
    for context in _DEPARTMENT_CONTEXTS:
        for name in list_department_names(context, include_disabled=False):
            if name not in names:
                names.append(name)
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


def register() -> None:
    """Register with ``tumbletrove.context``."""
    from tumbletrove import context

    context.register_provider(
        PROVIDER_NAME,
        context=workfile_context,
        departments=project_departments,
        project_dir=project_dir,
        stock_dir=stock_dir,
    )
