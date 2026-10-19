"""The render job: runs ``workers/render.py`` (husk) over a stage."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_batched
from tumblepipe.farm.specs import render as render_spec


def build(config, paths, staging_path):
    """The render job for ``config`` (``specs.render.is_valid_config``)."""
    assert render_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('render', dict(
        tile_count = config['tile_count'],
        receipt_path = config['receipt_path'],
        input_path = config['input_path'],
        output_paths = config['output_paths']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'karma',
        priority = config['priority'],
        max_minutes = 45,
        paths = paths
    )
    run_batched(
        job,
        config['first_frame'], config['last_frame'],
        config['step_size'], config['batch_size'], config['frames']
    )
    job.output_paths.append(Path(config['receipt_path']))
    return job
