"""Config schemas of the denoise job (``jobs/denoise.py``) and its worker
(``workers/denoise.py``).

The job builder consumes the scheduling keys itself and writes only the
path keys and ``force_cpu`` into the worker's context.json; Deadline
appends the frame range on the command line. Hence two schemas:
``is_valid_config`` (the builder's) and ``is_valid_context`` (the worker's).

config = {
    'title': 'string',
    'priority': 'int',
    'pool_name': 'string',
    'first_frame': 'int',
    'last_frame': 'int',
    'frames': ['int'],          # empty => the whole first..last range
    'step_size': 'int',
    'batch_size': 'int',
    'force_cpu': 'bool',        # optional; OIDN on a CPU device (default: DEFAULT_FORCE_CPU)
    'receipt_path': 'string',   # '.####.json' frame pattern
    'input_paths': {'aov': 'string'},   # per-AOV '.####.exr' patterns
    'output_paths': {'aov': 'string'}
}
"""

from tumblepipe.farm.checks import (
    check_bool,
    check_str,
    check_str_dict,
    valid_frame_batches,
    valid_job_header,
)


# OIDN runs on the CPU unless a config says otherwise. Its CUDA device
# quantizes every denoised AOV to a couple of hundred values per stop (seen
# with Houdini 22.0.368's bundled OIDN 2.3.3 and Intel's own oidnDenoise 2.5.1;
# the CPU device keeps the full float precision), which shows as banding the
# moment a grade pushes the image. The CPU costs a few seconds a frame.
DEFAULT_FORCE_CPU = True


def _valid_paths(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'receipt_path'): return False
    if not check_str_dict(config, 'input_paths'): return False
    if not check_str_dict(config, 'output_paths'): return False
    return True


def is_valid_context(config) -> bool:
    """The keys the worker reads out of its context.json.

    ``force_cpu`` is required here: the job builder always writes it,
    resolving DEFAULT_FORCE_CPU on the submit side, the one place that
    default lives.
    """
    if not _valid_paths(config): return False
    if not check_bool(config, 'force_cpu'): return False
    return True


def is_valid_config(config) -> bool:
    """The schema the job builder is handed."""
    if not valid_job_header(config): return False
    if not valid_frame_batches(config): return False
    # Optional here: absent means DEFAULT_FORCE_CPU.
    if 'force_cpu' in config and not isinstance(config['force_cpu'], bool): return False
    return _valid_paths(config)
