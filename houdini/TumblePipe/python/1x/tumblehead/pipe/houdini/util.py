import json

from tumblehead.config import BlockRange, FrameRange

import hou

###############################################################################
# Helper functions
###############################################################################

def list_to_menu(items):
    result = []
    for item in items:
        result.append(item)
        result.append(item)
    return result

def list_to_checked_menu(items, checked):
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

def get_frame_range():
    first_frame, last_frame = hou.playbar.frameRange()
    start_frame, end_frame = hou.playbar.playbackRange()
    start_roll = first_frame - start_frame
    end_roll = end_frame - last_frame
    return FrameRange(
        start_frame,
        end_frame,
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

class _UpdateModeContext:
    def __init__(self, mode):
        self.mode = mode
        self.previous_mode = None
        self.has_ui = hou.isUIAvailable()

    def __enter__(self):
        if not self.has_ui: return
        self.previous_mode = hou.updateModeSetting()
        hou.setUpdateMode(self.mode)

    def __exit__(self, *args):
        if not self.has_ui: return
        hou.setUpdateMode(self.previous_mode)

def update_mode(mode):
    return _UpdateModeContext(mode)

###############################################################################
# Tracked primitives
###############################################################################

def get_metadata(prim):
    if not prim.HasMetadata('customData'): return None
    _metadata = prim.GetMetadata('customData')
    if _metadata is None: return None
    if 'context' not in _metadata: return None
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

def is_asset(prim):
    metadata = get_metadata(prim)
    if metadata is None: return False
    if metadata['context'] != 'asset': return False
    return True

def is_kit(prim):
    metadata = get_metadata(prim)
    if metadata is None: return False
    if metadata['context'] != 'kit': return False
    return True

def is_camera(prim):
    return prim.GetTypeName() == 'Camera'

def is_light(prim):
    return prim.GetTypeName().endswith('Light')

def is_render_var(prim):
    return prim.GetTypeName() == 'RenderVar'

def list_assets(root):
    metadata_root = root.GetPrimAtPath('/METADATA')
    if not metadata_root.IsValid(): return []
    return list(map(get_metadata, iter_scene(metadata_root, is_asset)))

def list_kits(prim):
    metadat_root = prim.GetPrimAtPath('/METADATA')
    if not metadat_root.IsValid(): return []
    return list(map(get_metadata, iter_scene(metadat_root, is_kit)))

def list_cameras(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_camera)))

def list_lights(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_light)))

def list_render_vars(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_render_var)))