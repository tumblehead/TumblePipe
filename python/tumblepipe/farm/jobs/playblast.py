"""The playblast job: runs ``workers/playblast.py`` (husk, Karma XPU preview)."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import playblast as playblast_spec

# Deadline worker group for playblast renders. A playblast is a Karma XPU
# render, so it runs on the same GPU workers as final renders; a dedicated
# 'playblast' group was never created on the farm, and a job whose group is
# missing lands in 'none' and runs on any worker.
PLAYBLAST_GROUP = 'karma'


def build(config, paths, staging_path):
    """The playblast job for ``config`` (``specs.playblast.is_valid_config``)."""
    assert playblast_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
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
    # can encode the mp4.
    run_once(job, config['first_frame'], config['last_frame'])
    job.output_paths.extend(map(Path, config['output_paths']))
    return job
