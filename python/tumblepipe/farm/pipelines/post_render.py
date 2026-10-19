"""The jobs that follow a shot's render or composite: the review chain.

Both the render pipeline (``pipelines/render.py``) and the composite
pipeline (``pipelines/composite.py``) end the same way once their frames
exist: each channel's beauty is encoded to a review mp4, the channels are
stacked into a slapcomp that is encoded and posted to Discord, and with
*Copy to edit* the shot's best AOVs are synced into the edit. This module
builds those jobs, so the two pipelines differ only in what they render.

Every frame stack a job writes gets a ``context.json`` beside its versions
(:func:`write_framestack_context`); the render readers rely on its keys.
"""

from dataclasses import dataclass
from pathlib import Path

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.config.timeline import BlockRange, require_fps
from tumblepipe.farm.jobs import mp4 as mp4_job
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.jobs import slapcomp as slapcomp_job
from tumblepipe.farm.jobs import sync as sync_job
from tumblepipe.pipe.paths import (
    get_aov_frame_path,
    get_daily_path,
    get_frame_path,
    get_layer_daily_path,
    get_layer_playblast_path,
    get_next_frame_path,
    get_playblast_path,
)
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri

SLAPCOMP_CHANNEL = 'slapcomp'

# Copy to edit runs ahead of renders still queued behind it.
SYNC_PRIORITY = 90


@dataclass(frozen=True)
class Shot:
    """What every job of a shot's review chain shares."""
    entity_uri: Uri
    department: str     # the shot department the render or comp is of
    purpose: str
    user_name: str
    pool_name: str
    first_frame: int
    last_frame: int
    step_size: int
    staging_path: Path

    @property
    def frames(self) -> BlockRange:
        return BlockRange(self.first_frame, self.last_frame, self.step_size)

    @property
    def middle_frame(self) -> int:
        return self.frames.frame(0.5)


def write_framestack_context(
    shot: Shot,
    receipt_path: Path,
    render_department: str,
    channel: str,
    version_name: str
    ) -> None:
    """The ``context.json`` of a rendered frame stack, beside its versions.

    An on-disk format the render readers (build_comp, the AOV resolve) key
    on. Nothing reads ``channel`` back — the channel is the stack's own
    directory — so stacks written before the rename, which spell it
    ``variant``, need no reader for it.
    """
    store_json(receipt_path.parent / 'context.json', dict(
        entity = str(shot.entity_uri),
        department = shot.department,
        channel = channel,
        render_department_name = render_department,
        version_name = version_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        step_size = shot.step_size
    ))


def sync_job_for(shot: Shot) -> Job:
    """Copy the shot's best AOVs into the edit, resolved when the job runs
    (so the frames rendered by the jobs it waits for are included)."""
    if shot.entity_uri.segments[0] != 'shots':
        raise ValueError(f'Copy to edit only syncs shots: {shot.entity_uri}')
    return sync_job.build(dict(
        title = f'sync {shot.entity_uri}',
        priority = SYNC_PRIORITY,
        pool_name = shot.pool_name,
        entity_uri = str(shot.entity_uri),
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        purpose = shot.purpose
    ), dict(), shot.staging_path)


def _mp4_job(shot: Shot, title: str, priority: int, input_path: Path, output_paths: list[Path]) -> Job:
    return mp4_job.build(dict(
        title = title,
        fps = require_fps(shot.entity_uri),
        priority = priority,
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        step_size = shot.step_size,
        input_path = path_str(input_path),
        output_paths = list(map(path_str, output_paths))
    ), dict(), shot.staging_path)


def layer_mp4_job(
    shot: Shot,
    render_department: str,
    channel: str,
    version_name: str,
    priority: int
    ) -> Job:
    """A channel's beauty, encoded to its versioned review mp4 and daily."""
    uri, purpose = shot.entity_uri, shot.purpose
    return _mp4_job(
        shot,
        f'mp4 layer {render_department} {channel} {version_name}',
        priority,
        get_aov_frame_path(
            uri, render_department, channel, version_name,
            'beauty', '####', 'exr', purpose
        ),
        [
            get_layer_playblast_path(uri, render_department, channel, version_name, purpose),
            get_layer_daily_path(uri, render_department, channel, purpose),
        ]
    )


