from functools import partial
from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import logging
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    fix_path,
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
import tumblehead.farm.tasks.publish.task as publish_task

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

def _is_out_of_date(entity_uri, variant_name, department_name):
    hip_path = latest_hip_file_path(entity_uri, department_name)
    export_path = latest_export_path(entity_uri, variant_name, department_name)
    if export_path is None: return True
    if not export_path.exists(): return True
    return hip_path.stat().st_mtime > export_path.stat().st_mtime

def _error(msg):
    logging.error(msg)
    return 1

def _create_publish_job(
    entity_uri,
    department_name,
    pool_name,
    priority,
    paths: dict,
    temp_path: Path
    ):
    """Create a publish job using publish_task.build() pattern.

    Args:
        entity_uri: Entity URI to publish
        department_name: Department name
        pool_name: Deadline pool name
        priority: Job priority
        paths: Dict mapping source paths to relative dest paths (modified in place)
        temp_path: Staging directory for job files

    Returns:
        Job object or None if workfile not found
    """
    logging.debug(
        f'Creating publish job for '
        f'{entity_uri} '
        f'{department_name}'
    )

    # Find the workfile
    workfile_path = latest_hip_file_path(entity_uri, department_name)
    if workfile_path is None or not workfile_path.exists():
        logging.warning(f'No workfile found for {entity_uri}/{department_name}')
        return None

    # Get frame range
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise ValueError(f"Cannot get frame range for entity: {entity_uri}. Ensure the entity has frame_start, frame_end, roll_start, roll_end properties configured.")
    render_range = frame_range.full_range()

    # Add workfile to paths for bundling
    workfile_dest = Path('workfiles') / f'{department_name}_{workfile_path.name}'
    paths[workfile_path] = workfile_dest

    # Bundle context.json alongside workfile (for group workfile detection)
    context_path = workfile_path.parent / 'context.json'
    if context_path.exists():
        context_dest = Path('workfiles') / 'context.json'
        paths[context_path] = context_dest

    # Build config for publish_task.build()
    config = {
        'entity': {
            'uri': str(entity_uri),
            'department': department_name
        },
        'settings': {
            'priority': priority,
            'pool_name': pool_name,
            'first_frame': render_range.first_frame,
            'last_frame': render_range.last_frame
        },
        'tasks': {
            'publish': {}
        },
        'workfile_path': path_str(workfile_dest)
    }

    # Build job using publish_task
    return publish_task.build(config, paths, temp_path)


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] = None
    ) -> list[str]:
    """Build update jobs and add to provided dicts.

    Args:
        config: Job configuration with department_name, pool_name, priority
        paths: Files to bundle with jobs (modified in place)
        temp_path: Staging directory
        jobs: Dict to add Job objects to (modified in place)
        deps: Dict to add dependencies to (modified in place)
        depends_on: Optional list of job names this job depends on

    Returns:
        List of terminal job names (for dependency chaining)
    """
    if depends_on is None:
        depends_on = []

    # Config
    department_name = config['entity']['department']
    pool_name = config['settings']['pool_name']
    priority = config['settings']['priority']

    # Find department names up to and including the given department name
    shot_departments = list_departments('shots')
    department_names = [d.name for d in shot_departments]
    department_names = department_names[:department_names.index(department_name) + 1]

    # Helper to add job
    def _add_job(job_name, job, job_deps):
        if job is None:
            return
        jobs[job_name] = job
        deps[job_name] = job_deps

    # Track terminal jobs (last job in each shot's chain)
    terminal_job_names = []

    # Create update jobs for each sequence and shot
    for uri in api.config.list_entities(Uri.parse_unsafe('entity:/shots/*/*')):
        prev_job_name = None
        down_stream_changed = False
        # Use 'default' variant for batch update jobs
        variant_name = 'default'
        first_job_in_shot = True
        for dept_name in department_names:
            if not _is_submissable(uri, dept_name): continue
            out_of_date = _is_out_of_date(uri, variant_name, dept_name)
            if not down_stream_changed and not out_of_date: continue
            # Create job name from URI path segments
            uri_name = '_'.join(uri.segments[1:])
            job_name = f'{uri_name}_{dept_name}'
            job = _create_publish_job(
                uri,
                dept_name,
                pool_name,
                priority,
                paths,
                temp_path
            )
            # First job in shot chain depends on depends_on parameter
            if first_job_in_shot:
                job_deps = depends_on.copy()
                first_job_in_shot = False
            else:
                job_deps = [] if prev_job_name is None else [prev_job_name]
            _add_job(job_name, job, job_deps)
            if job is not None:
                prev_job_name = job_name
                down_stream_changed = True

        # Track last job in this shot's chain
        if prev_job_name is not None:
            terminal_job_names.append(prev_job_name)

    return terminal_job_names


def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:
    """Create batch, build jobs, and submit to farm.

    Args:
        config: Job configuration
        paths: Files to bundle with jobs

    Returns:
        0 on success, 1 on error
    """
    # Parameters
    project_name = api.PROJECT_PATH.name
    user_name = get_user_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    try: farm = Deadline()
    except: return _error('Could not connect to Deadline')

    # Open temporary directory for staging job files
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Batch
        batch = Batch(
            f'{project_name} '
            'update '
            f'{user_name} '
            f'{timestamp}'
        )

        # Build jobs using the new build() function
        jobs = {}
        deps = {}
        build(config, paths, temp_path, jobs, deps)

        # Check if there are any jobs to submit
        if not jobs:
            logging.debug('No updates needed')
            return 0

        # Add jobs to batch
        batch.add_jobs_with_deps(jobs, deps)

        # Submit (within temp directory context so files can be copied)
        farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

    # Done
    logging.debug(f'Submitted update batch with {len(jobs)} jobs')
    return 0


def main(
    api,
    department_name,
    pool_name,
    priority
    ) -> int:
    """Legacy entry point for backward compatibility.

    Args:
        api: API client instance
        department_name: Department name to update up to
        pool_name: Deadline pool name
        priority: Job priority

    Returns:
        0 on success, 1 on error
    """
    # Convert parameters to config format
    config = {
        'entity': {
            'uri': 'entity:/shots',  # Placeholder, build() scans all shots
            'department': department_name
        },
        'settings': {
            'priority': priority,
            'pool_name': pool_name,
            'first_frame': 0,  # Not used by update jobs
            'last_frame': 0    # Not used by update jobs
        },
        'tasks': {
            'publish': {}
        }
    }

    # Call submit with converted config
    return submit(config, {})

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