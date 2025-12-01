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
    get_user_name,
    fix_path,
    path_str,
    default_client
)
from tumblehead.util.io import load_json
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)

import tumblehead.farm.tasks.publish.task as publish_task
import tumblehead.farm.tasks.notify.task as notify_task

from importlib import reload
reload(publish_task)
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
        'priority': 'int',
        'pool_name': 'string',
        'first_frame': 'int',
        'last_frame': 'int'
    },
    'tasks': {
        'publish': {
            'downstream_departments': ['list']
        }
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)

    def _is_int(datum):
        return isinstance(datum, int)

    def _is_list(datum):
        return isinstance(datum, list)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True

    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_list = partial(_check, _is_list)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_publish(publish):
            if not isinstance(publish, dict): return False
            if 'downstream_departments' in publish:
                if not _check_list(publish, 'downstream_departments'): return False
                # Validate each department name is a string
                for dept in publish['downstream_departments']:
                    if not isinstance(dept, str): return False
            return True
        
        if not isinstance(tasks, dict): return False
        if 'publish' in tasks:
            if not _valid_publish(tasks['publish']): return False
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
    user_name = get_user_name()
    pool_name = config['settings']['pool_name']
    downstream_departments = config['tasks']['publish'].get('downstream_departments', [])
    
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
        logging.debug(f'Temporary directory: {temp_path}')
        
        # Batch
        downstream_suffix = f" +{len(downstream_departments)}" if downstream_departments else ""
        batch = Batch(
            f'{project_name} '
            f'publish '
            f'{entity_uri} '
            f'{user_name} '
            f'{timestamp}'
            f'{downstream_suffix}'
        )
        
        # Prepare adding jobs
        jobs = dict()
        deps = dict()
        def _add_job(job_name, job, job_deps):
            jobs[job_name] = job
            deps[job_name] = job_deps
        
        # Add publish job
        publish_job = publish_task.build(config, paths, temp_path)
        notify_job = notify_task.build(dict(
            title = f'notify publish {entity_uri}',
            priority = 90,
            pool_name = pool_name,
            user_name = user_name,
            channel_name = 'exports',
            message = f'Published {entity_uri}' + (f' +{len(downstream_departments)} downstream' if downstream_departments else ''),
            command = dict(
                mode = 'notify'
            )
        ), dict(), temp_path)
        
        _add_job('publish', publish_job, [])
        _add_job('notify', notify_job, ['publish'])
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
    return submit(config, {})

if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())