"""
Resolve command queue utilities.

This module provides file-based command queue functionality for the Resolve
background service. Commands are stored as JSON files in a directory structure:

    queue_path/
      - pending/    - Commands waiting to be processed
      - running/    - Commands currently being processed
      - completed/  - Successfully completed commands
      - failed/     - Commands that failed

Each command file contains JSON with action, project, params, timestamp, etc.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from datetime import datetime
from pathlib import Path
import logging
import shutil
import json
import uuid


@dataclass
class ResolveCommand:
    """A command to execute in DaVinci Resolve.

    Attributes:
        action: Command type ('import_render', 'relink', 'create_timeline', etc.)
        project: Resolve project name to operate on.
        params: Command-specific parameters.
        timestamp: When the command was created.
        status: Current status ('pending', 'running', 'completed', 'failed').
        command_id: Unique identifier for this command.
        result: Result data after execution (set by service).
        error: Error message if failed (set by service).
    """
    action: str
    project: str
    params: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = 'pending'
    command_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    result: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ResolveCommand':
        """Create from dictionary."""
        return cls(
            action=data.get('action', ''),
            project=data.get('project', ''),
            params=data.get('params', {}),
            timestamp=data.get('timestamp', datetime.now().isoformat()),
            status=data.get('status', 'pending'),
            command_id=data.get('command_id', str(uuid.uuid4())[:8]),
            result=data.get('result'),
            error=data.get('error')
        )


def _ensure_queue_dirs(queue_path: Path) -> None:
    """Ensure queue directory structure exists."""
    for subdir in ['pending', 'running', 'completed', 'failed']:
        (queue_path / subdir).mkdir(parents=True, exist_ok=True)


def _command_filename(command: ResolveCommand) -> str:
    """Generate filename for a command."""
    # Format: YYYY-MM-DD_HHMMSS_action_id.json
    ts = datetime.fromisoformat(command.timestamp)
    ts_str = ts.strftime('%Y-%m-%d_%H%M%S')
    return f"{ts_str}_{command.action}_{command.command_id}.json"


def write_command(queue_path: Path, command: ResolveCommand) -> Path:
    """Write a command to the pending queue.

    Args:
        queue_path: Base path for the queue directory.
        command: Command to write.

    Returns:
        Path to the created command file.
    """
    _ensure_queue_dirs(queue_path)

    pending_path = queue_path / 'pending'
    filename = _command_filename(command)
    command_path = pending_path / filename

    with open(command_path, 'w') as f:
        json.dump(command.to_dict(), f, indent=2)

    logging.info(f"Wrote command to queue: {filename}")
    return command_path


def read_command(command_path: Path) -> Optional[ResolveCommand]:
    """Read a command from a file.

    Args:
        command_path: Path to the command JSON file.

    Returns:
        ResolveCommand object, or None if read failed.
    """
    try:
        with open(command_path, 'r') as f:
            data = json.load(f)
        return ResolveCommand.from_dict(data)
    except Exception as e:
        logging.error(f"Failed to read command {command_path}: {e}")
        return None


def read_pending_commands(queue_path: Path) -> list[tuple[Path, ResolveCommand]]:
    """Read all pending commands from the queue.

    Args:
        queue_path: Base path for the queue directory.

    Returns:
        List of (path, command) tuples, sorted by timestamp (oldest first).
    """
    _ensure_queue_dirs(queue_path)

    pending_path = queue_path / 'pending'
    results = []

    for json_file in sorted(pending_path.glob('*.json')):
        command = read_command(json_file)
        if command:
            results.append((json_file, command))

    return results


def move_to_running(command_path: Path, queue_path: Path) -> Path:
    """Move a command from pending to running.

    Args:
        command_path: Current path of the command file.
        queue_path: Base path for the queue directory.

    Returns:
        New path of the command file.
    """
    running_path = queue_path / 'running' / command_path.name
    shutil.move(str(command_path), str(running_path))

    # Update status in file
    command = read_command(running_path)
    if command:
        command.status = 'running'
        with open(running_path, 'w') as f:
            json.dump(command.to_dict(), f, indent=2)

    return running_path


def mark_command_complete(
    command_path: Path,
    queue_path: Path,
    result: dict
) -> Path:
    """Mark a command as completed with result.

    Args:
        command_path: Current path of the command file.
        queue_path: Base path for the queue directory.
        result: Result data from execution.

    Returns:
        New path of the command file.
    """
    completed_path = queue_path / 'completed' / command_path.name

    # Read, update, and move
    command = read_command(command_path)
    if command:
        command.status = 'completed'
        command.result = result

        with open(completed_path, 'w') as f:
            json.dump(command.to_dict(), f, indent=2)

        # Remove from running
        if command_path.exists():
            command_path.unlink()

    logging.info(f"Command completed: {command_path.name}")
    return completed_path


def mark_command_failed(
    command_path: Path,
    queue_path: Path,
    error: str
) -> Path:
    """Mark a command as failed with error message.

    Args:
        command_path: Current path of the command file.
        queue_path: Base path for the queue directory.
        error: Error message describing the failure.

    Returns:
        New path of the command file.
    """
    failed_path = queue_path / 'failed' / command_path.name

    # Read, update, and move
    command = read_command(command_path)
    if command:
        command.status = 'failed'
        command.error = error

        with open(failed_path, 'w') as f:
            json.dump(command.to_dict(), f, indent=2)

        # Remove from running
        if command_path.exists():
            command_path.unlink()

    logging.error(f"Command failed: {command_path.name} - {error}")
    return failed_path


def cleanup_old_commands(
    queue_path: Path,
    max_age_days: int = 7,
    keep_failed: bool = True
) -> int:
    """Clean up old completed (and optionally failed) commands.

    Args:
        queue_path: Base path for the queue directory.
        max_age_days: Maximum age in days before cleanup.
        keep_failed: Whether to keep failed commands.

    Returns:
        Number of files removed.
    """
    _ensure_queue_dirs(queue_path)

    cutoff = datetime.now().timestamp() - (max_age_days * 24 * 60 * 60)
    removed = 0

    # Clean completed
    for json_file in (queue_path / 'completed').glob('*.json'):
        if json_file.stat().st_mtime < cutoff:
            json_file.unlink()
            removed += 1

    # Optionally clean failed
    if not keep_failed:
        for json_file in (queue_path / 'failed').glob('*.json'):
            if json_file.stat().st_mtime < cutoff:
                json_file.unlink()
                removed += 1

    if removed:
        logging.info(f"Cleaned up {removed} old command files")

    return removed


# =============================================================================
# Convenience functions for creating common commands
# =============================================================================

def create_import_command(
    project: str,
    shot: str,
    department: str,
    version: str,
    render_path: str,
    frame_range: Optional[tuple[int, int]] = None,
    folder_path: Optional[str] = None
) -> ResolveCommand:
    """Create an import_render command.

    Args:
        project: Resolve project name.
        shot: Shot name (e.g., 'SEQ010_SHOT020').
        department: Department name (e.g., 'comp').
        version: Version string (e.g., 'v0003').
        render_path: Path to the render files.
        frame_range: Optional (start, end) frame range.
        folder_path: Optional target folder path in media pool.

    Returns:
        ResolveCommand configured for import.
    """
    params = {
        'shot': shot,
        'department': department,
        'version': version,
        'render_path': render_path
    }
    if frame_range:
        params['frame_range'] = list(frame_range)
    if folder_path:
        params['folder_path'] = folder_path

    return ResolveCommand(
        action='import_render',
        project=project,
        params=params
    )


def create_relink_command(
    project: str,
    shot_pattern: str,
    old_version: str,
    new_version: str
) -> ResolveCommand:
    """Create a relink command.

    Args:
        project: Resolve project name.
        shot_pattern: Shot name pattern (e.g., 'SEQ010_SHOT020*').
        old_version: Current version string (e.g., 'v0002').
        new_version: New version string (e.g., 'v0003').

    Returns:
        ResolveCommand configured for relinking.
    """
    return ResolveCommand(
        action='relink',
        project=project,
        params={
            'shot_pattern': shot_pattern,
            'old_version': old_version,
            'new_version': new_version
        }
    )


def create_timeline_command(
    project: str,
    timeline_name: str,
    shots: list[dict]
) -> ResolveCommand:
    """Create a create_timeline command.

    Args:
        project: Resolve project name.
        timeline_name: Name for the new timeline.
        shots: List of shot dicts with 'name', 'clip_name', 'start', 'end'.

    Returns:
        ResolveCommand configured for timeline creation.
    """
    return ResolveCommand(
        action='create_timeline',
        project=project,
        params={
            'timeline_name': timeline_name,
            'shots': shots
        }
    )


def create_render_command(
    project: str,
    timeline_name: str,
    preset: str,
    output_path: str,
    mark_in: Optional[int] = None,
    mark_out: Optional[int] = None
) -> ResolveCommand:
    """Create a render command.

    Args:
        project: Resolve project name.
        timeline_name: Name of timeline to render.
        preset: Render preset name.
        output_path: Output file/folder path.
        mark_in: Optional in point (frame).
        mark_out: Optional out point (frame).

    Returns:
        ResolveCommand configured for rendering.
    """
    params = {
        'timeline_name': timeline_name,
        'preset': preset,
        'output_path': output_path
    }
    if mark_in is not None:
        params['mark_in'] = mark_in
    if mark_out is not None:
        params['mark_out'] = mark_out

    return ResolveCommand(
        action='render',
        project=project,
        params=params
    )


def create_daily_command(
    project: str,
    date: Optional[str] = None,
    output_path: Optional[str] = None,
    preset: str = 'H.264 Master'
) -> ResolveCommand:
    """Create a generate_daily command.

    Args:
        project: Resolve project name.
        date: Date string (YYYY-MM-DD), defaults to today.
        output_path: Output file path for rendered daily.
        preset: Render preset name.

    Returns:
        ResolveCommand configured for daily generation.
    """
    params = {
        'preset': preset
    }
    if date:
        params['date'] = date
    if output_path:
        params['output_path'] = output_path

    return ResolveCommand(
        action='generate_daily',
        project=project,
        params=params
    )
