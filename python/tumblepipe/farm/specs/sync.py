"""Config schemas of the sync job (``jobs/sync.py``) and its worker
(``workers/sync.py``), which copies a shot's best AOVs into the edit.

config = {
    'title': 'sync',
    'priority': 50,
    'pool_name': 'general',
    'entity_uri': 'entity:/shots/sequence/shot',
    'first_frame': 1,
    'last_frame': 100,
    'purpose': 'render',
    'publish_contexts': ['path/to/v0004/context.json']  # optional
}

context = {
    'entity_uri': 'entity:/shots/sequence/shot',
    'purpose': 'render',
    'publish_contexts': ['path/to/v0004/context.json']  # optional
}

``publish_contexts`` (a comp's Copy to edit) are the ``context.json`` of the
versions the sync follows. The worker reads them when it runs and, if one
records ``"published": false`` (``pipe.comp.unpublished_reason``: a comp
that turned out to read a check render), syncs nothing, logs a ``WARNING:``
line and exits 0.
"""

from tumblepipe.farm.checks import (
    check_str,
    valid_frame_range,
    valid_job_header,
    valid_publish_contexts,
)


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'entity_uri'): return False
    if not check_str(config, 'purpose'): return False
    if not valid_publish_contexts(config): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_range(config, step=False): return False
    return is_valid_context(config)
