"""The composite job: runs ``workers/composite.py`` over a comp workfile."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_batched
from tumblepipe.farm.specs import composite as composite_spec


def build(config, paths, staging_path):
    """The composite job for ``config`` (``specs.composite.is_valid_config``)."""
    assert composite_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('composite', dict(
        entity = config['entity'],
        step_size = config['step_size'],
        receipt_path = config['receipt_path'],
        input_path = config['input_path'],
        node_path = config['node_path'],
        layer_names = config['layer_names'],
        output_paths = config['output_paths']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'houdini',
        priority = config['priority'],
        max_minutes = 20,
        paths = paths
    )
    run_batched(
        job,
        config['first_frame'], config['last_frame'],
        config['step_size'], config['batch_size'], config['frames']
    )
    job.output_paths.append(Path(config['receipt_path']))
    return job
