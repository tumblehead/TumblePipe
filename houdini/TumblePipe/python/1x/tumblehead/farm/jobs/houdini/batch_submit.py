"""Batch job submission for the project browser dialog.

This module orchestrates the creation and submission of publish and render jobs
based on the job submission dialog configuration.
"""

from pathlib import Path
import datetime as dt
import logging
from typing import Optional

from tumblehead.api import (
    path_str,
    to_wsl_path,
    to_windows_path,
    get_user_name,
    default_client
)
from tumblehead.util.uri import Uri
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
    latest_staged_path
)

api = default_client()

# Script paths for farm execution
PUBLISH_SCRIPT_PATH = Path(__file__).parent / 'update' / 'publish.py'
STAGE_SCRIPT_PATH = Path(__file__).parent / 'stage_render' / 'stage.py'


class BatchSubmitError(Exception):
    """Error during batch submission."""
    pass


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
    variant_name: str,
    department_name: str,
    pool_name: str,
    priority: int
) -> Job:
    """Create a publish job for a single department."""
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise BatchSubmitError(
            f"Cannot get frame range for entity: {entity_uri}. "
            "Ensure the entity has frame_start, frame_end, roll_start, roll_end properties configured."
        )
    render_range = frame_range.full_range()

    output_path = next_export_path(entity_uri, variant_name, department_name)
    version_name = output_path.name

    job = Job(
        to_wsl_path(PUBLISH_SCRIPT_PATH), None,
        str(entity_uri),
        department_name
    )
    job.name = f'publish {entity_uri} {department_name} {version_name}'
    job.pool = pool_name
    job.group = 'houdini'
    job.priority = priority
    job.start_frame = render_range.first_frame
    job.end_frame = render_range.last_frame
    job.step_size = 1
    job.chunk_size = len(render_range)
    job.max_frame_time = 10
    job.env.update(dict(
        TH_USER=get_user_name(),
        TH_CONFIG_PATH=path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH=path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH=path_str(to_wsl_path(api.PIPELINE_PATH))
    ))
    job.output_paths.append(to_windows_path(output_path))
    return job


def _create_stage_job(
    entity_uri: Uri,
    department_name: str,
    variants: list[str],
    pool_name: str,
    priority: int
) -> Job:
    """Create a stage job for rendering preparation."""
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise BatchSubmitError(
            f"Cannot get frame range for entity: {entity_uri}. "
            "Ensure the entity has frame_start, frame_end, roll_start, roll_end properties configured."
        )
    render_range = frame_range.full_range()

    # Stage job uses the staging script
    job = Job(
        to_wsl_path(STAGE_SCRIPT_PATH), None,
        str(entity_uri),
        department_name,
        ','.join(variants)
    )
    job.name = f'stage {entity_uri} {department_name} [{",".join(variants)}]'
    job.pool = pool_name
    job.group = 'houdini'
    job.priority = priority
    job.start_frame = render_range.first_frame
    job.end_frame = render_range.last_frame
    job.step_size = 1
    job.chunk_size = len(render_range)
    job.max_frame_time = 30
    job.env.update(dict(
        TH_USER=get_user_name(),
        TH_CONFIG_PATH=path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH=path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH=path_str(to_wsl_path(api.PIPELINE_PATH))
    ))
    return job


def submit_entity_batch(config: dict) -> list[str]:
    """
    Submit a batch of jobs for a single entity based on configuration.

    Args:
        config: Job configuration dict with keys:
            - entity: {uri, name, context}
            - settings: {publish, render, variants, department, pool_name, priority,
                        tile_count, pre_roll, first_frame, last_frame, post_roll,
                        batch_size, denoise}

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
    variants = settings.get('variants', ['default'])
    target_department = settings.get('department', 'lighting')
    pool_name = settings.get('pool_name', 'general')
    priority = settings.get('priority', 50)
    tile_count = settings.get('tile_count', 4)
    first_frame = settings.get('first_frame', 1001)
    last_frame = settings.get('last_frame', 1100)
    batch_size = settings.get('batch_size', 10)
    denoise = settings.get('denoise', True)

    if not do_publish and not do_render:
        return []

    # Get departments list
    departments = list_departments(entity_context)
    department_names = [d.name for d in departments]

    if target_department not in department_names:
        raise BatchSubmitError(f"Invalid department: {target_department}")

    # Get departments up to and including target
    department_names = department_names[:department_names.index(target_department) + 1]

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

    # Add publish jobs
    if do_publish:
        prev_job_name = None
        # Use 'default' variant for batch publish jobs
        variant_name = 'default'
        for dept_name in department_names:
            if not _is_submissable(entity_uri, dept_name):
                continue

            # Check if out of date (or if downstream changed)
            if prev_job_name is None and not _is_out_of_date(entity_uri, variant_name, dept_name):
                continue

            job_name = f'publish_{dept_name}'
            try:
                job = _create_publish_job(entity_uri, variant_name, dept_name, pool_name, priority)
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

        # Stage job depends on last publish (if any)
        stage_job_name = 'stage'
        try:
            stage_job = _create_stage_job(
                entity_uri, target_department, variants, pool_name, priority
            )
            jobs[stage_job_name] = stage_job
            deps[stage_job_name] = [last_publish_job_name] if last_publish_job_name else []
        except BatchSubmitError as e:
            logging.warning(f"Could not create stage job for {entity_uri}: {e}")
            stage_job_name = None

        # TODO: Add render jobs per variant
        # This requires importing and using the render job module:
        # - partial_render (test frames)
        # - full_render
        # - denoise (if enabled)
        # - mp4
        # - slapcomp (if multiple variants)
        # - notify

        # For now, log what would be created
        if stage_job_name:
            for variant in variants:
                logging.info(f"Would create render jobs for variant: {variant}")
                # render_job_name = f'render_{variant}'
                # ... create render job ...
                # deps[render_job_name] = [stage_job_name]

    # Add all jobs to batch with dependencies
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
