"""Config schemas of the slapcomp job (``jobs/slapcomp.py``) and its worker
(``workers/slapcomp.py``), which stacks each channel's RGBA beauty over the
ones below it.

config = {
    'title': 'slapcomp',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'step_size': 1,
    'input_paths': {            # bottom of the stack first
        'default': {'beauty': 'path/to/beauty.####.exr'},
        'characters': {'beauty': 'path/to/beauty.####.exr'}
    },
    'receipt_path': 'path/to/receipt.####.json',
    'output_path': 'path/to/output.####.exr'
}

The worker comps the configured range itself, so its context is the config
without the job header.
"""

from tumblepipe.farm.checks import (
    check_str,
    is_str_dict,
    valid_frame_range,
    valid_job_header,
)


def is_valid_context(config) -> bool:
    if not valid_frame_range(config): return False
    if not isinstance(config.get('input_paths'), dict): return False
    for layer_name, layer in config['input_paths'].items():
        if not isinstance(layer_name, str): return False
        if not is_str_dict(layer): return False
    if not check_str(config, 'receipt_path'): return False
    if not check_str(config, 'output_path'): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
