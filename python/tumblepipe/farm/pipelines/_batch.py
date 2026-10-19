"""What every pipeline shares: submitting its jobs as one Deadline batch, and
its command-line entry point.

A pipeline (``farm/pipelines/<name>.py``) has the same shape throughout:
``build(config, paths, temp_path, jobs, deps, depends_on)`` adds its jobs
to ``jobs``/``deps`` and returns the names later work should depend on;
``submit(config, paths)`` submits them as one batch; and ``python -m
tumblepipe.farm.pipelines.<name> <config.json>`` submits a config file.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Callable
import argparse
import datetime as dt
import logging

from tumblepipe.api import api, get_project_name, path_str
from tumblepipe.util.io import load_json
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
    deps)``. Returns the submitted Deadline job ids (none when the pipeline
    found nothing to do); a farm that cannot be reached raises.
    """
    # Deadline is imported here: the pipeline modules are imported by the
    # workers that chain them, which need not reach Deadline to do so.
    from tumblepipe.apps.deadline import Deadline, Batch

    farm = Deadline()
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


def run_cli(
    is_valid_config: Callable[[dict], bool],
    submit: Callable[[dict], object]
    ) -> int:
    """``<module> <config.json>``: load, validate and submit a config file."""
    logging.basicConfig(level = logging.DEBUG, format = '%(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=Path)
    args = parser.parse_args()
    config = load_json(args.config_path)
    if not is_valid_config(config):
        logging.error(f'Invalid config: {args.config_path}')
        return 1
    submit(config)
    return 0
