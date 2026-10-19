"""The build job: runs ``workers/build.py`` to write an entity's staged USD."""

from pathlib import Path
import json

from tumblepipe.config.entities import get_entity_type
from tumblepipe.config.timeline import get_frame_range
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import build as build_spec
from tumblepipe.util.uri import Uri


def build_range(config) -> tuple[int, int] | None:
    """The frames the staged USD is built for: the config's range, else the
    entity's full range. A shot must have one; an asset without one is
    static and is built without timing."""
    if 'first_frame' in config:
        return config['first_frame'], config['last_frame']
    entity_uri = Uri.parse(config['entity_uri'])
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        if get_entity_type(entity_uri) == 'shot':
            raise ValueError(
                f'Cannot get frame range for shot: {entity_uri}. Ensure the '
                'entity has frame_start, frame_end, roll_start, roll_end '
                'properties configured.'
            )
        return None
    full_range = frame_range.full_range()
    return full_range.first_frame, full_range.last_frame


def build(config, paths, staging_path):
    """The build job for ``config`` (``specs.build.is_valid_config``)."""
    assert build_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    context = {
        'entity_uri': config['entity_uri'],
        'output_path': config['output_path'],
        'channel_name': config['channel_name'],
    }
    frames = build_range(config)
    if frames is not None:
        context['first_frame'], context['last_frame'] = frames
    job = farm_job('build', context, staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'general',
        priority = config['priority'],
        max_minutes = 5,
        paths = paths
    )
    # One task: the worker builds the whole range itself.
    run_once(job, *(frames or (0, 0)))
    job.output_paths.append(Path(config['output_path']))
    return job
