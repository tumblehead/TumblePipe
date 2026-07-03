"""Shared scaffolding for the farm task and job modules.

Every ``farm/tasks/<name>/<name>.py`` (worker-side CLI) and
``farm/tasks/<name>/task.py`` (submit-side task builder) validates the same
JSON config, and every worker CLI parses the same
``config_path start_frame end_frame`` argument triple. Only the config schema
genuinely differs per task; everything else was copy-pasted. This module owns
the copy-pasted parts:

- ``error``                — uniform error-log-and-return-1
- config-check primitives  — ``is_str``/``is_int``/``is_bool`` + ``check_*``
- ``valid_entity``         — the universal ``{uri, department}`` entity block
- ``run_task_cli``         — argparse + load + validate + main
- ``configure_logging``    — the ``__main__`` logging setup

``farm/jobs/houdini/_common.py`` re-exports the primitives so the job modules
keep their existing import surface. These modules run as standalone scripts on
the farm, so imports are absolute (``tumblepipe`` is already importable at
that point, same as ``tumblepipe.api``). Keep this module import-light: it is
pulled in by worker scripts that must not require Deadline or ``tomli_w``.
"""

from functools import partial
from pathlib import Path
from typing import Callable
import argparse
import logging
import sys

from tumblepipe.util.io import load_json


def error(msg: str) -> int:
    logging.error(msg)
    return 1


# Config-validation primitives ------------------------------------------------
# Each task's/job's _is_valid_config declares its own schema but shares these.
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


def valid_entity(entity) -> bool:
    """The ``{uri, department}`` entity block every task config carries."""
    if not isinstance(entity, dict): return False
    if not check_str(entity, 'uri'): return False
    if not check_str(entity, 'department'): return False
    return True


def run_task_cli(
    is_valid_config: Callable[[dict], bool],
    main: Callable[[dict], int]
    ) -> int:
    """Standard worker CLI: parse a config path (plus the frame range Deadline
    appends), load + validate the config, and run ``main``."""
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('start_frame', type=int)
    parser.add_argument('end_frame', type=int)
    args = parser.parse_args()

    # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return error(f'Config file not found: {config_path}')
    if not is_valid_config(config):
        return error(f'Invalid config file: {config_path}')

    # Run main
    return main(config)


def configure_logging() -> None:
    """The standard ``__main__`` logging setup used by every farm script."""
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
