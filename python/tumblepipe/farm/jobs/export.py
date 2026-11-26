"""The export job: runs ``workers/export.py``, which exports a workfile's
stage and submits its render (the lookdev turntable)."""

from tumblepipe.farm.checks import checked
from tumblepipe.farm.deadline import farm_job, run_once
from tumblepipe.farm.specs import export as export_spec


def build(config, paths, staging_path):
    """The export job for ``config`` (``specs.export.is_valid_config``)."""
    checked(export_spec.is_valid_config, config, 'export')
    entity = config['entity']
    settings = config['settings']
    job = farm_job('export', config, staging_path,
        name = f"export {entity['uri']} {entity['department']}",
        pool = settings['pool_name'],
        group = 'houdini',
        priority = config['tasks']['export']['priority'],
        max_minutes = 30,
        paths = paths
    )
    # One task over the range: the limit is the whole export's, 30 minutes
    # plus 15 s a frame, at most 6 hours (run_once).
    run_once(job, settings['first_frame'], settings['last_frame'],
        minutes_per_frame = 0.25, most_minutes = 6 * 60)
    return job
