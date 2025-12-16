"""Simple CLI wrapper for publish task.

This script is called by farm jobs with direct arguments rather than a config file.
It builds the config dict and delegates to the publish task infrastructure.
"""

from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent.parent
if str(tumblehead_packages_path) not in sys.path:
    sys.path.insert(0, str(tumblehead_packages_path))

from tumblehead.api import path_str
from tumblehead.util.uri import Uri
from tumblehead.pipe.paths import latest_hip_file_path
from tumblehead.farm.tasks.publish import publish


def _error(msg):
    logging.error(msg)
    return 1


def cli():
    import argparse
    parser = argparse.ArgumentParser(
        description='Publish an entity department to the farm'
    )
    parser.add_argument('entity_uri', type=str, help='Entity URI (e.g., entity:/shots/010/020)')
    parser.add_argument('department', type=str, help='Department name (e.g., layout)')
    parser.add_argument('start_frame', type=int, help='Start frame')
    parser.add_argument('end_frame', type=int, help='End frame')
    args = parser.parse_args()

    # Find the latest workfile for this entity/department
    entity_uri = Uri.parse_unsafe(args.entity_uri)
    workfile_path = latest_hip_file_path(entity_uri, args.department)
    if workfile_path is None or not workfile_path.exists():
        logging.error(f'No workfile found for {entity_uri}/{args.department}')
        return 1

    # Build config matching what publish.main() expects
    config = {
        'entity': {
            'uri': args.entity_uri,
            'department': args.department
        },
        'settings': {
            'priority': 50,
            'pool_name': 'general',
            'first_frame': args.start_frame,
            'last_frame': args.end_frame
        },
        'tasks': {
            'publish': {}
        },
        'workfile_path': path_str(workfile_path)
    }

    # Run the publish task
    return publish.main(config)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(message)s',
        stream=sys.stdout
    )
    sys.exit(cli())
