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
    to_windows_path,
    api
)
from tumblepipe.farm.tasks.env import get_base_env
from tumblepipe.util.io import store_json
from tumblepipe.naming import random_name
from tumblepipe.farm.deadline import Task
from tumblepipe.config.timeline import BlockRange
from tumblepipe.farm.tasks.playblast import _spec

# Deadline worker group for GL (Storm) playblast renders. Kept distinct from
# 'karma' so preview playblasts never contend with final-frame render slots;
# farm workers assigned to this group must have a GL-capable GPU context.
PLAYBLAST_GROUP = 'playblast'

SCRIPT_PATH = Path(__file__).parent / 'playblast.py'


def build(config, paths, staging_path):

    # Check if the config is valid
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
        config['last_frame'],
        config['step_size']
    )
    output_paths = list(map(Path, config['output_paths']))

    # Task context
    task_path = staging_path / f'playblast_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, dict(
        title = config['title'],
        priority = config['priority'],
        pool_name = config['pool_name'],
        first_frame = render_range.first_frame,
        last_frame = render_range.last_frame,
        step_size = render_range.step_size,
        fps = config['fps'],
        res = list(config['res']),
        input_path = config['input_path'],
        output_paths = config['output_paths']
    ))

    # Create the task -- single monolithic chunk so the one worker sees the
    # whole range and can encode the mp4 (mirrors the mp4 task).
    task = Task(
        local_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = pool_name
    task.group = PLAYBLAST_GROUP
    task.priority = priority
    task.start_frame = render_range.first_frame
    task.end_frame = render_range.last_frame
    task.step_size = 1
    task.chunk_size = len(render_range)
    task.max_frame_time = 45
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))
    for output_path in output_paths:
        task.output_paths.append(to_windows_path(output_path))

    # Done
    return task
