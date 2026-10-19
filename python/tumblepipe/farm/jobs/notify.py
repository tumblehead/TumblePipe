"""The notify job: runs ``workers/notify.py`` to post to Discord."""

import json

from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import notify as notify_spec

# Every notify job runs at this priority, whatever its config asks for. A
# notify only starts once the work it reports on is done and takes seconds,
# so it goes ahead of everything: finished work should not wait behind the
# farm's queue before anyone hears of it.
PRIORITY = 100


def build(config, paths, staging_path):
    """The notify job for ``config`` (``specs.notify.is_valid_config``)."""
    assert notify_spec.is_valid_config(config), (
        f'Invalid config: {json.dumps(config, indent=4)}'
    )
    job = farm_job('notify', dict(
        user_name = config['user_name'],
        channel_name = config['channel_name'],
        message = config['message'],
        command = config['command']
    ), staging_path,
        name = config['title'],
        pool = config['pool_name'],
        group = 'general',
        priority = PRIORITY,
        max_minutes = 10,
        paths = paths
    )
    run_once(job, 1, 1)
    return job
