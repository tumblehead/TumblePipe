"""Config schema for the denoise task family.

Shared by ``denoise.py`` (worker CLI) and ``task.py`` (submit-side task
builder) so neither carries its own copy of the validator, and so the worker
never has to import ``task.py`` (which pulls in Deadline / tomli_w).

The worker receives a subset: ``task.py`` consumes the scheduling keys
(``title``/``priority``/``pool_name``/``step_size``/``batch_size``) itself and
writes only the path keys into the task's context.json, while Deadline appends
the frame range on the command line. Hence the two validators below rather than
one: ``is_valid_config`` is the full submit-side schema, ``is_valid_context``
the worker-side one.

config = {
    'title': 'string',
    'priority': 'int',
    'pool_name': 'string',
    'first_frame': 'int',
    'last_frame': 'int',
    'frames': ['int'],          # empty => the whole first..last range
    'step_size': 'int',
    'batch_size': 'int',
    'force_cpu': 'bool',        # optional; force OIDN onto a CPU device
    'receipt_path': 'string',   # '.####.json' frame pattern
    'input_paths': {'aov': 'string'},   # per-AOV '.####.exr' patterns
    'output_paths': {'aov': 'string'}
}
"""

from tumblepipe.farm._common import (
    check_str,
    check_int,
    check_list,
    is_bool,
    is_int,
)


def is_valid_layer(layer) -> bool:
    """The ``{aov_name: frame_path_pattern}`` map both path keys carry."""
    if not isinstance(layer, dict): return False
    for aov_name, aov_path in layer.items():
        if not isinstance(aov_name, str): return False
        if not isinstance(aov_path, str): return False
    return True


def is_valid_context(config) -> bool:
    """The keys the worker actually reads out of its context.json."""
    if not isinstance(config, dict): return False
    if not check_str(config, 'receipt_path'): return False
    if 'input_paths' not in config: return False
    if not is_valid_layer(config['input_paths']): return False
    if 'output_paths' not in config: return False
    if not is_valid_layer(config['output_paths']): return False
    # Optional: absent means "let OIDN pick its own device".
    if 'force_cpu' in config and not is_bool(config['force_cpu']): return False
    return True


def is_valid_config(config) -> bool:
    """The full submit-side schema ``task.build`` is handed."""
    if not isinstance(config, dict): return False
    if not check_str(config, 'title'): return False
    if not check_int(config, 'priority'): return False
    if not check_str(config, 'pool_name'): return False
    if not check_int(config, 'first_frame'): return False
    if not check_int(config, 'last_frame'): return False
    if not check_list(config, 'frames'): return False
    if not all(is_int(frame) for frame in config['frames']): return False
    if not check_int(config, 'step_size'): return False
    if not check_int(config, 'batch_size'): return False
    return is_valid_context(config)
