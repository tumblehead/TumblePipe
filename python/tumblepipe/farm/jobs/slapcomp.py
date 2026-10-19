"""The slapcomp job: runs ``workers/slapcomp.py`` over the channels' beauties."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import slapcomp as slapcomp_spec


def build(config, paths, staging_path):
    """The slapcomp job for ``config`` (``specs.slapcomp.is_valid_config``)."""
    assert slapcomp_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('slapcomp', dict(
        first_frame = config['first_frame'],
        last_frame = config['last_frame'],
        step_size = config['step_size'],
        input_paths = config['input_paths'],
        receipt_path = config['receipt_path'],
        output_path = config['output_path']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'houdini',
        priority = config['priority'],
        max_minutes = 20,
        paths = paths
    )
    run_once(job, config['first_frame'], config['last_frame'])
    job.output_paths.append(Path(config['output_path']))
    return job
