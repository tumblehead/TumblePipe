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
    'title': 'slapcomp',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'step_size': 1,
    'input_paths': {
        'layer1': {
            'diffuse': 'path/to/diffuse.####.exr',
            'depth': 'path/to/depth.####.exr'
        },
        'layer2': {
            'diffuse': 'path/to/diffuse.####.exr',
            'depth': 'path/to/depth.####.exr'
        }
    },
    'receipt_path': 'path/to/receipt.####.json',
    'output_path': 'path/to/output.####.exr'
}
"""

def _is_valid_config(config):

    def _is_valid_layer(layer):
        if not isinstance(layer, dict): return False
        for aov_name, aov_path in layer.items():
            if not isinstance(aov_name, str): return False
            if not isinstance(aov_path, str): return False
        return True

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
    if 'step_size' not in config: return False
    if not isinstance(config['step_size'], int): return False
    if 'input_paths' not in config: return False
    if not isinstance(config['input_paths'], dict): return False
    for layer_name, layer in config['input_paths'].items():
        if not isinstance(layer_name, str): return False
        if not _is_valid_layer(layer): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'output_path' not in config: return False
    if not isinstance(config['output_path'], str): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'slapcomp.py'
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
        config['last_frame'],
        config['step_size']
    )
    output_path = Path(config['output_path'])

    # Task context
    task_path = staging_path / f'slapcomp_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        first_frame = render_range.first_frame,
        last_frame = render_range.last_frame,
        step_size = render_range.step_size,
        input_paths = config['input_paths'],
        receipt_path = config['receipt_path'],
        output_path = config['output_path']
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
    task.start_frame = render_range.first_frame
    task.end_frame = render_range.last_frame
    task.step_size = 1
    task.chunk_size = len(render_range)
    task.max_frame_time = 20
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))
    task.output_paths.append(to_windows_path(output_path))

    # Done
    return task