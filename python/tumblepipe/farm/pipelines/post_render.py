"""The jobs that follow a shot's render or composite: the review chain.

The render pipeline (``pipelines/render.py``) ends this way once its frames
exist: each channel's beauty is encoded to a review mp4, the channels are
stacked into a slapcomp that is encoded and posted to Discord, and with
*Copy to edit* the shot's best AOVs are synced into the edit. The composite
pipeline (``pipelines/composite.py``) uses the same jobs, but its
cross-channel layer is the merged comp it renders itself, so it has no
slapcomp job.

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
from tumblepipe.farm.specs import frame_ranges as frame_list
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
    # The frames in the cut (the range minus its pre/post roll), when the
    # submitter knows them. The preview's check frames come from it: a
    # first or last frame on the roll is a handle nobody sees.
    cut_range: tuple[int, int] | None = None
    # The Preview Frames List (``farm.specs.preview_frames``), already checked
    # against the range: the frames a preview renders, in this order. None
    # (or empty) previews the first, middle and last frame of the cut.
    preview_frames: tuple[int, ...] | None = None
    # A multi-range render's ranges (``farm.specs.frame_ranges``, #84),
    # normalised, at least two; None renders first..last. Its frames are
    # rendered, comped and encoded, but never published: no daily, no
    # copy to the edit.
    frame_ranges: tuple[tuple[int, int], ...] | None = None

    @property
    def frames(self) -> BlockRange:
        return BlockRange(self.first_frame, self.last_frame, self.step_size)

    @property
    def publishes(self) -> bool:
        """Whether the review chain publishes: writes the dailies and may
        copy to the edit. A multi-range render does not."""
        return not self.frame_ranges

    @property
    def frame_ranges_entry(self) -> dict:
        """The ``frame_ranges`` a job config of this shot carries, ``{}``
        for an ordinary render."""
        return frame_list.config_entry(self.frame_ranges or ())

    @property
    def middle_frame(self) -> int:
        return self.frames.frame(0.5)

    @property
    def check_frames(self) -> tuple[int, ...]:
        """What a preview renders and its notify posts: the Preview Frames
        List when one was given, else the first, middle and last frame of
        the cut (else of the range)."""
        if self.preview_frames:
            return tuple(self.preview_frames)
        if self.frame_ranges:
            # The middle of the listed frames: the middle of the span may
            # lie in a gap between two ranges, where nothing is rendered.
            listed = frame_list.frames(self.frame_ranges, self.step_size)
            return listed[0], listed[(len(listed) - 1) // 2], listed[-1]
        if self.cut_range is None:
            return self.first_frame, self.middle_frame, self.last_frame
        first, last = self.cut_range
        return first, BlockRange(first, last, 1).frame(0.5), last


def write_framestack_context(
    shot: Shot,
    receipt_path: Path,
    render_department: str,
    channel: str,
    version_name: str,
    reads: dict | None = None
    ) -> None:
    """The ``context.json`` of a rendered frame stack, beside its versions.

    An on-disk format the render readers (th::import_render, the AOV
    resolve) key on. Nothing reads ``channel`` back — the channel is the
    stack's own directory — so stacks written before the rename, which spell
    it ``variant``, need no reader for it. A composite version also records
    what it read, ``reads = {channel: {department, version}}``.
    """
    store_json(receipt_path.parent / 'context.json', dict(
        entity = str(shot.entity_uri),
        department = shot.department,
        channel = channel,
        render_department_name = render_department,
        version_name = version_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        step_size = shot.step_size,
        # A multi-range render's (#84): the version holds only these frames
        # of first..last, so it never reads as a complete render
        **shot.frame_ranges_entry,
        **({} if reads is None else dict(reads = reads))
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
        output_paths = list(map(path_str, output_paths)),
        **shot.frame_ranges_entry
    ), dict(), shot.staging_path)


def layer_mp4_job(
    shot: Shot,
    render_department: str,
    channel: str,
    version_name: str,
    priority: int
    ) -> Job:
    """A channel's beauty, encoded to its versioned review mp4 and daily
    (no daily for a multi-range render: it is not published)."""
    uri, purpose = shot.entity_uri, shot.purpose
    output_paths = [
        get_layer_playblast_path(uri, render_department, channel, version_name, purpose),
    ]
    if shot.publishes:
        output_paths.append(get_layer_daily_path(uri, render_department, channel, purpose))
    return _mp4_job(
        shot,
        f'mp4 layer {render_department} {channel} {version_name}',
        priority,
        get_aov_frame_path(
            uri, render_department, channel, version_name,
            'beauty', '####', 'exr', purpose
        ),
        output_paths
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
        output_path = path_str(output_path),
        **shot.frame_ranges_entry
    ), dict(), shot.staging_path)
    write_framestack_context(
        shot, receipt_path, render_department, SLAPCOMP_CHANNEL, version_name
    )
    return job, version_name


def slapcomp_mp4_job(shot: Shot, render_department: str, version_name: str, priority: int) -> Job:
    """The slapcomp, encoded to the department's review mp4 and daily
    (no daily for a multi-range render: it is not published)."""
    uri, purpose = shot.entity_uri, shot.purpose
    output_paths = [get_playblast_path(uri, render_department, version_name, purpose)]
    if shot.publishes:
        output_paths.append(get_daily_path(uri, render_department, purpose))
    return _mp4_job(
        shot,
        f'mp4 slapcomp {render_department} {version_name}',
        priority,
        get_frame_path(
            uri, render_department, SLAPCOMP_CHANNEL, version_name,
            '####', 'exr', purpose
        ),
        output_paths
    )


def frames_notify_job(
    shot: Shot,
    render_department: str,
    channel: str,
    version_name: str,
    discord_channel: str,
    frame_path: Path | None = None
    ) -> Job:
    """Post a channel's preview frames (``Shot.check_frames``: first,
    middle and last, or the Preview Frames List) of its beauty, or of
    ``frame_path``, a frame pattern, such as a merged comp's."""
    uri = shot.entity_uri
    if frame_path is None:
        frame_path = get_aov_frame_path(
            uri, render_department, channel, version_name,
            'beauty', '####', 'exr', shot.purpose
        )
    return notify_job.build(dict(
        title = f'notify partial {render_department} {version_name}',
        priority = notify_job.PRIORITY,
        pool_name = shot.pool_name,
        user_name = shot.user_name,
        channel_name = discord_channel,
        message = f'{uri} - {render_department} - {version_name}',
        command = dict(
            mode = 'partial',
            frame_path = path_str(frame_path),
            frames = list(shot.check_frames)
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
    sync (with *Copy to edit*, never for a multi-range render, which is not
    published), slapcomp -> its mp4 -> its notify.

    Returns the names later work should depend on.
    """
    if copy_to_edit and shot.publishes:
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
