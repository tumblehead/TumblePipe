from pathlib import Path
import datetime as dt
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import get_project_name
from tumblepipe.util.uri import Uri
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.jobs.houdini import _common

import tumblepipe.farm.tasks.export.task as export_task
import tumblepipe.farm.tasks.notify.task as notify_task

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
        'variant_name': 'string',
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'export': {
            'priority': 'int',
            'input_path': 'string',
            'node_path': 'string',
            'channel_name': 'string'
        },
        'partial_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string
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

    _check_str = _common.check_str
    _check_int = _common.check_int
    _check_bool = _common.check_bool

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
        if not _check_str(settings, 'variant_name'): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        if not _check_int(settings, 'tile_count'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_export(export):
            if not isinstance(export, dict): return False
            if not _check_int(export, 'priority'): return False
            if not _check_str(export, 'input_path'): return False
            if not _check_str(export, 'node_path'): return False
            if not _check_str(export, 'channel_name'): return False
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
        if 'export' in tasks:
            if not _valid_export(tasks['export']): return False
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


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] = None
    ) -> list[str]:
    """Build export render jobs and add to provided dicts.

    Args:
        config: Job configuration
        paths: Files to bundle with jobs
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
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['export']['channel_name']

    # Helper to add job
    def _add_job(job_name, job, job_deps):
        jobs[job_name] = job
        deps[job_name] = job_deps

    # Add jobs
    export_job = export_task.build(config, paths, temp_path)
    notify_job = notify_task.build(dict(
        title = f'notify export {entity_uri}',
        priority = 90,
        pool_name = pool_name,
        user_name = user_name,
        channel_name = channel_name,
        message = f'Exported {purpose} {entity_uri}',
        command = dict(
            mode = 'notify'
        )
    ), dict(), temp_path)
    _add_job('export', export_job, depends_on.copy())
    _add_job('notify', notify_job, ['export'])

    return ['notify']


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
    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    variant_name = config['settings']['variant_name']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    batch_title = (
        f'{project_name} '
        f'{purpose} '
        f'{entity_uri} '
        f'{variant_name} '
        f'{user_name} '
        f'{timestamp}'
    )

    return _common.submit_batch(batch_title, build, config, paths)

def cli():
    return _common.run_cli(_is_valid_config, submit)

if __name__ == "__main__":
    _common.configure_logging()
    sys.exit(cli())