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
from tumblehead.util.uri import Uri
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)

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
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'render_layer_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'stage': {
            'priority': 'int',
            'channel_name': 'string'
        },
        'partial_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
            'priority': 'int',
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
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_str(settings, 'user_name'): return False
        if not _check_str(settings, 'purpose'): return False
        if not _check_str(settings, 'pool_name'): return False
        if 'render_layer_names' not in settings: return False
        if not isinstance(settings['render_layer_names'], list): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        if not _check_int(settings, 'tile_count'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_stage(stage):
            if not isinstance(stage, dict): return False
            if not _check_int(stage, 'priority'): return False
            if not _check_str(stage, 'channel_name'): return False
            return True

        def _valid_partial_render(partial_render):
            if not isinstance(partial_render, dict): return False
            if not _check_int(partial_render, 'priority'): return False
            if not _check_bool(partial_render, 'denoise'): return False
            if not _check_str(partial_render, 'channel_name'): return False
            return True
    
        def _valid_full_render(full_render):
            if not isinstance(full_render, dict): return False
            if not _check_int(full_render, 'priority'): return False
            if not _check_bool(full_render, 'denoise'): return False
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

def _add_jobs(
    batch: Batch,
    jobs: dict[str, Job],
    deps: dict[str, list[str]]
    ):
    indicies = {
        job_name: batch.add_job(job)
        for job_name, job in jobs.items()
    }
    for job_name, job_deps in deps.items():
        if len(job_deps) == 0: continue
        for dep_name in job_deps:
            if dep_name not in indicies:
                logging.warning(
                    f'Job "{job_name}" depends on '
                    f'non-existing job "{dep_name}"'
                )
                continue
            batch.add_dep(
                indicies[job_name],
                indicies[dep_name]
            )

def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['stage']['channel_name']
    render_layer_names = config['settings']['render_layer_names']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    try: farm = Deadline()
    except: return _error('Could not connect to Deadline')

    # Open temporary directory
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.info(f'Temporary directory: {temp_path}')

        # Batch
        layers_text = f"[{', '.join(render_layer_names)}]"
        batch = Batch(
            f'{project_name} '
            f'{purpose} '
            f'{entity_uri} '
            f'{layers_text} '
            f'{user_name} '
            f'{timestamp}'
        )

        # Prepare adding jobs
        jobs = dict()
        deps = dict()
        def _add_job(job_name, job, job_deps):
            jobs[job_name] = job
            deps[job_name] = job_deps

        # Add jobs
        stage_job = stage_task.build(config, paths, temp_path)
        notify_job = notify_task.build(dict(
            title = f'notify stage {entity_uri}',
            priority = 60,
            pool_name = pool_name,
            user_name = user_name,
            channel_name = channel_name,
            message = f'Staged {purpose} {entity_uri}',
            command = dict(
                mode = 'notify'
            )
        ), dict(), temp_path)
        _add_job('stage', stage_job, [])
        _add_job('notify', notify_job, ['stage'])
        _add_jobs(batch, jobs, deps)

        # Submit
        farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

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