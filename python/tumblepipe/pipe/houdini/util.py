import json

from tumblepipe.config.timeline import BlockRange, FrameRange
from tumblepipe.util.uri import Uri

import hou

###############################################################################
# Helper functions
###############################################################################

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

def iter_scene(prim, predicate):
    if not prim.IsActive(): return
    for subprim in prim.GetChildren():
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

def set_frame_range(frame_range: FrameRange):
    hou.playbar.setFrameRange(
        frame_range.start_frame - frame_range.start_roll,
        frame_range.end_frame + frame_range.end_roll
    )
    hou.playbar.setPlaybackRange(
        frame_range.start_frame,
        frame_range.end_frame
    )
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

def get_source_department(inputs: list[dict], department_order: list[str]) -> str | None:
    """
    Find the source department (first shot department) from inputs array.

    Extracts all shot department entries from inputs and returns the one
    that appears earliest in the pipeline department order.

    Args:
        inputs: List of input dicts with 'uri' and 'department' keys
        department_order: List of department names in pipeline order

    Returns:
        The source department name, or None if no shot entries found
    """
    # Extract shot department entries (URIs starting with entity:/shots/)
    shot_depts = [
        inp['department'] for inp in inputs
        if inp.get('uri', '').startswith('entity:/shots/')
    ]
    if not shot_depts:
        return None

    # Return the one earliest in pipeline order
    for dept in department_order:
        if dept in shot_depts:
            return dept

    # Fallback to first shot department found
    return shot_depts[0]

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

def list_dropped_asset_prims(root):
    """Return prim paths that sit beside a real asset but carry no metadata.

    An asset's parent (its category prim) is proven to be a category by the
    presence of at least one metadata-carrying child. Any *other* Scope/Xform
    child of that same parent that lacks metadata is an asset whose customData was
    dropped somewhere between import and export: because list_assets() only
    sees prims with metadata, such a prim silently vanishes from the export
    and from every downstream import. The classic trigger is multi-instance
    duplication (the base prim keeps its metadata but the duplicated instances
    don't) or a layerbreak stripping the authored customData.

    Mirrors iter_scene's traversal: we stop at the first metadata boundary, so
    an asset's own internal sub-Scopes (e.g. the 'mtl' material scope) are
    never mistaken for dropped assets.

    Prims carrying the 'inlined' marker (see mark_inlined) are deliberately
    baked into the export by an import node in 'inline' mode — they are
    neither drops nor descended into.
    """
    dropped = []

    def walk(prim):
        # Categories and asset roots compose as Scope OR Xform depending on
        # which layer wins (import nodes author Scopes; asset exports type
        # them Xform via set_kinds) — accept both or Xform-typed assets
        # become invisible to the guard.
        scope_children = [
            child for child in prim.GetChildren()
            if child.GetTypeName() in ('Scope', 'Xform')
        ]
        has_asset = any(get_metadata(child) is not None for child in scope_children)
        if has_asset:
            # This prim is a category: any metadata-less Scope sibling of a
            # real asset is a drop. Don't descend past this boundary.
            dropped.extend(
                str(child.GetPath())
                for child in scope_children
                if get_metadata(child) is None and not is_inlined(child)
            )
            return
        # No asset here yet — keep looking deeper for category scopes.
        for child in scope_children:
            if is_inlined(child): continue
            walk(child)

    walk(root)
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

def get_selected_lop_node():
    """Get the currently selected LOP node, if any."""
    selected = hou.selectedNodes()
    for node in selected:
        if node.type().category().name() != 'Lop':
            continue
        try:
            stage = node.stage()
            if stage is not None:
                return node
        except:
            continue
    return None

def get_display_flag_lop_node():
    """Get the LOP node with the display flag set, if any."""
    try:
        # Find all LOP networks in the scene
        lop_networks = hou.nodeType(hou.lopNodeTypeCategory(), "lopnet").instances()
        for network in lop_networks:
            display_node = network.displayNode()
            if display_node is not None:
                try:
                    stage = display_node.stage()
                    if stage is not None:
                        return display_node
                except:
                    continue

        # Also check /stage context directly
        stage_context = hou.node("/stage")
        if stage_context is not None:
            display_node = stage_context.displayNode()
            if display_node is not None:
                try:
                    stage = display_node.stage()
                    if stage is not None:
                        return display_node
                except:
                    pass
    except:
        pass
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