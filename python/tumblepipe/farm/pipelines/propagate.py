"""The propagate pipeline: republish what depends on a department.

A command-line entry point: publishes the entity's department and every
later, non-independent, publishable department after it (a group's from
its group workfile where it has one, else per member shot), rebuilds the
staged USD of every shot affected, and posts a notify.

config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot' | 'groups:/...',
        'department': 'string'
    },
    'settings': {
        'priority': 'int',
        'pool_name': 'string',
        'first_frame': 'int',
        'last_frame': 'int'
    }
}

Command line: ``python -m tumblepipe.farm.pipelines.propagate <config.json>``.
"""

from pathlib import Path
import logging
import sys

from tumblepipe.api import get_user_name, path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.config.groups import get_group
from tumblepipe.farm.checks import (
    check_int,
    check_str,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.jobs import publish as publish_job
from tumblepipe.farm.pipelines import build as build_pipeline
from tumblepipe.farm.pipelines._batch import batch_title, run_cli, submit_batch
from tumblepipe.farm.pipelines.publish import bundle_workfile
from tumblepipe.pipe import graph
from tumblepipe.pipe.paths import latest_complete_hip_file_path, latest_hip_file_path
from tumblepipe.util.uri import Uri


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    settings = config.get('settings')
    if not valid_frame_range(settings, step=False): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    return True


def _entity_type(entity_uri: Uri) -> str:
    if entity_uri.purpose == 'groups':
        return 'group'
    if entity_uri.segments[0] == 'assets':
        return 'asset'
    if entity_uri.segments[0] == 'shots':
        return 'shot'
    raise ValueError(f'Cannot propagate {entity_uri}: not a shot, asset or group')


def _require_group(group_uri):
    group = get_group(group_uri)
    if group is None:
        raise ValueError(f'Group not found: {group_uri}')
    return group


def dependent_departments(department_name: str, departments: list) -> list[str]:
    """The departments after ``department_name`` a publish of it reaches:
    the later ones that are publishable and not independent."""
    names = [d.name for d in departments]
    if department_name not in names:
        raise ValueError(
            f'{department_name} is not in the department pool '
            f'({", ".join(names)})'
        )
    return [
        d.name for d in departments[names.index(department_name) + 1:]
        if d.publishable and not d.independent
    ]


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the propagation's publish, build and notify jobs after
    ``depends_on``; returns the notify (nothing when nothing propagates)."""
    depends_on = list(depends_on or [])
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    entity_type = _entity_type(entity_uri)

    # A group's departments are the shots'
    departments = list_departments('shots' if entity_type == 'group' else f'{entity_type}s')
    department_map = {d.name: d for d in departments}
    dependents = dependent_departments(department_name, departments)

    if entity_type == 'group':
        affected_shots = list(_require_group(entity_uri).members)
    elif entity_type == 'asset':
        affected_shots = graph.find_shots_referencing_asset(graph.scan(), entity_uri)
    else:
        affected_shots = [entity_uri]
    if not dependents and not affected_shots:
        logging.info('No propagation needed')
        return []
    logging.debug(f'Dependent departments: {dependents}')
    logging.debug(f'Affected shots: {[str(uri) for uri in affected_shots]}')

    # Publishes, in pipeline order: each waits for the ones before it unless
    # its department is independent; the first waits for depends_on.
    # Job keys spell the entity as its URI: a name flattened with '_' can
    # read as two (sq_1/sh10 and sq/1_sh10), and a repeated key silently
    # replaces the job before it.
    publish_job_names = []

    def _add_publish(job_name, target_uri, dept_name):
        # The newest finished version; see pipelines.publish.create_publish_job.
        workfile_path = latest_complete_hip_file_path(target_uri, dept_name)
        if workfile_path is None or not workfile_path.exists():
            logging.info(f'No finished workfile for {target_uri}/{dept_name}: not republished')
            return
        workfile_dest = bundle_workfile(workfile_path, dept_name, paths)
        jobs[job_name] = publish_job.build({
            'entity': {'uri': str(target_uri), 'department': dept_name},
            'settings': dict(settings),
            'tasks': {'publish': {}},
            'workfile_path': path_str(workfile_dest)
        }, paths, temp_path)
        if not publish_job_names:
            deps[job_name] = list(depends_on)
        elif department_map[dept_name].independent:
            deps[job_name] = []
        else:
            deps[job_name] = list(publish_job_names)
        publish_job_names.append(job_name)

    for dept_name in [department_name, *dependents]:
        if entity_type != 'group':
            _add_publish(f'publish_{entity_type}_{dept_name}', entity_uri, dept_name)
            continue
        # A group publishes from its group workfile where the department has
        # one, else from each member shot's own
        group_workfile = latest_hip_file_path(entity_uri, dept_name)
        if group_workfile is not None and group_workfile.exists():
            _add_publish(f'publish_group_{dept_name}', entity_uri, dept_name)
            continue
        for member_uri in _require_group(entity_uri).members:
            _add_publish(
                f'publish {member_uri} {dept_name}',
                member_uri, dept_name
            )

    # Rebuild every channel of every affected shot after all the publishes
    build_job_names = []
    for shot_uri in affected_shots:
        for channel_name in list_channels(shot_uri):
            job_name = f'build {shot_uri} {channel_name}'
            jobs[job_name] = build_pipeline.create({
                'entity_uri': str(shot_uri),
                'channel_name': channel_name,
                'priority': settings['priority'],
                'pool_name': settings['pool_name'],
            }, temp_path)
            deps[job_name] = list(publish_job_names)
            build_job_names.append(job_name)

    message = f'Propagated {entity_uri}'
    if dependents:
        message += f' -> {len(dependents)} departments'
    if affected_shots:
        message += f' -> {len(affected_shots)} shots'
    jobs['notify'] = notify_job.build(dict(
        title = f'notify propagate {entity_uri}',
        priority = notify_job.PRIORITY,
        pool_name = settings['pool_name'],
        user_name = get_user_name(),
        channel_name = 'exports',
        message = message,
        command = dict(mode = 'notify')
    ), dict(), temp_path)
    deps['notify'] = publish_job_names + build_job_names
    return ['notify']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the propagation of ``config`` as one batch."""
    return submit_batch(batch_title(
        'propagate', config['entity']['uri'], get_user_name()
    ), build, config, paths)


if __name__ == '__main__':
    sys.exit(run_cli(is_valid_config, submit))
