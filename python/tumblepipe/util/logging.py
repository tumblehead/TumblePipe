"""Centralized file-based logging configuration for the pipeline."""

import logging
import os
import socket
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB
DEFAULT_BACKUP_COUNT = 3
LOG_DIR_NAME = "_logs"

_initialized = False
_project_handler: Optional[logging.Handler] = None
_workspace_handler: Optional[logging.Handler] = None


def _get_log_filename() -> str:
    """Get log filename based on hostname and user."""
    hostname = socket.gethostname()
    user = os.environ.get('TH_USER', os.environ.get('USERNAME', 'unknown'))
    return f"{hostname}_{user}.log"


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


def setup_logging(
    workspace_path: Optional[Path] = None,
    level: int = logging.INFO,
    console: bool = False
) -> None:
    """Configure file-based logging for the pipeline.

    Args:
        workspace_path: Optional workspace for workspace-specific logs
        level: Logging level (default: INFO)
        console: If True, also log to console (default: False)
    """
    global _initialized, _project_handler, _workspace_handler

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not _initialized:
        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Project-level file handler
        project_log_path = _get_project_log_path()
        if project_log_path:
            _project_handler = _create_file_handler(project_log_path, level)
            root_logger.addHandler(_project_handler)

        # Console handler only if requested or in dev mode
        if console or os.environ.get('TH_DEV', '0') == '1':
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            console_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
            root_logger.addHandler(console_handler)

        _initialized = True

    # Update workspace handler
    if workspace_path:
        if _workspace_handler:
            root_logger.removeHandler(_workspace_handler)
        workspace_log_path = workspace_path / LOG_DIR_NAME / _get_log_filename()
        _workspace_handler = _create_file_handler(workspace_log_path, level)
        root_logger.addHandler(_workspace_handler)


def set_workspace(workspace_path: Optional[Path]) -> None:
    """Update workspace-specific log location."""
    global _workspace_handler

    root_logger = logging.getLogger()
    if _workspace_handler:
        root_logger.removeHandler(_workspace_handler)
        _workspace_handler = None

    if workspace_path:
        workspace_log_path = workspace_path / LOG_DIR_NAME / _get_log_filename()
        _workspace_handler = _create_file_handler(workspace_log_path)
        root_logger.addHandler(_workspace_handler)
