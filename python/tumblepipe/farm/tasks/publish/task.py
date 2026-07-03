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
from tumblepipe.farm.tasks.publish._spec import is_valid_build_config as _is_valid_config
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.naming import random_name
from tumblepipe.farm.deadline import Task
from tumblepipe.config.timeline import BlockRange


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
    department_name = config['entity']['department']
    title = f"publish {entity_uri} {department_name}"
    render_range = BlockRange(first_frame, last_frame)

    # Task context
    task_path = staging_path / f'publish_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, config)

    # Create the task
    task = Task(
        local_path(SCRIPT_PATH), None,
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
    task.env.update(get_base_env(api))

    # Done
    return task