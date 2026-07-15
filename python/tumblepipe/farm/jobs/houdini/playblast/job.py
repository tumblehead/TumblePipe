"""Playblast job family: a farm GL (Storm) playblast of a shot's staged stage.

Mirrors the render family's public surface so the Submit Jobs dialog can drive
it through ``batch_submit`` exactly like render and publish:

- ``build(config, paths, temp_path, jobs, deps, depends_on=None)`` — populate
  the batch with a playblast task (+ a Discord notify carrying the mp4).
- ``submit(config, paths=None)`` / ``cli()`` — standalone submission.

Unlike render there are no variants/render-layers and no chunked frame DAG: a
playblast is one monolithic task (the mp4 encode needs every frame) plus one
notify. The job-level config is the ``{entity, settings}`` shape the dialog
builds; ``build`` translates it into the flat, worker-facing task config that
``tasks.playblast.task`` validates.

config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',        # 'render' -> render:/playblast + render:/daily
        'pool_name': 'string',
        'priority': 'int',
        'input_path': 'string',     # collapsed staged USD, relative to job data
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'fps': 'int',
        'res': ['int', 'int'],
        'channel_name': 'string'    # Discord channel for the notify
    }
}
"""

from pathlib import Path
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    path_str,
    to_windows_path,
)
from tumblepipe.util.uri import Uri
from tumblepipe.apps.deadline import Job
from tumblepipe.pipe.paths import (
    get_next_playblast_path,
    get_daily_path,
)
from tumblepipe.farm.jobs.houdini import _common

import tumblepipe.farm.tasks.playblast.task as playblast_task
import tumblepipe.farm.tasks.notify.task as notify_task


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not _common.check_str(settings, 'user_name'): return False
    if not _common.check_str(settings, 'purpose'): return False
    if not _common.check_str(settings, 'pool_name'): return False
    if not _common.check_int(settings, 'priority'): return False
    if not _common.check_str(settings, 'input_path'): return False
    if not _common.check_int(settings, 'first_frame'): return False
    if not _common.check_int(settings, 'last_frame'): return False
    if not _common.check_int(settings, 'step_size'): return False
    if not _common.check_int(settings, 'fps'): return False
    if not _common.check_list(settings, 'res'): return False
    if len(settings['res']) != 2: return False
    if not all(_common.is_int(value) for value in settings['res']): return False
    if not _common.check_str(settings, 'channel_name'): return False
    return True


def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _common.valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    return True


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] = None
    ) -> list[str]:
    """Build the playblast (+ notify) jobs and add them to the batch dicts.

    Returns the terminal job names (the notify), so a caller can chain further
    work off a completed playblast.
    """
    if depends_on is None:
        depends_on = []

    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    purpose = settings['purpose']

    # Output paths -- versioned playblast + rolling daily, resolved at submit
    # time exactly like the local playblast HDA (get_next_* bumps the version).
    playblast_path = get_next_playblast_path(entity_uri, department_name, purpose)
    daily_path = get_daily_path(entity_uri, department_name, purpose)
    version_name = playblast_path.stem  # '.../<dept>/vNNNN.mp4' -> 'vNNNN'

    # Playblast task (monolithic render + encode)
    playblast_title = f'playblast {department_name} {version_name}'
    playblast_job = playblast_task.build(dict(
        title = playblast_title,
        priority = settings['priority'],
        pool_name = settings['pool_name'],
        first_frame = settings['first_frame'],
        last_frame = settings['last_frame'],
        step_size = settings['step_size'],
        fps = settings['fps'],
        res = list(settings['res']),
        input_path = settings['input_path'],
        output_paths = [
            path_str(to_windows_path(playblast_path)),
            path_str(to_windows_path(daily_path)),
        ]
    ), paths, temp_path)
    jobs['playblast'] = playblast_job
    deps['playblast'] = list(depends_on)

    # Notify with the finished mp4
    notify_message = (
        f'{entity_uri} - {department_name} - playblast - '
        f'{version_name}'
    )
    notify_job = notify_task.build(dict(
        title = f'notify playblast {department_name}',
        priority = 90,
        pool_name = settings['pool_name'],
        user_name = settings['user_name'],
        channel_name = settings['channel_name'],
        message = notify_message,
        command = dict(
            mode = 'full',
            video_path = path_str(to_windows_path(playblast_path))
        )
    ), dict(), temp_path)
    jobs['playblast_notify'] = notify_job
    deps['playblast_notify'] = ['playblast']

    return ['playblast_notify']


def submit(config: dict, paths=None) -> int:
    entity_uri = config['entity']['uri']
    department_name = config['entity']['department']
    batch_title = f'playblast {entity_uri} {department_name}'
    return _common.submit_batch(batch_title, build, config, paths)


def cli() -> int:
    return _common.run_cli(_is_valid_config, submit)


if __name__ == '__main__':
    _common.configure_logging()
    sys.exit(cli())
