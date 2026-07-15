import json
import logging
import os
from pathlib import Path

from pxr import Usd, UsdGeom

from tumblepipe.config.timeline import BlockRange, FrameRange
from tumblepipe.util.uri import Uri

import hou

logger = logging.getLogger(__name__)

def apply_placement_op_order(prim) -> bool:
    """Author the xformOpOrder that applies composed placement op values.

    The order derivation lives in pipe.usd.composed_placement_op_order
    (shared with the static batch-submit flatten); see its docstring for
    the why. Called on each re-established instance prim (import_asset's
    metadata script, import_shot's duplicates subnet) after it is
    (re)created. Returns True when an order was authored; False when no
    placement composed (the caller decides the fallback).
    """
    from tumblepipe.pipe.usd import composed_placement_op_order

    order = composed_placement_op_order(prim)
    if not order:
        return False
    UsdGeom.Xformable(prim).GetXformOpOrderAttr().Set(order)
    return True

###############################################################################
# Helper functions
###############################################################################

# H22 flipped the sopcreate/sopimport 'Layer Save Path' default to ON with
# $HIP/usd/$OS.usd. A save-path'ed layer is written NEXT TO THE WORKFILE by
# the export ROP (savestyle=flattenimplicitlayers) instead of flattening into
# the published layer, which then composes empty on every other machine.
_LAYER_SAVE_PATH_PARMS = (
    ('enable_savepath', 'savepath'),  # sopcreate / sopimport / sopmodify
)

def disable_layer_save_path(node) -> None:
    """Turn off a LOP node's layer save path so its layer flattens on export."""
    for enable_name, path_name in _LAYER_SAVE_PATH_PARMS:
        enable_parm = node.parm(enable_name)
        if enable_parm is None:
            continue
        enable_parm.set(False)
        path_parm = node.parm(path_name)
        if path_parm is not None:
            path_parm.set('')

def list_to_menu(items: list[str]) -> list[str]:
    result = []
    for item in items:
        result.append(item)
        result.append(item)
    return result

def list_to_checked_menu(items: list[str], checked: list[str]) -> list[str]:
    result = list()
    for item in items:
        result.append(item)
        result.append(f'{item} <--' if item in checked else item)
    return result

def _iter_prim_children(prim):
    """Children including instance proxies and undefined (over) prims.

    GetChildren() skips both, and each hides tracked assets from every
    scrape built on this traversal: instanceable copies (e.g. Duplicate
    LOP output) hold their assets in instance proxies, and an asset
    imported via a department-layer import composes as a typeless *over*
    once the layerbreak discards its def — the customData survives on
    the over, and the export scrape must still see it so staging can
    re-reference the asset's real definition downstream.
    """
    predicate = Usd.TraverseInstanceProxies(
        Usd.PrimIsActive & Usd.PrimIsLoaded & ~Usd.PrimIsAbstract
    )
    return prim.GetFilteredChildren(predicate)

def iter_scene(prim, predicate):
    if not prim.IsActive(): return
    for subprim in _iter_prim_children(prim):
        if predicate(subprim):
            yield subprim
            continue
        for subsubprim in iter_scene(subprim, predicate):
            yield subsubprim

def get_frame_range() -> FrameRange:
    first_frame, last_frame = hou.playbar.frameRange()
    start_frame, end_frame = hou.playbar.playbackRange()
    start_roll = round(first_frame - start_frame)
    end_roll = round(end_frame - last_frame)
    return FrameRange(
        round(start_frame),
        round(end_frame),
        start_roll,
        end_roll
    )

def set_frame_range(frame_range: FrameRange, preserve_current_frame: bool = False):
    """Apply a shot frame range to the playbar.

    By default the playhead snaps to the shot's start frame. Pass
    preserve_current_frame=True to leave the playhead alone when it already
    sits inside the new range (rolls included) and only snap it to the start
    when the current frame falls outside — this keeps a re-import from yanking
    the artist back to frame one every time.
    """
    range_start = frame_range.start_frame - frame_range.start_roll
    range_end = frame_range.end_frame + frame_range.end_roll
    hou.playbar.setFrameRange(range_start, range_end)
    hou.playbar.setPlaybackRange(
        frame_range.start_frame,
        frame_range.end_frame
    )
    if preserve_current_frame and range_start <= hou.frame() <= range_end:
        return
    hou.setFrame(
        frame_range.start_frame
    )

def set_block_range(block_range: BlockRange):
    hou.playbar.setFrameRange(
        block_range.first_frame,
        block_range.last_frame
    )
    hou.playbar.setPlaybackRange(
        block_range.first_frame,
        block_range.last_frame
    )
    hou.setFrame(
        block_range.first_frame
    )

