"""The export job: runs ``workers/export.py``, which exports a workfile's
stage and submits its render (the lookdev turntable)."""

import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import export as export_spec


def build(config, paths, staging_path):
    """The export job for ``config`` (``specs.export.is_valid_config``)."""
    assert export_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    entity = config['entity']
    settings = config['settings']
    job = farm_job('export', config, staging_path,
        name = f"export {entity['uri']} {entity['department']}",
        pool = settings['pool_name'],
        group = 'houdini',
        priority = config['tasks']['export']['priority'],
        max_minutes = 15,
        paths = paths
    )
    run_once(job, settings['first_frame'], settings['last_frame'])
    return job
