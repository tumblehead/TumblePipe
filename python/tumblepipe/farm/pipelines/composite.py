"""The composite pipeline: cook a comp workfile (``th::build_comp``) on the
farm, and end in the review chain (``post_render``).

A *partial* composite does the first, middle and last frames and posts
them; a *full* composite does the whole range (into the partial's
version), encodes each layer's review mp4, and adds the slapcomp chain
over the latest beauty of every channel.

Command line: ``python -m tumblepipe.farm.pipelines.composite <config.json>``.

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'input_path': 'string',         # the comp workfile, relative to the job's data dir
        'node_path': 'string',
        'layer_names': ['main', 'mirror'],
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
        'copy_to_edit': 'bool'
    },
    'tasks': {                          # either or both
        'partial_composite': {'channel_name': 'string'},
        'full_composite': {'channel_name': 'string'}
    }
}

``tasks.*.channel_name`` is the Discord channel the notify posts to.
"""

from pathlib import Path
import logging
import sys

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_str,
    check_str_list,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.jobs import composite as composite_job
from tumblepipe.farm.pipelines import post_render
from tumblepipe.farm.pipelines._batch import batch_title, run_cli, submit_batch
from tumblepipe.pipe.paths import (
    get_aov_frame_path,
    get_frame_path,
    get_next_frame_path,
    get_render_context,
)
from tumblepipe.util.uri import Uri

COMPOSITE_DEPARTMENT = 'composite'
PARTIAL_PRIORITY = 90

COMPOSITE_TASK_KEYS = ('partial_composite', 'full_composite')


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_str(settings, 'input_path'): return False
    if not check_str(settings, 'node_path'): return False
    if not check_str_list(settings, 'layer_names'): return False
    if len(settings['layer_names']) == 0: return False
    if not check_int(settings, 'batch_size'): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    return True


