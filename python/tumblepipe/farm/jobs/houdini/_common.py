"""Shared scaffolding for the Houdini farm job modules.

Every ``farm/jobs/houdini/<name>/job.py`` follows the same shape: validate a
JSON config, build a Deadline batch of jobs, and submit it. Only the config
schema, the batch title, and the ``build()`` internals genuinely differ per
job; everything else was copy-pasted. This module owns the copy-pasted parts:

- ``error``                — uniform error-log-and-return-1
- config-check primitives  — ``is_str``/``is_int``/``is_bool`` + ``check_*``
- ``submit_batch``         — Deadline connect + temp dir + batch + submit
- ``run_cli``              — argparse + load + validate + submit
- ``configure_logging``    — the ``__main__`` logging setup

These modules run as standalone scripts on the farm, so imports are absolute
(``tumblepipe`` is already importable at that point, same as ``tumblepipe.api``).
"""

from functools import partial
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Callable, Optional
import argparse
import logging
import sys

from tumblepipe.api import local_path, path_str, api
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri


def error(msg: str) -> int:
    logging.error(msg)
    return 1


# Config-validation primitives ------------------------------------------------
# Each job's _is_valid_config declares its own schema but shares these checks.
def is_str(datum) -> bool:
    return isinstance(datum, str)

def is_int(datum) -> bool:
    return isinstance(datum, int)

def is_bool(datum) -> bool:
    return isinstance(datum, bool)

def is_list(datum) -> bool:
    return isinstance(datum, list)

def check(value_checker: Callable, data: dict, key: str) -> bool:
    if key not in data: return False
    if not value_checker(data[key]): return False
    return True

check_str = partial(check, is_str)
check_int = partial(check, is_int)
check_bool = partial(check, is_bool)
check_list = partial(check, is_list)


def submit_batch(
    batch_title: str,
    build: Callable,
    config: dict,
    paths: Optional[dict[Path, Path]] = None
    ) -> int:
    """Connect to Deadline, assemble the batch in a temp dir, and submit it.

    ``build`` is the module's own ``build(config, paths, temp_path, jobs, deps)``
    function, invoked to populate the job/dependency dicts. Returns 0 on
    success, 1 if Deadline is unreachable.

    Deadline is imported lazily here so this module stays Deadline-free at import
    time — its ``error``/``check_*``/``configure_logging`` helpers are then safe
    to reuse from lightweight or hython-side scripts that must not pull Deadline.
    """
    from tumblepipe.apps.deadline import Deadline, Batch

    if paths is None:
        paths = {}

    # Get deadline ready
    try: farm = Deadline()
    except Exception: return error('Could not connect to Deadline')

    # Open temporary directory
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.debug(f'Temporary directory: {temp_path}')

        # Batch
        batch = Batch(batch_title)

        # Build jobs
        jobs = {}
        deps = {}
        build(config, paths, temp_path, jobs, deps)

        # Nothing to do (e.g. propagate found no dependents) -> skip submission
        if not jobs:
            logging.debug('No jobs to submit')
            return 0

        # Add jobs to batch and submit
        batch.add_jobs_with_deps(jobs, deps)
        farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

    # Done
    return 0


def run_cli(
    is_valid_config: Callable[[dict], bool],
    submit: Callable[[dict], int]
    ) -> int:
    """Standard CLI: parse a config path, load + validate it, and submit."""
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    args = parser.parse_args()

    # Check config path
    config_path = Path(args.config_path)
    if not config_path.exists():
        return error(f'Config path not found: {config_path}')

    # Load and check config
    config = load_json(config_path)
    if not is_valid_config(config):
        return error(f'Invalid config: {config_path}')

    # Run submit
    return submit(config)


def configure_logging() -> None:
    """The standard ``__main__`` logging setup used by every job script."""
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
