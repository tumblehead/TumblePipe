"""Config schemas of the playblast job (``jobs/playblast.py``) and its worker
(``workers/playblast.py``).

A playblast is monolithic: unlike a render (frames chunked across many
Deadline tasks), the mp4 encode needs every frame, so one task renders the
whole range with Karma XPU in a preview mode (see ``workers.playblast.
XPU_ARGS``) and encodes the video.

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

The worker's context is the config without the job header.
"""

from tumblepipe.farm.checks import (
    check_int,
    check_list,
    check_str,
    check_str_list,
    is_int,
    valid_frame_range,
    valid_job_header,
)


def is_valid_context(config) -> bool:
    if not valid_frame_range(config): return False
    if not check_int(config, 'fps'): return False
    if not check_list(config, 'res'): return False
    if len(config['res']) != 2: return False
    if not all(is_int(value) for value in config['res']): return False
    if not check_str(config, 'input_path'): return False
    if not check_str_list(config, 'output_paths'): return False
    if len(config['output_paths']) == 0: return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
