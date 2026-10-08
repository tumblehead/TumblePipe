"""Shared publish-job building for batch submitters.

Used by the Submit Jobs dialog path (batch_submit.py) and the scheduled
farm update (update/job.py) — previously three byte-identical copies of
these functions lived in each.
"""

from pathlib import Path
import logging

from tumblepipe.api import path_str
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import get_frame_range
from tumblepipe.pipe.paths import (
    latest_complete_hip_file_path,
    latest_hip_file_path,
    latest_export_path,
)
import tumblepipe.farm.tasks.publish.task as publish_task


def is_submissable(entity_uri: Uri, department_name: str) -> bool:
    """Check if entity/department has a submittable hip file."""
    # Skip placeholder entities (any segment with '000')
    if '000' in entity_uri.segments:
        return False
    hip_path = latest_hip_file_path(entity_uri, department_name)
    if hip_path is None:
        return False
    return hip_path.exists()


def is_out_of_date(entity_uri: Uri, channel_name: str, department_name: str) -> bool:
    """Check if the latest export is older than the latest hip file."""
    hip_path = latest_hip_file_path(entity_uri, department_name)
    export_path = latest_export_path(entity_uri, channel_name, department_name)
    if export_path is None:
        return True
    if not export_path.exists():
        return True
    return hip_path.stat().st_mtime > export_path.stat().st_mtime


def bundle_workfile(workfile_path: Path, department_name: str, paths: dict) -> Path:
    """Add a workfile and its context.json to a job bundle.

    Returns the workfile's path inside the bundle. Each workfile gets a
    folder of its own so its context.json travels with it: export nodes set
    to 'from_context' resolve their entity through ``get_workfile_context``,
    which reads the context.json next to the hip. The old layout put every
    hip of a batch in one ``workfiles/`` folder with a single context.json,
    so the last workfile bundled won and every other department resolved to
    its entity and failed with "No export node found".
    """
    bundle_dir = Path('workfiles') / f'{department_name}_{workfile_path.stem}'
    workfile_dest = bundle_dir / workfile_path.name
    paths[workfile_path] = workfile_dest
    context_path = workfile_path.parent / 'context.json'
    if context_path.exists():
        paths[context_path] = bundle_dir / 'context.json'
    return workfile_dest


def create_publish_job(
    entity_uri: Uri,
    department_name: str,
    pool_name: str,
    priority: int,
    paths: dict,
    temp_path: Path
    ):
    """Create a publish job using publish_task.build().

    Args:
        entity_uri: Entity URI to publish
        department_name: Department name
        pool_name: Deadline pool name
        priority: Job priority
        paths: Dict mapping source paths to relative dest paths (modified in place)
        temp_path: Staging directory for job files

    Returns:
        Job object, or None if no workfile exists for the department.

    Raises:
        ValueError: If the entity's frame range cannot be determined.
    """
    # Find the workfile
    # The newest FINISHED version: a save still in flight would bundle a
    # half-written hip ("Invalid .hip file header" on the worker).
    workfile_path = latest_complete_hip_file_path(entity_uri, department_name)
    if workfile_path is None or not workfile_path.exists():
        logging.warning(f'No workfile found for {entity_uri}/{department_name}')
        return None

    # Get frame range
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise ValueError(
            f'Cannot get frame range for entity: {entity_uri}. Ensure the '
            'entity has frame_start, frame_end, roll_start, roll_end '
            'properties configured.'
        )
    render_range = frame_range.full_range()

    workfile_dest = bundle_workfile(workfile_path, department_name, paths)

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

    return publish_task.build(config, paths, temp_path)
