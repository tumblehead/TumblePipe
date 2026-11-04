"""The "Asset" place a dropped texture set can go to: a publish.

The asset browser places a Poly Haven or AmbientCG texture set from its
library download, or hands it to a destination some catalog offers
(``Catalog.texture_destinations``). The pipeline offers the open workfile's
asset: choosing it publishes the set as a version of one of the asset's
texture sets (``tumblepipe.pipe.textures``, under
``<export>/<asset segments>/_textures/<set>/vNNNN``), and the nodes read
that version.

Node parameters spell it ``$TH_EXPORT_PATH/...``, the variable every
pipeline session and farm job carries, so a scene does not hold one
machine's absolute path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

from tumblepipe.pipe.textures import EXPORT_VAR, portable_root  # noqa: F401


def asset_texture_root(hip_path: Path):
    """``(entity uri, the asset's texture sets folder)`` for the asset
    workfile at *hip_path*, or ``None`` when it is not one (a shot's, a hip
    from anywhere else, an untitled scene)."""
    from tumblepipe.pipe import textures as T
    from tumblepipe.pipe.paths.workspace import get_workfile_context

    context = get_workfile_context(Path(hip_path))
    if context is None:
        return None
    segments = list(context.entity_uri.segments)
    if not segments or segments[0] != "assets":
        return None
    return context.entity_uri, T.sets_dir(context.entity_uri)


def publish_library_set(entity_uri, tex_map: dict, asset_id: str) -> dict:
    """Publish a library texture set (role -> downloaded path) as a version
    of the asset's set named after *asset_id*; role -> the published map,
    spelled ``$TH_EXPORT_PATH/...``.

    Dropping the same set again reuses the newest version when it came
    from the same files (``matching_version``), so it is not republished.
    """
    from tumblepipe.api import get_user_name
    from tumblepipe.pipe import textures as T
    from tumblepipe.pipe.context import announce_change
    from tumblepipe.pipe.provenance import scene_producer
    from tumblepipe.util import events

    # A set spelled the same but for case is the same set (one folder on
    # Windows and macOS): publish into it rather than be refused.
    set_name = T.existing_spelling(T.sets_dir(entity_uri), T.name_from(asset_id))
    folder = T.set_dir(set_name, entity_uri)
    version = T.matching_version(folder, tex_map)
    if version is None:
        version = T.publish_set(
            folder, set_name, tex_map, owner=entity_uri, source="library",
            user=get_user_name(), producer=scene_producer(None))
        announce_change(events.EXPORT, entity_uri, f"textures/{set_name}")
        log.info("published %s as %s", set_name, version)
    return {role: T.portable_path(path)
            for role, path in T.version_maps(version).items()}


def texture_destinations() -> list:
    """The open asset workfile's asset, as the browser takes it: choosing
    it publishes the dropped set as a version of the asset's texture set
    (``publish_library_set``) and the nodes read that version."""
    import hou
    from tumbletrove.asset_browser.core.texture_destination import (
        TextureDestination,
    )

    if "publish" not in getattr(TextureDestination, "__dataclass_fields__", {}):
        # A tumbletrove before 0.54 can only copy; a copy beside the
        # publishes would be the unversioned folder this replaced.
        return []
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
        id="tumblepipe_asset", label=f"Asset ({name}): publish", root=root,
        portable=portable_root(root, os.environ.get(EXPORT_VAR)),
        publish=lambda tex_map, asset_id: publish_library_set(
            entity_uri, tex_map, asset_id),
    )]