def set_fps(fps: int):
    """Set Houdini session FPS.

    Only affects playback speed - keeps the frame range, frame count, and
    keyframes exactly where they are.

    preserve_frame_start=True is load-bearing: hou.setFps defaults it to
    False, which holds the start *time* (seconds) constant and therefore
    SHIFTS the start frame by the fps ratio. Paired with
    modify_frame_count=False (which keeps the frame *count* fixed) that
    produces the classic "start/end frames wrong but the number of frames
    is right" symptom whenever the session fps differs from the shot fps
    (e.g. opening a 25fps shot in a fresh 24fps session). preserve_frame_start
    pins the start frame, and with modify_frame_count=False the end frame is
    pinned too, so the range survives the fps change untouched.
    """
    hou.setFps(
        fps,
        modify_frame_count=False,
        preserve_keyframes=True,
        preserve_frame_start=True,
    )

class _UpdateModeContext:
    def __init__(self, mode):
        self.mode = mode
        self.previous_mode = None
        self.has_ui = False

    def __enter__(self):
        self.has_ui = hou.isUIAvailable()
        if not self.has_ui: return
        self.previous_mode = hou.updateModeSetting()
        hou.setUpdateMode(self.mode)

    def __exit__(self, *args):
        if not self.has_ui: return
        hou.setUpdateMode(self.previous_mode)
        self.has_ui = False

def update_mode(mode):
    return _UpdateModeContext(mode)

###############################################################################
# Tracked primitives
###############################################################################

def get_metadata(prim):
    if prim is None or not prim.IsValid(): return None
    if not prim.HasMetadata('customData'): return None
    _metadata = prim.GetMetadata('customData')
    if _metadata is None: return None
    if 'uri' not in _metadata: return None
    metadata = _metadata.copy()
    metadata['inputs'] = json.loads(metadata['inputs'])
    return metadata

def set_metadata(prim, metadata):
    _metadata = metadata.copy()
    _metadata['inputs'] = json.dumps(_metadata['inputs'])
    prim.SetMetadata('customData', _metadata)

def add_metadata_input(metadata, input_datum):
    input_data = set(map(json.dumps, metadata['inputs']))
    input_data.add(json.dumps(input_datum))
    metadata['inputs'] = list(map(json.loads, input_data))

def remove_metadata(prim):
    prim.ClearMetadata('customData')

def mark_inlined(prim):
    """Replace pipeline metadata with an 'inlined' marker.

    An inlined asset (import node in 'inline' import mode) is deliberately
    baked into the export instead of being re-referenced, so its customData
    must carry no 'uri' (or the export scrape would re-add it as a sublayer)
    while still telling list_dropped_asset_prims the missing metadata is
    intentional, not a drop.
    """
    prim.SetMetadata('customData', {'inlined': True})

def is_inlined(prim):
    if prim is None or not prim.IsValid(): return False
    if not prim.HasMetadata('customData'): return False
    _metadata = prim.GetMetadata('customData')
    if _metadata is None: return False
    return bool(_metadata.get('inlined', False))

def is_asset(prim):
    metadata = get_metadata(prim)
    if metadata is None: return False
    uri = Uri.parse_unsafe(metadata['uri'])
    return uri.segments[0] == 'assets' if len(uri.segments) > 0 else False

def is_camera(prim):
    return prim.GetTypeName() == 'Camera'

def is_light(prim):
    return prim.GetTypeName().endswith('Light')

def is_render_var(prim):
    return prim.GetTypeName() == 'RenderVar'

def list_assets(root):
    """
    List all asset metadata dicts in the stage, including prim paths.

    Walks the scene tree looking for prims with asset metadata in customData.

    Returns list of dicts with:
    - 'prim_path': The scene prim path (e.g., /CHAR/mom)
    - 'metadata': The asset metadata dict (uri, instance, inputs, etc.)
    """
    def get_asset_info(prim):
        metadata = get_metadata(prim)
        if metadata is None:
            return None
        return {
            'prim_path': str(prim.GetPath()),
            'metadata': metadata
        }

    results = []
    for prim in iter_scene(root, is_asset):
        info = get_asset_info(prim)
        if info is not None:
            results.append(info)
    return results

def _normcased_roots(roots) -> list[str]:
    """Resolve + normcase the pipeline roots once for containment tests.

    Unreadable roots are dropped, never raised on — a root that cannot be
    resolved simply stops matching.
    """
    normed = []
    for root in roots:
        try:
            normed.append(os.path.normcase(str(Path(root).resolve())))
        except OSError:
            continue
    return normed


