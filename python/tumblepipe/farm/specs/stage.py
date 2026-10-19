"""Config schemas of the stage job (``jobs/stage.py``), its worker
(``workers/stage.py``) and the worker's hython half
(``workers/stage_houdini.py``).

A standalone render: the worker builds one render stage per channel in
hython, then submits the render pipeline (``specs.render_pipeline``) over
them. The job stores its config as the worker's context.

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'channel_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'string',       # relative to the job's data dir
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
        'copy_to_edit': 'bool'
    },
    'tasks': {
        'stage': {'priority': 'int', 'channel_name': 'string'},
        'partial_render': {...},                # and/or
        'full_render': {...}                    # (specs.render_pipeline)
    }
}

houdini_config = {
    'entity': {'uri': ..., 'department': ...},
    'settings': {
        'first_frame': 'int',
        'last_frame': 'int',
        'render_settings_path': 'string',
        'render_department_name': 'string'
    },
    'output_paths': {'channel_name': 'path/to/stage_<channel>.usd'}
}
"""

from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_str,
    check_str_dict,
    valid_channel_names,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.specs.render_pipeline import (
    valid_render_settings,
    valid_render_tasks,
)


def _valid_stage(stage) -> bool:
    if not isinstance(stage, dict): return False
    if not check_int(stage, 'priority'): return False
    if not check_str(stage, 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_render_settings(settings): return False
    if not valid_channel_names(settings): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    tasks = config.get('tasks')
    if not valid_render_tasks(tasks): return False
    if not _valid_stage(tasks.get('stage')): return False
    return True


is_valid_context = is_valid_config


def is_valid_houdini_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_frame_range(settings, step=False): return False
    if not check_str(settings, 'render_settings_path'): return False
    if not check_str(settings, 'render_department_name'): return False
    if not check_str_dict(config, 'output_paths'): return False
    if len(config['output_paths']) == 0: return False
    return True
