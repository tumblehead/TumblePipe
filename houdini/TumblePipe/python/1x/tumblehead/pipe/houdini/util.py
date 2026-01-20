import json

from tumblehead.config.timeline import BlockRange, FrameRange
from tumblehead.util.uri import Uri

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

    Only affects playback speed - does not modify frame count or move keyframes.
    """
    hou.setFps(fps, modify_frame_count=False, preserve_keyframes=True)

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

def is_asset(prim):
    metadata = get_metadata(prim)
    if metadata is None: return False
    uri = Uri.parse_unsafe(metadata['uri'])
    return uri.segments[0] == 'assets' if len(uri.segments) > 0 else False

def is_shot(prim):
    """Check if a prim contains shot metadata."""
    metadata = get_metadata(prim)
    if metadata is None: return False
    uri = Uri.parse_unsafe(metadata['uri'])
    return uri.segments[0] == 'shots' if len(uri.segments) > 0 else False

def is_camera(prim):
    return prim.GetTypeName() == 'Camera'

def is_light(prim):
    return prim.GetTypeName().endswith('Light')

def is_render_var(prim):
    return prim.GetTypeName() == 'RenderVar'

def list_assets(root):
    """
    List all asset metadata dicts in the stage, including prim paths.

    Returns list of dicts with:
    - 'prim_path': The metadata prim path (includes copy suffixes like /MiniFig1)
    - 'metadata': The asset metadata dict (uri, instance, inputs, etc.)
    """
    metadata_root = root.GetPrimAtPath('/_METADATA')
    if not metadata_root.IsValid():
        return []

    def get_asset_info(prim):
        metadata = get_metadata(prim)
        if metadata is None:
            return None
        return {
            'prim_path': str(prim.GetPath()),
            'metadata': metadata
        }

    results = []
    for prim in iter_scene(metadata_root, is_asset):
        info = get_asset_info(prim)
        if info is not None:
            results.append(info)
    return results

def list_shots(root):
    """List all shot metadata dicts in the stage."""
    metadata_root = root.GetPrimAtPath('/_METADATA')
    if not metadata_root.IsValid(): return []
    return list(map(get_metadata, iter_scene(metadata_root, is_shot)))

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

    Returns first shot's frame range, or None if no shots found.
    """
    if stage is None: return None
    root = stage.GetPseudoRoot()
    shots = list_shots(root)
    if len(shots) == 0: return None

    shot = shots[0]  # Use first shot found
    return FrameRange(
        shot['start_frame'],
        shot['end_frame'],
        shot['start_roll'],
        shot['end_roll']
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

def _sanitize_prim_segment(segment: str) -> str:
    """Always prefix with underscore for consistent metadata prim naming."""
    if not segment:
        return segment
    return f'_{segment}'

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


def uri_to_metadata_prim_path(uri: Uri) -> str:
    """
    Convert entity URI to USD metadata prim path, keeping all segments.

    Examples:
        entity:/shots/seq010/shot0010 -> /_METADATA/_shots/_seq010/_shot0010
        entity:/assets/char/mom -> /_METADATA/_assets/_char/_mom

    Args:
        uri: Entity URI

    Returns:
        USD metadata prim path string
    """
    if len(uri.segments) == 0:
        return '/_METADATA'

    # Build path: /_METADATA/{all_segments}
    sanitized_segments = [_sanitize_prim_segment(s) for s in uri.segments]
    path_segments = ['', '_METADATA'] + sanitized_segments
    return '/'.join(path_segments)


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