from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import get_user_name, path_str, to_windows_path, default_client
from tumblehead.config import BlockRange
from tumblehead.apps.houdini import Hython
from tumblehead.pipe.paths import next_shot_export_path

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

PUBLISH_SCRIPT_PATH = Path(__file__).parent / 'publish_houdini.py'

def main(
    sequence_name: str,
    shot_name: str,
    department_name: str,
    render_range: str
    ) -> int:

    # Output files
    output_path = next_shot_export_path(
        sequence_name,
        shot_name,
        department_name
    )
    output_file_path = output_path / 'context.json'

    # Check if outputs already exist
    if output_file_path.exists():
        print(f'Output already exist at {output_file_path}')
        return 0

    # Get hython ready
    hython = Hython('20.5.410')
    
    # Run script in hython
    hython.run(
        to_windows_path(PUBLISH_SCRIPT_PATH),
        [
            sequence_name,
            shot_name,
            department_name,
            str(render_range.first_frame),
            str(render_range.last_frame)
        ],
        env = dict(
            TH_USER = get_user_name(),
            TH_PROJECT_PATH = path_str(to_windows_path(api.PROJECT_PATH)),
            TH_PIPELINE_PATH = path_str(to_windows_path(api.PIPELINE_PATH)),
            TH_CONFIG_PATH = path_str(to_windows_path(api.CONFIG_PATH)),
            HOUDINI_PACKAGE_DIR = ';'.join([
                path_str(to_windows_path(api.storage.resolve('pipeline:/houdini'))),
                path_str(to_windows_path(api.storage.resolve('project:/_pipeline/houdini')))
            ])
        )
    )

    # Check if outputs were generated
    if not output_file_path.exists():
        return _error(f'Output not found: {output_file_path}')

    # Done
    print('Success')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('sequence_name', type=str)
    parser.add_argument('shot_name', type=str)
    parser.add_argument('department_name', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()
    
    # Check sequence name
    sequence_names = api.config.list_sequence_names()
    sequence_name = args.sequence_name
    if sequence_name not in sequence_names:
        return _error(f'Invalid sequence name: {sequence_name}')

    # Check shot name
    shot_names = api.config.list_shot_names(sequence_name)
    shot_name = args.shot_name
    if shot_name not in shot_names:
        return _error(f'Invalid shot name: {shot_name}')
    
    # Check department name
    department_names = api.config.list_shot_department_names()
    department_name = args.department_name
    if department_name not in department_names:
        return _error(f'Invalid department name: {department_name}')

    # Check render range
    frame_range = api.config.get_frame_range(sequence_name, shot_name)
    render_range = BlockRange(args.first_frame, args.last_frame)
    if render_range not in frame_range:
        return _error(f'Invalid render range: {render_range} not in {frame_range}')

    # Run main
    return main(
        sequence_name,
        shot_name,
        department_name,
        render_range
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())