from tempfile import TemporaryDirectory
from functools import partial
from pathlib import Path
import datetime as dt
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    get_project_name,
    fix_path,
    path_str,
    default_client
)
from tumblehead.util.io import load_json
from tumblehead.pipe.paths import Entity
from tumblehead.apps.deadline import Deadline, Batch

import tumblehead.farm.tasks.stage.task as stage_task
import tumblehead.farm.tasks.notify.task as notify_task

from importlib import reload
reload(stage_task)
reload(notify_task)

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

"""
config = {
    'entity': {
        'tag': 'asset',
        'category_name': 'string',
        'asset_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'shot',
        'sequence_name': 'string',
        'shot_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'kit',
        'category_name': 'string',
        'kit_name': 'string',
        'department_name': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'render_layer_name': 'string',
        'render_department_name': 'string',
        'render_settings_path': 'string'
    },
    'tasks': {
        'stage': {
            'first_frame': 'int',
            'last_frame': 'int',
            'channel_name': 'string'
        },
        'partial_render': {
            'first_frame': 'int',
            'middle_frame': 'int',
            'last_frame': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
            'first_frame': 'int',
            'last_frame': 'int',
            'step_size': 'int',
            'batch_size': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        }
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)
    
    def _is_int(datum):
        return isinstance(datum, int)
    
    def _is_bool(datum):
        return isinstance(datum, bool)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_bool = partial(_check, _is_bool)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if 'tag' not in entity: return False
        match entity['tag']:
            case 'asset':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'asset_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'shot':
                if not _check_str(entity, 'sequence_name'): return False
                if not _check_str(entity, 'shot_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'kit':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'kit_name'): return False
                if not _check_str(entity, 'department_name'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_str(settings, 'user_name'): return False
        if not _check_str(settings, 'purpose'): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_str(settings, 'render_layer_name'): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_stage(stage):
            if not isinstance(stage, dict): return False
            if not _check_int(stage, 'first_frame'): return False
            if not _check_int(stage, 'last_frame'): return False
            if not _check_str(stage, 'channel_name'): return False
            return True

        def _valid_partial_render(partial_render):
            if not isinstance(partial_render, dict): return False
            if not _check_int(partial_render, 'first_frame'): return False
            if not _check_int(partial_render, 'middle_frame'): return False
            if not _check_int(partial_render, 'last_frame'): return False
            if not _check_bool(partial_render, 'denoise'): return False
            if not _check_str(partial_render, 'channel_name'): return False
            return True
    
        def _valid_full_render(full_render):
            if not isinstance(full_render, dict): return False
            if not _check(full_render, 'first_frame', int): return False
            if not _check(full_render, 'last_frame', int): return False
            if not _check(full_render, 'step_size', int): return False
            if not _check(full_render, 'batch_size', int): return False
            if not _check(full_render, 'denoise', bool): return False
            if not _check_str(full_render, 'channel_name'): return False
            return True
        
        if not isinstance(tasks, dict): return False
        if 'stage' in tasks:
            if not _valid_stage(tasks['stage']): return False
        if 'partial_render' in tasks:
            if not _valid_partial_render(tasks['partial_render']): return False
        if 'full_render' in tasks:
            if not _valid_full_render(tasks['full_render']): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    return True

def _add_tasks(batch, tasks):
    tasks = list(filter(lambda job_layer: len(job_layer) != 0, tasks))
    task_indices = [
        [
            batch.add_job(task)
            for task in job_layer
        ]
        for job_layer in tasks
    ]
    if len(tasks) <= 1: return
    prev_layer = task_indices[0]
    for curr_layer in task_indices[1:]:
        for curr_index in curr_layer:
            for prev_index in prev_layer:
                batch.add_dep(curr_index, prev_index)
        prev_layer = curr_layer

def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:

    # Config
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['stage']['channel_name']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    farm = Deadline()

    # Open temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.info(f'Temporary directory: {temp_path}')

        # Batch and jobs
        job_batch = Batch(
            f'{project_name} '
            f'{purpose} '
            f'{entity} '
            f'{user_name} '
            f'{timestamp}'
        )

        # Add job layers
        _add_tasks(job_batch, [
            [stage_task.build(config, paths, temp_path)],
            [notify_task.build(dict(
                title = f'notify stage {entity}',
                priority = 90,
                pool_name = pool_name,
                user_name = user_name,
                channel_name = channel_name,
                message = f'Staged {purpose} {entity}',
                command = dict(
                    mode = 'notify'
                )
            ), dict(), temp_path)]
        ])

        # Submit
        farm.submit(job_batch, api.storage.resolve('export:/other/jobs'))

    # Done
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    args = parser.parse_args()

    # Check config path
    config_path = Path(args.config_path)
    if not config_path.exists():
        return _error(f'Config path not found: {config_path}')
    
    # Load and check config
    config = load_json(config_path)
    if not _is_valid_config(config):
        return _error(f'Invalid config: {config_path}')
    
    # Run submit
    return submit(config)

if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())