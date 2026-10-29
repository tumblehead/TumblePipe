"""The publish pipeline: publish a department (and the downstream ones the
config names) from a bundled workfile, then post a notify.

Also the publish pieces the batch submitter (``batch_submit``) uses:
which workfiles can be published, and the publish job of an entity's
latest finished workfile, bundled.

Config: ``specs.publish``.
"""

from pathlib import Path

from tumblepipe.api import get_user_name, path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.config.timeline import get_export_frame_range
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.jobs import publish as publish_job
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.farm.specs import publish as publish_spec
from tumblepipe.pipe.paths import (
    latest_complete_hip_file_path,
    latest_export_path,
    latest_hip_file_path,
)
from tumblepipe.util.uri import Uri


def is_submissable(entity_uri: Uri, department_name: str) -> bool:
    """Whether the entity's department has a workfile to publish."""
    # Placeholder entities (any segment '000') are never published.
    if '000' in entity_uri.segments:
        return False
    hip_path = latest_hip_file_path(entity_uri, department_name)
    if hip_path is None:
        return False
    return hip_path.exists()


def is_out_of_date(entity_uri: Uri, channel_name: str, department_name: str) -> bool:
    """Whether the latest export is older than the latest workfile."""
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
    # A workfile saved outside the pipeline's save has no context.json; its
    # export nodes then name their entity themselves.
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
    ) -> Job:
    """The publish job of the department's latest finished workfile,
    bundled into ``paths``.

    Raises ValueError when there is no finished workfile, or no frame range.
    """
    # The newest FINISHED version: a save still in flight would bundle a
    # half-written hip ("Invalid .hip file header" on the worker).
    workfile_path = latest_complete_hip_file_path(entity_uri, department_name)
    if workfile_path is None or not workfile_path.exists():
        raise ValueError(
            f'No finished workfile to publish for {entity_uri}/'
            f'{department_name} (a save may still be in flight)'
        )
    frame_range = get_export_frame_range(entity_uri)
    if frame_range is None:
        raise ValueError(
            f'Cannot get frame range for entity: {entity_uri}. Ensure the '
            'entity has frame_start, frame_end, roll_start, roll_end '
            'properties configured.'
        )
    render_range = frame_range.full_range()
    workfile_dest = bundle_workfile(workfile_path, department_name, paths)
    return publish_job.build({
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
    }, paths, temp_path)


def _downstream_suffix(config) -> str:
    downstream = publish_spec.downstream_departments(config)
    return f' +{len(downstream)} downstream' if downstream else ''


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the publish job and its notify after ``depends_on``."""
    entity_uri = config['entity']['uri']
    jobs['publish'] = publish_job.build(config, paths, temp_path)
    deps['publish'] = list(depends_on or [])
    jobs['notify'] = notify_job.build(dict(
        title = f'notify publish {entity_uri}',
        priority = notify_job.PRIORITY,
        pool_name = config['settings']['pool_name'],
        user_name = get_user_name(),
        channel_name = 'exports',
        message = f'Published {entity_uri}{_downstream_suffix(config)}',
        command = dict(mode = 'notify')
    ), dict(), temp_path)
    deps['notify'] = ['publish']
    return ['notify']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the publish of ``config`` as one batch."""
    return submit_batch(batch_title(
        'publish', config['entity']['uri'], get_user_name()
    ) + _downstream_suffix(config), build, config, paths)
