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
    to_windows_path,
    default_client
)
from tumblehead.util.io import store_json
from tumblehead.naming import random_name
from tumblehead.apps.deadline import Job as Task
from tumblehead.config import BlockRange

api = default_client()

"""
config = {
    'title': 'render',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'slapcomp_path': None | 'path/to/slapcomp.bgeo.sc',
    'input_path': 'path/to/input.usd',
    'output_paths': {
        'diffuse': 'path/to/diffuse.####.exr',
        'depth': 'path/to/depth.####.exr'
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
    if 'slapcomp_path' not in config: return False
    if not (
        config['slapcomp_path'] == None or
        isinstance(config['slapcomp_path'], str)): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'output_paths' not in config: return False
    if not isinstance(config['output_paths'], dict): return False
    for key, value in config['output_paths'].items():
        if not isinstance(key, str): return False
        if not isinstance(value, str): return False
    return True

SCRIPT_PATH = Path(__file__).parent / 'render.py'
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
    task_path = staging_path / f'render_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        receipt_path = config['receipt_path'],
        slapcomp_path = config['slapcomp_path'],
        input_path = config['input_path'],
        output_paths = config['output_paths']
    ))

    # Create the task
    task = Task(
        to_wsl_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'karma'
    task.priority = priority
    if len(frames) > 0:
        task.frames.extend(frames)
    else:
        task.start_frame = render_range.first_frame
        task.end_frame = render_range.last_frame
    task.step_size = step_size
    task.chunk_size = batch_size
    task.max_frame_time = 45
    task.paths.update(paths)
    task.paths[context_path] = context_path.relative_to(staging_path)
    task.env.update(dict(
        TH_USER = get_user_name(),
        TH_CONFIG_PATH = path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH = path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH = path_str(to_wsl_path(api.PIPELINE_PATH)),
        OCIO = path_str(to_wsl_path(Path(os.environ['OCIO'])))
    ))
    task.output_paths.append(to_windows_path(receipt_path))

    # Done
    return task