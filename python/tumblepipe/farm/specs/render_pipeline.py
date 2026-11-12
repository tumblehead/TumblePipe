"""Config schema of the render pipeline (``pipelines/render.py``): a shot's
per-channel render, denoise, review and notify chain. The stage and export
workers hand it on, so their schemas share its render-task blocks.

config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'channel_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'input_paths': {'<channel>': 'string'},  # one input stage per channel
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
        'copy_to_edit': 'bool',
        'cut_range': ['int', 'int'],  # optional: the range minus its roll,
                                      # where the preview's check frames sit
        'preview_frame_list': ['int'],  # optional: the preview's frames, in
                                       # order, inside the range and on the
                                       # step (farm.specs.preview_frames); absent:
                                       # first, middle and last of the cut
        'frame_ranges': [['int', 'int']]  # optional (#84): a multi-range
                                       # render, only these frames of
                                       # first..last (farm.specs.frame_ranges);
                                       # never published, never comped
    },
    'tasks': {                  # either or both
        'partial_render': {'priority': 'int', 'denoise': 'bool', 'channel_name': 'string'},
        'full_render': {'priority': 'int', 'denoise': 'bool', 'channel_name': 'string'}
    },
    # Optional: re-comp the shot after the full render (Farm Submit's Comp
    # column), waiting for its denoise, else the render
    'composite': {'pool_name': 'string', 'priority': 'int', 'batch_size': 'int',
                  'copy_to_edit': 'bool', 'channel_name': 'string',
                  # optional: specs.composite.valid_recomp
                  'step_size': 'int', 'preview_frames': 'bool',
                  'preview_frame_list': ['int'],
                  'first_frame': 'int', 'last_frame': 'int'}
}

``tasks.*.channel_name`` is the Discord channel the chain's notify posts to.
"""

from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_str,
    is_str_dict,
    valid_channel_names,
    valid_entity,
)
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs.preview_frames import (
    CONFIG_KEY as PREVIEW_FRAMES_KEY,
    valid_frame_list,
)

RENDER_TASK_KEYS = ('partial_render', 'full_render')


def valid_render_task(render) -> bool:
    """A ``partial_render``/``full_render`` block."""
    if not isinstance(render, dict): return False
    if not check_int(render, 'priority'): return False
    if not check_bool(render, 'denoise'): return False
    if not check_str(render, 'channel_name'): return False
    return True


def valid_render_tasks(tasks) -> bool:
    """The render-task blocks of a ``tasks`` map (other keys are the
    caller's to check)."""
    if not isinstance(tasks, dict): return False
    for key in RENDER_TASK_KEYS:
        if key in tasks and not valid_render_task(tasks[key]): return False
    return True


def valid_render_settings(settings) -> bool:
    """The settings every render source carries (stage, export, pipeline)."""
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_str(settings, 'render_department_name'): return False
    if not check_str(settings, 'render_settings_path'): return False
    if not check_int(settings, 'tile_count'): return False
    if not check_int(settings, 'first_frame'): return False
    if not check_int(settings, 'last_frame'): return False
    if not check_int(settings, 'step_size'): return False
    if not check_int(settings, 'batch_size'): return False
    if not valid_cut_range(settings): return False
    if not valid_preview_frame_list(settings, settings['step_size']): return False
    if not frame_ranges.valid_entry(settings): return False
    return True


def valid_preview_frame_list(block, step: int = 1) -> bool:
    """An optional ``preview_frame_list`` inside ``block``'s first_frame..
    last_frame, on ``step`` (``farm.specs.preview_frames.valid_frame_list``): the
    frames a preview renders, in order, instead of first, middle and last
    (#74). Absent is the default."""
    if PREVIEW_FRAMES_KEY not in block: return True
    return valid_frame_list(
        block[PREVIEW_FRAMES_KEY], block['first_frame'], block['last_frame'], step
    )


def valid_cut_range(block) -> bool:
    """An optional ``cut_range`` [first, last] inside ``block``'s
    first_frame..last_frame (the frames in the cut, without the roll)."""
    if 'cut_range' not in block: return True
    cut_range = block['cut_range']
    if not isinstance(cut_range, list) or len(cut_range) != 2: return False
    first, last = cut_range
    if not all(isinstance(f, int) and not isinstance(f, bool) for f in cut_range): return False
    return block['first_frame'] <= first <= last <= block['last_frame']


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_render_settings(settings): return False
    if not valid_channel_names(settings): return False
    if not is_str_dict(settings.get('input_paths')): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    if not valid_render_tasks(config.get('tasks')): return False
    if 'composite' in config:
        from tumblepipe.farm.specs.composite import valid_recomp
        if not valid_recomp(config['composite']): return False
        # The re-comp follows the full render; a preview-only render has none.
        if 'full_render' not in config['tasks']: return False
        # A multi-range render is not published: no comp follows it (#84).
        if frame_ranges.CONFIG_KEY in settings: return False
        # It comps the frames this render makes, so its own frames (when it
        # names any) lie inside the render's.
        block = config['composite']
        if block.get('first_frame', settings['first_frame']) < settings['first_frame']: return False
        if block.get('last_frame', settings['last_frame']) > settings['last_frame']: return False
    return True
