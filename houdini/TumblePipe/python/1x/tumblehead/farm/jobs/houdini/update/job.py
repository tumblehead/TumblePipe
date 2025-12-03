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
    path_str,
    to_wsl_path,
    to_windows_path,
    get_user_name,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import get_frame_range
from tumblehead.config.department import list_departments
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)
from tumblehead.pipe.paths import (
    latest_hip_file_path,
    latest_export_path,
    next_export_path
)

api = default_client()

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
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        return True
    
    def _valid_jobs(tasks):

        def _valid_publish(publish):
            if not isinstance(publish, dict): return False
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
    if not _valid_jobs(config['tasks']): return False
    return True

def _is_submissable(entity_uri, department_name):
    # Skip placeholder entities (any segment with '000')
    if '000' in entity_uri.segments:
        return False
    hip_path = latest_hip_file_path(entity_uri, department_name)
    if hip_path is None: return False
    return hip_path.exists()

def _is_out_of_date(entity_uri, department_name):
    hip_path = latest_hip_file_path(entity_uri, department_name)
    export_path = latest_export_path(entity_uri, department_name)
    if export_path is None: return True
    if not export_path.exists(): return True
    return hip_path.stat().st_mtime > export_path.stat().st_mtime

def _error(msg):
    logging.error(msg)
    return 1

PUBLISH_SCRIPT_PATH = Path(__file__).parent / 'publish.py'
def _create_publish_job(
    api,
    entity_uri,
    department_name,
    pool_name,
    priority
    ):
    logging.debug(
        f'Creating publish job for '
        f'{entity_uri} '
        f'{department_name}'
    )

    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise ValueError(f"Cannot get frame range for entity: {entity_uri}. Ensure the entity has frame_start, frame_end, roll_start, roll_end properties configured.")
    render_range = frame_range.full_range()

    output_path = next_export_path(entity_uri, department_name)
    version_name = output_path.name
    job = Job(
        to_wsl_path(PUBLISH_SCRIPT_PATH), None,
        str(entity_uri),
        department_name
    )
    job.name = (
        f'publish '
        f'{entity_uri} '
        f'{department_name} '
        f'{version_name}'
    )
    job.pool = pool_name
    job.group = 'houdini'
    job.priority = priority
    job.start_frame = render_range.first_frame
    job.end_frame = render_range.last_frame
    job.step_size = 1
    job.chunk_size = len(render_range)
    job.max_frame_time = 10
    job.env.update(dict(
        TH_USER = get_user_name(),
        TH_CONFIG_PATH = path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH = path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH = path_str(to_wsl_path(api.PIPELINE_PATH))
    ))
    job.output_paths.append(to_windows_path(output_path))
    return job

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

def main(
    api,
    department_name,
    pool_name,
    priority
    ) -> int:

    # Parameters
    project_name = api.PROJECT_PATH.name
    user_name = get_user_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Find partment names up to and including the given department name
    shot_departments = list_departments('shots')
    department_names = [d.name for d in shot_departments]
    department_names = department_names[:department_names.index(department_name) + 1]

    # Get deadline ready
    try: farm = Deadline()
    except: return _error('Could not connect to Deadline')

    # Batch
    batch = Batch(
        f'{project_name} '
        'update '
        f'{user_name} '
        f'{timestamp}'
    )

    # Prepare adding jobs
    jobs = dict()
    deps = dict()
    def _add_job(job_name, job, job_deps):
        jobs[job_name] = job
        deps[job_name] = job_deps

    # Create update jobs for each sequence and shot
    for uri in api.config.list_entities(Uri.parse_unsafe('entity:/shots/*/*')):
        prev_job_name = None
        down_stream_changed = False
        for department_name in department_names:
                if not _is_submissable(uri, department_name): continue
                out_of_date = _is_out_of_date(uri, department_name)
                if not down_stream_changed and not out_of_date: continue
                # Create job name from URI path segments
                uri_name = '_'.join(uri.segments[1:])
                job_name = f'{uri_name}_{department_name}'
                job = _create_publish_job(
                    api,
                    uri,
                    department_name,
                    pool_name,
                    priority
                )
                _add_job(job_name, job, (
                    [] if prev_job_name is None else
                    [prev_job_name]
                ))
                prev_job_name = job_name
                down_stream_changed = True

    # Add jobs
    _add_jobs(batch, jobs, deps)

    # Submit
    farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

    # Done
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('department_name', type = str)
    parser.add_argument('pool_name', type = str)
    parser.add_argument('priority', type = int)
    args = parser.parse_args()

    # Prepare
    try: deadline = Deadline()
    except: return _error('Could not connect to Deadline')

    # Check department name
    department_name = args.department_name
    shot_departments = list_departments('shots')
    department_names = [d.name for d in shot_departments]
    if department_name not in department_names:
        return _error(f'Invalid department name: {department_name}')

    # Check pool name
    pool_name = args.pool_name
    if pool_name not in deadline.list_pools():
        return _error(f'Invalid pool name: {pool_name}')

    # Check priority
    priority = args.priority
    if priority < 0 or priority > 100:
        return _error(f'Invalid priority: {priority}')
    
    # Run main
    return main(
        api,
        department_name,
        pool_name,
        priority
    )

if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())