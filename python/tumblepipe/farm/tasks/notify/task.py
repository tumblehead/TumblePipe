from pathlib import Path
import json
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    path_str,
    local_path,
    api
)
from tumblepipe.farm.tasks.env import get_base_env
from tumblepipe.farm.tasks.notify import _spec
from tumblepipe.util.io import store_json
from tumblepipe.naming import random_name
from tumblepipe.farm.deadline import Task

"""
config = {
    'title': 'mp4',
    'priority': 50,
    'pool_name': 'rnd',
    'user_name': 'user',
    'channel_name': 'channel',
    'message': 'message',
    'command': {
        'mode': 'notify'
    } | {
        'mode': 'partial',
        'frame_path': 'path/to/frame.####.exr',
        'first_frame': 1,
        'middle_frame': 50,
        'last_frame': 100
    } | {
        'mode': 'full',
        'video_path': 'path/to/mp4.mp4'
    }
}
"""

def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'title' not in config: return False
    if not isinstance(config['title'], str): return False
    if 'priority' not in config: return False
    if not isinstance(config['priority'], int): return False
    if 'pool_name' not in config: return False
    if not isinstance(config['pool_name'], str): return False
    if 'user_name' not in config: return False
    if not isinstance(config['user_name'], str): return False
    if 'channel_name' not in config: return False
    if not isinstance(config['channel_name'], str): return False
    if 'message' not in config: return False
    if not isinstance(config['message'], str): return False
    if 'command' not in config: return False
    if not _spec.is_valid_command(config['command']): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'notify.py'
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
    task_path = staging_path / f'notify_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        user_name = config['user_name'],
        channel_name = config['channel_name'],
        message = config['message'],
        command = config['command']
    ))

    # Create the task
    task = Task(
        local_path(SCRIPT_PATH),
        local_path(REQUIREMENTS_PATH),
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
    task.max_frame_time = 10
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))

    # Done
    return task