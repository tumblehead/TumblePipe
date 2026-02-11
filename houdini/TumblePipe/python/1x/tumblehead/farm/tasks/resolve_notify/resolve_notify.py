"""
Resolve notify task - writes a command to the Resolve queue.

This task is run as a Deadline post-job script after render completion.
It writes a command file to the shared queue directory, which the
Resolve background service monitors and processes.
"""

from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.util.io import load_json
from tumblehead.apps.resolve import (
    ResolveCommand,
    write_command
)
from tumblehead.farm.tasks.env import print_env

api = default_client()


def _error(msg):
    logging.error(msg)
    return 1


def main(queue_path: Path, command_data: dict) -> int:
    """Write a command to the Resolve queue.

    Args:
        queue_path: Path to the queue directory.
        command_data: Command data dictionary with action, project, params.

    Returns:
        0 on success, 1 on error.
    """
    # Print environment variables for debugging
    print_env()

    # Validate command data
    if 'action' not in command_data:
        return _error('Missing action in command data')
    if 'project' not in command_data:
        return _error('Missing project in command data')

    # Create the command
    command = ResolveCommand(
        action=command_data['action'],
        project=command_data['project'],
        params=command_data.get('params', {})
    )

    # Ensure queue directory exists and write command
    try:
        command_path = write_command(queue_path, command)
        logging.info(f'Wrote command to queue: {path_str(command_path)}')
        print(f'Success: Command written to {path_str(command_path)}')
        return 0
    except Exception as e:
        return _error(f'Failed to write command: {e}')


"""
config = {
    'queue_path': 'export:/other/resolve_queue',
    'command': {
        'action': 'import_render',
        'project': 'ShowName',
        'params': {
            'shot': 'SEQ010_SHOT020',
            'department': 'comp',
            'version': 'v0003',
            'render_path': '/renders/SEQ010/SHOT020/comp/v0003/',
            'frame_range': [1001, 1100]
        }
    }
}
"""

def _is_valid_config(config):

    def _is_valid_command(command):
        if not isinstance(command, dict): return False
        if 'action' not in command: return False
        if not isinstance(command['action'], str): return False
        if 'project' not in command: return False
        if not isinstance(command['project'], str): return False
        # params is optional but must be dict if present
        if 'params' in command:
            if not isinstance(command['params'], dict): return False
        return True

    if not isinstance(config, dict): return False
    if 'queue_path' not in config: return False
    if not isinstance(config['queue_path'], str): return False
    if 'command' not in config: return False
    if not _is_valid_command(config['command']): return False
    return True


def cli():

    # Define CLI
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()

    # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')

    # Resolve queue path (supports URIs like export:/)
    queue_path_str = config['queue_path']
    try:
        uri = Uri.parse_unsafe(queue_path_str)
        queue_path = to_wsl_path(api.storage.resolve(uri))
    except Exception:
        # Fallback to treating as regular path
        queue_path = to_wsl_path(Path(queue_path_str))

    # Get command data
    command_data = config['command']

    # Run main
    return main(queue_path, command_data)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(message)s',
        stream=sys.stdout
    )
    sys.exit(cli())
