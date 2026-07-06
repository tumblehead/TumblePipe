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


def is_out_of_date(entity_uri: Uri, variant_name: str, department_name: str) -> bool:
    """Check if the latest export is older than the latest hip file."""
    hip_path = latest_hip_file_path(entity_uri, department_name)
    export_path = latest_export_path(entity_uri, variant_name, department_name)
    if export_path is None:
        return True
    if not export_path.exists():
        return True
    return hip_path.stat().st_mtime > export_path.stat().st_mtime


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
    workfile_path = latest_hip_file_path(entity_uri, department_name)
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

    return publish_task.build(config, paths, temp_path)
