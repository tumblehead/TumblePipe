"""What every pipeline shares: submitting its jobs as one farm batch.

A pipeline (``farm/pipelines/<name>.py``) has the same shape throughout:
``build(config, paths, temp_path, jobs, deps, depends_on)`` adds its jobs
to ``jobs``/``deps`` and returns the names later work should depend on;
and ``submit(config, paths)`` submits them as one batch.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Callable
import datetime as dt
import logging

from tumblepipe.api import api, get_project_name, path_str
from tumblepipe.util.uri import Uri


def batch_title(*parts) -> str:
    """``<project> <parts...> <timestamp>``, the name a batch shows in Deadline."""
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    return ' '.join([get_project_name(), *map(str, parts), timestamp])


def submit_batch(
    title: str,
    build: Callable,
    config: dict,
    paths: dict[Path, Path] | None = None
    ) -> list[str]:
    """Build a pipeline's jobs in a temp dir and submit them as one batch.

    ``build`` is the pipeline's ``build(config, paths, temp_path, jobs,
    deps)``. Returns the submitted job ids, Deadline's or the local farm's
    (none when the pipeline found nothing to do); a farm that cannot be
    reached raises.
    """
    # Imported here: the pipeline modules are imported by the workers that
    # chain them, which need not reach Deadline to do so.
    from tumblepipe.apps.deadline import Batch
    from tumblepipe.farm.backend import get_farm

    # Deadline, or this machine's local farm (farm.backend).
    farm = get_farm()
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.debug(f'Temporary directory: {temp_path}')

        jobs = {}
        deps = {}
        build(config, dict(paths or {}), temp_path, jobs, deps)
        if not jobs:
            logging.info(f'{title}: nothing to submit')
            return []

        batch = Batch(title)
        batch.add_jobs_with_deps(jobs, deps)
        return farm.submit(batch, api.storage.resolve(Uri.parse('export:/other/jobs')))
