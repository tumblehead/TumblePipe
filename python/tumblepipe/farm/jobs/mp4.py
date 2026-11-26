"""The mp4 job: runs ``workers/mp4.py`` to encode a frame sequence."""

from pathlib import Path

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import mp4 as mp4_spec


def build(config, paths, staging_path):
    """The mp4 job for ``config`` (``specs.mp4.is_valid_config``)."""
    checked(mp4_spec.is_valid_config, config, 'mp4')
    job = farm_job('mp4', dict(
        first_frame = config['first_frame'],
        last_frame = config['last_frame'],
        step_size = config['step_size'],
        fps = config['fps'],
        input_path = config['input_path'],
        output_paths = config['output_paths'],
        # A multi-range render's frames only (#84)
        **({} if 'frame_ranges' not in config
           else dict(frame_ranges = config['frame_ranges'])),
        # A comp's: its dailies skipped when it turned out unpublished
        **{key: config[key] for key in ('daily_paths', 'publish_contexts')
           if key in config}
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'encode',
        priority = config['priority'],
        max_minutes = 20,
        paths = paths
    )
    # One task over the range: the limit is the whole encode's, 20 minutes
    # plus 3 s a frame (frames read over the share), at most 3 hours.
    run_once(job, config['first_frame'], config['last_frame'],
        minutes_per_frame = 0.05, most_minutes = 3 * 60)
    job.output_paths.extend(map(Path, config['output_paths']))
    return job