def slapcomp_job_for(
    shot: Shot,
    render_department: str,
    input_paths: dict[str, dict[str, Path]],
    priority: int
    ) -> tuple[Job, str]:
    """The slapcomp of ``input_paths`` (``{channel: {aov: frame pattern}}``,
    the bottom of the stack first) and the version it writes."""
    uri, purpose = shot.entity_uri, shot.purpose
    receipt_path = get_next_frame_path(
        uri, render_department, SLAPCOMP_CHANNEL, '####', 'json', purpose
    )
    version_name = receipt_path.parent.name
    output_path = get_frame_path(
        uri, render_department, SLAPCOMP_CHANNEL, version_name,
        '####', 'exr', purpose
    )
    job = slapcomp_job.build(dict(
        title = f'slapcomp {render_department} {version_name}',
        priority = priority,
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        step_size = shot.step_size,
        input_paths = {
            channel: {
                aov_name: path_str(aov_path)
                for aov_name, aov_path in aov_paths.items()
            }
            for channel, aov_paths in input_paths.items()
        },
        receipt_path = path_str(receipt_path),
        output_path = path_str(output_path)
    ), dict(), shot.staging_path)
    write_framestack_context(
        shot, receipt_path, render_department, SLAPCOMP_CHANNEL, version_name
    )
    return job, version_name


def slapcomp_mp4_job(shot: Shot, render_department: str, version_name: str, priority: int) -> Job:
    """The slapcomp, encoded to the department's review mp4 and daily."""
    uri, purpose = shot.entity_uri, shot.purpose
    return _mp4_job(
        shot,
        f'mp4 slapcomp {render_department} {version_name}',
        priority,
        get_frame_path(
            uri, render_department, SLAPCOMP_CHANNEL, version_name,
            '####', 'exr', purpose
        ),
        [
            get_playblast_path(uri, render_department, version_name, purpose),
            get_daily_path(uri, render_department, purpose),
        ]
    )


def frames_notify_job(
    shot: Shot,
    render_department: str,
    channel: str,
    version_name: str,
    discord_channel: str
    ) -> Job:
    """Post a channel's first, middle and last beauty frames."""
    uri = shot.entity_uri
    return notify_job.build(dict(
        title = f'notify partial {render_department} {version_name}',
        priority = notify_job.PRIORITY,
        pool_name = shot.pool_name,
        user_name = shot.user_name,
        channel_name = discord_channel,
        message = f'{uri} - {render_department} - {version_name}',
        command = dict(
            mode = 'partial',
            frame_path = path_str(get_aov_frame_path(
                uri, render_department, channel, version_name,
                'beauty', '####', 'exr', shot.purpose
            )),
            first_frame = shot.first_frame,
            middle_frame = shot.middle_frame,
            last_frame = shot.last_frame
        )
    ), dict(), shot.staging_path)


def slapcomp_notify_job(
    shot: Shot,
    render_department: str,
    version_name: str,
    discord_channel: str
    ) -> Job:
    """Post the slapcomp's review mp4."""
    uri = shot.entity_uri
    return notify_job.build(dict(
        title = f'notify slapcomp {render_department} {version_name}',
        priority = notify_job.PRIORITY,
        pool_name = shot.pool_name,
        user_name = shot.user_name,
        channel_name = discord_channel,
        message = f'{uri} - {render_department}/{SLAPCOMP_CHANNEL} - {version_name}',
        command = dict(
            mode = 'full',
            video_path = path_str(get_playblast_path(
                uri, render_department, version_name, shot.purpose
            ))
        )
    ), dict(), shot.staging_path)


def add_review_chain(
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    shot: Shot,
    *,
    render_department: str,
    input_paths: dict[str, dict[str, Path]],
    after: list[str],
    priority: int,
    discord_channel: str,
    copy_to_edit: bool
    ) -> list[str]:
    """Add the cross-channel end of the chain, after the jobs in ``after``:
    sync (with *Copy to edit*), slapcomp -> its mp4 -> its notify.

    Returns the names later work should depend on.
    """
    if copy_to_edit:
        jobs['sync'] = sync_job_for(shot)
        deps['sync'] = list(after)
    slapcomp, version_name = slapcomp_job_for(
        shot, render_department, input_paths, priority
    )
    jobs['slapcomp'] = slapcomp
    deps['slapcomp'] = list(after)
    jobs['slapcomp_mp4'] = slapcomp_mp4_job(
        shot, render_department, version_name, priority
    )
    deps['slapcomp_mp4'] = ['slapcomp']
    jobs['slapcomp_notify'] = slapcomp_notify_job(
        shot, render_department, version_name, discord_channel
    )
    deps['slapcomp_notify'] = ['slapcomp_mp4']
    return ['slapcomp_notify']
