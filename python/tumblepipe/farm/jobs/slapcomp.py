"""The slapcomp job: runs ``workers/slapcomp.py`` over the channels' beauties."""

from pathlib import Path

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import slapcomp as slapcomp_spec


def build(config, paths, staging_path):
    """The slapcomp job for ``config`` (``specs.slapcomp.is_valid_config``)."""
    checked(slapcomp_spec.is_valid_config, config, 'slapcomp')
    job = farm_job('slapcomp', dict(
        first_frame = config['first_frame'],
        last_frame = config['last_frame'],
        step_size = config['step_size'],
        input_paths = config['input_paths'],
        receipt_path = config['receipt_path'],
        output_path = config['output_path'],
        # A multi-range render's frames only (#84)
        **({} if 'frame_ranges' not in config
           else dict(frame_ranges = config['frame_ranges']))
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'houdini',
        priority = config['priority'],
        max_minutes = 20,
        paths = paths
    )
    # One task over the range: the limit is the whole comp's, 20 minutes
    # plus 15 s a frame, at most 6 hours (run_once).
    run_once(job, config['first_frame'], config['last_frame'],
        minutes_per_frame = 0.25, most_minutes = 6 * 60)
    job.output_paths.append(Path(config['output_path']))
    return job
