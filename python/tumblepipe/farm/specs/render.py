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
    'frame_ranges': [[1, 30], [60, 100]],   # optional (#84): only these
                                    # ranges, in blocks that never span a gap
                                    # (farm.specs.frame_ranges); frames empty
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.usd',    # relative to the job's data dir
    'output_paths': {
        'beauty': 'path/to/beauty.####.exr',
        'depth': 'path/to/depth.####.exr'
    },
    'render_pass': '/Render/Passes/characters',  # optional: husk --pass
    'slap_comp': 'slapcomp/sq010_sh010_slapcomp_v0002.bgeo.sc'
                    # optional: husk --slap-comp, relative to the job's data
                    # dir (pipe.slap_comp); only with tile_count 1
}

context = {     # what the worker reads; Deadline appends the frame span
    'tile_count': 4,
    'step_size': 1,         # the task's frames: first..last by this (required)
    'blocks': [[1, 10], ...],   # optional: a multi-range job's tasks, each
                                # named by its first frame (worker.stepped)
    'receipt_path': ...,
    'input_path': ...,
    'output_paths': {...},
    'render_pass': ...,     # optional
    'slap_comp': ...        # optional
}
"""

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.checks import (
    check_int,
    check_str,
    check_str_dict,
    valid_frame_batches,
    valid_job_header,
    valid_step,
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
    if 'render_pass' in config and not check_str(config, 'render_pass'): return False
    if 'slap_comp' in config:
        if not check_str(config, 'slap_comp'): return False
        # husk runs a slap comp on each tile, not on the stitched frame.
        if config['tile_count'] != 1: return False
    if not valid_step(config): return False
    if not frame_ranges.valid_context_entry(config): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_batches(config): return False
    if not frame_ranges.valid_entry(config, explicit_frames=True): return False
    return is_valid_context(config)
