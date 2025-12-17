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
    to_windows_path,
    default_client
)
from tumblehead.farm.tasks.env import get_base_env
from tumblehead.util.io import store_json
from tumblehead.naming import random_name
from tumblehead.apps.deadline import Job as Task
from tumblehead.config.timeline import BlockRange

api = default_client()

"""
config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'title': 'composite',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'node_path': 'path/to/node',
    'layer_names': ['main', 'mirror'],
    'output_paths': {
        'main': 'path/to/main/beauty.####.exr',
        'mirror': 'path/to/mirror/beauty.####.exr'
    }
}
"""

def _is_valid_config(config):

    def _is_valid_entity(entity):
        if not isinstance(entity, dict): return False
        if 'uri' not in entity: return False
        if not isinstance(entity['uri'], str): return False
        if 'department' not in entity: return False
        if not isinstance(entity['department'], str): return False
        return True

    def _is_valid_output_paths(output_paths):
        if not isinstance(output_paths, dict): return False
        for layer_name, output_path in output_paths.items():
            if not isinstance(layer_name, str): return False
            if not isinstance(output_path, str): return False
        return True

    if 'entity' not in config: return False
    if not _is_valid_entity(config['entity']): return False
    if 'title' not in config: return False
    if not isinstance(config['title'], str): return False
    if 'priority' not in config: return False
    if not isinstance(config['priority'], int): return False
    if 'pool_name' not in config: return False
    if not isinstance(config['pool_name'], str): return False
    if 'first_frame' not in config: return False
    if not isinstance(config['first_frame'], int): return False
    if 'last_frame' not in config: return False
    if not isinstance(config['last_frame'], int): return False
    if 'frames' not in config: return False
    if not isinstance(config['frames'], list): return False
    for frame in config['frames']:
        if not isinstance(frame, int): return False
    if 'step_size' not in config: return False
    if not isinstance(config['step_size'], int): return False
    if 'batch_size' not in config: return False
    if not isinstance(config['batch_size'], int): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'node_path' not in config: return False
    if not isinstance(config['node_path'], str): return False
    if 'layer_names' not in config: return False
    if not isinstance(config['layer_names'], list): return False
    if len(config['layer_names']) == 0: return False
    for layer_name in config['layer_names']:
        if not isinstance(layer_name, str): return False
    if 'output_paths' not in config: return False
    if not _is_valid_output_paths(config['output_paths']): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'composite.py'
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
    frames = config['frames']
    step_size = config['step_size']
    batch_size = config['batch_size']
    receipt_path = Path(config['receipt_path'])

    # Task context
    task_path = staging_path / f'composite_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        entity = config['entity'],
        first_frame = config['first_frame'],
        last_frame = config['last_frame'],
        frames = config['frames'],
        receipt_path = config['receipt_path'],
        input_path = config['input_path'],
        node_path = config['node_path'],
        layer_names = config['layer_names'],
        output_paths = config['output_paths']
    ))

    # Create the task
    task = Task(
        to_wsl_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'houdini'
    task.priority = priority
    if len(frames) > 0:
        task.frames.extend(frames)
    else:
        task.start_frame = render_range.first_frame
        task.end_frame = render_range.last_frame
    task.step_size = step_size
    task.chunk_size = batch_size
    task.max_frame_time = 20
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))
    task.output_paths.append(to_windows_path(receipt_path))
    return task