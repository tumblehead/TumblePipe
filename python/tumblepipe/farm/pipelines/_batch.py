"""What every pipeline shares: submitting its jobs as one farm batch.

A pipeline (``farm/pipelines/<name>.py``) has the same shape throughout:
``build(config, paths, temp_path, jobs, deps, depends_on)`` adds its jobs
to ``jobs``/``deps`` and returns the names later work should depend on;
and ``submit(config, paths)`` submits them as one batch.
"""

from pathlib import Path
from typing import Callable
import datetime as dt
import logging

from tumblepipe.api import api, get_project_name, project_temp_dir
from tumblepipe.util.uri import Uri


def batch_title(*parts) -> str:
    """``<project> <parts...> <timestamp>``, the name a batch shows in Deadline."""
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    return ' '.join([get_project_name(), *map(str, parts), timestamp])


def new_batch(title: str, entity=None):
    """An empty batch named ``title``, every job of it stamped with this
    project and ``entity`` (a URI; ``farm.stamp``), which is how the Farm
    Manager and the clean-ups tell whose jobs they are."""
    # Imported here: the pipeline modules are imported by the workers that
    # chain them, which need not reach Deadline to do so.
    from tumblepipe.farm.job import Batch
    from tumblepipe.farm import stamp
    return Batch(title, extra_info=stamp.stamp(get_project_name(), entity))


def submit_batch(
    title: str,
    build: Callable,
    config: dict,
    paths: dict[Path, Path] | None = None,
    *,
    entity=None,
    ) -> list[str]:
    """Build a pipeline's jobs in a temp dir and submit them as one batch.

    ``build`` is the pipeline's ``build(config, paths, temp_path, jobs,
    deps)``. ``entity`` is the URI of the entity the batch is for, stamped
    on its jobs with the project (:func:`new_batch`). Returns the submitted
    job ids, Deadline's or the local farm's (none when the pipeline found
    nothing to do); a farm that cannot be reached raises.
    """
    from tumblepipe.farm.backend import get_farm

    # Deadline, or this machine's local farm (farm.backend).
    farm = get_farm()
    with project_temp_dir() as temp_path:
        logging.debug(f'Temporary directory: {temp_path}')

        jobs = {}
        deps = {}
        build(config, dict(paths or {}), temp_path, jobs, deps)
        if not jobs:
            logging.info(f'{title}: nothing to submit')
            return []

        batch = new_batch(title, entity)
        batch.add_jobs_with_deps(jobs, deps)
        return farm.submit(batch, api.storage.resolve(Uri.parse('export:/other/jobs')))
