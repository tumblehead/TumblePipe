"""Config schema for the stage task family.

Shared by ``stage.py`` (worker CLI), ``task.py`` (submit-side task builder)
and ``farm/jobs/houdini/stage_render/job.py`` — they all validate the same
config and used to carry identical copies of this validator.

config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'variant_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'stage': {
            'priority': 'int',
            'channel_name': 'string'
        },
        'partial_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        }
    }
}
"""

from tumblepipe.farm._common import (
    valid_entity,
    check_str,
    check_int,
    check_bool,
    check_list,
)


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_list(settings, 'variant_names'): return False
    if not check_str(settings, 'render_department_name'): return False
    if not check_str(settings, 'render_settings_path'): return False
    if not check_int(settings, 'tile_count'): return False
    if not check_int(settings, 'first_frame'): return False
    if not check_int(settings, 'last_frame'): return False
    if not check_int(settings, 'step_size'): return False
    if not check_int(settings, 'batch_size'): return False
    return True


def _valid_stage(stage):
    if not isinstance(stage, dict): return False
    if not check_int(stage, 'priority'): return False
    if not check_str(stage, 'channel_name'): return False
    return True


def _valid_render(render):
    if not isinstance(render, dict): return False
    if not check_int(render, 'priority'): return False
    if not check_bool(render, 'denoise'): return False
    if not check_str(render, 'channel_name'): return False
    return True


def _valid_tasks(tasks):
    if not isinstance(tasks, dict): return False
    if 'stage' in tasks:
        if not _valid_stage(tasks['stage']): return False
    if 'partial_render' in tasks:
        if not _valid_render(tasks['partial_render']): return False
    if 'full_render' in tasks:
        if not _valid_render(tasks['full_render']): return False
    return True


def is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    return True
