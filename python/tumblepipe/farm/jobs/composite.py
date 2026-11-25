"""The composite job: runs ``workers/composite.py`` over a comp workfile:
renders its ``th::export_comp`` nodes with the imports pinned."""

from pathlib import Path

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_batched
from tumblepipe.farm.specs import composite as composite_spec

#: A comp frame's time limit. Deadline counts it per frame (frame
#: timeouts), and so does the local runner (``queue.task_timeout``), so
#: one task over a whole range (This machine, #44) gets it for each frame.
FRAME_MINUTES = 20


def build(config, paths, staging_path):
    """The composite job for ``config`` (``specs.composite.is_valid_config``)."""
    checked(composite_spec.is_valid_config, config, 'composite')
    job = farm_job('composite', dict(
        entity = config['entity'],
        step_size = config['step_size'],
        receipt_path = config['receipt_path'],
        input_path = config['input_path'],
        exports = config['exports'],
        imports = config['imports'],
        # A comp of its submit's render (#80) reads it, not imports.
        **({} if 'renders' not in config else dict(renders = config['renders'])),
        # A re-comp beside a stepped render: the render it reads.
        **({} if 'follows' not in config else dict(follows = config['follows'])),
        # What a re-comp's worker checks a check render against.
        **({} if 'comp_frames' not in config else dict(comp_frames = config['comp_frames']))
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'houdini',
        priority = config['priority'],
        max_minutes = FRAME_MINUTES,
        paths = paths
    )
    run_batched(
        job,
        config['first_frame'], config['last_frame'],
        config['step_size'], config['batch_size'], config['frames']
    )
    job.output_paths.append(Path(config['receipt_path']))
    return job
