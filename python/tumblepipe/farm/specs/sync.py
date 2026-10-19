"""Config schemas of the sync job (``jobs/sync.py``) and its worker
(``workers/sync.py``), which copies a shot's best AOVs into the edit.

config = {
    'title': 'sync',
    'priority': 50,
    'pool_name': 'general',
    'entity_uri': 'entity:/shots/sequence/shot',
    'first_frame': 1,
    'last_frame': 100,
    'purpose': 'render'
}

context = {
    'entity_uri': 'entity:/shots/sequence/shot',
    'purpose': 'render'
}
"""

from tumblepipe.farm.checks import (
    check_str,
    valid_frame_range,
    valid_job_header,
)


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'entity_uri'): return False
    if not check_str(config, 'purpose'): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_range(config, step=False): return False
    return is_valid_context(config)
