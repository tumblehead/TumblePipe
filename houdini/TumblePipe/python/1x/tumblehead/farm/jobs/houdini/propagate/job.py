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
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.groups import get_group
from tumblehead.pipe.paths import next_staged_path, next_staged_file_path, latest_hip_file_path
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)
from tumblehead.pipe import graph

import tumblehead.farm.tasks.publish.task as publish_task
import tumblehead.farm.tasks.build.task as build_task
import tumblehead.farm.tasks.notify.task as notify_task

from importlib import reload
reload(publish_task)
reload(build_task)
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
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)

    def _is_int(datum):
        return isinstance(datum, int)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True

    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)

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

    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
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

def _find_dependent_departments(department_name, entity_type):
    # Get departments for entity type (assets or shots)
    departments = list_departments(f'{entity_type}s')

    all_dept_names = [d.name for d in departments]
    current_dept_index = all_dept_names.index(department_name) if department_name in all_dept_names else -1

    if current_dept_index == -1:
        return []

    dependent_depts = []
    for i in range(current_dept_index + 1, len(departments)):
        dept = departments[i]
        if not dept.independent and dept.publishable:
            dependent_depts.append(dept.name)

    return dependent_depts

def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    user_name = get_user_name()
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    config['settings']['first_frame']
    config['settings']['last_frame']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Scan dependency graph
    logging.debug('Scanning dependency graph...')
    dependency_graph = graph.scan(api)

    # Find dependent departments to republish
    logging.debug('Finding dependent departments...')
    # Get entity type from URI - handle groups specially
    if entity_uri.purpose == 'groups':
        entity_type = 'group'
    elif entity_uri.segments[0] == 'assets':
        entity_type = 'asset'
    elif entity_uri.segments[0] == 'shots':
        entity_type = 'shot'
    else:
        entity_type = None
    dependent_departments = _find_dependent_departments(department_name, entity_type if entity_type != 'group' else 'shot')
    # Include the current department at the start so it gets a publish task too
    departments_to_publish = [department_name] + dependent_departments

    # Find affected shots
    logging.debug('Finding affected shots...')
    affected_shots = []
    if entity_type == 'group':
        group = get_group(entity_uri)
        if group:
            affected_shots = list(group.members)
    elif entity_type == 'asset':
        affected_shots = graph.find_shots_referencing_asset(
            dependency_graph,
            entity_uri
        )
    elif entity_type == 'shot':
        affected_shots = [entity_uri]

    # Check if there's anything to do
    if not dependent_departments and not affected_shots:
        logging.debug('No propagation needed')
        return 0

    logging.debug(f'Found {len(dependent_departments)} dependent departments')
    logging.debug(f'Found {len(affected_shots)} affected shots')

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
        batch = Batch(
            f'{project_name} '
            f'propagate '
            f'{entity_uri} '
            f'{user_name} '
            f'{timestamp}'
        )

        # Prepare adding jobs
        jobs = dict()
        deps = dict()
        def _add_job(job_name, job, job_deps):
            jobs[job_name] = job
            deps[job_name] = job_deps

        # Add publish jobs for dependent departments
        publish_job_names = []

        # Get department list to check independence (use 'shots' for groups)
        dept_category = 'shots' if entity_type == 'group' else f'{entity_type}s'
        departments = list_departments(dept_category)
        dept_map = {d.name: d for d in departments}

        # Helper to create a publish job for a given entity URI and department
        def _create_publish_job_for_entity(target_uri, dept_name, job_name_suffix=''):
            workfile_path = latest_hip_file_path(target_uri, dept_name)
            if workfile_path is None or not workfile_path.exists():
                return None

            logging.debug(f'Found workfile for {target_uri}: {workfile_path}')

            workfile_dest = Path('workfiles') / f'{dept_name}_{workfile_path.name}'
            paths[workfile_path] = workfile_dest

            # Bundle context.json alongside workfile (for group workfile detection)
            context_path = workfile_path.parent / 'context.json'
            if context_path.exists():
                context_dest = Path('workfiles') / 'context.json'
                paths[context_path] = context_dest

            publish_config = {
                'entity': {'uri': str(target_uri), 'department': dept_name},
                'settings': config['settings'].copy(),
                'tasks': {'publish': {}},
                'workfile_path': path_str(workfile_dest)
            }
            publish_config['settings']['priority'] = priority + 5

            return publish_task.build(publish_config, paths, temp_path)

        for dept_name in departments_to_publish:
            if entity_type == 'group':
                # For groups: check if dept has group workfile, else split to members
                group_workfile = latest_hip_file_path(entity_uri, dept_name)
                if group_workfile and group_workfile.exists():
                    # Department has group workfile - create 1 job for group
                    job_name = f'publish_group_{dept_name}'
                    publish_job = _create_publish_job_for_entity(entity_uri, dept_name)
                    if publish_job:
                        dept = dept_map.get(dept_name)
                        job_deps = publish_job_names.copy() if (dept and not dept.independent) else []
                        _add_job(job_name, publish_job, job_deps)
                        publish_job_names.append(job_name)
                else:
                    # Department doesn't have group workfile - split into member shots
                    group = get_group(entity_uri)
                    if group:
                        for member_uri in group.members:
                            member_name = '_'.join(member_uri.segments[1:])
                            job_name = f'publish_shot_{member_name}_{dept_name}'
                            publish_job = _create_publish_job_for_entity(member_uri, dept_name)
                            if publish_job:
                                dept = dept_map.get(dept_name)
                                job_deps = publish_job_names.copy() if (dept and not dept.independent) else []
                                _add_job(job_name, publish_job, job_deps)
                                publish_job_names.append(job_name)
            else:
                # Non-group: existing logic
                job_name = f'publish_{entity_type}_{dept_name}'
                publish_job = _create_publish_job_for_entity(entity_uri, dept_name)
                if publish_job:
                    dept = dept_map.get(dept_name)
                    job_deps = publish_job_names.copy() if (dept and not dept.independent) else []
                    _add_job(job_name, publish_job, job_deps)
                    publish_job_names.append(job_name)

        # Add build jobs for affected shots
        build_job_names = []
        for shot_uri in affected_shots:
            job_name = f'build_{shot_uri.display_name().replace("/", "_")}'

            # Build build job
            build_config = {
                'title': f'build {shot_uri}',
                'priority': priority,
                'pool_name': pool_name,
                'entity_uri': str(shot_uri),
                'output_path': path_str(next_staged_file_path(shot_uri))
            }
            build_job = build_task.build(build_config, paths, temp_path)

            # Build jobs depend on all publish jobs
            _add_job(job_name, build_job, publish_job_names)
            build_job_names.append(job_name)

        # Add notification job
        notify_msg = f'Propagated {entity_uri}'
        if dependent_departments:
            notify_msg += f' -> {len(dependent_departments)} departments'
        if affected_shots:
            notify_msg += f' -> {len(affected_shots)} shots'

        notify_job = notify_task.build(dict(
            title = f'notify propagate {entity_uri}',
            priority = 90,
            pool_name = pool_name,
            user_name = user_name,
            channel_name = 'exports',
            message = notify_msg,
            command = dict(
                mode = 'notify'
            )
        ), dict(), temp_path)

        # Notify depends on all publish and build jobs
        _add_job('notify', notify_job, publish_job_names + build_job_names)

        # Add all jobs to batch with dependencies
        _add_jobs(batch, jobs, deps)

        # Submit
        farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

    # Done
    logging.debug(f'Submitted propagate batch with {len(jobs)} jobs')
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
