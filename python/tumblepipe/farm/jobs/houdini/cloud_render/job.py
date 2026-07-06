from pathlib import Path
import datetime as dt
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    get_project_name,
    to_windows_path,
    path_str
)
from tumblepipe.util.uri import Uri
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.jobs.houdini import _common, _render_build
import tumblepipe.farm.tasks.cloud_render.task as render_job

"""
config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'variant_name': 'string',
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'archive_path': 'string',
        'input_path': 'string',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'partial_render': {
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
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
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_str(settings, 'variant_name'): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        if not _check_str(settings, 'archive_path'): return False
        if not _check_str(settings, 'input_path'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_jobs(tasks):

        def _valid_partial_render(partial_render):
            if not isinstance(partial_render, dict): return False
            if not _check_bool(partial_render, 'denoise'): return False
            if not _check_str(partial_render, 'channel_name'): return False
            return True
    
        def _valid_full_render(full_render):
            if not isinstance(full_render, dict): return False
            if not _check_bool(full_render, 'denoise'): return False
            if not _check_str(full_render, 'channel_name'): return False
            return True
        
        if not isinstance(tasks, dict): return False
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
    if not _valid_jobs(config['tasks']): return False
    return True

def _build_partial_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path
    ):
    return _render_build.build_partial_render_job(
        config, paths, staging_path,
        render_task = render_job,
        priority = 90,
        render_task_extra = dict(
            archive_path = path_str(to_windows_path(
                Path(config['settings']['archive_path'])
            ))
        )
    )

def _build_full_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path,
    version_name: str
    ):
    return _render_build.build_full_render_job(
        config, paths, staging_path, version_name,
        render_task = render_job,
        priority = config['settings']['priority'],
        render_task_extra = dict(
            archive_path = path_str(to_windows_path(
                Path(config['settings']['archive_path'])
            ))
        )
    )

def _build_partial_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str
    ):
    return _render_build.build_partial_denoise_job(
        config, staging_path, render_department_name, render_version_name,
        priority = 90
    )

def _build_full_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str
    ):
    return _render_build.build_full_denoise_job(
        config, staging_path, render_department_name, render_version_name,
        priority = config['settings']['priority']
    )

def _build_slapcomp_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    return _render_build.build_slapcomp_job(
        config, staging_path, render_department_name, version_name,
        priority = config['settings']['priority']
    )

def _build_mp4_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    slapcomp_version_name: str
    ):
    return _render_build.build_playblast_mp4_job(
        config, staging_path, render_department_name, slapcomp_version_name,
        title = (
            f'mp4 '
            f'{render_department_name} '
            f'{slapcomp_version_name}'
        ),
        log_label = 'mp4',
        priority = config['settings']['priority']
    )

def _build_partial_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    return _render_build.build_partial_notify_job(
        config, staging_path, render_department_name, version_name,
        priority = 90
    )

def _build_full_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    return _render_build.build_playblast_notify_job(
        config, staging_path, version_name,
        title = (
            f'notify full '
            f'{render_department_name} '
            f'{version_name}'
        ),
        message = (
            f'{entity_uri} - '
            f'{render_department_name} - '
            f'{version_name}'
        ),
        log_label = 'full notify',
        priority = 90
    )


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] = None
    ) -> list[str]:
    """Build cloud render jobs and add to provided dicts.

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
    render_department_name = config['settings']['render_department_name']

    # Helper to add job
    def _add_job(job_name, job, job_deps):
        jobs[job_name] = job
        deps[job_name] = job_deps

    # Track terminal jobs
    terminal_jobs = []

    # Initial partial render job
    render_version_name = None
    if 'partial_render' in config['tasks']:
        render_result = _build_partial_render_job(config, paths, temp_path)
        render_job_obj, render_version_name = render_result
        _add_job('partial_render', render_job_obj, depends_on.copy())
        if config['tasks']['partial_render']['denoise']:
            denoise_result = _build_partial_denoise_job(
                config,
                temp_path,
                render_department_name,
                render_version_name
            )
            denoise_job_obj, denoise_version_name = denoise_result
            notify_job_obj = _build_partial_notify_job(
                config,
                temp_path,
                'denoise',
                denoise_version_name
            )
            _add_job('partial_denoise', denoise_job_obj, ['partial_render'])
            _add_job('partial_notify', notify_job_obj, ['partial_denoise'])
            terminal_jobs.append('partial_notify')
        else:
            notify_job_obj = _build_partial_notify_job(
                config,
                temp_path,
                render_department_name,
                render_version_name
            )
            _add_job('partial_notify', notify_job_obj, ['partial_render'])
            terminal_jobs.append('partial_notify')

    # Following full render jobs
    if 'full_render' in config['tasks']:
        terminal_jobs = []  # Clear - full render jobs become the terminals
        render_result = _build_full_render_job(
            config,
            paths,
            temp_path,
            render_version_name
        )
        render_job_obj, render_version_name = render_result
        full_render_deps = (
            depends_on.copy() if 'partial_render' not in config['tasks'] else
            ['partial_render']
        )
        _add_job('full_render', render_job_obj, full_render_deps)
        if config['tasks']['full_render']['denoise']:
            denoise_result = _build_full_denoise_job(
                config,
                temp_path,
                render_department_name,
                render_version_name
            )
            denoise_job_obj, denoise_version_name = denoise_result
            slapcomp_result = _build_slapcomp_job(
                config,
                temp_path,
                'denoise',
                denoise_version_name
            )
            slapcomp_job_obj, slapcomp_version_name = slapcomp_result
            mp4_result = _build_mp4_job(
                config,
                temp_path,
                'denoise',
                slapcomp_version_name
            )
            mp4_job_obj, mp4_version_name = mp4_result
            notify_job_obj = _build_full_notify_job(
                config,
                temp_path,
                'denoise',
                mp4_version_name
            )
            _add_job('full_denoise', denoise_job_obj, ['full_render'])
            _add_job('slapcomp', slapcomp_job_obj, ['full_denoise'])
            _add_job('mp4', mp4_job_obj, ['slapcomp'])
            _add_job('full_notify', notify_job_obj, ['mp4'])
            terminal_jobs.append('full_notify')
        else:
            slapcomp_result = _build_slapcomp_job(
                config,
                temp_path,
                render_department_name,
                render_version_name
            )
            slapcomp_job_obj, slapcomp_version_name = slapcomp_result
            mp4_result = _build_mp4_job(
                config,
                temp_path,
                render_department_name,
                slapcomp_version_name
            )
            mp4_job_obj, mp4_version_name = mp4_result
            notify_job_obj = _build_full_notify_job(
                config,
                temp_path,
                render_department_name,
                mp4_version_name
            )
            _add_job('slapcomp', slapcomp_job_obj, ['full_render'])
            _add_job('mp4', mp4_job_obj, ['slapcomp'])
            _add_job('full_notify', notify_job_obj, ['mp4'])
            terminal_jobs.append('full_notify')

    return terminal_jobs


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

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    batch_title = (
        f'[cloud] '
        f'{project_name} '
        f'{purpose} '
        f'{entity_uri} '
        f'{user_name} '
        f'{timestamp}'
    )

    return _common.submit_batch(batch_title, build, config, paths)

def cli():
    return _common.run_cli(_is_valid_config, submit)

if __name__ == "__main__":
    _common.configure_logging()
    sys.exit(cli())