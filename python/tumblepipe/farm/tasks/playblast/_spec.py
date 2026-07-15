"""Config schema for the playblast task family.

Shared by ``playblast.py`` (worker CLI) and ``task.py`` (submit-side task
builder) so neither carries its own copy of the validator, and so the worker
never has to import ``task.py`` (which pulls in Deadline / tomli_w).

A playblast task is monolithic: unlike render (which chunks frames across many
tasks), the mp4 encode needs every frame, so a single task renders the whole
range with the Houdini GL (Hydra Storm) delegate and encodes the video. The
config is therefore flat and worker-facing, mirroring the render/mp4 task
configs rather than the job-level ``{entity, settings, tasks}`` shape.

config = {
    'title': 'string',
    'priority': 'int',
    'pool_name': 'string',
    'first_frame': 'int',
    'last_frame': 'int',
    'step_size': 'int',
    'fps': 'int',
    'res': ['int', 'int'],
    'input_path': 'string',        # collapsed staged USD, relative to job data
    'output_paths': ['string']     # versioned playblast mp4 + rolling daily mp4
}
"""

from tumblepipe.farm._common import (
    check_str,
    check_int,
    check_list,
    is_int,
)


def is_valid_config(config):
    if not isinstance(config, dict): return False
    if not check_str(config, 'title'): return False
    if not check_int(config, 'priority'): return False
    if not check_str(config, 'pool_name'): return False
    if not check_int(config, 'first_frame'): return False
    if not check_int(config, 'last_frame'): return False
    if not check_int(config, 'step_size'): return False
    if not check_int(config, 'fps'): return False
    if not check_list(config, 'res'): return False
    if len(config['res']) != 2: return False
    if not all(is_int(value) for value in config['res']): return False
    if not check_str(config, 'input_path'): return False
    if not check_list(config, 'output_paths'): return False
    if len(config['output_paths']) == 0: return False
    for path in config['output_paths']:
        if not isinstance(path, str): return False
    return True
