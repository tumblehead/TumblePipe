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
    ],
    'frame_ranges': [[1, 30], [60, 100]],   # optional (#84): encode only
                    # these frames, back to back, each frame's number burnt
                    # in (farm.specs.frame_ranges)
    'daily_paths': ['path/to/output2.mp4'],     # optional: the outputs
                    # that are dailies, each one of output_paths
    'publish_contexts': ['path/to/v0004/context.json']  # optional: the
                    # context.json of each version the dailies publish
}

When ``publish_contexts`` is given the worker reads each when it runs and,
if one records ``"published": false`` (``pipe.comp.unpublished_reason``: a
comp that turned out to read a check render), writes the outputs that are
not dailies and no daily, logs a ``WARNING:`` line and exits 0. Without it
every output is written.

The worker encodes the configured range itself, so its context is the
config without the job header.
"""

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.checks import (
    check_int,
    check_str,
    check_str_list,
    is_str_list,
    valid_frame_range,
    valid_job_header,
    valid_publish_contexts,
)


def is_valid_context(config) -> bool:
    if not valid_frame_range(config): return False
    if not check_int(config, 'fps'): return False
    if not check_str(config, 'input_path'): return False
    if not check_str_list(config, 'output_paths'): return False
    # With no outputs the worker encoded nothing and exited 0.
    if len(config['output_paths']) == 0: return False
    if not frame_ranges.valid_entry(config): return False
    if 'daily_paths' in config:
        if not is_str_list(config['daily_paths']): return False
        if not set(config['daily_paths']) <= set(config['output_paths']): return False
    if not valid_publish_contexts(config): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