def _valid_tasks(tasks) -> bool:
    if not isinstance(tasks, dict): return False
    for key in COMPOSITE_TASK_KEYS:
        if key not in tasks: continue
        if not isinstance(tasks[key], dict): return False
        if not check_str(tasks[key], 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    if not _valid_tasks(config.get('tasks')): return False
    return True


def _shot(config: dict, staging_path: Path) -> post_render.Shot:
    settings = config['settings']
    return post_render.Shot(
        entity_uri = Uri.parse(config['entity']['uri']),
        department = config['entity']['department'],
        purpose = settings['purpose'],
        user_name = settings['user_name'],
        pool_name = settings['pool_name'],
        first_frame = settings['first_frame'],
        last_frame = settings['last_frame'],
        step_size = settings['step_size'],
        staging_path = staging_path
    )


def _composite_job(config, shot, paths, partial, version_name=None) -> tuple[Job, str]:
    """The composite of every layer (partial: three frames), and the version
    it writes. Versions are counted in the first layer's folder."""
    settings = config['settings']
    layer_names = settings['layer_names']
    uri, purpose = shot.entity_uri, shot.purpose
    if version_name is None:
        version_name = get_next_frame_path(
            uri, COMPOSITE_DEPARTMENT, layer_names[0], '####', 'json', purpose
        ).parent.name
    receipt_path = get_frame_path(
        uri, COMPOSITE_DEPARTMENT, layer_names[0], version_name,
        '####', 'json', purpose
    )
    job = composite_job.build(dict(
        entity = config['entity'],
        title = f"{'partial' if partial else 'full'} composite {version_name}",
        priority = PARTIAL_PRIORITY if partial else settings['priority'],
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = [shot.first_frame, shot.middle_frame, shot.last_frame] if partial else [],
        step_size = 1 if partial else shot.step_size,
        batch_size = 1 if partial else settings['batch_size'],
        receipt_path = path_str(receipt_path),
        input_path = settings['input_path'],
        node_path = settings['node_path'],
        layer_names = layer_names,
        output_paths = {
            layer_name: path_str(get_aov_frame_path(
                uri, COMPOSITE_DEPARTMENT, layer_name, version_name,
                'beauty', '####', 'exr', purpose
            ))
            for layer_name in layer_names
        }
    ), paths, shot.staging_path)
    for layer_name in layer_names:
        post_render.write_framestack_context(
            shot,
            get_frame_path(
                uri, COMPOSITE_DEPARTMENT, layer_name, version_name,
                '####', 'json', purpose
            ),
            COMPOSITE_DEPARTMENT, layer_name, version_name
        )
    return job, version_name


def _latest_beauties(shot: post_render.Shot) -> dict[str, dict[str, Path]]:
    """Each channel's latest beauty across the departments (the higher ones
    first), in the shot's channel order: what the slapcomp stacks. The
    beauty is RGBA; the slapcomp takes the colour and the alpha from it."""
    if shot.entity_uri.segments[0] != 'shots':
        raise ValueError(f'A slapcomp stacks a shot\'s channels: {shot.entity_uri}')
    latest_aovs = get_render_context(shot.entity_uri, purpose=shot.purpose).resolve_latest_aovs(
        [d.name for d in list_departments('shots')],
        [d.name for d in list_departments('render')],
        min_shot_department = None,
        min_render_department = None,
        aov_filter = lambda aov_name: aov_name == 'beauty'
    )
    input_paths = {
        channel: {
            aov_name: aov.get_aov_frame_path('####')
            for aov_name, (_, _, aov, _) in latest_aovs[channel].items()
        }
        for channel in list_channels(shot.entity_uri)
        if channel in latest_aovs
    }
    if not input_paths:
        raise ValueError(
            f'No rendered beauty to slapcomp for {shot.entity_uri}: render '
            'its channels first.'
        )
    return input_paths


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the composite jobs of ``config`` to ``jobs``/``deps``, after
    ``depends_on``; returns the names later work should depend on."""
    depends_on = list(depends_on or [])
    settings = config['settings']
    tasks = config['tasks']
    shot = _shot(config, temp_path)
    layer_names = settings['layer_names']
    terminal_jobs = []

    version_name = None
    if 'partial_composite' in tasks:
        job, version_name = _composite_job(config, shot, paths, partial=True)
        jobs['partial_composite'] = job
        deps['partial_composite'] = list(depends_on)
        jobs['partial_notify'] = post_render.frames_notify_job(
            shot, COMPOSITE_DEPARTMENT, layer_names[0], version_name,
            tasks['partial_composite']['channel_name']
        )
        deps['partial_notify'] = ['partial_composite']
        terminal_jobs = ['partial_notify']

    if 'full_composite' in tasks:
        job, version_name = _composite_job(
            config, shot, paths, partial=False, version_name=version_name
        )
        jobs['full_composite'] = job
        deps['full_composite'] = (
            ['partial_composite'] if 'partial_composite' in tasks else list(depends_on)
        )
        for layer_name in layer_names:
            jobs[f'layer_mp4_{layer_name}'] = post_render.layer_mp4_job(
                shot, COMPOSITE_DEPARTMENT, layer_name, version_name,
                settings['priority']
            )
            deps[f'layer_mp4_{layer_name}'] = ['full_composite']
        terminal_jobs = post_render.add_review_chain(
            jobs, deps, shot,
            render_department = COMPOSITE_DEPARTMENT,
            input_paths = _latest_beauties(shot),
            after = ['full_composite'],
            priority = settings['priority'],
            discord_channel = tasks['full_composite']['channel_name'],
            copy_to_edit = settings['copy_to_edit']
        )

    logging.debug(f'Composite jobs: {list(jobs)}')
    return terminal_jobs


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the composite pipeline of ``config`` as one batch."""
    settings = config['settings']
    return submit_batch(batch_title(
        settings['purpose'], config['entity']['uri'], settings['user_name']
    ), build, config, paths)


if __name__ == '__main__':
    sys.exit(run_cli(is_valid_config, submit))
