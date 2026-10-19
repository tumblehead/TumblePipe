"""Config schemas of the export job (``jobs/export.py``), its worker
(``workers/export.py``) and the worker's hython half
(``workers/export_houdini.py``).

A lookdev turntable (``th::lookdev_studio``): the worker exports the
workfile's stage in hython, then submits the render pipeline
(``specs.render_pipeline``) over it. The job stores its config as the
worker's context.

config = {
    'entity': {'uri': 'entity:/assets/category/asset', 'department': 'string'},
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'channel_name': 'string',               # the publish channel
        'render_department_name': 'string',
        'render_settings_path': 'string',       # relative to the job's data dir
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'export': {
            'priority': 'int',
            'input_path': 'string',             # relative to the job's data dir
            'node_path': 'string',
            'channel_name': 'string'            # the Discord channel
        },
        'partial_render': {...},                # and/or
        'full_render': {...}                    # (specs.render_pipeline)
    }
}

houdini_config = {
    'first_frame': 'int',
    'last_frame': 'int',
    'batch_size': 'int',        # 0 = no batching, >0 = export in chunks then stitch
    'render_settings_path': 'path/to/render_settings.json',
    'input_path': 'path/to/input.hip',
    'node_path': '/stage/path/to/node',
    'output_path': 'path/to/stage.usd'
}
"""

from tumblepipe.farm.checks import (
    check_int,
    check_str,
    valid_channel_name,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.specs.render_pipeline import (
    valid_render_settings,
    valid_render_tasks,
)


def _valid_export(export) -> bool:
    if not isinstance(export, dict): return False
    if not check_int(export, 'priority'): return False
    if not check_str(export, 'input_path'): return False
    if not check_str(export, 'node_path'): return False
    if not check_str(export, 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_render_settings(settings): return False
    if not valid_channel_name(settings): return False
    tasks = config.get('tasks')
    if not valid_render_tasks(tasks): return False
    if not _valid_export(tasks.get('export')): return False
    return True


is_valid_context = is_valid_config


def is_valid_houdini_config(config) -> bool:
    if not valid_frame_range(config, step=False): return False
    if not check_int(config, 'batch_size'): return False
    if not check_str(config, 'render_settings_path'): return False
    if not check_str(config, 'input_path'): return False
    if not check_str(config, 'node_path'): return False
    if not check_str(config, 'output_path'): return False
    return True