def _path_under_roots(candidate: str, normed_roots) -> bool:
    """True if ``candidate`` resolves inside one of ``normed_roots``.

    Case-insensitive and separator-aware so a ``P:\\proj\\export`` root
    matches a sublayer resolved to ``P:/proj/export/...`` (Windows paths
    differ freely in case and slash direction; ``is_relative_to`` is
    purely lexical and case-sensitive, so we normcase both sides).
    """
    if not candidate:
        return False
    try:
        target = os.path.normcase(str(Path(candidate).resolve()))
    except OSError:
        return False
    for root in normed_roots:
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def _prim_pulls_from_roots(prim, normed_roots) -> bool:
    """True if any layer composing into ``prim`` resolves under a pipeline root.

    ``GetPrimStack`` returns every PrimSpec contributing to the prim across
    sublayers, references and payloads, so this recognises an imported
    asset however its layer arrived — TumblePipe's import nodes sublayer
    the staged file, but a raw Reference/Payload LOP is covered too. On a
    live stage each contributing layer carries a resolved ``realPath``; an
    ``entity:/assets|shots/`` ``identifier`` is matched as a fallback for a
    layer not yet resolved to a real path.

    Empty ``normed_roots`` yields False — the caller decides what an
    unknown pipeline root means (list_dropped_asset_prims falls back to
    flagging every metadata-less sibling).
    """
    if not normed_roots:
        return False
    for spec in prim.GetPrimStack():
        layer = getattr(spec, 'layer', None)
        if layer is None:
            continue
        if _path_under_roots(getattr(layer, 'realPath', '') or '', normed_roots):
            return True
        identifier = getattr(layer, 'identifier', '') or ''
        if 'entity:/assets/' in identifier or 'entity:/shots/' in identifier:
            return True
    return False


def list_dropped_asset_prims(root, ignore_prim_paths=(), pipeline_roots=()):
    """Return prim paths that sit beside a real asset but carry no metadata.

    An asset's parent (its category prim) is proven to be a category by the
    presence of at least one metadata-carrying child. Any *other* Scope/Xform
    child of that same parent that lacks metadata is an asset whose customData was
    dropped somewhere between import and export: because list_assets() only
    sees prims with metadata, such a prim silently vanishes from the export
    and from every downstream import. Triggers include a layerbreak
    stripping the authored customData, a stale import node whose metadata
    script never cooked against the current entity, or (historically) the
    scrape itself skipping instance proxies — see _iter_prim_children.

    Mirrors iter_scene's traversal: we stop at the first metadata boundary, so
    an asset's own internal sub-Scopes (e.g. the 'mtl' material scope) are
    never mistaken for dropped assets.

    Three kinds of metadata-less sibling are NOT drops:
    - a grouping prim whose subtree holds metadata-carrying assets (artist
      set-dressing Xforms, e.g. Duplicate LOP destination prims) — we
      descend into it and keep checking instead;
    - a prim listed in ignore_prim_paths (the exporting entity's own root,
      which only gets tagged when some *other* workfile imports it) — also
      descended into, not flagged;
    - a prim that composes from NO pipeline export layer (see
      pipeline_roots) — geometry the artist authored or cached directly
      into the workfile. A real dropped asset still pulls its geometry
      from the pipeline export tree via the import node's sublayer; only
      its customData tag went missing. Artist-added geometry never had a
      pipeline layer, so it is a supported addition to the hierarchy, not
      a drop, and must never block the export. This is the check that
      distinguishes "an asset that lost its metadata" (block) from "a new
      prim the artist modelled directly" (allow).

    Prims carrying the 'inlined' marker (see mark_inlined) are deliberately
    baked into the export by an import node in 'inline' mode — they are
    neither drops nor descended into.

    pipeline_roots: filesystem roots (the resolved ``export:/`` tree)
    against which a metadata-less prim's composed layers are tested. When
    EMPTY the pipeline test is skipped and every metadata-less sibling is
    flagged (the historical, fail-closed behaviour) — the export caller
    resolves the root and passes it; an unresolvable root degrades to
    over-blocking rather than risking a silent drop.
    """
    ignored = set(ignore_prim_paths)
    normed_roots = _normcased_roots(pipeline_roots)
    dropped = []
    allowed = []

    def subtree_has_asset(prim):
        def has_metadata(p): return get_metadata(p) is not None
        return next(iter_scene(prim, has_metadata), None) is not None

    def walk(prim):
        # Categories and asset roots compose as Scope OR Xform depending on
        # which layer wins (import nodes author Scopes; asset exports type
        # them Xform via set_kinds) — accept both or Xform-typed assets
        # become invisible to the guard.
        scope_children = [
            child for child in _iter_prim_children(prim)
            if child.GetTypeName() in ('Scope', 'Xform')
        ]
        has_asset = any(get_metadata(child) is not None for child in scope_children)
        if not has_asset:
            # No asset here yet — keep looking deeper for category scopes.
            for child in scope_children:
                if is_inlined(child): continue
                walk(child)
            return
        # This prim is a category: a metadata-less Scope/Xform sibling of a
        # real asset is a drop, unless it is ignored or merely groups
        # tracked assets deeper down. Don't descend past asset boundaries.
        for child in scope_children:
            if get_metadata(child) is not None: continue
            if is_inlined(child): continue
            if str(child.GetPath()) in ignored or subtree_has_asset(child):
                walk(child)
                continue
            if normed_roots and not _prim_pulls_from_roots(child, normed_roots):
                # Composes from no pipeline export layer -> artist-authored
                # geometry, a supported addition, not a dropped asset.
                allowed.append(str(child.GetPath()))
                continue
            dropped.append(str(child.GetPath()))

    walk(root)
    if allowed:
        logger.info(
            "export drop-guard: allowed %d metadata-less prim(s) that "
            "compose from no pipeline export layer (artist-added "
            "geometry, not dropped assets): %s",
            len(allowed), ", ".join(allowed)
        )
    return dropped

