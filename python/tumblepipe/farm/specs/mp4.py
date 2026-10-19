"""Config schemas of the mp4 job (``jobs/mp4.py``) and its worker
(``workers/mp4.py``), which encodes a frame sequence into one or more mp4s.

config = {
    'title': 'mp4',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'step_size': 1,
    'fps': 24,
    'input_path': 'path/to/input.####.exr',
    'output_paths': [
        'path/to/output1.mp4',
        'path/to/output2.mp4'
    ]
}

The worker encodes the configured range itself, so its context is the
config without the job header.
"""

from tumblepipe.farm.checks import (
    check_int,
    check_str,
    check_str_list,
    valid_frame_range,
    valid_job_header,
)


def is_valid_context(config) -> bool:
    if not valid_frame_range(config): return False
    if not check_int(config, 'fps'): return False
    if not check_str(config, 'input_path'): return False
    if not check_str_list(config, 'output_paths'): return False
    # With no outputs the worker encoded nothing and exited 0.
    if len(config['output_paths']) == 0: return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
