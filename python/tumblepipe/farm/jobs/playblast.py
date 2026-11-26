"""The playblast job: runs ``workers/playblast.py`` (husk with our viewport delegate, or a Karma XPU preview)."""

from pathlib import Path

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import playblast as playblast_spec

# Deadline worker group for playblast renders. A playblast is a Karma XPU
# render, so it runs on the same GPU workers as final renders; a dedicated
# 'playblast' group was never created on the farm, and a job whose group is
# missing lands in 'none' and runs on any worker.
PLAYBLAST_GROUP = 'karma'


def build(config, paths, staging_path):
    """The playblast job for ``config`` (``specs.playblast.is_valid_config``)."""
    checked(playblast_spec.is_valid_config, config, 'playblast')
    job = farm_job('playblast', dict(
        first_frame = config['first_frame'],
        last_frame = config['last_frame'],
        step_size = config['step_size'],
        fps = config['fps'],
        res = list(config['res']),
        look = dict(config['look']),
        input_path = config['input_path'],
        output_paths = config['output_paths']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = PLAYBLAST_GROUP,
        priority = config['priority'],
        max_minutes = 45,
        paths = paths
    )
    # One task for the whole range, so the one worker sees every frame and
    # can encode the mp4; its frame list the frames it renders, on its step.
    # Its limit is the whole playblast's: 45 minutes plus 30 s a frame, at
    # most 12 hours (run_once).
    run_once(job, config['first_frame'], config['last_frame'],
        minutes_per_frame = 0.5, most_minutes = 12 * 60,
        step_size = config.get('step_size', 1))
    job.output_paths.extend(map(Path, config['output_paths']))
    return job
