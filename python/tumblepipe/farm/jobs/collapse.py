"""The collapse job: runs ``workers/collapse.py``, which snapshots a freshly
published stage and submits its previews."""

import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import collapse as collapse_spec


def build(config, paths, staging_path):
    """The collapse job for ``config`` (``specs.collapse.is_valid_config``)."""
    assert collapse_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    entity = config['entity']
    previews = [key for key in ('render', 'playblast') if key in config]
    job = farm_job('collapse', config, staging_path,
        name = f"collapse {'+'.join(previews)} {entity['uri']} {entity['department']}",
        pool = config['settings']['pool_name'],
        # hython composes the staged stage through the project's resolver,
        # which is what the houdini group's workers are set up for.
        group = 'houdini',
        priority = config['settings']['priority'],
        max_minutes = 15,
        paths = paths
    )
    # One frame: the worker snapshots the stage once and submits the
    # previews; it renders nothing itself.
    run_once(job, 1, 1)
    return job
