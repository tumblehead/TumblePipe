from pathlib import Path
import json
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.farm.tasks.env import get_base_env
from tumblehead.util.io import store_json
from tumblehead.naming import random_name
from tumblehead.apps.deadline import Job as Task

api = default_client()

"""
config = {
    'title': 'Resolve Notify',
    'priority': 50,
    'pool_name': 'general',
    'queue_path': 'export:/other/resolve_queue',
    'command': {
        'action': 'import_render',
        'project': 'ShowName',
        'params': {
            'shot': 'SEQ010_SHOT020',
            'department': 'comp',
            'version': 'v0003',
            'render_path': '/renders/SEQ010/SHOT020/comp/v0003/',
            'frame_range': [1001, 1100]
        }
    }
}
"""

def _is_valid_config(config):

    def _is_valid_command(command):
        if not isinstance(command, dict): return False
        if 'action' not in command: return False
        if not isinstance(command['action'], str): return False
        if 'project' not in command: return False
        if not isinstance(command['project'], str): return False
        # params is optional but must be dict if present
        if 'params' in command:
            if not isinstance(command['params'], dict): return False
        return True

    if not isinstance(config, dict): return False
    if 'title' not in config: return False
    if not isinstance(config['title'], str): return False
    if 'priority' not in config: return False
    if not isinstance(config['priority'], int): return False
    if 'pool_name' not in config: return False
    if not isinstance(config['pool_name'], str): return False
    if 'queue_path' not in config: return False
    if not isinstance(config['queue_path'], str): return False
    if 'command' not in config: return False
    if not _is_valid_command(config['command']): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'resolve_notify.py'
REQUIREMENTS_PATH = Path(__file__).parent / 'requirements.txt'

def build(config, paths, staging_path):

    # Check if the config is valid
    assert _is_valid_config(config), (
        'Invalid config: '
        f'{json.dumps(config, indent=4)}'
    )

    # Config parameters
    title = config['title']
    priority = config['priority']
    pool_name = config['pool_name']

    # Task context
    task_path = staging_path / f'resolve_notify_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        queue_path = config['queue_path'],
        command = config['command']
    ))

    # Create the task
    task = Task(
        to_wsl_path(SCRIPT_PATH),
        to_wsl_path(REQUIREMENTS_PATH),
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'general'
    task.priority = priority
    task.start_frame = 1
    task.end_frame = 1
    task.step_size = 1
    task.chunk_size = 1
    task.max_frame_time = 5  # Quick task, just writes a file
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))

    # Done
    return task
