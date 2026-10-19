"""Config schemas of the build job (``jobs/build.py``) and its worker
(``workers/build.py``), which writes an entity's staged USD
(``pipe.staged_build``).

config = {
    'title': 'build',
    'priority': 50,
    'pool_name': 'general',
    'entity_uri': 'entity:/shots/sequence/shot',
    'channel_name': 'default',
    'output_path': 'path/to/_staged/default/v0003/010_sh020_v0003.usda',
    'first_frame': 1001,            # optional, both or neither: the range
    'last_frame': 1100              # to build (default: the entity's own)
}

The context is the same without the job header, the range resolved: a
shot's always, an asset's when it is animated (has a frame range).
"""

from tumblepipe.farm.checks import (
    check_str,
    valid_channel_name,
    valid_frame_range,
    valid_job_header,
)


def _valid_optional_range(config) -> bool:
    has_range = ('first_frame' in config, 'last_frame' in config)
    if has_range == (True, True):
        return valid_frame_range(config, step=False)
    return has_range == (False, False)


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'entity_uri'): return False
    if not check_str(config, 'output_path'): return False
    if not valid_channel_name(config): return False
    return _valid_optional_range(config)


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
