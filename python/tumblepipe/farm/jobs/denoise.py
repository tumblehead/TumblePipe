"""The denoise job: runs ``workers/denoise.py`` over a render's AOVs."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_batched
from tumblepipe.farm.specs import denoise as denoise_spec


def build(config, paths, staging_path):
    """The denoise job for ``config`` (``specs.denoise.is_valid_config``)."""
    assert denoise_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )

    # Only the path keys and force_cpu reach the worker; the scheduling keys
    # are consumed here, and Deadline appends the frame range on the command
    # line.
    job = farm_job('denoise', dict(
        receipt_path = config['receipt_path'],
        input_paths = config['input_paths'],
        output_paths = config['output_paths'],
        # CPU unless the config says otherwise: see
        # specs.denoise.DEFAULT_FORCE_CPU for why the GPU is not used.
        force_cpu = config.get('force_cpu', denoise_spec.DEFAULT_FORCE_CPU)
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'denoise',
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