def get_shot_metadata(stage) -> dict | None:
    """Get shot metadata from stage-level customLayerData.

    Returns shot metadata dict, or None if not present.
    """
    if stage is None: return None
    layer_data = stage.GetRootLayer().customLayerData
    if 'shot_metadata' not in layer_data: return None
    metadata = dict(layer_data['shot_metadata'])
    metadata['inputs'] = json.loads(metadata['inputs'])
    return metadata

def set_shot_metadata(stage, metadata):
    """Set shot metadata on stage-level customLayerData."""
    _metadata = metadata.copy()
    _metadata['inputs'] = json.dumps(_metadata['inputs'])
    layer_data = dict(stage.GetRootLayer().customLayerData)
    layer_data['shot_metadata'] = _metadata
    stage.GetRootLayer().customLayerData = layer_data

def list_cameras(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_camera)))

def list_lights(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_light)))

def list_render_vars(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_render_var)))

def get_frame_range_from_stage(stage) -> FrameRange | None:
    """Extract frame range from USD stage shot metadata.

    Reads from stage-level customLayerData.
    """
    metadata = get_shot_metadata(stage)
    if metadata is None: return None
    return FrameRange(
        metadata['start_frame'],
        metadata['end_frame'],
        metadata['start_roll'],
        metadata['end_roll']
    )

def _stage_or_none(node):
    """The node's stage, or None if the stage fails to cook.

    Only hou.OperationFailed is treated as "no stage" — anything else
    raising here is a real bug and must surface.
    """
    try:
        return node.stage()
    except hou.OperationFailed:
        return None

def get_selected_lop_node():
    """Get the currently selected LOP node, if any."""
    selected = hou.selectedNodes()
    for node in selected:
        if node.type().category().name() != 'Lop':
            continue
        if _stage_or_none(node) is not None:
            return node
    return None

def get_display_flag_lop_node():
    """Get the LOP node with the display flag set, if any."""
    # Find all LOP networks in the scene
    lop_networks = hou.nodeType(hou.lopNodeTypeCategory(), "lopnet").instances()
    for network in lop_networks:
        display_node = network.displayNode()
        if display_node is not None and _stage_or_none(display_node) is not None:
            return display_node

    # Also check /stage context directly
    stage_context = hou.node("/stage")
    if stage_context is not None:
        display_node = stage_context.displayNode()
        if display_node is not None and _stage_or_none(display_node) is not None:
            return display_node
    return None

###############################################################################
# USD Prim Path Helpers
###############################################################################

def uri_to_prim_path(uri: Uri) -> str:
    """
    Convert entity URI to USD prim path by omitting first segment.

    Examples:
        entity:/shots/seq010/shot0010 -> /seq010/shot0010
        entity:/assets/char/mom -> /char/mom

    Args:
        uri: Entity URI

    Returns:
        USD prim path string
    """
    segments = uri.segments[1:]
    return '/' + '/'.join(segments) if segments else '/'


def uri_to_parent_prim_path(uri: Uri) -> str:
    """
    Convert entity URI to parent-level prim path.

    Returns all URI segments except the entity type and last segment.

    Examples:
        entity:/assets/char/mom -> /char
        entity:/assets/props/furniture/chair -> /props/furniture

    Args:
        uri: Entity URI

    Returns:
        USD prim path for the parent level
    """
    if len(uri.segments) < 3:
        return '/'
    parent_segments = uri.segments[1:-1]
    return '/' + '/'.join(parent_segments)