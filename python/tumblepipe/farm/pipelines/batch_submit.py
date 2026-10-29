"""Batch job submission for the Submit Jobs dialog.

This module orchestrates the creation and submission of publish, render and
playblast jobs for one entity, from the settings the dialog resolved for it.

When the same submission publishes the entity *and* previews it (render or
playblast), the preview's input is snapshotted on the farm after the publish
chain and a fresh staged build, by a ``collapse`` job — see
``farm/pipelines/preview.py`` for why it cannot be taken at submit time.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import logging

from tumblepipe.api import (
    path_str,
    get_user_name,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import (
    DEFAULT_CHANNEL,
    list_channels,
    read_channel_names,
)
from tumblepipe.config.department import list_departments, department_names_up_to
from tumblepipe.apps.deadline import Batch
from tumblepipe.farm.backend import get_farm
from tumblepipe.config.timeline import FrameRange
from tumblepipe.farm.jobs import stage as stage_job
from tumblepipe.farm.jobs import collapse as collapse_job
from tumblepipe.farm.jobs import publish as publish_job
from tumblepipe.farm.pipelines import build as build_pipeline
from tumblepipe.farm.pipelines import render as render_pipeline
from tumblepipe.farm.pipelines import playblast as playblast_pipeline
from tumblepipe.farm.pipelines import preview
from tumblepipe.farm.pipelines import publish as publish_pipeline
from tumblepipe.farm.pipelines._batch import batch_title

# Mapping from submit-dialog settings keys to Karma/USD render setting
# attribute paths, used to build overrides for render_settings.json. A key
# reaches here only when the artist set it in the dialog: unset, the scene's
# own render settings render (submit_settings.Field.override).
# Attribute names are from the Karma schema (P:/buzz2/_config/usd/root_default_prims.usda)
RENDER_OVERRIDE_MAP = {
    'samples': 'karma:global:pathtracedsamples',       # int
    'mblur': 'karma:object:mblur',                     # bool (object namespace)
    'dof': 'karma:global:enable_dof',                  # bool (note underscore)
    'denoise': None,                                   # Not a render setting (post-process)
    'diffuse_limit': 'karma:object:diffuselimit',      # float (object namespace)
    'reflection_limit': 'karma:object:reflectlimit',   # float (note: "reflect" not "reflection")
    'refraction_limit': 'karma:object:refractlimit',   # float (note: "refract" not "refraction")
    'volume_limit': 'karma:object:volumelimit',        # float (object namespace)
    'sss_limit': 'karma:object:ssslimit',              # float (object namespace)
}

# Attributes that need float type conversion (stored as int in settings but float in Karma schema)
FLOAT_ATTRIBUTES = {
    'karma:object:diffuselimit',
    'karma:object:reflectlimit',
    'karma:object:refractlimit',
    'karma:object:volumelimit',
    'karma:object:ssslimit',
}

# The render chains each Range mode submits. With both, pipelines/render.py makes
# every channel's full_render wait on its partial_render and reuse its
# version: the first/middle/last check frames render (and go to Discord)
# while the full range sits queued behind them.
RENDER_TASK_KEYS = {
    'full': ('full_render',),
    'first_middle_last': ('partial_render',),
    'preview_then_full': ('partial_render', 'full_render'),
}


class BatchSubmitError(Exception):
    """Error during batch submission."""
    pass


def _build_render_overrides(settings: dict) -> dict:
    """Build render overrides dict from job settings.

    Maps settings keys to Karma/USD attribute paths using RENDER_OVERRIDE_MAP.
    Skips entries with None USD path (e.g., denoise which is handled as post-process).
    Converts limit values to float for correct USD type output.

    Args:
        settings: Job settings dict from submission dialog

    Returns:
        Dict mapping USD attribute paths to values
    """
    overrides = {}
    for settings_key, usd_path in RENDER_OVERRIDE_MAP.items():
        if usd_path is None:
            continue  # Skip settings without USD attribute (e.g., denoise)
        if settings_key in settings:
            value = settings[settings_key]
            # Convert to float for attributes that require float type
            if usd_path in FLOAT_ATTRIBUTES and isinstance(value, int):
                value = float(value)
            overrides[usd_path] = value
    return overrides


def publish_department_names(settings: dict, department_names: list[str]) -> tuple[list[str], bool]:
    """``(departments to publish in pool order, whether to skip an up-to-date prefix)``.

    Two spellings, from two eras of the dialog:

    * ``pub_departments`` — the grid's: exactly the ticked departments, in
      pool order. The grid shows what is stale and the artist picked, so
      nothing is skipped behind their back.
    * ``pub_department`` — the old form's: every department up to this one,
      skipping the up-to-date ones at the front of the chain.
    """
    chosen = settings.get('pub_departments')
    if chosen is not None:
        unknown = [name for name in chosen if name not in department_names]
        if unknown:
            raise BatchSubmitError(
                f"Invalid publish department(s): {', '.join(unknown)}"
            )
        wanted = set(chosen)
        return [name for name in department_names if name in wanted], False
    pub_department = settings.get('pub_department')
    if pub_department is None:
        raise BatchSubmitError("Publish department not specified")
    if pub_department not in department_names:
        raise BatchSubmitError(f"Invalid publish department: {pub_department}")
    return department_names_up_to(department_names, pub_department), True


def _check_preview_department(kind: str, name, department_names, renderable_names):
    """A render or playblast department must be real and renderable.

    The preview composes the pool prefix ending at this department, so a
    non-renderable pick has no layer to end on, and silently composing the
    whole pool is the bug this prevents.
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
                - Publish section: pub_departments (the ticked departments)
                                   or pub_department (up to this one),
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
                                  created.
                - Playblast section: pb_department, pb_pool, pb_priority,
                                     pb_res

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

    do_publish = settings['publish']
    do_render = settings['render']
    do_playblast = settings['playblast']
    if not do_publish and not do_render and not do_playblast:
        return []

    # The publish job itself always runs at publish_job.PRIORITY. The builds
    # and the collapse after it are the same chain and create the render
    # batch, so they run there too: at pub_priority (which follows the
    # entity's farm.priority, like the render) they queued behind other
    # shots' renders and held this shot's render back.
    chain_priority = publish_job.PRIORITY
    if do_publish:
        pub_pool = settings['pub_pool']
        pub_priority = settings['pub_priority']

    if do_render:
        render_department = settings.get('render_department')
        channels = read_channel_names(settings)
        if not channels:
            raise BatchSubmitError(f"No render channels for {entity_uri}")
        render_pool = settings['render_pool']
        render_priority = settings['render_priority']
        tile_count = settings['tile_count']
        first_frame = settings.get('first_frame')
        last_frame = settings.get('last_frame')
        pre_roll = settings['pre_roll']
        post_roll = settings['post_roll']
        batch_size = settings['batch_size']
        denoise = settings['denoise']
        copy_to_edit = settings['copy_to_edit']
        standalone = settings['standalone']
        # 'first_middle_last' submits the partial_render chain (3 check
        # frames + notify) instead of the full_render chain (all frames +
        # slapcomp/mp4); 'preview_then_full' submits both, and the render
        # pipeline holds each channel's full render until its partial render
        # is done.
        render_mode = settings['render_mode']
        render_task_keys = RENDER_TASK_KEYS.get(render_mode)
        if render_task_keys is None:
            raise BatchSubmitError(
                f"Unknown render_mode {render_mode!r}: expected one of "
                f"{', '.join(RENDER_TASK_KEYS)}."
            )

    if do_playblast:
        pb_department = settings.get('pb_department')
        pb_pool = settings['pb_pool']
        pb_priority = settings['pb_priority']
        pb_res = settings['pb_res']

    # A Multi owns no staged stage, frame range or channels — its members
    # do. Without this, it got as far as the staged-file lookup and failed
    # with "Publish the shot first", which no publish could ever satisfy.
    if entity_uri.purpose == 'groups':
        raise BatchSubmitError(
            f"{entity_uri} is a Multi, not a shot or asset. Submit its "
            "member entities instead (the Submit Jobs dialog does this "
            "when opened from a Multi)."
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
        first_frame = render_frame_range.first_frame
        last_frame = render_frame_range.last_frame

    # Playblast is a shots-only preview (mirrors the shot playblast HDAs). The
    # dialog only offers the column for the shots context, but guard here too.
    if do_playblast and entity_context != 'shots':
        raise BatchSubmitError(
            f"Playblast is only supported for shots, not '{entity_context}'"
        )

    # Get departments list
    departments = list_departments(entity_context)
    department_names = [d.name for d in departments]
    renderable_names = [d.name for d in departments if d.renderable]

    pub_dept_names, skip_up_to_date = [], False
    if do_publish:
        pub_dept_names, skip_up_to_date = publish_department_names(
            settings, department_names
        )
    if do_render:
        _check_preview_department(
            'Render', render_department, department_names, renderable_names
        )
    if do_playblast:
        _check_preview_department(
            'Playblast', pb_department, department_names, renderable_names
        )

    # Deadline, or this machine's local farm (farm.backend)
    farm = get_farm()

    # Create batch
    user_name = get_user_name()
    kinds = [
        kind for kind, wanted in (
            ('publish', do_publish),
            ('render', do_render),
            ('playblast', do_playblast)
        )
        if wanted
    ]
    batch = Batch(batch_title(*kinds, entity_uri, user_name))

    # Track job indices for dependencies
    jobs = {}  # name -> Job
    deps = {}  # name -> [dep_names]
    last_publish_job_name = None

    # Helper to finalize and submit batch
    def _finalize_batch():
        if not jobs:
            logging.info(f"No jobs to submit for {entity_uri}")
            return []

        batch.add_jobs_with_deps(jobs, deps)

        # Submit batch
        jobs_dir = api.storage.resolve(Uri.parse('export:/other/jobs'))
        job_ids = farm.submit(batch, jobs_dir)

        logging.info(f"Submitted batch for {entity_uri}: {len(job_ids)} jobs")
        return job_ids

    # Create temp directory for staging files (used by both publish and render jobs)
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_path_str:
        temp_path = Path(temp_path_str)
        paths = {}  # Shared paths dict for bundling files

        # Add publish jobs
        if do_publish:
            prev_job_name = None
            # Exports are checked against the default channel's
            channel_name = DEFAULT_CHANNEL
            for dept_name in pub_dept_names:
                if not publish_pipeline.is_submissable(entity_uri, dept_name):
                    # The old "up to" spelling walks the whole chain, and an
                    # entity need not have a workfile in every department.
                    # A department ticked explicitly must have one.
                    if skip_up_to_date:
                        continue
                    raise BatchSubmitError(
                        f"No workfile to publish for {entity_uri}/{dept_name}"
                    )

                # The old "up to" spelling skips an up-to-date prefix. A
                # department named explicitly is published regardless.
                if (
                    skip_up_to_date and prev_job_name is None
                    and not publish_pipeline.is_out_of_date(entity_uri, channel_name, dept_name)
                ):
                    continue

                job_name = f'publish_{dept_name}'
                try:
                    job = publish_pipeline.create_publish_job(entity_uri, dept_name, pub_pool, pub_priority, paths, temp_path)
                except ValueError as e:
                    raise BatchSubmitError(str(e)) from e
                jobs[job_name] = job
                deps[job_name] = [prev_job_name] if prev_job_name else []
                prev_job_name = job_name
                last_publish_job_name = job_name

        render_overrides = _build_render_overrides(settings) if do_render else {}

        # A preview after a publish reads the stage the publish produces, so
        # the staged build runs again (a first-ever department export or a
        # newly imported asset is absent from the old one) and the previews'
        # input is snapshotted on the farm once it has.
        preview_after_publish = last_publish_job_name is not None and (
            do_render or do_playblast
        )
        preview_deps = [last_publish_job_name] if last_publish_job_name else []
        if preview_after_publish:
            build_channels = list(channels) if do_render else []
            if do_playblast and DEFAULT_CHANNEL not in build_channels:
                build_channels.append(DEFAULT_CHANNEL)
            build_names = []
            for build_channel in build_channels:
                job_name = f'build_{build_channel}'
                jobs[job_name] = build_pipeline.create({
                    'entity_uri': str(entity_uri),
                    'priority': chain_priority,
                    'pool_name': pub_pool,
                    'channel_name': build_channel,
                }, temp_path)
                deps[job_name] = [last_publish_job_name]
                build_names.append(job_name)
            preview_deps = build_names

        # Farm-side snapshot for the direct render and/or the playblast.
        collapse_config = None
        if preview_after_publish and ((do_render and not standalone) or do_playblast):
            collapse_config = dict(
                entity=dict(
                    uri=str(entity_uri),
                    department=render_department if do_render else pb_department,
                ),
                settings=dict(
                    user_name=user_name,
                    pool_name=pub_pool,
                    priority=chain_priority,
                ),
            )

        # Add stage + render jobs
        if do_render:
            if not standalone:
                # === DIRECT RENDER MODE (standalone=False) ===
                # Render a collapsed snapshot of the staged file, with the
                # overrides baked onto its render-settings prim.
                if collapse_config is not None:
                    collapse_config['render'] = dict(
                        department=render_department,
                        channel_names=list(channels),
                        overrides=render_overrides,
                        task_keys=list(render_task_keys),
                        pool_name=render_pool,
                        priority=render_priority,
                        tile_count=tile_count,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        batch_size=batch_size,
                        denoise=denoise,
                        copy_to_edit=copy_to_edit,
                    )
                else:
                    try:
                        input_paths = preview.collapse_render_inputs(
                            entity_uri, channels, render_department,
                            render_overrides, temp_path, paths,
                        )
                    except preview.PreviewError as e:
                        raise BatchSubmitError(str(e)) from e
                    render_settings_path = preview.write_render_settings(
                        temp_path, paths, channels,
                        preview.aov_names(entity_uri, channels),
                    )
                    render_config = preview.render_config(
                        entity_uri,
                        department=render_department,
                        channels=channels,
                        input_paths=input_paths,
                        render_settings_path=render_settings_path,
                        user_name=user_name,
                        pool_name=render_pool,
                        priority=render_priority,
                        tile_count=tile_count,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        batch_size=batch_size,
                        denoise=denoise,
                        copy_to_edit=copy_to_edit,
                        task_keys=list(render_task_keys),
                    )
                    try:
                        render_pipeline.build(
                            render_config, paths, temp_path, jobs, deps,
                            depends_on=preview_deps,
                        )
                        logging.info(f"Added render jobs for {entity_uri}")
                    except Exception as e:
                        raise BatchSubmitError(f"Could not build render jobs for {entity_uri}: {e}") from e
            else:
                # === STAGE + RENDER MODE (standalone=True) ===
                # A farm stage job builds the render stage, applying the
                # overrides, then submits the render itself. It composes when
                # it runs, so after a publish it only needs to wait for it.
                render_settings_path = preview.write_render_settings(
                    temp_path, {}, channels,
                    preview.aov_names(entity_uri, channels),
                    overrides=render_overrides,
                )
                relative_render_settings_path = render_settings_path.relative_to(temp_path)

                stage_config = dict(
                    entity=dict(
                        uri=str(entity_uri),
                        department=render_department
                    ),
                    settings=dict(
                        user_name=user_name,
                        purpose='render',
                        pool_name=render_pool,
                        channel_names=channels,
                        render_department_name=render_department,
                        render_settings_path=path_str(relative_render_settings_path),
                        tile_count=tile_count,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        step_size=1,  # Default
                        batch_size=batch_size,
                        copy_to_edit=copy_to_edit
                    ),
                    tasks={
                        'stage': dict(priority=render_priority, channel_name='exports'),
                        **{
                            task_key: dict(priority=render_priority, denoise=denoise, channel_name='renders')
                            for task_key in render_task_keys
                        }
                    }
                )

                # The stage job gates the render chain it submits, so it runs
                # in that chain's band: with a preview it must not wait behind
                # other shots' full renders.
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

        # Add playblast job (GL preview of the shot's staged stage)
        if do_playblast:
            if collapse_config is not None:
                collapse_config['playblast'] = dict(
                    department=pb_department,
                    pool_name=pb_pool,
                    priority=pb_priority,
                    res=list(pb_res),
                )
            else:
                try:
                    playblast_input = preview.collapse_playblast_input(
                        entity_uri, pb_department, temp_path, paths,
                    )
                    playblast_config = preview.playblast_config(
                        entity_uri,
                        department=pb_department,
                        input_path=playblast_input,
                        user_name=user_name,
                        pool_name=pb_pool,
                        priority=pb_priority,
                        res=pb_res,
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

        if collapse_config is not None:
            try:
                jobs['collapse'] = collapse_job.build(collapse_config, {}, temp_path)
                deps['collapse'] = list(preview_deps)
            except Exception as e:
                raise BatchSubmitError(
                    f"Could not build the collapse job for {entity_uri}: {e}"
                ) from e

        # Submit batch (within temp directory context so files can be copied)
        return _finalize_batch()

