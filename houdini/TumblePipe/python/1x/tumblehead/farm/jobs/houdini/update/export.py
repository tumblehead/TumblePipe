from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import path_str, fix_path, to_windows_path, default_client
from tumblehead.config import BlockRange
from tumblehead.apps.houdini import Houdini

api = default_client()

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    logging.error(msg)
    return 1

EXPORT_SCRIPT_PATH = Path(__file__).parent / 'export_houdini.py'

def main(
    sequence_name: str,
    shot_name: str,
    render_department_name: str,
    render_layer_name: str,
    render_range: BlockRange
    ) -> int:

    # Output files
    output_path = Path.cwd() / 'data'
    output_stage_path = output_path / 'stage.usd'

    # Check if output already exist
    if not output_stage_path.exists():
        print('Output already exists')
        return 0

    # Get houdini ready
    houdini_version = '21.0.480'
    houdini = Houdini(houdini_version)

    # Export
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_stage_path = temp_path / output_stage_path.name

        # Export the USD stage
        _headline('Exporting USD stage')
        houdini.run(
            to_windows_path(EXPORT_SCRIPT_PATH),
            [
                sequence_name,
                shot_name,
                render_department_name,
                render_layer_name,
                str(render_range.first_frame),
                str(render_range.last_frame),
                path_str(temp_stage_path)
            ]
        )

        # Check if stage was exported
        if not temp_stage_path.exists():
            return _error(f'Failed to export USD stage: {temp_stage_path}')

        # Copy stage to network
        _headline('Copying files to output')
        shutil.copytree(
            temp_path,
            output_path,
            dirs_exist_ok = True
        )

    # Done
    print('Success')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('sequence_name', type=str)
    parser.add_argument('shot_name', type=str)
    parser.add_argument('render_department_name', type=str)
    parser.add_argument('render_layer_name', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()
    
    # Check sequence code
    sequence_names = api.config.list_sequence_names()
    sequence_name = args.sequence_name
    if sequence_name not in sequence_names:
        return _error(f'Invalid sequence code: {sequence_name}')

    # Check shot code
    shot_names = api.config.list_shot_names(sequence_name)
    shot_name = args.shot_name
    if shot_name not in shot_names:
        return _error(f'Invalid shot code: {shot_name}')
    
    # Check render department name
    render_department_name = args.render_department_name
    render_department_names = api.config.list_render_department_names()
    if render_department_name not in render_department_names:
        return _error(
            f'Invalid render department name: '
            f'{render_department_name}'
        )

    # Check render layer name
    render_layer_name = args.render_layer_name
    render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
    if render_layer_name not in render_layer_names:
        return _error(f'Invalid layer name: {render_layer_name}')
    
    # Check render range
    first_frame = args.first_frame
    last_frame = args.last_frame
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame)

    # Run main
    return main(
        sequence_name,
        shot_name,
        render_department_name,
        render_layer_name,
        render_range
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())