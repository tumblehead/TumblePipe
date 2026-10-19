"""Config-validation primitives for the farm's JSON configs.

Every config a farm job builder takes, and every context a worker reads,
is validated against a schema in ``tumblepipe.farm.specs``; the schemas
are written with these. A validator answers True or False and never
raises, so a malformed config of any shape is refused rather than
crashing the check. Import-light: workers load it.
"""

from functools import partial
from typing import Callable


def is_str(datum) -> bool:
    return isinstance(datum, str)


def is_int(datum) -> bool:
    # bool is an int subclass, so a plain isinstance() accepts True/False
    # wherever a frame number, priority, tile count or batch size is expected,
    # and int(True) is 1: the worker would render frame 1 rather than refuse
    # the config. These validators are the last check before a farm worker
    # acts on a config, so the bool is refused here even though the submit
    # dialog refuses it upstream (farm/submit/settings.py coerce).
    return isinstance(datum, int) and not isinstance(datum, bool)


def is_bool(datum) -> bool:
    return isinstance(datum, bool)


def is_list(datum) -> bool:
    return isinstance(datum, list)


def is_str_list(datum) -> bool:
    return isinstance(datum, list) and all(isinstance(item, str) for item in datum)


def is_int_list(datum) -> bool:
    return isinstance(datum, list) and all(is_int(item) for item in datum)


def is_str_dict(datum) -> bool:
    """A ``{str: str}`` mapping (AOV or layer name to a path)."""
    if not isinstance(datum, dict): return False
    return all(isinstance(k, str) and isinstance(v, str) for k, v in datum.items())


def check(value_checker: Callable, data: dict, key: str) -> bool:
    if key not in data: return False
    if not value_checker(data[key]): return False
    return True


check_str = partial(check, is_str)
check_int = partial(check, is_int)
check_bool = partial(check, is_bool)
check_list = partial(check, is_list)
check_str_list = partial(check, is_str_list)
check_int_list = partial(check, is_int_list)
check_str_dict = partial(check, is_str_dict)


def valid_entity(entity) -> bool:
    """The ``{uri, department}`` entity block a pipeline config carries."""
    if not isinstance(entity, dict): return False
    if not check_str(entity, 'uri'): return False
    if not check_str(entity, 'department'): return False
    return True


def valid_job_header(config) -> bool:
    """The ``title``/``priority``/``pool_name`` every job builder config carries."""
    if not isinstance(config, dict): return False
    if not check_str(config, 'title'): return False
    if not check_int(config, 'priority'): return False
    if not check_str(config, 'pool_name'): return False
    return True


def valid_frame_range(config, step: bool = True) -> bool:
    """``first_frame``/``last_frame`` (and ``step_size`` unless ``step`` is
    False) and a positive step. The order of first and last is the worker's
    to check (``worker.run``): these are shape checks."""
    if not isinstance(config, dict): return False
    if not check_int(config, 'first_frame'): return False
    if not check_int(config, 'last_frame'): return False
    if step:
        if not check_int(config, 'step_size'): return False
        if config['step_size'] <= 0: return False
    return True


def valid_frame_batches(config) -> bool:
    """A batched frame range: :func:`valid_frame_range` plus the explicit
    ``frames`` list (empty: the whole range) and a positive ``batch_size``."""
    if not valid_frame_range(config): return False
    if not check_int_list(config, 'frames'): return False
    if not check_int(config, 'batch_size'): return False
    if config['batch_size'] <= 0: return False
    return True


def valid_channel_names(settings) -> bool:
    """A ``channel_names`` list holding channel names.

    The readers take ``list(value)``, so a bare string read as one channel per
    letter -- ``'default'`` became seven one-letter renders. A validator must
    refuse it, and a ``None``, instead.
    """
    if not isinstance(settings, dict): return False
    return is_str_list(settings.get('channel_names'))


def valid_channel_name(settings) -> bool:
    """A ``channel_name`` naming one channel."""
    if not isinstance(settings, dict): return False
    return isinstance(settings.get('channel_name'), str)
