"""The build pipeline: one job writing an entity's next staged USD version.

config = {
    'entity_uri': 'entity:/shots/sequence/shot',
    'channel_name': 'default',
    'priority': 'int',
    'pool_name': 'string',
    'first_frame': 'int',           # optional, both or neither (default:
    'last_frame': 'int'             # the entity's own range)
}
"""

from pathlib import Path

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.checks import checked
from tumblepipe.farm.specs import build_pipeline as build_pipeline_spec
from tumblepipe.farm.jobs import build as build_job
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.pipe.paths import next_staged_file_path
from tumblepipe.util.uri import Uri


def create(config: dict, staging_path: Path) -> Job:
    """The build job for ``config``, for a caller chaining several (a
    submission rebuilding several channels of one entity needs one each)."""
    checked(build_pipeline_spec.is_valid_config, config, 'build pipeline')
    entity_uri = Uri.parse(config['entity_uri'])
    channel_name = config['channel_name']
    output_path = next_staged_file_path(entity_uri, channel_name)
    job_config = {
        'title': f'build {entity_uri.display_name()} {channel_name} {output_path.parent.name}',
        'priority': config['priority'],
        'pool_name': config['pool_name'],
        'entity_uri': config['entity_uri'],
        'channel_name': channel_name,
        'output_path': path_str(output_path),
    }
    if 'first_frame' in config:
        job_config['first_frame'] = config['first_frame']
        job_config['last_frame'] = config['last_frame']
    return build_job.build(job_config, dict(), staging_path)


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the build job (named ``build``) after ``depends_on``."""
    jobs['build'] = create(config, temp_path)
    deps['build'] = list(depends_on or [])
    return ['build']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the build of ``config`` as a batch of its own."""
    return submit_batch(
        batch_title('build', config['entity_uri']), build, config, paths,
        entity=config['entity_uri'],
    )
