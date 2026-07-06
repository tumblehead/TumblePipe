from pathlib import Path
import datetime as dt
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    get_user_name,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_departments
from tumblepipe.apps.deadline import (
    Deadline,
    Job
)
from tumblepipe.farm.jobs.houdini import _common
from tumblepipe.farm.jobs.houdini import _publish

"""
config = {
    'entity': {
        'department': 'string'  # update every shot up to and including this department
    },
    'settings': {
        'priority': 'int',
        'pool_name': 'string'
    },
    'tasks': {
        'publish': {
            'denoise': 'bool',
            'channel_name': 'string'
        }
    }
}

Note: update jobs have no entity URI or frame range in their config —
build() scans every shot (entity:/shots/*/*) by design, and each publish
job derives its frame range from the shot's own configured range.
"""

def _is_valid_config(config):

    _check_str = _common.check_str
    _check_int = _common.check_int

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if not _check_str(entity, 'department'): return False
        return True

    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
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
            if not _publish.is_submissable(uri, dept_name): continue
            out_of_date = _publish.is_out_of_date(uri, variant_name, dept_name)
            if not down_stream_changed and not out_of_date: continue
            # Create job name from URI path segments
            uri_name = '_'.join(uri.segments[1:])
            job_name = f'{uri_name}_{dept_name}'
            job = _publish.create_publish_job(
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
    paths: dict[Path, Path] = None
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
    batch_title = (
        f'{project_name} '
        'update '
        f'{user_name} '
        f'{timestamp}'
    )

    return _common.submit_batch(batch_title, build, config, paths)


def main(
    api,
    department_name,
    pool_name,
    priority
    ) -> int:
    """Entry point for the scheduled farm update (invoked via cli()).

    Args:
        api: API client instance
        department_name: Department name to update up to
        pool_name: Deadline pool name
        priority: Job priority

    Returns:
        0 on success, 1 on error
    """
    config = {
        'entity': {
            'department': department_name
        },
        'settings': {
            'priority': priority,
            'pool_name': pool_name
        },
        'tasks': {
            'publish': {}
        }
    }
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
    except Exception: return _common.error('Could not connect to Deadline')

    # Check department name
    department_name = args.department_name
    shot_departments = list_departments('shots')
    department_names = [d.name for d in shot_departments]
    if department_name not in department_names:
        return _common.error(f'Invalid department name: {department_name}')

    # Check pool name
    pool_name = args.pool_name
    if pool_name not in deadline.list_pools():
        return _common.error(f'Invalid pool name: {pool_name}')

    # Check priority
    priority = args.priority
    if priority < 0 or priority > 100:
        return _common.error(f'Invalid priority: {priority}')

    # Run main
    return main(
        api,
        department_name,
        pool_name,
        priority
    )

if __name__ == "__main__":
    _common.configure_logging()
    sys.exit(cli())