"""Batch job submission for the project browser dialog.

This module orchestrates the creation and submission of publish and render jobs
based on the job submission dialog configuration.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import logging
import os
from typing import Optional

from tumblehead.api import (
    fix_path,
    path_str,
    to_wsl_path,
    to_windows_path,
    get_user_name,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.util.io import store_json, load_json, store_text
from tumblehead.pipe.context import get_aov_names_from_context
from tumblehead.config.timeline import get_frame_range
from tumblehead.config.department import list_departments
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)
from tumblehead.pipe.paths import (
    latest_hip_file_path,
    latest_export_path,
    next_export_path,
    get_latest_staged_file_path,
)
from tumblehead.pipe.usd import collapse_latest_references
import tumblehead.farm.tasks.stage.task as stage_task
import tumblehead.farm.tasks.render.task as render_task
import tumblehead.farm.tasks.publish.task as publish_task
import tumblehead.farm.jobs.houdini.render.job as render_job

api = default_client()

# Mapping from column keys to Karma/USD render setting attribute paths
# These are used to build overrides for render_settings.json
# Supports both column keys (samples, mblur) and property paths (render.pathtracedsamples)
RENDER_OVERRIDE_MAP = {
    # Column keys (from job submission dialog)
    'samples': 'karma:global:pathtracedsamples',
    'mblur': 'karma:global:enablemotionblur',
    'dof': 'karma:global:enabledof',
    'denoise': 'karma:global:enabledenoising',
    'diffuse_limit': 'karma:global:diffuselimit',
    'reflection_limit': 'karma:global:reflectionlimit',
    'refraction_limit': 'karma:global:refractionlimit',
    'volume_limit': 'karma:global:volumelimit',
    'sss_limit': 'karma:global:ssslimit',
    # Property paths (for backward compatibility)
    'render.pathtracedsamples': 'karma:global:pathtracedsamples',
    'render.enablemblur': 'karma:global:enablemotionblur',
    'render.enabledof': 'karma:global:enabledof',
    'render.enabledenoising': 'karma:global:enabledenoising',
    'render.diffuselimit': 'karma:global:diffuselimit',
    'render.reflectionlimit': 'karma:global:reflectionlimit',
    'render.refractionlimit': 'karma:global:refractionlimit',
    'render.volumelimit': 'karma:global:volumelimit',
    'render.ssslimit': 'karma:global:ssslimit',
}


class BatchSubmitError(Exception):
    """Error during batch submission."""
    pass


def _build_render_overrides(settings: dict) -> dict:
    """Build render overrides dict from job settings.

    Maps settings keys to Karma/USD attribute paths using RENDER_OVERRIDE_MAP.

    Args:
        settings: Job settings dict from submission dialog

    Returns:
        Dict mapping USD attribute paths to values
    """
    overrides = {}
    for settings_key, usd_path in RENDER_OVERRIDE_MAP.items():
        if settings_key in settings:
            overrides[usd_path] = settings[settings_key]
    return overrides


def _get_aov_names(entity_uri: Uri, department: str, variants: list[str]) -> list[str]:
    """Get AOV names from layer exports or root layer context.

    Args:
        entity_uri: The entity URI for the shot/asset
        department: The render department name
        variants: List of variant names to check

    Returns:
        List of AOV names, or empty list if not found
    """
    # Try to get from layer exports
    for variant in variants:
        export_path = latest_export_path(entity_uri, variant, department)
        if export_path is not None:
            context_path = export_path / 'context.json'
            context_data = load_json(context_path)
            aov_names = get_aov_names_from_context(context_data, variant)
            if aov_names:
                return aov_names

    # Fallback: read from root layer context
    root_context_path = api.storage.resolve(Uri.parse_unsafe('config:/usd/context.json'))
    root_context = load_json(root_context_path)
    aov_names = get_aov_names_from_context(root_context)
    if aov_names:
        return aov_names

    return []


def _is_submissable(entity_uri: Uri, department_name: str) -> bool:
    """Check if entity/department has a submittable hip file."""
    # Skip placeholder entities (any segment with '000')
    if '000' in entity_uri.segments:
        return False
    hip_path = latest_hip_file_path(entity_uri, department_name)
    if hip_path is None:
        return False
    return hip_path.exists()


def _is_out_of_date(entity_uri: Uri, variant_name: str, department_name: str) -> bool:
    """Check if export is older than hip file."""
    hip_path = latest_hip_file_path(entity_uri, department_name)
    export_path = latest_export_path(entity_uri, variant_name, department_name)
    if export_path is None:
        return True
    if not export_path.exists():
        return True
    return hip_path.stat().st_mtime > export_path.stat().st_mtime


def _create_publish_job(
    entity_uri: Uri,
    department_name: str,
    pool_name: str,
    priority: int,
    paths: dict,
    temp_path: Path
) -> Job:
    """Create a publish job using publish_task.build() pattern.

    Args:
        entity_uri: Entity URI to publish
        department_name: Department name
        pool_name: Deadline pool name
        priority: Job priority
        paths: Dict mapping source paths to relative dest paths (modified in place)
        temp_path: Staging directory for job files

    Returns:
        Job object or None if workfile not found

    Raises:
        BatchSubmitError: If frame range cannot be determined
    """
    # Find the workfile
    workfile_path = latest_hip_file_path(entity_uri, department_name)
    if workfile_path is None or not workfile_path.exists():
        logging.warning(f'No workfile found for {entity_uri}/{department_name}')
        return None

    # Get frame range
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise BatchSubmitError(
            f"Cannot get frame range for entity: {entity_uri}. "
            "Ensure the entity has frame_start, frame_end, roll_start, roll_end properties configured."
        )
    render_range = frame_range.full_range()

    # Add workfile to paths for bundling
    workfile_dest = Path('workfiles') / f'{department_name}_{workfile_path.name}'
    paths[workfile_path] = workfile_dest

    # Bundle context.json alongside workfile (for group workfile detection)
    context_path = workfile_path.parent / 'context.json'
    if context_path.exists():
        context_dest = Path('workfiles') / 'context.json'
        paths[context_path] = context_dest

    # Build config for publish_task.build()
    config = {
        'entity': {
            'uri': str(entity_uri),
            'department': department_name
        },
        'settings': {
            'priority': priority,
            'pool_name': pool_name,
            'first_frame': render_range.first_frame,
            'last_frame': render_range.last_frame
        },
        'tasks': {
            'publish': {}
        },
        'workfile_path': path_str(workfile_dest)
    }

    # Build job using publish_task
    return publish_task.build(config, paths, temp_path)


def submit_entity_batch(config: dict) -> list[str]:
    """
    Submit a batch of jobs for a single entity based on configuration.

    Args:
        config: Job configuration dict with keys:
            - entity: {uri, name, context}
            - settings:
                - publish: bool - whether to submit publish jobs
                - render: bool - whether to submit render jobs
                - Publish section: pub_department, pub_pool, pub_priority
                - Render section: render_department, variants, render_pool,
                                  render_priority, tile_count, pre_roll,
                                  first_frame, last_frame, post_roll,
                                  batch_size, denoise

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

    # Publish settings
    pub_department = settings.get('pub_department')
    pub_pool = settings.get('pub_pool', 'general')
    pub_priority = settings.get('pub_priority', 50)

    # Render settings
    render_department = settings.get('render_department')
    variants = settings.get('variants', ['default'])
    render_pool = settings.get('render_pool', 'general')
    render_priority = settings.get('render_priority', 50)
    tile_count = settings.get('tile_count', 4)
    first_frame = settings.get('first_frame', 1001)
    last_frame = settings.get('last_frame', 1100)
    batch_size = settings.get('batch_size', 10)
    denoise = settings.get('denoise', True)
    standalone = settings.get('standalone', False)

    if not do_publish and not do_render:
        return []

    # Get departments list
    departments = list_departments(entity_context)
    department_names = [d.name for d in departments]

    # Validate publish department
    if do_publish:
        if pub_department is None:
            raise BatchSubmitError("Publish department not specified")
        if pub_department not in department_names:
            raise BatchSubmitError(f"Invalid publish department: {pub_department}")

    # Validate render department
    if do_render:
        if render_department is None:
            raise BatchSubmitError("Render department not specified")
        if render_department not in department_names:
            raise BatchSubmitError(f"Invalid render department: {render_department}")

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
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        paths = {}  # Shared paths dict for bundling files

        # Add publish jobs
        if do_publish:
            # Get departments up to and including publish target
            pub_dept_names = department_names[:department_names.index(pub_department) + 1]

            prev_job_name = None
            # Use 'default' variant for batch publish jobs
            variant_name = 'default'
            for dept_name in pub_dept_names:
                if not _is_submissable(entity_uri, dept_name):
                    continue

                # Check if out of date (or if downstream changed)
                if prev_job_name is None and not _is_out_of_date(entity_uri, variant_name, dept_name):
                    continue

                job_name = f'publish_{dept_name}'
                try:
                    job = _create_publish_job(entity_uri, dept_name, pub_pool, pub_priority, paths, temp_path)
                    if job is not None:
                        jobs[job_name] = job
                        deps[job_name] = [prev_job_name] if prev_job_name else []
                        prev_job_name = job_name
                        last_publish_job_name = job_name
                except BatchSubmitError as e:
                    logging.warning(f"Could not create publish job for {entity_uri}/{dept_name}: {e}")

        # Add stage + render jobs
        if do_render:
            if not variants:
                variants = ['default']

            # Build render overrides from settings (maps column keys to USD paths)
            render_overrides = _build_render_overrides(settings)

            # Get AOV names from layer exports or root layer context
            aov_names = _get_aov_names(entity_uri, render_department, variants)

            # Create render_settings.json
            render_settings_path = temp_path / 'render_settings.json'
            store_json(render_settings_path, dict(
                variant_names=variants,
                aov_names=aov_names,
                overrides=render_overrides
            ))
            relative_render_settings_path = render_settings_path.relative_to(temp_path)

            if not standalone:
                # === DIRECT RENDER MODE (standalone=False) ===
                # Skip stage task, render directly using existing staged file with resolved references

                # Get the latest staged file (use first variant)
                variant_name = variants[0] if variants else 'default'
                latest_staged_path = get_latest_staged_file_path(entity_uri, variant_name)
                if latest_staged_path is None or not latest_staged_path.exists():
                    raise BatchSubmitError(
                        f"No staged file found for {entity_uri} variant '{variant_name}'. "
                        f"Expected staged files at: export:/{'/'.join(entity_uri.segments)}/_staged/{variant_name}/. "
                        "Publish the entity first to create staged files."
                    )

                # Create collapsed USD with resolved version references
                collapsed_stage_path = temp_path / 'collapsed_stage.usda'
                collapsed_content = collapse_latest_references(latest_staged_path, collapsed_stage_path)
                store_text(collapsed_stage_path, collapsed_content)
                relative_collapsed_path = collapsed_stage_path.relative_to(temp_path)

                # Build render config for render job module
                # - render_settings_path: absolute (loaded locally during job building)
                # - input_path: relative (resolves in job data dir on farm)
                render_config = dict(
                    entity=dict(
                        uri=str(entity_uri),
                        department=render_department
                    ),
                    settings=dict(
                        user_name=user_name,
                        purpose='render',
                        pool_name=render_pool,
                        variant_names=variants,
                        render_department_name=render_department,
                        render_settings_path=path_str(render_settings_path),
                        input_path=path_str(relative_collapsed_path),  # Relative path for farm (resolves in job data dir)
                        tile_count=tile_count,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        step_size=1,
                        batch_size=batch_size
                    ),
                    tasks=dict(
                        full_render=dict(
                            priority=render_priority,
                            denoise=denoise,
                            channel_name='renders'
                        )
                    )
                )

                # Submit any pending publish jobs first
                pub_job_ids = _finalize_batch()

                # Submit render job directly (creates its own batch)
                render_paths = {
                    render_settings_path: relative_render_settings_path,
                    collapsed_stage_path: relative_collapsed_path
                }
                try:
                    result = render_job.submit(render_config, render_paths)
                    if result != 0:
                        raise BatchSubmitError(f"Standalone render submission failed for {entity_uri}")
                    logging.info(f"Submitted standalone render for {entity_uri}")
                    return pub_job_ids
                except Exception as e:
                    raise BatchSubmitError(f"Could not submit standalone render for {entity_uri}: {e}")
            else:
                # === STAGE + RENDER MODE (standalone=True) ===
                # Create stage job on farm, then render
                stage_config = dict(
                    entity=dict(
                        uri=str(entity_uri),
                        department=render_department
                    ),
                    settings=dict(
                        user_name=user_name,
                        purpose='render',
                        pool_name=render_pool,
                        variant_names=variants,
                        render_department_name=render_department,
                        render_settings_path=path_str(relative_render_settings_path),
                        tile_count=tile_count,
                        first_frame=first_frame,
                        last_frame=last_frame,
                        step_size=1,  # Default
                        batch_size=batch_size
                    ),
                    tasks=dict(
                        stage=dict(priority=render_priority, channel_name='exports'),
                        full_render=dict(priority=render_priority, denoise=denoise, channel_name='renders')
                    )
                )

                # Build stage job using existing task builder
                stage_job_name = 'stage'
                try:
                    stage_paths = {render_settings_path: relative_render_settings_path}
                    stage_job = stage_task.build(stage_config, stage_paths, temp_path)
                    jobs[stage_job_name] = stage_job
                    deps[stage_job_name] = [last_publish_job_name] if last_publish_job_name else []
                except Exception as e:
                    logging.warning(f"Could not create stage job for {entity_uri}: {e}")

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
