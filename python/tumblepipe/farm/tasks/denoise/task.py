from pathlib import Path
import json
import sys

# Add tumblepipe python packages path
tumblepipe_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblepipe_packages_path not in sys.path:
    sys.path.append(str(tumblepipe_packages_path))

from tumblepipe.api import (
    path_str,
    local_path,
    to_windows_path,
    api
)
from tumblepipe.farm.tasks.env import get_base_env
from tumblepipe.farm.tasks.denoise import _spec
from tumblepipe.util.io import store_json
from tumblepipe.naming import random_name
from tumblepipe.farm.deadline import Task
from tumblepipe.config.timeline import BlockRange

SCRIPT_PATH = Path(__file__).parent / 'denoise.py'
def build(config, paths, staging_path):

    # Check if the config is valid (schema lives in _spec, shared with the worker)
    assert _spec.is_valid_config(config), (
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

    # Task context. Only the path keys and force_cpu reach the worker; the
    # scheduling keys above are consumed here, and Deadline appends the frame
    # range on the command line.
    task_path = staging_path / f'denoise_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        receipt_path = config['receipt_path'],
        input_paths = config['input_paths'],
        output_paths = config['output_paths'],
        # Off by default: OIDN picks its own device, using the CUDA backend
        # Houdini bundles when the worker has a usable GPU.
        force_cpu = config.get('force_cpu', False)
    ))

    # Create the task
    task = Task(
        local_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = 'denoise'
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