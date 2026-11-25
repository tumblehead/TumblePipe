"""Config schema of the build pipeline (``pipelines/build.py``, whose
docstring shows the config): one job writing an entity's next staged USD."""

from tumblepipe.farm.checks import (
    check_int,
    check_str,
    valid_channel_name,
    valid_frame_range,
)


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'entity_uri'): return False
    if not valid_channel_name(config): return False
    if not check_int(config, 'priority'): return False
    if not check_str(config, 'pool_name'): return False
    has_range = ('first_frame' in config, 'last_frame' in config)
    if has_range == (True, True):
        return valid_frame_range(config, step=False)
    return has_range == (False, False)
