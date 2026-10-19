"""Config schemas of the render job (``jobs/render.py``) and its worker
(``workers/render.py``).

config = {
    'title': 'render',
    'priority': 50,
    'pool_name': 'general',
    'tile_count': 4,
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 50, 100],         # empty => the whole first..last range
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.usd',    # relative to the job's data dir
    'output_paths': {
        'beauty': 'path/to/beauty.####.exr',
        'depth': 'path/to/depth.####.exr'
    }
}

context = {     # what the worker reads; Deadline appends the frame span
    'tile_count': 4,
    'receipt_path': ...,
    'input_path': ...,
    'output_paths': {...}
}
"""

from tumblepipe.farm.checks import (
    check_int,
    check_str,
    check_str_dict,
    valid_frame_batches,
    valid_job_header,
)

# husk tiles a frame into a square grid.
TILE_COUNTS = (1, 4, 16, 64, 256)


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_int(config, 'tile_count'): return False
    if not check_str(config, 'receipt_path'): return False
    if not check_str(config, 'input_path'): return False
    if not check_str_dict(config, 'output_paths'): return False
    # An empty map validated, and the worker then rendered, wrote receipts
    # holding {} and exited 0 having published no image at all.
    if len(config['output_paths']) == 0: return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_batches(config): return False
    return is_valid_context(config)
