from functools import partial
from pathlib import Path
import json
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    get_user_name,
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.util.io import store_json
from tumblehead.util.uri import Uri
from tumblehead.naming import random_name
from tumblehead.apps.deadline import Job as Task
from tumblehead.config.timeline import BlockRange

api = default_client()

"""
config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'priority': 'int',
        'pool_name': 'string',
        'first_frame': 'int',
        'last_frame': 'int'
    },
    'tasks': {
        'publish': {
            'downstream_departments': ['list']
        }
    },
    'workfile_path': 'string'  # Required: path to bundled workfile
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)
    
    def _is_int(datum):
        return isinstance(datum, int)
    
    def _is_list(datum):
        return isinstance(datum, list)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_list = partial(_check, _is_list)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_publish(publish):
            if not isinstance(publish, dict): return False
            if 'downstream_departments' in publish:
                if not _check_list(publish, 'downstream_departments'): return False
                # Validate each department name is a string
                for dept in publish['downstream_departments']:
                    if not isinstance(dept, str): return False
            return True

        if not isinstance(tasks, dict): return False
        if 'publish' in tasks:
            if not _valid_publish(tasks['publish']): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    if not _check_str(config, 'workfile_path'): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'publish.py'
def build(config, paths, staging_path):

    # Check if the config is valid
    assert _is_valid_config(config), (
        'Invalid config: '
        f'{json.dumps(config, indent=4)}'
    )

    # Config
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']

    # Parameters
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    entity_display = entity_uri.display_name()
    title = f"publish {entity_display}"
    render_range = BlockRange(first_frame, last_frame)

    # Task context
    task_path = staging_path / f'publish_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, config)

    # Create the task
    task = Task(
        to_wsl_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'houdini'
    task.priority = 90
    task.start_frame = render_range.first_frame
    task.end_frame = render_range.last_frame
    task.step_size = 1
    task.chunk_size = len(render_range)
    task.max_frame_time = 15
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(dict(
        TH_USER = get_user_name(),
        TH_CONFIG_PATH = path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH = path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH = path_str(to_wsl_path(api.PIPELINE_PATH)),
        OCIO = path_str(to_wsl_path(Path(os.environ['OCIO'])))
    ))

    # Done
    return task