"""Centralized file-based logging configuration for the pipeline.

Nothing here runs on import: each process's entry point calls
:func:`setup_logging` once (``startup.bootstrap`` for a Houdini session,
``farm.worker.run`` for a farm task, the ``__main__`` of each standalone
tool), so importing ``tumblepipe`` never touches the host's loggers."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB
DEFAULT_BACKUP_COUNT = 3

_initialized = False
_project_handler: Optional[logging.Handler] = None
_console_handler: Optional[logging.Handler] = None


def _get_log_filename() -> str:
    """Get log filename based on the pipeline user.

    Never the OS username or hostname — these logs land in shared project
    storage and must not leak the machine's identity. The anonymous
    fallback keeps the filename valid; identity-less writers then share
    one rotating log, which is acceptable degradation.
    """
    user = os.environ.get('TH_USER') or 'pipeline'
    return f"{user}.log"


def _create_file_handler(log_path: Path, level: int = logging.INFO) -> RotatingFileHandler:
    """Create a rotating file handler."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding='utf-8'
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    return handler


def _get_project_log_path() -> Optional[Path]:
    """Get project-level log path from environment."""
    project_path_str = os.environ.get('TH_PROJECT_PATH')
    if not project_path_str:
        return None
    project_path = Path(project_path_str)
    if not project_path.exists():
        return None
    # Log to export/other/logs/ directory (next to jobs/)
    return project_path / 'export' / 'other' / 'logs' / _get_log_filename()


def _is_ci() -> bool:
    """Woodpecker and other CI runs log nowhere on a share."""
    return os.environ.get('CI') == 'true' or os.environ.get('WOODPECKER') == 'true'


def setup_logging(level: int = logging.INFO, console: bool = False) -> None:
    """Add TumblePipe's handlers to the root logger, once per process.

    Handlers other packages installed stay. A later call only sets the root
    level again. Skipped under CI.

    Args:
        level: Logging level (default: INFO)
        console: If True, log everything to the console too (default: False).
            Warnings and above reach the console regardless.
    """
    global _initialized, _project_handler, _console_handler

    if _is_ci():
        return
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if _initialized:
        return

    # Project-level file handler
    project_log_path = _get_project_log_path()
    if project_log_path:
        _project_handler = _create_file_handler(project_log_path, level)
        root_logger.addHandler(_project_handler)

    # Console handler: everything when requested or in dev mode, and
    # warnings upward always. The process console is what the Desktop
    # launcher captures into its per-project Houdini log, which is the
    # file an artist can find and send in - a pipeline warning that
    # exists only in export/other/logs on the share is invisible to
    # them. INFO stays file-only so the console is not flooded.
    console_level = (
        level if console or os.environ.get('TH_DEV', '0') == '1'
        else max(level, logging.WARNING)
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    root_logger.addHandler(console_handler)
    _console_handler = console_handler

    _initialized = True


def get_log_paths() -> list[Path]:
    """Every file this session is logging to.

    For telling an artist where a failure's traceback landed: an error dialog
    that only says "check the log" is barely better than silence. Derived from
    the live handler rather than recomputed from the environment, so it names
    the file actually being written.
    """
    if _project_handler is None:
        return []
    return [Path(_project_handler.baseFilename)]

