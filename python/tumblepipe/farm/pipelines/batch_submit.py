"""Batch job submission for the Submit Jobs dialog.

This module orchestrates the creation and submission of publish, render and
playblast jobs for one entity, from the settings the dialog resolved for it.

When the same submission publishes the entity *and* previews it (render or
playblast), the preview's input is snapshotted on the farm after the publish
chain and a fresh staged build, by a ``collapse`` job — see
``farm/pipelines/preview.py`` for why it cannot be taken at submit time.

An asset's render is its turntable (#53), always through a collapse job: the
job builds a ``th::lookdev_studio`` workfile around the asset on the farm
(``pipe.turntable``), so the frames are the studio's turntable frames, not
the dialog's.
"""

from dataclasses import dataclass
import logging

from tumblepipe.api import (
    path_str,
    get_user_name,
    api,
    project_temp_dir,
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import (
    DEFAULT_CHANNEL,
    list_channels,
    read_channel_names,
)
from tumblepipe.config.department import (
    get_disabled_departments,
    list_departments,
)
from tumblepipe.farm.backend import get_farm
from tumblepipe.config.timeline import FrameRange, get_frame_range
from tumblepipe.farm.jobs import stage as stage_job
from tumblepipe.farm.jobs import collapse as collapse_job
from tumblepipe.farm.jobs import publish as publish_job
from tumblepipe.farm.pipelines import build as build_pipeline
from tumblepipe.farm.pipelines import render as render_pipeline
from tumblepipe.farm.pipelines import playblast as playblast_pipeline
from tumblepipe.farm.pipelines import edit_render as edit_render_pipeline
from tumblepipe.farm.pipelines import composite as composite_pipeline
from tumblepipe.farm.specs import composite as composite_spec
from tumblepipe.farm.pipelines import preview
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import repair as repair_spec
from tumblepipe.farm.specs import preview_frames
from tumblepipe.farm.specs import submission
from tumblepipe.farm.submit import settings as submit_settings
from tumblepipe.pipe import comp
from tumblepipe.pipe import render_layers
from tumblepipe.pipe import slap_comp
from tumblepipe.pipe import turntable
from tumblepipe.pipe.paths.render import list_render_versions
from tumblepipe.pipe.usd import render_settings
from tumblepipe.farm.pipelines import publish as publish_pipeline
from tumblepipe.farm.pipelines._batch import batch_title, new_batch

class BatchSubmitError(Exception):
    """Error during batch submission."""
    pass


def _build_render_overrides(settings: dict, is_turntable: bool = False) -> dict:
    """``{Karma attribute: value}`` of the render overrides ``settings``
    sends (``submit_settings.RENDER_OVERRIDES``). A key is there only when
    the artist set it: unset, the scene's own render settings render.

    Farm Submit's Camera (``submit_settings.CAMERA_KEY``) rides along under
    ``render_settings.CAMERA_OVERRIDE``, which the collapse authors as the
    settings prim's ``camera`` relationship and checks is on the stage. An
    asset's turntable renders the studio's camera (#53), so it sends none.
    """
    overrides = {
        attribute: settings[key]
        for key, attribute in submit_settings.RENDER_OVERRIDES.items()
        if key in settings
    }
    camera = settings.get(submit_settings.CAMERA_KEY)
    if camera is not None and not is_turntable:
        path = render_settings.valid_camera_override(camera)
        if path is None:
            raise BatchSubmitError(
                f"Camera '{camera}' is not a prim path like /cameras/render_camera"
            )
        overrides[render_settings.CAMERA_OVERRIDE] = path
    return overrides


def publish_department_names(settings: dict, department_names: list[str]) -> list[str]:
    """The ticked departments (``pub_departments``) in pool order. The grid
    shows what is stale and the artist picked, so nothing is skipped behind
    their back; a department turned off on the entity was refused before
    this (``farm.specs.submission``)."""
    chosen = settings['pub_departments']
    unknown = [name for name in chosen if name not in department_names]
    if unknown:
        raise BatchSubmitError(
            f"Invalid publish department(s): {', '.join(unknown)}"
        )
    wanted = set(chosen)
    return [name for name in department_names if name in wanted]


def _log_preview_warnings(entity_uri, kind: str, preview) -> None:
    """Put what a Preview Frames List dropped in the submit log."""
    for warning in preview.warnings:
        logging.warning(f"{entity_uri} {kind}: {warning}")


def _plan_repair(
    entity_uri, target, listed, channels, render_department, denoise, *,
    preview: bool, step: int, turntable: bool,
    ) -> dict | None:
    """The ``repair`` entry of Farm Submit's Into version (#92), None for a
    new version; raises :class:`BatchSubmitError` saying why it cannot be
    done. Reads the versions on disk, so it checks what the dialog could
    only pick from."""
    if target is None or target == repair_spec.TARGET_NEW:
        return None
    if not isinstance(target, str):
        raise BatchSubmitError(f"{entity_uri}: Into version {target!r} is not a version")
    problems = repair_spec.setup_problems(
        target, listed, preview=preview, step=step, turntable=turntable,
    )
    if problems:
        raise BatchSubmitError(f"{entity_uri}: {'; '.join(problems)}")
    denoise_department = render_pipeline.DENOISE_DEPARTMENT
    denoise_versions = {
        channel: list_render_versions(entity_uri, denoise_department, channel)
        for channel in channels
    }
    try:
        entry = repair_spec.plan(
            target, listed, channels,
            render_department=render_department,
            denoise=denoise,
            render_versions={
                channel: list_render_versions(entity_uri, render_department, channel)
                for channel in channels
            },
            denoise_versions=denoise_versions,
        )
    except repair_spec.RepairError as e:
        raise BatchSubmitError(f"{entity_uri}: {e}") from e
    left = repair_spec.unrepaired_denoise(
        channels, render_department=render_department, repair=entry,
        denoise_versions=denoise_versions,
    )
    if left:
        logging.warning(
            f"{entity_uri}: Denoise is off, so {', '.join(left)} keeps the old "
            "frames, and a comp's best pass reads it: tick Denoise to repair it too"
        )
    return entry


def _repair_publishes(entry: dict) -> bool:
    """Whether a repaired version is the shot's render: complete over its
    range (no ranges of its own) and not a check render."""
    return entry['published'] and frame_ranges.CONFIG_KEY not in entry


def _repair_targets(entry: dict) -> str:
    """``default/v0001, fg/v0003``: the versions a repair renders into."""
    return ', '.join(
        f"{channel}/{versions['render']}"
        for channel, versions in entry['versions'].items()
    )


def _repair_entry(entry: dict | None) -> dict:
    """``{'repair': entry}`` for a farm config, ``{}`` for new versions."""
    return {} if entry is None else {repair_spec.CONFIG_KEY: entry}


def _rendered_range(entity_uri) -> tuple[int, int] | None:
    """The frames every channel's best render covers now (what a re-comp's
    default imports read), None when none is rendered or a range is unknown
    (``comp.rendered_range``)."""
    from tumblepipe.pipe.paths import get_render_context
    renders = get_render_context(entity_uri, purpose='render').renders
    departments = [d.name for d in list_departments('render')]
    return comp.rendered_range(renders, departments, list_channels(entity_uri))


def _shot_range(entity_uri) -> tuple[int, int] | None:
    """The shot's range with its rolls, None when it has none."""
    shot_range = get_frame_range(entity_uri)
    if shot_range is None:
        return None
    full = shot_range.full_range()
    return (full.first_frame, full.last_frame)


def _check_preview_department(kind: str, name, department_names, renderable_names):
    """A render or playblast department must be real and renderable.

    The preview composes the pool prefix ending at this department, so a
    non-renderable pick has no layer to end on, and silently composing the
    whole pool is the bug this prevents. (One turned off on the entity, #64,
    was refused before this: ``farm.specs.submission``.)
    """
    if name is None:
        raise BatchSubmitError(f"{kind} department not specified")
    if name not in department_names:
        raise BatchSubmitError(f"Invalid {kind.lower()} department: {name}")
    if name not in renderable_names:
        raise BatchSubmitError(
            f"{kind} department '{name}' is not renderable. "
            f"Renderable departments: {', '.join(renderable_names)}"
        )


# ── The resolve phase ─────────────────────────────────────

@dataclass(frozen=True)
class PublishPlan:
    """The publish chain: ``departments`` in pool order, each after the one
    before."""
    pool: str
    priority: int
    departments: tuple[str, ...]


@dataclass(frozen=True)
class RenderPlan:
    """A checked render: its frames with the rolls (``cut_range`` the cut,
    where the check frames sit), the Frame List's and the Preview Frames
    List's config entries, and the Karma overrides the artist set. A
    ``turntable`` (an asset's, #53) renders the studio's frames on its step,
    always through the collapse job; ``standalone`` renders through a farm
    stage job. ``repair`` is Into version's entry (#92,
    ``farm.specs.repair``), None for new versions."""
    department: str
    channels: list[str]
    pool: str
    priority: int
    tile_count: int
    first_frame: int
    last_frame: int
    cut_range: list[int]
    step: int
    batch_size: int
    denoise: bool
    copy_to_edit: bool
    standalone: bool
    task_keys: tuple[str, ...]
    turntable: bool
    preview_entry: dict
    frame_ranges_entry: dict
    overrides: dict
    repair: dict | None = None


@dataclass(frozen=True)
class PlayblastPlan:
    """A checked playblast (``specs.playblast.valid_look``)."""
    department: str
    pool: str
    priority: int
    res: list
    look: dict


@dataclass(frozen=True)
class EntityPlan:
    """One entity's submission, its settings checked (``_resolve``).
    ``kinds`` are the ticked sections in submit order; ``comp`` is the
    re-comp's block (``specs.composite.valid_recomp``) over ``comp_range``."""
    uri: Uri
    context: str
    kinds: tuple[str, ...]
    publish: PublishPlan | None
    render: RenderPlan | None
    playblast: PlayblastPlan | None
    comp: dict | None
    comp_range: tuple[int, int] | None


def _kinds(settings: dict) -> tuple[str, ...]:
    """The ticked sections, in the order the batch title names them."""
    wanted = (
        ('publish', settings['publish']),
        ('render', settings['render']),
        ('playblast', settings['playblast']),
        ('comp', settings['comp']),
    )
    return tuple(kind for kind, on in wanted if on)


def _resolve(entity_uri: Uri, entity_context: str, settings: dict, kinds) -> EntityPlan:
    """Check ``settings`` for ``entity_uri`` and read what the builders need.

    Every refusal of the settings is raised here (``BatchSubmitError``),
    before the farm is contacted or a staging folder made: it reads the
    project (channels, departments, frame ranges, renders and staged builds
    on disk), never the farm. The builders refuse only what building finds:
    a ticked department with no workfile, a preview input that cannot be
    snapshotted, a comp workfile that cannot be read.
    """
    do_publish = 'publish' in kinds
    do_render = 'render' in kinds
    do_playblast = 'playblast' in kinds
    do_comp = 'comp' in kinds

    if do_publish:
        pub_pool = settings['pub_pool']
        pub_priority = settings['pub_priority']

    if do_render:
        render_department = settings.get('render_department')
        channels = read_channel_names(settings)
        render_pool = settings['render_pool']
        render_priority = settings['render_priority']
        tile_count = settings['tile_count']
        # Refused now, not when the render jobs are built on the farm.
        slap_comp_problem = slap_comp.tiles_problem(entity_uri, tile_count)
        if slap_comp_problem is not None:
            raise BatchSubmitError(slap_comp_problem)
        first_frame = settings.get('first_frame')
        last_frame = settings.get('last_frame')
        pre_roll = settings['pre_roll']
        post_roll = settings['post_roll']
        batch_size = settings['batch_size']
        # The Step (Remy, 2026-11-17): every Nth frame of the range, or of
        # each Frame List range, from its first.
        render_step = settings['render_step']
        if isinstance(render_step, bool) or not isinstance(render_step, int) or render_step < 1:
            raise BatchSubmitError(
                f"Invalid render step for {entity_uri}: {render_step!r} (a whole "
                "number from 1)"
            )
        denoise = settings['denoise']
        copy_to_edit = settings['copy_to_edit']
        standalone = settings['standalone']
        # 'first_middle_last' submits the partial_render chain (3 check
        # frames + notify) instead of the full_render chain (all frames +
        # slapcomp/mp4); 'preview_then_full' submits both, and the render
        # pipeline holds each channel's full render until its partial render
        # is done.
        render_mode = settings['render_mode']
        render_task_keys = submission.RENDER_TASK_KEYS.get(render_mode)
        if render_task_keys is None:
            raise BatchSubmitError(
                f"Unknown render_mode {render_mode!r}: expected one of "
                f"{', '.join(submission.RENDER_TASK_KEYS)}."
            )

    # The re-comp's schedule and frames (specs.composite.valid_recomp). It
    # posts to the comp channel, as an export_comp's submit does. A first
    # or last frame is sent only when the artist set one (a submit_settings
    # override field), else the comp follows the range.
    comp_block = None
    if do_comp:
        comp_block = dict(
            pool_name=settings['comp_pool'],
            priority=settings['comp_priority'],
            batch_size=settings['comp_batch_size'],
            copy_to_edit=settings['comp_copy_to_edit'],
            channel_name='comp',
            step_size=settings['comp_step'],
            preview_frames=settings['comp_preview'],
        )
        for key in ('first_frame', 'last_frame'):
            if settings.get(f'comp_{key}') is not None:
                comp_block[key] = settings[f'comp_{key}']
        # A workfile comp with no th::import_render (#80, a slap comp in the
        # render scene): Farm Submit saved the workfile with the submission
        # (``comp_scene``), and it comps the render beside it.
        if settings.get('comp_scene') is not None:
            comp_block['scene'] = dict(settings['comp_scene'])

    if do_playblast:
        pb_department = settings.get('pb_department')
        pb_pool = settings['pb_pool']
        pb_priority = settings['pb_priority']
        pb_res = settings['pb_res']
        pb_look = settings['pb_look']

    # A Multi owns no staged stage, frame range or channels — its members
    # do. Without this, it got as far as the staged-file lookup and failed
    # with "Publish the shot first", which no publish could ever satisfy.
    if entity_uri.purpose == 'groups':
        raise BatchSubmitError(
            f"{entity_uri} is a Multi, not a shot or asset. Submit its "
            "member entities instead (the Submit Jobs dialog does this "
            "when opened from a Multi)."
        )

    # Playblast is a shots-only preview (mirrors the shot playblast HDAs). The
    # dialog only offers the column for the shots context, but guard here too.
    if do_playblast and entity_context != 'shots':
        raise BatchSubmitError(
            f"Playblast is only supported for shots, not '{entity_context}'"
        )
    if do_render and entity_context not in ('shots', 'assets'):
        raise BatchSubmitError(
            f"Render is only supported for shots and assets, not '{entity_context}'"
        )

    # A comp is a shot's.
    if do_comp and entity_context != 'shots':
        raise BatchSubmitError(
            f"Comp is only supported for shots, not '{entity_context}'"
        )

    # A channel the entity does not define would still build (every
    # department falls back to default) and render the default scene again
    # under that name. The dialog only sends the checked channels each
    # entity defines; this is the backstop for any other caller.
    if do_render:
        defined = list_channels(entity_uri)
        undefined = [name for name in channels if name not in defined]
        if undefined:
            raise BatchSubmitError(
                f"{entity_uri} does not define render channel"
                f"{'s' if len(undefined) > 1 else ''} "
                f"{', '.join(undefined)} (it defines: {', '.join(defined)}). "
                f"Uncheck {'them' if len(undefined) > 1 else 'it'} for this "
                f"entity, or add the channel to it first."
            )

    # An asset renders as a turntable, on the studio's frames: the dialog's
    # range and rolls are a shot's (an asset has none).
    asset_turntable = do_render and entity_context == 'assets'
    # Into version's repair (#92), read once the Frame List is
    repair_entry = None
    turntable_frames = None
    if asset_turntable:
        turntable_frames = turntable.farm_frames()
        first_frame, last_frame = turntable_frames.first, turntable_frames.last
        pre_roll = post_roll = 0
        problems = submission.turntable_problems(settings, turntable_frames.step)
        if problems:
            raise BatchSubmitError(f"{entity_uri}: {problems[0]}")
        render_step = turntable_frames.step

    if do_render and (first_frame is None or last_frame is None):
        raise BatchSubmitError(
            f"No frame range for {entity_uri}: submit 'first_frame' and "
            f"'last_frame', or configure a frame range on the entity"
        )

    # Extend the render range by the handles. The submitter resolves pre_roll /
    # post_roll per entity and ships them, but nothing here ever read them, so
    # farm renders silently came out at the play range while the dialog showed
    # handles as applied. Routed through FrameRange rather than plain
    # arithmetic so the roll validation applies (a start_roll that would take
    # the first frame to <= 0 raises rather than producing a bad range), and so
    # this matches the playblast, which resolves its range through
    # FrameRange.full_range() too.
    if do_render:
        try:
            render_frame_range = FrameRange(
                first_frame, last_frame, pre_roll, post_roll
            ).full_range()
        except ValueError as e:
            raise BatchSubmitError(
                f"Invalid frame range for {entity_uri}: {e}"
            )
        # The preview's check frames sit in the cut, not on the roll.
        cut_range = [first_frame, last_frame]
        first_frame = render_frame_range.first_frame
        last_frame = render_frame_range.last_frame

    # What a Comp alone follows: the renders on disk (#43), else the shot.
    rendered = shot = None
    if do_comp and not do_render:
        rendered = _rendered_range(entity_uri)
        if rendered is None:
            shot = _shot_range(entity_uri)

    # Departments turned off on the entity (#64): never published, never a cut.
    disabled = get_disabled_departments(entity_uri)

    # The refusals Farm Submit showed before Submit (farm.specs.submission).
    refusals = submission.problems(settings, off=disabled, rendered=rendered, shot=shot)
    if refusals:
        raise BatchSubmitError(f"{entity_uri}: {refusals[0]}")

    if do_render:
        # The Frame List (#84): only these frames of the range (rolls
        # included). One range is an ordinary render of it; two or more
        # are a multi-range render, which is never published.
        listed = () if asset_turntable else submission.frame_list(settings)[0]
        frame_ranges_entry = frame_ranges.config_entry(listed)
        if listed:
            first_frame, last_frame = frame_ranges.span(listed)
            cut_range = [first_frame, last_frame]
        # Into version (#92): the listed frames go into existing versions,
        # which stay as published as they were.
        repair_entry = _plan_repair(
            entity_uri, settings['into_version'],
            listed, channels, render_department, denoise,
            preview='partial_render' in render_task_keys, step=render_step,
            turntable=asset_turntable,
        )
        if repair_entry is not None:
            logging.info(
                f"{entity_uri}: rendering {frame_ranges.format_ranges(listed)} "
                f"into {_repair_targets(repair_entry)}"
            )
            if not _repair_publishes(repair_entry):
                if copy_to_edit:
                    logging.warning(
                        f"{entity_uri}: Copy to edit skipped: the version is not "
                        "the shot's render"
                    )
                    copy_to_edit = False
                if do_comp:
                    raise BatchSubmitError(
                        f"{entity_uri}: Comp follows a published render, and the "
                        "version rendered into is not one: untick Comp"
                    )
        elif frame_ranges_entry:
            logging.info(
                f"{entity_uri}: rendering {frame_ranges.format_ranges(listed)} "
                f"({frame_ranges.summary(listed)}); {frame_ranges.NOT_PUBLISHED}"
            )
            if copy_to_edit:
                logging.warning(
                    f"{entity_uri}: Copy to edit skipped: "
                    f"{frame_ranges.NOT_PUBLISHED}"
                )
                copy_to_edit = False
        # A stepped shot render is a check render, never published
        # (pipelines.render.is_check_render): the same as a multi-range one.
        if render_step > 1 and not asset_turntable and repair_entry is None:
            logging.info(
                f"{entity_uri}: rendering every {render_step} frames; "
                f"{frame_ranges.STEPPED_NOT_PUBLISHED}"
            )
            if copy_to_edit:
                logging.warning(
                    f"{entity_uri}: Copy to edit skipped: "
                    f"{frame_ranges.STEPPED_NOT_PUBLISHED}"
                )
                copy_to_edit = False
        # The Preview Frames List, read against the frames this render
        # makes (rolls included, every frame); the dialog's Warnings column
        # showed the same drops (submit_settings.preview_frame_warnings).
        preview_entry = {}
        if 'partial_render' in render_task_keys:
            render_preview = preview_frames.parse(
                settings['preview_frame_list'], first_frame, last_frame,
                render_step,
            )
            if frame_ranges_entry:
                render_preview = preview_frames.inside_ranges(render_preview, listed)
            _log_preview_warnings(entity_uri, 'render', render_preview)
            preview_entry = preview_frames.config_entry(render_preview)

    if comp_block is not None and 'scene' in comp_block:
        scene = comp_block['scene']
        problems = comp.scene_comp_problems(
            do_render,
            do_render and 'full_render' in render_task_keys,
            channels if do_render else [],
            scene.get('aovs') or [],
        )
        if problems:
            raise BatchSubmitError(f"{entity_uri}: {'; '.join(problems)}")
        if not scene.get('path'):
            raise BatchSubmitError(
                f"{entity_uri}: the comp's workfile was not saved with the "
                "submission; submit it from Farm Submit in that workfile"
            )
    # The comp's frames (submission.comp_follows): the frames the render beside
    # it makes, else what the renders on disk cover, else the shot's; each end
    # replaced by a frame the artist set (checked with the refusals above).
    comp_range = None
    if do_comp:
        if do_render and repair_entry is not None:
            # A repair comps its whole version (#92), which only the disk
            # knows, so its comp frames are checked here, not with the
            # refusals Farm Submit shares.
            follows = (repair_entry['first_frame'], repair_entry['last_frame'])
        else:
            follows = submission.comp_follows(settings, rendered, shot)
        if follows is None:
            raise BatchSubmitError(f"No frame range for {entity_uri}")
        comp_range = submission.comp_frames(
            settings.get('comp_first_frame'), settings.get('comp_last_frame'), follows,
        )
        if repair_entry is not None:
            problems = submission.comp_frame_problems(comp_range, follows, inside=True)
            if problems:
                raise BatchSubmitError(f"{entity_uri}: {problems[0]}")
        if comp_block['preview_frames']:
            comp_preview = preview_frames.parse(
                settings['comp_preview_frame_list'], *comp_range,
                comp_block['step_size'],
            )
            _log_preview_warnings(entity_uri, 'comp', comp_preview)
            comp_block.update(preview_frames.config_entry(comp_preview))
        if not composite_spec.valid_recomp(comp_block):
            raise BatchSubmitError(
                f"Invalid comp settings for {entity_uri}: the step and batch "
                f"size must be at least 1 ({comp_block})"
            )

    # Get departments list
    departments = list_departments(entity_context)
    department_names = [d.name for d in departments]
    renderable_names = [d.name for d in departments if d.renderable]

    pub_dept_names = []
    if do_publish:
        pub_dept_names = publish_department_names(settings, department_names)
    if do_render:
        _check_preview_department(
            'Render', render_department, department_names, renderable_names,
        )
    if do_playblast:
        _check_preview_department(
            'Playblast', pb_department, department_names, renderable_names,
        )

    # A turntable with nothing published before it renders the staged builds
    # on disk: refused here, before the farm is contacted, when one is
    # missing (the dialog's Warnings column said so before Submit). With a
    # publish, the stage is built after it.
    if asset_turntable and not do_publish:
        missing = preview.unstaged_channels(entity_uri, channels)
        if missing:
            raise BatchSubmitError(
                f"{entity_uri} has no staged build of channel"
                f"{'s' if len(missing) > 1 else ''} {', '.join(missing)} "
                "to put on the turntable: publish the asset first, or "
                "tick a publish beside the render."
            )

    return EntityPlan(
        uri=entity_uri,
        context=entity_context,
        kinds=kinds,
        publish=PublishPlan(
            pool=pub_pool,
            priority=pub_priority,
            departments=tuple(pub_dept_names),
        ) if do_publish else None,
        render=RenderPlan(
            department=render_department,
            channels=channels,
            pool=render_pool,
            priority=render_priority,
            tile_count=tile_count,
            first_frame=first_frame,
            last_frame=last_frame,
            cut_range=cut_range,
            step=render_step,
            batch_size=batch_size,
            denoise=denoise,
            copy_to_edit=copy_to_edit,
            standalone=standalone,
            task_keys=tuple(render_task_keys),
            turntable=asset_turntable,
            preview_entry=preview_entry,
            frame_ranges_entry=frame_ranges_entry,
            overrides=_build_render_overrides(settings, is_turntable=asset_turntable),
            repair=repair_entry,
        ) if do_render else None,
        playblast=PlayblastPlan(
            department=pb_department,
            pool=pb_pool,
            priority=pb_priority,
            res=pb_res,
            look=pb_look,
        ) if do_playblast else None,
        comp=comp_block,
        comp_range=comp_range,
    )


# ── The builders ──────────────────────────────────────────

def _publish_jobs(plan: EntityPlan, paths, temp_path, jobs, deps) -> str | None:
    """Add the publish chain, each department after the one before; the
    name of its last job, None when nothing publishes."""
    if plan.publish is None:
        return None
    entity_uri = plan.uri
    prev_job_name = None
    for dept_name in plan.publish.departments:
        if not publish_pipeline.is_submissable(entity_uri, dept_name):
            raise BatchSubmitError(
                f"No workfile to publish for {entity_uri}/{dept_name}"
            )
        job_name = f'publish_{dept_name}'
        try:
            job = publish_pipeline.create_publish_job(
                entity_uri, dept_name, plan.publish.pool, plan.publish.priority,
                paths, temp_path,
            )
        except ValueError as e:
            raise BatchSubmitError(str(e)) from e
        jobs[job_name] = job
        deps[job_name] = [prev_job_name] if prev_job_name else []
        prev_job_name = job_name
    return prev_job_name


def _preview_builds(plan: EntityPlan, last_publish, temp_path, jobs, deps) -> list[str]:
    """What the previews wait on: nothing without a publish; after one, the
    staged builds it makes stale, added here.

    A preview after a publish reads the stage the publish produces, so the
    staged build runs again (a first-ever department export or a newly
    imported asset is absent from the old one) and the previews' input is
    snapshotted on the farm once it has. The builds and the collapse after
    the publish are its chain, so they run at its priority
    (``publish_job.PRIORITY``): at the publish's own priority (which follows
    the entity's farm.priority, like the render) they queued behind other
    shots' renders and held this shot's render back.
    """
    if last_publish is None:
        return []
    if plan.render is None and plan.playblast is None:
        return [last_publish]
    # A pass-only render layer renders the default stage
    # (pipe.render_layers), so it is the default channel that builds.
    build_channels = (
        render_layers.stage_channels(plan.uri, plan.render.channels)
        if plan.render is not None else []
    )
    if plan.playblast is not None and DEFAULT_CHANNEL not in build_channels:
        build_channels.append(DEFAULT_CHANNEL)
    build_names = []
    for build_channel in build_channels:
        job_name = f'build_{build_channel}'
        jobs[job_name] = build_pipeline.create({
            'entity_uri': str(plan.uri),
            'priority': publish_job.PRIORITY,
            'pool_name': plan.publish.pool,
            'channel_name': build_channel,
        }, temp_path)
        deps[job_name] = [last_publish]
        build_names.append(job_name)
    return build_names


def _render_request(plan: EntityPlan, user_name: str) -> dict:
    """The render request of ``plan``'s render and re-comp
    (``specs.render_pipeline.valid_request``): what the direct, collapse
    and stage paths each hand the render pipeline, with the paths they
    make for themselves added (``preview.render_config``)."""
    render = plan.render
    request = dict(
        entity=dict(uri=str(plan.uri), department=render.department),
        settings=dict(
            user_name=user_name,
            purpose='render',
            pool_name=render.pool,
            channel_names=list(render.channels),
            render_department_name=render.department,
            tile_count=render.tile_count,
            first_frame=render.first_frame,
            last_frame=render.last_frame,
            step_size=render.step,
            batch_size=render.batch_size,
            copy_to_edit=render.copy_to_edit,
            cut_range=list(render.cut_range),
            **render.preview_entry,
            **render.frame_ranges_entry,
            **_repair_entry(render.repair),
        ),
        tasks={
            task_key: dict(priority=render.priority, denoise=render.denoise, channel_name='renders')
            for task_key in render.task_keys
        },
    )
    if plan.comp is not None:
        request['composite'] = dict(plan.comp)
    return request


def _collapse_turntable(render: RenderPlan) -> dict:
    """The collapse job's turntable: the studio's frames on its step, with
    the overrides the studio's render settings take (#53)."""
    return dict(
        department=render.department,
        channel_names=list(render.channels),
        overrides=render.overrides,
        task_keys=list(render.task_keys),
        pool_name=render.pool,
        priority=render.priority,
        tile_count=render.tile_count,
        first_frame=render.first_frame,
        last_frame=render.last_frame,
        step_size=render.step,
        batch_size=render.batch_size,
        # As the studio's own farm render: no denoise, no edit.
        denoise=False,
        copy_to_edit=False,
        cut_range=render.cut_range,
        **render.preview_entry,
    )


def _collapse_config(plan: EntityPlan, after_publish: bool, user_name: str) -> dict | None:
    """The farm-side snapshot for the direct render and/or the playblast
    after a publish, None when neither takes one. A turntable always
    snapshots on the farm: its workfile is built in hython there. A
    standalone render's stage job composes on its own."""
    render, playblast = plan.render, plan.playblast
    collapses_render = render is not None and (render.turntable or not render.standalone)
    if not (
        (render is not None and render.turntable)
        or (after_publish and (collapses_render or playblast is not None))
    ):
        return None
    config = dict(
        entity=dict(
            uri=str(plan.uri),
            department=render.department if render is not None else playblast.department,
        ),
        settings=dict(
            user_name=user_name,
            # A turntable with no publish has no publish pool.
            pool_name=plan.publish.pool if plan.publish is not None else render.pool,
            priority=publish_job.PRIORITY,
        ),
    )
    if collapses_render and render.turntable:
        config['turntable'] = _collapse_turntable(render)
    elif collapses_render:
        # A snapshot of the staged file, the overrides baked onto its
        # render-settings prim.
        config['render'] = dict(
            request=_render_request(plan, user_name), overrides=render.overrides,
        )
    if playblast is not None:
        config['playblast'] = dict(
            department=playblast.department,
            pool_name=playblast.pool,
            priority=playblast.priority,
            res=list(playblast.res),
            look=dict(playblast.look),
        )
    return config


def _direct_render_jobs(
    plan: EntityPlan, preview_deps, user_name, paths, temp_path, jobs, deps,
) -> None:
    """Render a snapshot of the staged file taken now, with the overrides
    baked onto its render-settings prim (nothing published before it)."""
    entity_uri, render = plan.uri, plan.render
    try:
        input_paths = preview.collapse_render_inputs(
            entity_uri, render.channels, render.department,
            render.overrides, temp_path, paths,
        )
    except preview.PreviewError as e:
        raise BatchSubmitError(str(e)) from e
    render_settings_path = preview.write_render_settings(
        temp_path, paths, render.channels,
        preview.aov_names(entity_uri, render.channels),
    )
    render_config = preview.render_config(
        _render_request(plan, user_name), input_paths, render_settings_path,
    )
    try:
        render_pipeline.build(
            render_config, paths, temp_path, jobs, deps,
            depends_on=preview_deps,
        )
        logging.info(f"Added render jobs for {entity_uri}")
    except Exception as e:
        raise BatchSubmitError(f"Could not build render jobs for {entity_uri}: {e}") from e


def _stage_render_job(plan: EntityPlan, preview_deps, user_name, temp_path, jobs, deps) -> None:
    """A farm stage job builds the render stage, applying the overrides,
    then submits the render itself. It composes when it runs, so after a
    publish it only needs to wait for it."""
    render = plan.render
    render_settings_path = preview.write_render_settings(
        temp_path, {}, render.channels,
        preview.aov_names(plan.uri, render.channels),
        overrides=render.overrides,
    )
    relative_render_settings_path = render_settings_path.relative_to(temp_path)

    request = _render_request(plan, user_name)
    stage_config = dict(
        request,
        settings=dict(
            request['settings'],
            render_settings_path=path_str(relative_render_settings_path),
        ),
        tasks=dict(request['tasks'], stage=dict(priority=render.priority, channel_name='exports')),
    )

    # The stage job gates the render chain it submits, so it runs in that
    # chain's band: with a preview it must not wait behind other shots' full
    # renders.
    stage_config['tasks']['stage']['priority'] = max(
        task['priority']
        for key, task in render_pipeline.banded_priorities(
            stage_config['tasks']
        ).items()
        if key != 'stage'
    )

    # The stage job builds the render stages on the farm
    stage_paths = {render_settings_path: relative_render_settings_path}
    jobs['stage'] = stage_job.build(stage_config, stage_paths, temp_path)
    deps['stage'] = list(preview_deps)


def _render_jobs(
    plan: EntityPlan, collapse, preview_deps, user_name, paths, temp_path, jobs, deps,
) -> None:
    """Add the render: a turntable, or a direct render after a publish, is
    the collapse job's (``_collapse_config``); a direct render with nothing
    published before it renders a snapshot taken now; a standalone render
    is a farm stage job."""
    render = plan.render
    if render is None or render.turntable:
        return
    if render.standalone:
        _stage_render_job(plan, preview_deps, user_name, temp_path, jobs, deps)
    elif collapse is None:
        _direct_render_jobs(plan, preview_deps, user_name, paths, temp_path, jobs, deps)


def _playblast_job(
    plan: EntityPlan, collapse, preview_deps, user_name, paths, temp_path, jobs, deps,
) -> None:
    """Add the playblast (a GL preview of the shot's staged stage): the
    collapse job's after a publish, else of a snapshot taken now."""
    playblast = plan.playblast
    if playblast is None or collapse is not None:
        return
    entity_uri = plan.uri
    try:
        playblast_input = preview.collapse_playblast_input(
            entity_uri, playblast.department, temp_path, paths,
            light=playblast.look['light'],
        )
        playblast_config = preview.playblast_config(
            entity_uri,
            department=playblast.department,
            input_path=playblast_input,
            user_name=user_name,
            pool_name=playblast.pool,
            priority=playblast.priority,
            res=playblast.res,
            look=playblast.look,
        )
    except preview.PreviewError as e:
        raise BatchSubmitError(str(e)) from e
    try:
        playblast_pipeline.build(
            playblast_config, paths, temp_path, jobs, deps,
            depends_on=preview_deps,
        )
        logging.info(f"Added playblast job for {entity_uri}")
    except Exception as e:
        raise BatchSubmitError(
            f"Could not build playblast job for {entity_uri}: {e}"
        ) from e


def _comp_job(plan: EntityPlan, user_name, paths, temp_path, jobs, deps) -> None:
    """A comp on its own: the shot's newest composite workfile over its
    renders as they are now. With a render, the render chain carries it
    (its config's composite block) and starts it after the denoise."""
    if plan.comp is None or plan.render is not None:
        return
    try:
        composite_pipeline.build(
            composite_pipeline.recomp_config(
                plan.uri, plan.comp,
                user_name=user_name,
                purpose='render',
                first_frame=plan.comp_range[0],
                last_frame=plan.comp_range[1],
                temp_path=temp_path,
                paths=paths,
            ),
            paths, temp_path, jobs, deps,
            prefix='comp_',
        )
    except composite_pipeline.RecompError as e:
        raise BatchSubmitError(str(e)) from e


def _collapse_job(plan: EntityPlan, collapse, preview_deps, temp_path, jobs, deps) -> None:
    """Add the collapse job ``_collapse_config`` made, after the previews' inputs."""
    if collapse is None:
        return
    try:
        jobs['collapse'] = collapse_job.build(collapse, {}, temp_path)
        deps['collapse'] = list(preview_deps)
    except Exception as e:
        raise BatchSubmitError(
            f"Could not build the collapse job for {plan.uri}: {e}"
        ) from e


def _finalize_batch(entity_uri, farm, batch, jobs, deps) -> list[str]:
    """Submit ``jobs`` as ``batch``; the farm's job ids (none when no job
    was added)."""
    if not jobs:
        logging.info(f"No jobs to submit for {entity_uri}")
        return []

    batch.add_jobs_with_deps(jobs, deps)

    # Submit batch
    jobs_dir = api.storage.resolve(Uri.parse('export:/other/jobs'))
    job_ids = farm.submit(batch, jobs_dir)

    logging.info(f"Submitted batch for {entity_uri}: {len(job_ids)} jobs")
    return job_ids


# ── The entry point ───────────────────────────────────────

def submit_entity_batch(config: dict) -> list[str]:
    """
    Submit a batch of jobs for a single entity based on configuration.

    Args:
        config: Job configuration dict with keys:
            - entity: {uri, name, context}
            - settings:
                - publish: bool - whether to submit publish jobs
                - render: bool - whether to submit render jobs
                - playblast: bool - whether to submit a playblast (shots)
                - Publish section: pub_departments (the ticked departments),
                                   pub_pool, pub_priority
                - Render section: render_department, channels, render_pool,
                                  render_priority, tile_count, pre_roll,
                                  first_frame, last_frame, post_roll,
                                  batch_size, denoise, render_mode
                                  ('full' renders the whole range;
                                  'first_middle_last' renders 3 check
                                  frames via the partial_render chain;
                                  'preview_then_full' submits both, the
                                  full range waiting on the 3 frames).
                                  first_frame/last_frame are REQUIRED when
                                  render is on and have no default — the
                                  caller resolves them per entity.
                                  pre_roll/post_roll default to 0 and EXTEND
                                  the rendered range (first_frame - pre_roll
                                  .. last_frame + post_roll), matching what
                                  the dialog shows.
                                  Every channel must be one the entity
                                  defines (``list_channels``); an
                                  undefined one raises before any job is
                                  created. These are required (Farm
                                  Submit's resolve_settings always sends
                                  them; a missing one is a KeyError):
                                  preview_frame_list (str, the Preview
                                  Frames List, ``farm.specs.preview_frames``):
                                  the preview's frames instead of the 3
                                  check frames; empty keeps the 3.
                                  frame_list (str, the Frame List,
                                  ``farm.specs.frame_ranges``, #84): only
                                  these ranges of the rolled range; one
                                  range renders as First..Last would, two
                                  or more are a multi-range render, never
                                  published (no daily, no Copy to edit, no
                                  Comp); empty for none. Shots only.
                                  render_step (int, 1 for every frame):
                                  every Nth frame of the
                                  range, or of each Frame List range, from
                                  its first; above 1 a shot's render is a
                                  check render, never published (as a
                                  multi-range one). A turntable takes 1
                                  (it renders on the studio's step).
                                  into_version (str, '' for new
                                  versions; ``latest`` or a
                                  version name, ``farm.specs.repair``,
                                  #92): render the Frame List into that
                                  existing version of every channel,
                                  over the frames there; needs a Frame
                                  List, no preview, Step 1. The version
                                  stays as published as it was, so Copy
                                  to edit and Comp follow it.
                - Playblast section: pb_department, pb_pool, pb_priority,
                                     pb_res, pb_look
                                     (specs.playblast.valid_look)
                - comp: bool (required; shots) - re-comp the shot's newest
                        composite workfile; with render it waits for the
                        full render's denoise, else the render
                - comp_scene: dict (optional, #80) - comp this workfile
                        instead (one with no th::import_render, saved with
                        the submission): {path, exports, aovs}; it reads the
                        render beside it, so needs render with the full range
                - Comp section: comp_pool, comp_priority, comp_batch_size,
                                comp_copy_to_edit, and (required)
                                comp_step (1 for every frame),
                                comp_preview (True: comp first/middle/last
                                first), comp_preview_frame_list (str, those
                                frames instead of first/middle/last; ''
                                for them); optional comp_first_frame /
                                comp_last_frame (default:
                                the render's range, else the shot's; with a
                                render, inside the render's)

            An Edits tab row (context ``edits``) is a Render Edit instead:
            its settings are version, pool_name and priority
            (``pipelines.edit_render.config_from_row``).

    Returns:
        List of submitted job IDs

    Raises:
        BatchSubmitError: If submission fails
    """
    # Extract config. The settings are what submit_settings.resolve_settings
    # resolved for this entity: the three section booleans, and every field
    # of each section that is on (the defaults live there, per entity, not
    # here). A key an active section needs and lacks raises KeyError — the
    # frame range in particular is omitted when the entity has none, and
    # defaulting it is how a multi-shot batch once rendered every shot at a
    # wrong length.
    entity_uri = Uri.parse(config['entity']['uri'])
    entity_context = config['entity']['context']
    settings = config['settings']

    # An edit has no departments, publishes or previews: its one job is
    # Render Edit.
    if entity_context == 'edits':
        return edit_render_pipeline.submit(edit_render_pipeline.config_from_row(config))

    kinds = _kinds(settings)
    if not kinds:
        return []
    plan = _resolve(entity_uri, entity_context, settings, kinds)

    # Deadline, or this machine's local farm (farm.backend)
    farm = get_farm()

    # Stamped with the project and entity (farm.stamp), so the Farm Manager
    # and the next submit's clean-up know whose jobs these are.
    user_name = get_user_name()
    batch = new_batch(batch_title(*plan.kinds, entity_uri, user_name), entity_uri)
    jobs = {}  # name -> Job
    deps = {}  # name -> [dep_names]

    # Create temp directory for staging files (used by both publish and render jobs)

    with project_temp_dir() as temp_path:
        paths = {}  # Shared paths dict for bundling files
        last_publish = _publish_jobs(plan, paths, temp_path, jobs, deps)
        after_publish = last_publish is not None and (
            plan.render is not None or plan.playblast is not None
        )
        preview_deps = _preview_builds(plan, last_publish, temp_path, jobs, deps)
        collapse = _collapse_config(plan, after_publish, user_name)
        _render_jobs(plan, collapse, preview_deps, user_name, paths, temp_path, jobs, deps)
        _playblast_job(plan, collapse, preview_deps, user_name, paths, temp_path, jobs, deps)
        _comp_job(plan, user_name, paths, temp_path, jobs, deps)
        _collapse_job(plan, collapse, preview_deps, temp_path, jobs, deps)
        # Submitted within the temp directory so its files can be copied.
        return _finalize_batch(entity_uri, farm, batch, jobs, deps)
