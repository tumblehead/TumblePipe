"""Batch job submission for the Submit Jobs dialog.

This module orchestrates the creation and submission of publish, render and
playblast jobs for one entity, from the settings the dialog resolved for it.

When the same submission publishes the entity *and* previews it (render or
playblast), the preview's input is snapshotted on the farm after the publish
chain and a fresh staged build, by a ``collapse`` job — see
``farm/jobs/houdini/_preview.py`` for why it cannot be taken at submit time.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import logging

from tumblepipe.api import (
    local_path,
    path_str,
    get_user_name,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import read_channel_names
from tumblepipe.config.department import list_departments, department_names_up_to
from tumblepipe.apps.deadline import (
    Deadline,
    Batch
)
from tumblepipe.config.timeline import FrameRange
import tumblepipe.farm.tasks.stage.task as stage_task
import tumblepipe.farm.tasks.collapse.task as collapse_task
import tumblepipe.farm.jobs.houdini.build.job as build_job
import tumblepipe.farm.jobs.houdini.render.job as render_job
import tumblepipe.farm.jobs.houdini.playblast.job as playblast_job
from tumblepipe.farm.jobs.houdini import _preview, _publish

# Mapping from column keys to Karma/USD render setting attribute paths
# These are used to build overrides for render_settings.json
# Supports both column keys (samples, mblur) and property paths (render.pathtracedsamples)
# Attribute names are from the Karma schema (P:/buzz2/_config/usd/root_default_prims.usda)
RENDER_OVERRIDE_MAP = {
    # Column keys (from job submission dialog)
    'samples': 'karma:global:pathtracedsamples',       # int
    'mblur': 'karma:object:mblur',                     # bool (object namespace)
    'dof': 'karma:global:enable_dof',                  # bool (note underscore)
    'denoise': None,                                   # Not a render setting (post-process)
    'diffuse_limit': 'karma:object:diffuselimit',      # float (object namespace)
    'reflection_limit': 'karma:object:reflectlimit',   # float (note: "reflect" not "reflection")
    'refraction_limit': 'karma:object:refractlimit',   # float (note: "refract" not "refraction")
    'volume_limit': 'karma:object:volumelimit',        # float (object namespace)
    'sss_limit': 'karma:object:ssslimit',              # float (object namespace)
    # Property paths (for backward compatibility)
    'render.pathtracedsamples': 'karma:global:pathtracedsamples',
    'render.enablemblur': 'karma:object:mblur',
    'render.enabledof': 'karma:global:enable_dof',
    'render.enabledenoising': None,
    'render.diffuselimit': 'karma:object:diffuselimit',
    'render.reflectionlimit': 'karma:object:reflectlimit',
    'render.refractionlimit': 'karma:object:refractlimit',
    'render.volumelimit': 'karma:object:volumelimit',
    'render.ssslimit': 'karma:object:ssslimit',
}

# Attributes that need float type conversion (stored as int in settings but float in Karma schema)
FLOAT_ATTRIBUTES = {
    'karma:object:diffuselimit',
    'karma:object:reflectlimit',
    'karma:object:refractlimit',
    'karma:object:volumelimit',
    'karma:object:ssslimit',
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
                                  frames via the partial_render chain).
                                  first_frame/last_frame are REQUIRED when
                                  render is on and have no default — the
                                  caller resolves them per entity.
                                  pre_roll/post_roll default to 0 and EXTEND
                                  the rendered range (first_frame - pre_roll
                                  .. last_frame + post_roll), matching what
                                  the dialog shows.
                - Playblast section: pb_department, pb_pool, pb_priority,
                                     pb_res

    Returns:
        List of submitted job IDs

    Raises:
        BatchSubmitError: If submission fails
    """
    # Extract config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    entity_context = config['entity']['context']
    settings = config['settings']

    do_publish = settings.get('publish', False)
    do_render = settings.get('render', False)
    do_playblast = settings.get('playblast', False)

    # Publish settings
    pub_pool = settings.get('pub_pool', 'general')
    pub_priority = settings.get('pub_priority', 50)

    # Render settings
    render_department = settings.get('render_department')
    channels = read_channel_names(settings, where='submit settings') or ['default']
    render_pool = settings.get('render_pool', 'general')
    render_priority = settings.get('render_priority', 50)
    tile_count = settings.get('tile_count', 4)
    # No fallback: the submitter resolves the frame range per entity (see
    # tumblepipe/asset_browser/submit_jobs_resolve.py), and omits the keys
    # when the entity has none. Defaulting to 1001-1100 here is how a
    # multi-shot batch used to render every shot at a wrong length, and how
    # an entity with no configured range rendered a plausible-looking
    # sequence instead of failing. Playblast has always asked config per
    # shot; this is render catching up.
    first_frame = settings.get('first_frame')
    last_frame = settings.get('last_frame')
    # The render handles. Unlike first_frame/last_frame above, these DO take a
    # default: the dialog declares default=0 for both (the FIELDS table in
    # tumblepipe/asset_browser/submit_jobs_resolve.py, sourced from the entity's
    # roll_start / roll_end), and 0 is the identity - no handles - rather than
    # a guessed range.
    pre_roll = settings.get('pre_roll', 0)
    post_roll = settings.get('post_roll', 0)
    batch_size = settings.get('batch_size', 10)
    denoise = settings.get('denoise', True)
    copy_to_edit = settings.get('copy_to_edit', False)
    standalone = settings.get('standalone', False)
    render_mode = settings.get('render_mode', 'full')

    # Playblast settings
    pb_department = settings.get('pb_department')
    pb_pool = settings.get('pb_pool', 'general')
    pb_priority = settings.get('pb_priority', 50)
    pb_res = settings.get('pb_res', _preview.DEFAULT_PLAYBLAST_RES)

    # 'first_middle_last' submits the partial_render chain (3 check frames
    # + notify) instead of the full_render chain (all frames + slapcomp/mp4).
    render_task_key = (
        'partial_render' if render_mode == 'first_middle_last'
        else 'full_render'
    )

    if not do_publish and not do_render and not do_playblast:
        return []

    # A Multi owns no staged stage, frame range or channels — its members
    # do. Without this, it got as far as the staged-file lookup and failed
    # with "Publish the shot first", which no publish could ever satisfy.
    if entity_uri.purpose == 'groups':
        raise BatchSubmitError(
            f"{entity_uri} is a Multi, not a shot or asset. Submit its "
            "member entities instead (the Submit Jobs dialog does this "
            "when opened from a Multi)."
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

    # Connect to Deadline
    try:
        farm = Deadline()
    except Exception as e:
        raise BatchSubmitError(f"Could not connect to Deadline: {e}")

    # Create batch
    project_name = api.PROJECT_PATH.name
    user_name = get_user_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    batch_name_parts = [project_name]
    if do_publish:
        batch_name_parts.append('publish')
    if do_render:
        batch_name_parts.append('render')
    if do_playblast:
        batch_name_parts.append('playblast')
    batch_name_parts.extend([str(entity_uri), user_name, timestamp])

    batch = Batch(' '.join(batch_name_parts))

    # Track job indices for dependencies
    jobs = {}  # name -> Job
    deps = {}  # name -> [dep_names]
    last_publish_job_name = None

    # Helper to finalize and submit batch
    def _finalize_batch():
        if not jobs:
            logging.info(f"No jobs to submit for {entity_uri}")
            return []

        indices = {}
        for job_name, job in jobs.items():
            indices[job_name] = batch.add_job(job)

        for job_name, job_deps in deps.items():
            for dep_name in job_deps:
                if dep_name and dep_name in indices:
                    batch.add_dep(indices[job_name], indices[dep_name])

        # Submit batch
        jobs_dir = api.storage.resolve(Uri.parse_unsafe('export:/other/jobs'))
        job_ids = farm.submit(batch, jobs_dir)

        logging.info(f"Submitted batch for {entity_uri}: {len(job_ids)} jobs")
        return job_ids

    # Create temp directory for staging files (used by both publish and render jobs)
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_path_str:
        temp_path = Path(temp_path_str)
        paths = {}  # Shared paths dict for bundling files

        # Add publish jobs
        if do_publish:
            prev_job_name = None
            # Use 'default' channel for batch publish jobs
            channel_name = 'default'
            for dept_name in pub_dept_names:
                if not _publish.is_submissable(entity_uri, dept_name):
                    logging.warning(
                        f"No workfile to publish for {entity_uri}/{dept_name}"
                    )
                    continue

                # The old "up to" spelling skips an up-to-date prefix. A
                # department named explicitly is published regardless.
                if (
                    skip_up_to_date and prev_job_name is None
                    and not _publish.is_out_of_date(entity_uri, channel_name, dept_name)
                ):
                    continue

                job_name = f'publish_{dept_name}'
                try:
                    job = _publish.create_publish_job(entity_uri, dept_name, pub_pool, pub_priority, paths, temp_path)
                    if job is not None:
                        jobs[job_name] = job
                        deps[job_name] = [prev_job_name] if prev_job_name else []
                        prev_job_name = job_name
                        last_publish_job_name = job_name
                except ValueError as e:
                    logging.warning(f"Could not create publish job for {entity_uri}/{dept_name}: {e}")

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
            if do_playblast and 'default' not in build_channels:
                build_channels.append('default')
            build_names = []
            for build_channel in build_channels:
                job_name = f'build_{build_channel}'
                jobs[job_name] = build_job.create(dict(
                    entity_uri=str(entity_uri),
                    priority=pub_priority,
                    pool_name=pub_pool,
                    variant_name=build_channel,
                ), temp_path)
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
                    priority=pub_priority,
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
                        variant_names=list(channels),
                        overrides=render_overrides,
                        task_key=render_task_key,
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
                        input_paths = _preview.collapse_render_inputs(
                            entity_uri, channels, render_department,
                            render_overrides, temp_path, paths,
                        )
                    except _preview.PreviewError as e:
                        raise BatchSubmitError(str(e)) from e
                    render_settings_path = _preview.write_render_settings(
                        temp_path, paths, channels,
                        _preview.aov_names(entity_uri, channels),
                    )
                    render_config = _preview.render_config(
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
                        task_key=render_task_key,
                    )
                    try:
                        render_job.build(
                            render_config, paths, temp_path, jobs, deps,
                            depends_on=preview_deps,
                        )
                        logging.info(f"Added render jobs for {entity_uri}")
                    except Exception as e:
                        raise BatchSubmitError(f"Could not build render jobs for {entity_uri}: {e}")
            else:
                # === STAGE + RENDER MODE (standalone=True) ===
                # A farm stage job builds the render stage, applying the
                # overrides, then submits the render itself. It composes when
                # it runs, so after a publish it only needs to wait for it.
                render_settings_path = _preview.write_render_settings(
                    temp_path, {}, channels,
                    _preview.aov_names(entity_uri, channels),
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
                        variant_names=channels,
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
                        render_task_key: dict(priority=render_priority, denoise=denoise, channel_name='renders')
                    }
                )

                # Build stage job using existing task builder
                stage_job_name = 'stage'
                try:
                    stage_paths = {render_settings_path: relative_render_settings_path}
                    stage_job = stage_task.build(stage_config, stage_paths, temp_path)
                    jobs[stage_job_name] = stage_job
                    deps[stage_job_name] = list(preview_deps)
                except Exception as e:
                    logging.warning(f"Could not create stage job for {entity_uri}: {e}")

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
                    playblast_input = _preview.collapse_playblast_input(
                        entity_uri, pb_department, temp_path, paths,
                    )
                    playblast_config = _preview.playblast_config(
                        entity_uri,
                        department=pb_department,
                        input_path=playblast_input,
                        user_name=user_name,
                        pool_name=pb_pool,
                        priority=pb_priority,
                        res=pb_res,
                    )
                except _preview.PreviewError as e:
                    raise BatchSubmitError(str(e)) from e
                try:
                    playblast_job.build(
                        playblast_config, paths, temp_path, jobs, deps,
                        depends_on=preview_deps,
                    )
                    logging.info(f"Added playblast job for {entity_uri}")
                except Exception as e:
                    raise BatchSubmitError(
                        f"Could not build playblast job for {entity_uri}: {e}"
                    )

        if collapse_config is not None:
            try:
                jobs['collapse'] = collapse_task.build(collapse_config, {}, temp_path)
                deps['collapse'] = list(preview_deps)
            except Exception as e:
                raise BatchSubmitError(
                    f"Could not build the collapse job for {entity_uri}: {e}"
                )

        # Submit batch (within temp directory context so files can be copied)
        return _finalize_batch()


def submit_all(configs: list[dict]) -> dict[str, list[str]]:
    """
    Submit batches for multiple entities.

    Args:
        configs: List of job configurations (one per entity)

    Returns:
        Dict mapping entity URI to list of job IDs
    """
    results = {}
    errors = []

    for config in configs:
        entity_uri = config['entity']['uri']
        try:
            job_ids = submit_entity_batch(config)
            results[entity_uri] = job_ids
        except BatchSubmitError as e:
            logging.error(f"Failed to submit batch for {entity_uri}: {e}")
            errors.append((entity_uri, str(e)))

    if errors:
        logging.warning(f"Submission completed with {len(errors)} error(s)")

    return results
