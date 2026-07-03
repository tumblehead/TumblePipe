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

import tumblepipe.farm.tasks.stage.task as stage_task
import tumblepipe.farm.tasks.notify.task as notify_task

# The config schema lives with the stage task family (farm/tasks/stage/_spec.py)
from tumblepipe.farm.tasks.stage._spec import is_valid_config as _is_valid_config


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] = None
    ) -> list[str]:
    """Build stage render jobs and add to provided dicts.

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
    channel_name = config['tasks']['stage']['channel_name']

    # Helper to add job
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
    _add_job('stage', stage_job, depends_on.copy())
    _add_job('notify', notify_job, ['stage'])

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
    variant_names = config['settings']['variant_names']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    layers_text = f"[{', '.join(variant_names)}]"
    batch_title = (
        f'{project_name} '
        f'{purpose} '
        f'{entity_uri} '
        f'{layers_text} '
        f'{user_name} '
        f'{timestamp}'
    )

    return _common.submit_batch(batch_title, build, config, paths)

def cli():
    return _common.run_cli(_is_valid_config, submit)

if __name__ == "__main__":
    _common.configure_logging()
    sys.exit(cli())