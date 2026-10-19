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
        'copy_to_edit': 'bool'
    },
    'tasks': {                  # either or both
        'partial_render': {'priority': 'int', 'denoise': 'bool', 'channel_name': 'string'},
        'full_render': {'priority': 'int', 'denoise': 'bool', 'channel_name': 'string'}
    }
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
    return True


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
    return True
