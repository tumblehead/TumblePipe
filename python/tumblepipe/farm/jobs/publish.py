"""The publish job: runs ``workers/publish.py`` over a bundled workfile."""

import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import publish as publish_spec

# Every publish job runs at this priority, whatever its config asks for.
# batch_submit runs the rest of a publish chain (builds, collapse) at it too,
# so those steps don't queue behind other shots' renders.
PRIORITY = 90


def build(config, paths, staging_path):
    """The publish job for ``config`` (``specs.publish.is_valid_config``)."""
    assert publish_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    entity = config['entity']
    settings = config['settings']
    job = farm_job('publish', config, staging_path,
        name = f"publish {entity['uri']} {entity['department']}",
        pool = settings['pool_name'],
        group = 'houdini',
        priority = PRIORITY,
        max_minutes = 15,
        paths = paths
    )
    run_once(job, settings['first_frame'], settings['last_frame'])
    return job
