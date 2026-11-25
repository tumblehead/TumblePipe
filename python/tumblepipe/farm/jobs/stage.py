"""The stage job: runs ``workers/stage.py``, which builds a shot's render
stages and submits their render."""

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import stage as stage_spec


def build(config, paths, staging_path):
    """The stage job for ``config`` (``specs.stage.is_valid_config``)."""
    checked(stage_spec.is_valid_config, config, 'stage')
    entity = config['entity']
    settings = config['settings']
    job = farm_job('stage', config, staging_path,
        name = f"stage {entity['uri']} {entity['department']}",
        pool = settings['pool_name'],
        group = 'houdini',
        priority = config['tasks']['stage']['priority'],
        max_minutes = 15,
        paths = paths
    )
    run_once(job, settings['first_frame'], settings['last_frame'])
    return job
