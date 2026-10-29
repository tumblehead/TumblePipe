"""The playblast pipeline: a farm playblast (Karma XPU) of a shot's
collapsed stage, then a Discord notify carrying the mp4.

Unlike a render there are no channels and no chunked frames: a playblast
is one task (the mp4 encode needs every frame) plus its notify.

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
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
        'look': {...},              # specs.playblast.valid_look
        'channel_name': 'string'    # Discord channel for the notify
    }
}
"""

from pathlib import Path

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.checks import (
    check_int,
    check_list,
    check_str,
    is_int,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.jobs import playblast as playblast_job
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.farm.specs import playblast as playblast_spec
from tumblepipe.pipe.paths import get_daily_path, get_next_playblast_path
from tumblepipe.util.uri import Uri


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'input_path'): return False
    if not check_int(settings, 'fps'): return False
    if not check_list(settings, 'res'): return False
    if len(settings['res']) != 2: return False
    if not all(is_int(value) for value in settings['res']): return False
    if not playblast_spec.valid_look(settings.get('look')): return False
    if not check_str(settings, 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    return True


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the playblast and its notify after ``depends_on``; returns the
    notify, so a caller can chain further work off a finished playblast."""
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    purpose = settings['purpose']

    # Versioned playblast + rolling daily, resolved at submit time like the
    # local playblast HDA (get_next_* bumps the version).
    playblast_path = get_next_playblast_path(entity_uri, department_name, purpose)
    daily_path = get_daily_path(entity_uri, department_name, purpose)
    version_name = playblast_path.stem

    jobs['playblast'] = playblast_job.build(dict(
        title = f'playblast {department_name} {version_name}',
        priority = settings['priority'],
        pool_name = settings['pool_name'],
        first_frame = settings['first_frame'],
        last_frame = settings['last_frame'],
        step_size = settings['step_size'],
        fps = settings['fps'],
        res = list(settings['res']),
        look = dict(settings['look']),
        input_path = settings['input_path'],
        output_paths = [path_str(playblast_path), path_str(daily_path)]
    ), paths, temp_path)
    deps['playblast'] = list(depends_on or [])

    jobs['playblast_notify'] = notify_job.build(dict(
        title = f'notify playblast {department_name}',
        priority = notify_job.PRIORITY,
        pool_name = settings['pool_name'],
        user_name = settings['user_name'],
        channel_name = settings['channel_name'],
        message = f'{entity_uri} - {department_name} - playblast - {version_name}',
        command = dict(
            mode = 'full',
            video_path = path_str(playblast_path)
        )
    ), dict(), temp_path)
    deps['playblast_notify'] = ['playblast']
    return ['playblast_notify']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the playblast of ``config`` as one batch."""
    entity = config['entity']
    return submit_batch(
        batch_title('playblast', entity['uri'], entity['department']),
        build, config, paths
    )
