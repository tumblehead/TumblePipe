"""
Deadline scheduled job for generating daily edits in DaVinci Resolve.

This job is typically scheduled to run at end of day (e.g., 6 PM) to
automatically generate a daily edit timeline from the day's completed
renders.

Usage:
    # Submit directly
    python daily_job.py config.json

    # Or schedule in Deadline as a recurring job

Config format:
    {
        "project": "ShowName",
        "queue_path": "export:/other/resolve_queue",
        "output_path": "export:/renders/dailies/",
        "render_preset": "H.264 Master",
        "date": null,  # null = today
        "departments": ["comp", "lighting"],  # null = all
        "priority": 50,
        "pool_name": "general"
    }
"""

from tempfile import TemporaryDirectory
from pathlib import Path
from datetime import datetime
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    fix_path,
    default_client
)
from tumblehead.util.io import load_json, store_json
from tumblehead.util.uri import Uri
from tumblehead.apps.resolve import (
    ResolveCommand,
    write_command,
    create_daily_command
)

api = default_client()


def _error(msg):
    logging.error(msg)
    return 1


def _is_valid_config(config):
    """Validate job configuration."""
    if not isinstance(config, dict):
        return False
    if 'project' not in config:
        return False
    if not isinstance(config['project'], str):
        return False
    if 'queue_path' not in config:
        return False
    if not isinstance(config['queue_path'], str):
        return False
    # Optional fields
    if 'output_path' in config and not isinstance(config['output_path'], str):
        return False
    if 'render_preset' in config and not isinstance(config['render_preset'], str):
        return False
    if 'date' in config and config['date'] is not None:
        if not isinstance(config['date'], str):
            return False
    if 'departments' in config and config['departments'] is not None:
        if not isinstance(config['departments'], list):
            return False
    return True


def submit_daily_command(config: dict) -> int:
    """Submit a generate_daily command to the Resolve queue.

    Args:
        config: Job configuration dictionary.

    Returns:
        0 on success, 1 on error.
    """
    # Extract config
    project = config['project']
    queue_path_str = config['queue_path']
    date = config.get('date')  # None = today
    output_path = config.get('output_path')
    render_preset = config.get('render_preset', 'H.264 Master')
    departments = config.get('departments')  # None = all

    # Resolve queue path (supports URIs like export:/)
    try:
        uri = Uri.parse_unsafe(queue_path_str)
        queue_path = to_wsl_path(api.storage.resolve(uri))
    except Exception:
        queue_path = to_wsl_path(Path(queue_path_str))

    # Build command params
    params = {
        'preset': render_preset
    }
    if date:
        params['date'] = date
    if output_path:
        # Resolve output path URI if needed
        try:
            uri = Uri.parse_unsafe(output_path)
            resolved_output = api.storage.resolve(uri)
            params['output_path'] = path_str(resolved_output)
        except Exception:
            params['output_path'] = output_path
    if departments:
        params['departments'] = departments

    # Create the command
    command = create_daily_command(
        project=project,
        date=date,
        output_path=params.get('output_path'),
        preset=render_preset
    )

    # Add extra params not covered by create_daily_command
    if departments:
        command.params['departments'] = departments

    # Write to queue
    try:
        command_path = write_command(queue_path, command)
        logging.info(f'Submitted daily command: {path_str(command_path)}')
        print(f'Success: Daily command submitted to {path_str(queue_path)}')
        print(f'Command ID: {command.command_id}')
        return 0
    except Exception as e:
        return _error(f'Failed to submit daily command: {e}')


def cli():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(
        description='Submit a generate_daily command to the Resolve queue'
    )
    parser.add_argument('config_path', type=str, help='Path to config JSON file')
    parser.add_argument('--date', type=str, default=None,
                       help='Date override (YYYY-MM-DD), defaults to today')
    parser.add_argument('--project', type=str, default=None,
                       help='Project name override')
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config_path)
    if not config_path.exists():
        return _error(f'Config file not found: {config_path}')

    config = load_json(config_path)
    if config is None:
        return _error(f'Failed to load config: {config_path}')

    if not _is_valid_config(config):
        return _error(f'Invalid config: {config_path}')

    # Apply CLI overrides
    if args.date:
        config['date'] = args.date
    if args.project:
        config['project'] = args.project

    # Submit
    return submit_daily_command(config)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)s: %(message)s',
        stream=sys.stdout
    )
    sys.exit(cli())
