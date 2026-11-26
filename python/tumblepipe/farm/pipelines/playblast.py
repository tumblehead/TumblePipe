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
from tumblepipe.farm.job import Job
from tumblepipe.farm.checks import checked
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.jobs import playblast as playblast_job
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.farm.specs import playblast_pipeline as playblast_pipeline_spec
from tumblepipe.pipe.paths import (
    get_daily_path, get_next_playblast_path, playblast_version_of, reserve_playblast,
)
from tumblepipe.util.uri import Uri


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
    checked(playblast_pipeline_spec.is_valid_config, config, 'playblast pipeline')
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    purpose = settings['purpose']

    # Versioned playblast + rolling daily, resolved at submit time like the
    # local playblast HDA (get_next_* bumps the version).
    playblast_path = get_next_playblast_path(entity_uri, department_name, purpose)
    # Held until the worker writes it: a second submit before then takes
    # the next version instead of overwriting this one.
    reserve_playblast(playblast_path)
    daily_path = get_daily_path(entity_uri, department_name, purpose)
    version_name = playblast_version_of(playblast_path)

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
        build, config, paths, entity=entity['uri'],
    )
