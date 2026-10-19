"""Config schemas of the composite job (``jobs/composite.py``), its worker
(``workers/composite.py``) and the worker's hython half
(``workers/composite_houdini.py``), which cooks a comp workfile's render
node once per channel ("layer").

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
    'title': 'composite',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 50, 100],         # empty => the whole first..last range
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'node_path': '/path/to/node',
    'layer_names': ['main', 'mirror'],
    'output_paths': {
        'main': 'path/to/main/beauty.####.exr',
        'mirror': 'path/to/mirror/beauty.####.exr'
    }
}

The worker's context is the config without the scheduling keys, keeping
``step_size`` (Deadline hands the worker a span; the step is the config's).
The hython config replaces it with the span the worker was handed and the
``frames`` of it still missing their receipts.
"""

from tumblepipe.farm.checks import (
    check_int,
    check_int_list,
    check_str,
    check_str_dict,
    check_str_list,
    valid_entity,
    valid_frame_batches,
    valid_frame_range,
    valid_job_header,
)


def _valid_composite(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not check_str(config, 'receipt_path'): return False
    if not check_str(config, 'input_path'): return False
    if not check_str(config, 'node_path'): return False
    if not check_str_list(config, 'layer_names'): return False
    if len(config['layer_names']) == 0: return False
    if not check_str_dict(config, 'output_paths'): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_batches(config): return False
    return _valid_composite(config)


def is_valid_context(config) -> bool:
    if not _valid_composite(config): return False
    # The step reached the Deadline job but never the worker once, so the
    # worker rebuilt a step-1 range and composited every in-between frame.
    if not check_int(config, 'step_size'): return False
    if config['step_size'] <= 0: return False
    return True


def is_valid_houdini_config(config) -> bool:
    if not _valid_composite(config): return False
    if not valid_frame_range(config, step=False): return False
    if not check_int_list(config, 'frames'): return False
    return True
