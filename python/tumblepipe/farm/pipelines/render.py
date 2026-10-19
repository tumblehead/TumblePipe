"""The render pipeline: render a shot's channels, denoise them, and end in
the review chain (``post_render``).

Per channel, a *partial* render does the first, middle and last frames
(-> denoise -> a notify posting them), a *full* render the whole range
(-> denoise -> the channel's review mp4); a full render then adds the
cross-channel chain: sync to the edit, slapcomp, its mp4 and notify.
Each channel keeps its own version counter.

Config: ``specs.render_pipeline``. Command line:
``python -m tumblepipe.farm.pipelines.render <config.json>``.
"""

from pathlib import Path
import logging
import sys

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.jobs import denoise as denoise_job
from tumblepipe.farm.jobs import render as render_job
from tumblepipe.farm.pipelines import post_render
from tumblepipe.farm.pipelines._batch import batch_title, run_cli, submit_batch
from tumblepipe.farm.specs import render_pipeline as render_pipeline_spec
from tumblepipe.pipe.paths import (
    get_aov_frame_path,
    get_frame_path,
    get_next_frame_path,
)
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri

DENOISE_DEPARTMENT = 'denoise'
FULL_DENOISE_BATCH_SIZE = 20

# Farm priority bands: notify (jobs.notify.PRIORITY, 100) > publish
# (jobs.publish.PRIORITY, 90) > preview > render. The preview chain
# (first/middle/last frames and their denoise) runs at PREVIEW_PRIORITY
# whatever the submission asked for, so it jumps other shots' full renders.
# The full-range chain keeps the artist's priority for ordering renders among
# themselves, capped at RENDER_PRIORITY_MAX so it never outranks a preview or
# a publish.
PREVIEW_PRIORITY = 80
RENDER_PRIORITY_MAX = 70


def banded_priorities(tasks: dict) -> dict:
    """``tasks`` with the partial/full render priorities moved into their bands."""
    tasks = dict(tasks)
    if 'partial_render' in tasks:
        tasks['partial_render'] = dict(
            tasks['partial_render'], priority=PREVIEW_PRIORITY,
        )
    if 'full_render' in tasks:
        tasks['full_render'] = dict(
            tasks['full_render'],
            priority=min(tasks['full_render']['priority'], RENDER_PRIORITY_MAX),
        )
    return tasks


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


def _aov_paths(shot, render_department, channel, version_name, aov_names) -> dict[str, Path]:
    return {
        aov_name: get_aov_frame_path(
            shot.entity_uri, render_department, channel, version_name,
            aov_name, '####', 'exr', shot.purpose
        )
        for aov_name in aov_names
    }


def _receipt_path(shot, render_department, channel, version_name=None) -> Path:
    """The receipt pattern of ``version_name``, or of the channel's next version."""
    if version_name is None:
        return get_next_frame_path(
            shot.entity_uri, render_department, channel, '####', 'json', shot.purpose
        )
    return get_frame_path(
        shot.entity_uri, render_department, channel, version_name,
        '####', 'json', shot.purpose
    )


def _render_job(
    config, shot, paths, channel, aov_names, task_key, version_name=None
    ) -> tuple[Job, str]:
    """A channel's render (partial: three frames) and the version it writes."""
    settings = config['settings']
    render_department = settings['render_department_name']
    receipt_path = _receipt_path(shot, render_department, channel, version_name)
    version_name = receipt_path.parent.name
    partial = task_key == 'partial_render'
    job = render_job.build(dict(
        title = f"{'partial' if partial else 'full'} render {channel} {version_name}",
        priority = config['tasks'][task_key]['priority'],
        pool_name = shot.pool_name,
        tile_count = settings['tile_count'],
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = [shot.first_frame, shot.middle_frame, shot.last_frame] if partial else [],
        step_size = 1 if partial else shot.step_size,
        batch_size = 1 if partial else settings['batch_size'],
        input_path = settings['input_paths'][channel],
        receipt_path = path_str(receipt_path),
        output_paths = {
            aov_name: path_str(aov_path)
            for aov_name, aov_path in _aov_paths(
                shot, render_department, channel, version_name, aov_names
            ).items()
        }
    ), paths, shot.staging_path)
    post_render.write_framestack_context(
        shot, receipt_path, render_department, channel, version_name
    )
    return job, version_name


