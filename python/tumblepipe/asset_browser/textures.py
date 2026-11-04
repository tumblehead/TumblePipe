"""The "Asset" place a dropped texture set can be copied to.

The asset browser places a Poly Haven or AmbientCG texture set from its
library download, or from a copy in a folder some catalog offers
(``Catalog.texture_destinations``). The pipeline offers the open workfile's
asset: ``<export>/<asset segments>/textures``, beside the asset's
publishes, where the farm and every artist read it. Each set is copied into
its own ``<asset id>`` folder there.

Node parameters spell it ``$TH_EXPORT_PATH/...``, the variable every
pipeline session and farm job carries, so a scene does not hold one
machine's absolute path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

#: The export variable texture paths are written with.
EXPORT_VAR = "TH_EXPORT_PATH"


def portable_root(root: Path, export_base: str | None) -> str:
    """*root* as ``$TH_EXPORT_PATH/<rest>`` when under *export_base*,
    else empty (the absolute path is written)."""
    if not export_base:
        return ""
    base = str(export_base).replace("\\", "/").rstrip("/")
    text = str(root).replace("\\", "/")
    if base and text.casefold().startswith(base.casefold() + "/"):
        return f"${EXPORT_VAR}{text[len(base):]}"
    return ""


def asset_texture_root(hip_path: Path):
    """``(entity uri, textures folder)`` for the asset workfile at
    *hip_path*, or ``None`` when it is not one (a shot's, a hip from
    anywhere else, an untitled scene)."""
    from tumblepipe.api import api
    from tumblepipe.pipe.paths.workspace import get_workfile_context
    from tumblepipe.util.uri import Uri

    context = get_workfile_context(Path(hip_path))
    if context is None:
        return None
    segments = list(context.entity_uri.segments)
    if not segments or segments[0] != "assets":
        return None
    root = api.storage.resolve(Uri.parse("export:/") / segments)
    return context.entity_uri, Path(root) / "textures"


def texture_destinations() -> list:
    """The open asset workfile's texture folder, as the browser takes it."""
    import hou
    from tumbletrove.asset_browser.core.texture_destination import (
        TextureDestination,
    )

    try:
        if hou.hipFile.isNewFile():
            return []
        found = asset_texture_root(Path(hou.hipFile.path()))
    except Exception:
        # A workfile whose context cannot be read offers nothing here; the
        # drop still places from the library or the scene.
        log.debug("no asset texture destination", exc_info=True)
        return []
    if found is None:
        return []
    entity_uri, root = found
    name = list(entity_uri.segments)[-1]
    return [TextureDestination(
        id="tumblepipe_asset", label=f"Asset ({name})", root=root,
        portable=portable_root(root, os.environ.get(EXPORT_VAR)),
    )]
