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
from tumblehead.naming import random_name
from tumblehead.apps.deadline import Job as Task
from tumblehead.config.timeline import BlockRange

api = default_client()

"""
config = {
    'title': 'edit',
    'priority': 50,
    'pool_name': 'general',
    'entity_uri': 'entity:/shots/sequence/shot',
    'first_frame': 1,
    'last_frame': 100,
    'purpose': 'render'  # optional
}
"""

def _is_valid_config(config):
    if 'title' not in config: return False
    if not isinstance(config['title'], str): return False
    if 'priority' not in config: return False
    if not isinstance(config['priority'], int): return False
    if 'pool_name' not in config: return False
    if not isinstance(config['pool_name'], str): return False
    if 'entity_uri' not in config: return False
    if not isinstance(config['entity_uri'], str): return False
    if 'first_frame' not in config: return False
    if not isinstance(config['first_frame'], int): return False
    if 'last_frame' not in config: return False
    if not isinstance(config['last_frame'], int): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'edit.py'
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
    render_range = BlockRange(
        config['first_frame'],
        config['last_frame']
    )

    # Task context
    task_path = staging_path / f'edit_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        entity_uri = config['entity_uri'],
        purpose = config.get('purpose', 'render')
    ))

    # Create the task
    task = Task(
        to_wsl_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'edit'
    task.priority = priority
    task.start_frame = render_range.first_frame
    task.end_frame = render_range.last_frame
    task.step_size = 1
    task.chunk_size = len(render_range)
    task.max_frame_time = 20
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(dict(
        TH_USER = get_user_name(),
        TH_CONFIG_PATH = path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH = path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH = path_str(to_wsl_path(api.PIPELINE_PATH)),
        OCIO = path_str(to_wsl_path(Path(os.environ['OCIO'])))
    ))
    return task