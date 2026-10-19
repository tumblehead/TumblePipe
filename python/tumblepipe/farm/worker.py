"""The entry point and shared helpers of the farm's worker scripts.

A worker (``tumblepipe.farm.workers.<name>``) is what a farm job runs: the
Deadline HPM plugin starts it as ``python -m <module> <context> <first>
<last>``, where ``<context>`` is the JSON its job builder bundled into the
job and ``<first>``/``<last>`` the frame span of the Deadline task. The
hython-side halves (``<name>_houdini.py``) are run by their worker as
``hython <script> <config>``, without frames. Each ends in::

    if __name__ == '__main__':
        sys.exit(worker.run(spec.is_valid_context, main))

(``hou.exit`` in place of ``sys.exit`` under hython).

Import-light: no Deadline, no ``tomli_w``, no ``hou``.
"""

from pathlib import Path
from typing import Callable
import argparse
import logging
import sys
import time

from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.io import load_json


class WorkerConfigError(ValueError):
    """A worker's config does not match its schema."""


def error(msg: str) -> int:
    """Log ``msg`` as the worker's failure and return its exit code (1)."""
    logging.error(msg)
    return 1


def headline(title: str) -> None:
    """A section banner in the worker's log."""
    print(f' {title} '.center(80, '='))


def configure_logging() -> None:
    """Log everything to stdout, which Deadline captures as the task log."""
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )


def load_config(path: Path, is_valid: Callable[[dict], bool]) -> dict:
    """The worker config at ``path``; raises when it is missing or invalid."""
    config = load_json(path)
    if not is_valid(config):
        raise WorkerConfigError(f'Invalid config: {path}')
    return config


def run(
    is_valid: Callable[[dict], bool],
    main: Callable[..., int],
    frames: bool = True
    ) -> int:
    """Run a worker script; returns ``main``'s exit code.

    Parses ``<config> <first> <last>`` (``<config>`` alone with ``frames``
    False), loads and validates the config, and calls ``main(config,
    BlockRange(first, last))`` -- or ``main(config)`` without frames.
    """
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=Path)
    if frames:
        parser.add_argument('first_frame', type=int)
        parser.add_argument('last_frame', type=int)
    args = parser.parse_args()
    config = load_config(args.config_path, is_valid)
    if not frames:
        return main(config)
    if args.first_frame > args.last_frame:
        return error(f'Invalid frame range: {args.first_frame}-{args.last_frame}')
    return main(config, BlockRange(args.first_frame, args.last_frame))


def ensure_dir(path: Path, attempts: int = 5, delay: float = 0.2) -> None:
    """``path.mkdir(parents=True, exist_ok=True)`` that survives a race on SMB.

    Farm tasks running at once create the same output folders on the shared
    drive. ``exist_ok`` only swallows the "already exists" error when the
    folder then reads back as a directory, and on an SMB share a folder
    another worker created a moment ago can still read as missing -- so the
    second worker failed with ``FileExistsError`` for a folder that was
    there (HideAndReek 070 denoise, 2026-10-08). Retry briefly until it is
    visible; anything else, or a folder that never appears, still raises.
    """
    for attempt in range(attempts):
        try:
            path.mkdir(parents=True, exist_ok=True)
            return
        except FileExistsError:
            if path.is_dir():
                return
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