def _denoise_job(
    config, shot, channel, aov_names, task_key, render_version_name
    ) -> tuple[Job, str]:
    """A channel's denoise of its render version, and the version it writes."""
    render_department = config['settings']['render_department_name']
    receipt_path = _receipt_path(shot, DENOISE_DEPARTMENT, channel)
    version_name = receipt_path.parent.name
    partial = task_key == 'partial_render'
    job = denoise_job.build(dict(
        title = f"{'partial' if partial else 'full'} denoise {channel} {version_name}",
        priority = config['tasks'][task_key]['priority'],
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = [shot.first_frame, shot.middle_frame, shot.last_frame] if partial else [],
        step_size = 1,
        batch_size = 1 if partial else FULL_DENOISE_BATCH_SIZE,
        input_paths = {
            aov_name: path_str(aov_path)
            for aov_name, aov_path in _aov_paths(
                shot, render_department, channel, render_version_name, aov_names
            ).items()
        },
        receipt_path = path_str(receipt_path),
        output_paths = {
            aov_name: path_str(aov_path)
            for aov_name, aov_path in _aov_paths(
                shot, DENOISE_DEPARTMENT, channel, version_name, aov_names
            ).items()
        }
    ), dict(), shot.staging_path)
    post_render.write_framestack_context(
        shot, receipt_path, DENOISE_DEPARTMENT, channel, version_name
    )
    return job, version_name


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the render jobs of ``config`` to ``jobs``/``deps``, after
    ``depends_on``; returns the names later work should depend on."""
    depends_on = list(depends_on or [])
    # Every caller (direct, collapse, stage, export) lands here: band once
    # for all.
    config = dict(config, tasks=banded_priorities(config['tasks']))
    settings = config['settings']
    tasks = config['tasks']
    shot = _shot(config, temp_path)
    render_department = settings['render_department_name']
    channel_names = settings['channel_names']
    render_settings = load_json(Path(settings['render_settings_path']))
    aov_names = render_settings['aov_names']

    def _add(name, job, job_deps):
        jobs[name] = job
        deps[name] = list(job_deps)

    render_versions = {}
    terminal_jobs = []

    # Partial: first, middle and last frames of each channel, posted
    if 'partial_render' in tasks:
        partial = tasks['partial_render']
        for channel in channel_names:
            job, version_name = _render_job(
                config, shot, paths, channel, aov_names, 'partial_render'
            )
            render_versions[channel] = version_name
            _add(f'partial_render_{channel}', job, depends_on)
            frames_job, frames_department, frames_version = (
                f'partial_render_{channel}', render_department, version_name
            )
            if partial['denoise']:
                job, frames_version = _denoise_job(
                    config, shot, channel, aov_names, 'partial_render', version_name
                )
                _add(f'partial_denoise_{channel}', job, [frames_job])
                frames_job, frames_department = f'partial_denoise_{channel}', DENOISE_DEPARTMENT
            # The Discord channel to post in is the task's, not the render
            # channel: reusing one name for both pointed the frame path at
            # denoise/<discord channel>/ and every partial notify failed.
            _add(f'partial_notify_{channel}', post_render.frames_notify_job(
                shot, frames_department, channel, frames_version,
                partial['channel_name']
            ), [frames_job])
            terminal_jobs.append(f'partial_notify_{channel}')

    # Full: the whole range of each channel (into the partial's version),
    # then the review chain across the channels
    if 'full_render' in tasks:
        full = tasks['full_render']
        review_department = DENOISE_DEPARTMENT if full['denoise'] else render_department
        review_versions = {}
        review_jobs = []
        for channel in channel_names:
            job, version_name = _render_job(
                config, shot, paths, channel, aov_names, 'full_render',
                render_versions.get(channel)
            )
            render_deps = (
                [f'partial_render_{channel}'] if 'partial_render' in tasks
                else depends_on
            )
            _add(f'full_render_{channel}', job, render_deps)
            review_job, review_version = f'full_render_{channel}', version_name
            if full['denoise']:
                job, review_version = _denoise_job(
                    config, shot, channel, aov_names, 'full_render', version_name
                )
                _add(f'full_denoise_{channel}', job, [review_job])
                review_job = f'full_denoise_{channel}'
            review_versions[channel] = review_version
            review_jobs.append(review_job)
            _add(f'layer_mp4_{channel}', post_render.layer_mp4_job(
                shot, review_department, channel, review_version, full['priority']
            ), [review_job])

        # The slapcomp stacks the channels in the render settings' order,
        # each at its own version.
        slapcomp_channels = render_settings['channel_names']
        terminal_jobs = post_render.add_review_chain(
            jobs, deps, shot,
            render_department = review_department,
            input_paths = {
                channel: _aov_paths(
                    shot, review_department, channel,
                    review_versions[channel], aov_names
                )
                for channel in slapcomp_channels
            },
            after = review_jobs,
            priority = full['priority'],
            discord_channel = full['channel_name'],
            copy_to_edit = settings['copy_to_edit']
        )

    logging.debug(f'Render jobs: {list(jobs)}')
    return terminal_jobs


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the render pipeline of ``config`` as one batch."""
    settings = config['settings']
    channel_names = settings['channel_names']
    return submit_batch(batch_title(
        settings['purpose'],
        config['entity']['uri'],
        f"[{', '.join(channel_names)}]",
        settings['user_name']
    ), build, config, paths)


if __name__ == '__main__':
    sys.exit(run_cli(render_pipeline_spec.is_valid_config, submit))
