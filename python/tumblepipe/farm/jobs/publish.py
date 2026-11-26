"""The publish job: runs ``workers/publish.py`` over a bundled workfile."""

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import publish as publish_spec

# Every publish job runs at this priority, whatever its config asks for.
# batch_submit runs the rest of a publish chain (builds, collapse) at it too,
# so those steps don't queue behind other shots' renders.
PRIORITY = 90


def build(config, paths, staging_path):
    """The publish job for ``config`` (``specs.publish.is_valid_config``)."""
    checked(publish_spec.is_valid_config, config, 'publish')
    entity = config['entity']
    settings = config['settings']
    job = farm_job('publish', config, staging_path,
        name = f"publish {entity['uri']} {entity['department']}",
        pool = settings['pool_name'],
        group = 'houdini',
        priority = PRIORITY,
        max_minutes = 30,
        paths = paths
    )
    # One task over the shot: the limit is the whole publish's, 30 minutes
    # plus 15 s a frame, at most 6 hours (run_once).
    run_once(job, settings['first_frame'], settings['last_frame'],
        minutes_per_frame = 0.25, most_minutes = 6 * 60)
    return job
