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
        'step_size': 'int',     # a shot's above 1 (Farm Submit's Step) is a
                                # check render: never published; a comp
                                # after it is a check comp (valid_comp_after)
        'batch_size': 'int',
        'copy_to_edit': 'bool',
        'cut_range': ['int', 'int'],  # optional: the range minus its roll,
                                      # where the preview's check frames sit
        'preview_frame_list': ['int'],  # optional: the preview's frames, in
                                       # order, inside the range and on the
                                       # step (farm.specs.preview_frames); absent:
                                       # first, middle and last of the cut
        'frame_ranges': [['int', 'int']],  # optional (#84): a multi-range
                                       # render, only these frames of
                                       # first..last (farm.specs.frame_ranges);
                                       # never published, never comped
        'repair': {...}                # optional (#92): render these frames
                                       # into existing versions
                                       # (farm.specs.repair); full_render only
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
from tumblepipe.farm.specs import repair
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
    """The settings every render source carries (stage, export, pipeline,
    a render request), but the paths each source makes for itself."""
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_str(settings, 'render_department_name'): return False
    if not check_int(settings, 'tile_count'): return False
    if not check_int(settings, 'first_frame'): return False
    if not check_int(settings, 'last_frame'): return False
    if not check_int(settings, 'step_size'): return False
    if settings['step_size'] < 1: return False
    if not check_int(settings, 'batch_size'): return False
    if not valid_cut_range(settings): return False
    if not valid_preview_frame_list(settings, settings['step_size']): return False
    if not frame_ranges.valid_entry(settings): return False
    if not repair.valid_entry(settings): return False
    return True


def comped_span(settings: dict) -> dict:
    """The frames a comp after this render comps: a repair's version's
    (``farm.specs.repair``, #92), else the render's own settings."""
    return settings.get(repair.CONFIG_KEY, settings)


def valid_comp_after(settings: dict, task_keys, composite) -> bool:
    """Whether ``composite`` (a re-comp block, ``specs.composite.
    valid_recomp``) may follow the render of ``settings`` with ``task_keys``:
    the one rule of the render pipeline, the stage and the collapse.

    The comp follows the full render (a preview-only render has none) of
    one range: a multi-range render is not published (#84), and a repair
    (#92) leaves its version as published as it was, so its version
    decides. Beside a render on a Step above 1 (no repair: a repair is on
    Step 1) the comp reads that check render and is a check comp
    (``pipelines.render``'s ``follows_check_render``): its frames may reach
    past the render's, held from its first and last frame
    (``submission.stepped_comp_frames``, v1.128.0). Otherwise it comps the
    frames the render makes, so its own frames lie inside them."""
    from tumblepipe.farm.specs.composite import valid_recomp
    if not valid_recomp(composite): return False
    if 'full_render' not in task_keys: return False
    span = comped_span(settings)
    if repair.CONFIG_KEY in settings and not span['published']: return False
    if frame_ranges.CONFIG_KEY in span: return False
    if repair.CONFIG_KEY not in settings and settings.get('step_size', 1) > 1:
        return True
    if composite.get('first_frame', span['first_frame']) < span['first_frame']: return False
    if composite.get('last_frame', span['last_frame']) > span['last_frame']: return False
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


def valid_request(config) -> bool:
    """A render request: a render pipeline config without the paths each
    path to the farm makes for itself (``settings.input_paths`` and
    ``settings.render_settings_path``). The collapse job carries one
    (``specs.collapse``) and the stage job is one with those paths
    (``specs.stage``); ``pipelines.preview.render_config`` adds them.
    It renders at least one of ``RENDER_TASK_KEYS``; other task keys are
    the carrier's to check."""
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_render_settings(settings): return False
    if not valid_channel_names(settings): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    tasks = config.get('tasks')
    if not valid_render_tasks(tasks): return False
    if not any(key in tasks for key in RENDER_TASK_KEYS): return False
    if not repair.valid_entry(settings, tasks): return False
    if 'composite' in config and not valid_comp_after(settings, tasks, config['composite']):
        return False
    return True


def is_valid_config(config) -> bool:
    if not valid_request(config): return False
    settings = config['settings']
    if not check_str(settings, 'render_settings_path'): return False
    if not is_str_dict(settings.get('input_paths')): return False
    return True
