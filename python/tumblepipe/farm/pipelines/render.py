"""The render pipeline: render a shot's channels, denoise them, and end in
the review chain (``post_render``).

Per channel, a *partial* render does the preview frames (first, middle and
last, or the Preview Frames List, ``farm.specs.preview_frames``)
(-> denoise -> a notify posting them), a *full* render the whole range
(-> denoise -> the channel's review mp4); a full render then adds the
cross-channel chain: sync to the edit, slapcomp, its mp4 and notify.
Each channel keeps its own version counter.

Config: ``specs.render_pipeline``.
"""

from pathlib import Path
import logging

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.jobs import denoise as denoise_job
from tumblepipe.farm.jobs import render as render_job
from tumblepipe.farm.pipelines import post_render
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs.preview_frames import CONFIG_KEY as PREVIEW_FRAMES_KEY
from tumblepipe.pipe import render_layers, slap_comp
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
# (the preview frames and their denoise) runs at PREVIEW_PRIORITY
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
        staging_path = staging_path,
        cut_range = _cut_range(settings),
        preview_frames = _preview_frames(settings),
        frame_ranges = frame_ranges.from_config(settings)
    )


def _cut_range(settings: dict) -> tuple[int, int] | None:
    cut_range = settings.get('cut_range')
    return None if cut_range is None else (cut_range[0], cut_range[1])


def _preview_frames(settings: dict) -> tuple[int, ...] | None:
    """The Preview Frames List the submitter checked (``farm.specs.preview_frames``),
    None for the default first, middle and last frame."""
    frames = settings.get(PREVIEW_FRAMES_KEY)
    return tuple(frames) if frames else None


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
    """A channel's render (partial: the preview frames) and the version it writes."""
    settings = config['settings']
    render_department = settings['render_department_name']
    receipt_path = _receipt_path(shot, render_department, channel, version_name)
    version_name = receipt_path.parent.name
    partial = task_key == 'partial_render'
    # A render layer renders its own RenderPass, which the collapse authored
    # from the same channel_prims (pipe.render_layers).
    render_pass = render_layers.render_pass(shot.entity_uri, channel)
    job_config = dict(
        title = f"{'partial' if partial else 'full'} render {channel} {version_name}",
        priority = config['tasks'][task_key]['priority'],
        pool_name = shot.pool_name,
        tile_count = settings['tile_count'],
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = list(shot.check_frames) if partial else [],
        step_size = 1 if partial else shot.step_size,
        batch_size = 1 if partial else settings['batch_size'],
        input_path = settings['input_paths'][channel],
        receipt_path = path_str(receipt_path),
        output_paths = {
            aov_name: path_str(aov_path)
            for aov_name, aov_path in _aov_paths(
                shot, render_department, channel, version_name, aov_names
            ).items()
        },
        # A multi-range full render: only its ranges, in blocks (#84)
        **({} if partial else shot.frame_ranges_entry)
    )
    if render_pass is not None:
        job_config['render_pass'] = render_pass
    job_paths = paths
    # The entity's newest published Copernicus slap comp, bundled into the
    # job as it is now (pipe.slap_comp): a later publish changes later
    # submits only.
    published = slap_comp.farm_program(shot.entity_uri)
    if published is not None:
        problem = slap_comp.tiles_problem(shot.entity_uri, job_config['tile_count'])
        if problem is not None:
            raise ValueError(problem)
        bundled = slap_comp.bundle_path(published)
        job_paths = {**paths, published.program_path: bundled}
        job_config['slap_comp'] = path_str(bundled)
        job_config['title'] += f' + slap comp {published.version_name}'
    job = render_job.build(job_config, job_paths, shot.staging_path)
    post_render.write_framestack_context(
        shot, receipt_path, render_department, channel, version_name
    )
    return job, version_name


def _denoise_job(
    config, shot, channel, aov_names, task_key, render_version_name,
    version_name=None
    ) -> tuple[Job, str]:
    """A channel's denoise of its render version, and the version it writes:
    ``version_name`` (the partial denoise's, for the full one), else the
    channel's next denoise version."""
    render_department = config['settings']['render_department_name']
    receipt_path = _receipt_path(shot, DENOISE_DEPARTMENT, channel, version_name)
    version_name = receipt_path.parent.name
    partial = task_key == 'partial_render'
    job = denoise_job.build(dict(
        title = f"{'partial' if partial else 'full'} denoise {channel} {version_name}",
        priority = config['tasks'][task_key]['priority'],
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = list(shot.check_frames) if partial else [],
        # The frames the render rendered: a stepped render has no
        # in-between frames to denoise.
        step_size = 1 if partial else shot.step_size,
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
        },
        # The frames the render rendered (#84)
        **({} if partial else shot.frame_ranges_entry)
    ), dict(), shot.staging_path)
    # What it denoises, so a comp's "best" can tell this version from a
    # stale one once a newer render lands (pipe.comp.is_stale)
    post_render.write_framestack_context(
        shot, receipt_path, DENOISE_DEPARTMENT, channel, version_name,
        reads = {channel: dict(department=render_department, version=render_version_name)},
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
    denoise_versions = {}
    terminal_jobs = []

    # Partial: the preview frames of each channel, posted
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
                denoise_versions[channel] = frames_version
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
                # Into the partial denoise's version, as the full render
                # goes into the partial render's: one version per submit
                # (QA 2026-11-10: the full denoise took the next version,
                # leaving a three-frame denoise version beside it).
                job, review_version = _denoise_job(
                    config, shot, channel, aov_names, 'full_render', version_name,
                    denoise_versions.get(channel)
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

        # Farm Submit's Comp column: re-comp the shot once the frames the
        # comp reads (this submit's denoise, else its render: the imports'
        # best passes over an older denoise, pipe.comp.is_stale) are on disk.
        # A workfile comp with no th::import_render (#80) reads exactly
        # those frames: ``renders``, each channel's version this submit writes.
        if config.get('composite') is not None:
            from tumblepipe.farm.pipelines import composite as composite_pipeline
            terminal_jobs = terminal_jobs + composite_pipeline.build(
                composite_pipeline.chained_config(
                    shot.entity_uri, config['composite'],
                    [
                        dict(channel = channel, department = review_department,
                             version = review_versions[channel])
                        for channel in slapcomp_channels
                    ],
                    user_name = settings['user_name'],
                    purpose = settings['purpose'],
                    first_frame = settings['first_frame'],
                    last_frame = settings['last_frame'],
                    temp_path = temp_path,
                    paths = paths,
                ),
                paths, temp_path, jobs, deps,
                depends_on = review_jobs,
                prefix = 'comp_',
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
    ), build, config, paths, entity=config['entity']['uri'])
