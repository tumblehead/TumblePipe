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
    api
)
from tumblepipe.farm.tasks.env import get_base_env
from tumblepipe.farm.tasks.collapse._spec import is_valid_config as _is_valid_config
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.naming import random_name
from tumblepipe.farm.deadline import Task

SCRIPT_PATH = Path(__file__).parent / 'collapse.py'
def build(config, paths, staging_path):

    # Check if the config is valid
    assert _is_valid_config(config), (
        'Invalid config: '
        f'{json.dumps(config, indent=4)}'
    )

    # Parameters
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    previews = [key for key in ('render', 'playblast') if key in config]
    title = f"collapse {'+'.join(previews)} {entity_uri} {department_name}"

    # Task context
    task_path = staging_path / f'collapse_{random_name(8)}'
    context_path = task_path / 'context.json'
    store_json(context_path, config)

    # Create the task. One frame: the task snapshots the stage once and
    # submits the previews, it renders nothing itself.
    task = Task(
        local_path(SCRIPT_PATH), None,
        path_str(context_path.relative_to(staging_path))
    )
    task.name = title
    task.pool = config['settings']['pool_name']
    # hython composes the staged stage through the project's resolver, which
    # is what the houdini group's workers are set up for (as for stage).
    task.group = 'houdini'
    task.priority = config['settings']['priority']
    task.start_frame = 1
    task.end_frame = 1
    task.step_size = 1
    task.chunk_size = 1
    task.max_frame_time = 15
    task.paths.update(paths)
    task.paths[task_path] = task_path.relative_to(staging_path)
    task.env.update(get_base_env(api))

    # Done
    return task
