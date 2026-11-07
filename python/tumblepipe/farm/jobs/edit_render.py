"""The Render Edit job: runs ``workers/edit_render.py`` to render a published
edit version into a movie."""

from pathlib import Path
import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import edit_render as edit_render_spec

# A long edit renders every frame on the CPU and encodes it: well past the
# 20 minutes an mp4 encode of one shot gets.
MAX_MINUTES = 240


def build(config, paths, staging_path):
    """The Render Edit job for ``config`` (``specs.edit_render.is_valid_config``)."""
    assert edit_render_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('edit_render', dict(
        edit_uri = config['edit_uri'],
        version = config['version'],
        output_path = config['output_path']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'encode',
        priority = config['priority'],
        max_minutes = MAX_MINUTES,
        paths = paths
    )
    # One task: the player renders the whole edit, and the encode needs
    # every frame. The edit has no frame range of its own to give Deadline.
    run_once(job, 1, 1)
    job.output_paths.append(Path(config['output_path']))
    return job
