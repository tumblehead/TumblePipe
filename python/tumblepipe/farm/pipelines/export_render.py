"""The export-render pipeline: a lookdev turntable (``th::lookdev_studio``).

One export job, which exports the workfile's stage and then submits the
render pipeline over it, and a notify once the export is through.

Config: ``specs.export``. Command line:
``python -m tumblepipe.farm.pipelines.export_render <config.json>``.
"""

from pathlib import Path
import sys

from tumblepipe.apps.deadline import Job
from tumblepipe.farm.jobs import export as export_job
from tumblepipe.farm.jobs import notify as notify_job
from tumblepipe.farm.pipelines._batch import batch_title, run_cli, submit_batch
from tumblepipe.farm.specs import export as export_spec


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the export and its notify after ``depends_on``."""
    settings = config['settings']
    jobs['export'] = export_job.build(config, paths, temp_path)
    deps['export'] = list(depends_on or [])
    jobs['notify'] = notify_job.build(dict(
        title = f"notify export {config['entity']['uri']}",
        priority = notify_job.PRIORITY,
        pool_name = settings['pool_name'],
        user_name = settings['user_name'],
        channel_name = config['tasks']['export']['channel_name'],
        message = f"Exported {settings['purpose']} {config['entity']['uri']}",
        command = dict(mode = 'notify')
    ), dict(), temp_path)
    deps['notify'] = ['export']
    return ['notify']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the export render of ``config`` as one batch."""
    settings = config['settings']
    return submit_batch(batch_title(
        settings['purpose'],
        config['entity']['uri'],
        settings['channel_name'],
        settings['user_name']
    ), build, config, paths)


if __name__ == '__main__':
    sys.exit(run_cli(export_spec.is_valid_config, submit))
