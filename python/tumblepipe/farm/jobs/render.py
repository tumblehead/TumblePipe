"""The render job: runs ``workers/render.py`` (husk) over a stage."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, schedule_frames
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import render as render_spec


def build(config, paths, staging_path):
    """The render job for ``config`` (``specs.render.is_valid_config``)."""
    assert render_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    context = dict(
        tile_count = config['tile_count'],
        # The farm hands a task only its first and last frame
        step_size = config['step_size'],
        receipt_path = config['receipt_path'],
        input_path = config['input_path'],
        output_paths = config['output_paths']
    )
    if 'render_pass' in config:
        context['render_pass'] = config['render_pass']
    if 'slap_comp' in config:
        context['slap_comp'] = config['slap_comp']
    # A multi-range render's tasks are its blocks (#84)
    context.update(frame_ranges.context_entry(config))
    job = farm_job('render', context, staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'karma',
        priority = config['priority'],
        max_minutes = 45,
        paths = paths
    )
    schedule_frames(job, config)
    job.output_paths.append(Path(config['receipt_path']))
    return job
