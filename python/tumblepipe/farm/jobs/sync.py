"""The sync job: runs ``workers/sync.py`` to copy a shot's AOVs to the edit."""

import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import sync as sync_spec


def build(config, paths, staging_path):
    """The sync job for ``config`` (``specs.sync.is_valid_config``)."""
    assert sync_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('sync', dict(
        entity_uri = config['entity_uri'],
        purpose = config['purpose']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'general',
        priority = config['priority'],
        max_minutes = 20,
        paths = paths
    )
    run_once(job, config['first_frame'], config['last_frame'])
    return job
