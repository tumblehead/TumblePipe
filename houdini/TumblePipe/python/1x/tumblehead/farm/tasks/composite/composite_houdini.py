from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import json
import os

import hou

from tumblehead.api import (
    path_str,
    to_windows_path,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config import BlockRange
from tumblehead.pipe.houdini import util
from tumblehead.apps.deadline import log_progress
from tumblehead.apps import exr

api = default_client()

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _fix_frames_pattern(frames_path):
    if '$F4' in frames_path.name: return frames_path
    name, _, ext = frames_path.name.rsplit('.', 2)
    return frames_path.parent / f'{name}.$F4.{ext}'

def _get_frame_path(frames_path, frame_index):
    assert '$F4' in frames_path.name, 'Frame path does not contain $F4'
    frame_name = str(frame_index).zfill(4)
    return (
        frames_path.parent /
        frames_path.name.replace('$F4', frame_name)
    )

def main(
    render_range: BlockRange,
    frames: list[int],
    receipt_path: Path,
    input_path: Path,
    node_path: str,
    output_paths: dict[str, dict[str, Path]]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Open the input file
    if not input_path.exists():
        return _error(f'Input not found: {input_path}')
    hou.hipFile.load(path_str(input_path))

    # Find the render node
    render_node = hou.node(node_path)
    if render_node is None:
        return _error(f'Node not found: {node_path}')

    # Set the playback range
    util.set_block_range(render_range)

    # Open a temporary directory
    root_temp_path = to_windows_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Set the output path
        temp_frames_path = temp_path / f'composite.$F4.exr'
        render_node.parm('copoutput').set(path_str(temp_frames_path))

        # HACK: Make sure the graph is cooked
        render_node.parm('f1').set(render_range.first_frame)
        render_node.parm('f2').set(render_range.first_frame)
        render_node.parm('execute').pressButton()
        # HACK: Make sure the graph is cooked

        # Render the frames
        _headline('Rendering')
        for frame_index in log_progress(render_range):

            # Skip if already rendered
            if frame_index not in frames: continue
            print(f'Rendering frame: {frame_index}')

            # Render the frame
            render_node.parm('f1').set(frame_index)
            render_node.parm('f2').set(frame_index)
            render_node.parm('execute').pressButton()
        
        # Split the frames into AOVss
        _headline('Splitting AOVs')
        temp_output_paths = dict()
        for frame_index in render_range:

            # Skip if already rendered
            if frame_index not in frames: continue
            print(f'Splitting frame: {frame_index}')

            # Split the frame
            current_frame_path = _get_frame_path(temp_frames_path, frame_index)
            split_frame_paths = exr.split_subimages(current_frame_path, temp_path)
            print(split_frame_paths)

            # Check splitter output
            if split_frame_paths is None:
                return _error(f'Failed to split AOVs: {current_frame_path}')
        
            # Store the paths
            aov_paths = dict()
            for aov_name, temp_aov_path in split_frame_paths.items():
                output_aov_path = output_paths.get(aov_name)
                if output_aov_path is None: continue
                output_aov_path = _fix_frames_pattern(output_aov_path)
                aov_paths[aov_name] = (
                    temp_aov_path,
                    _get_frame_path(output_aov_path, frame_index)
                )
            temp_output_paths[frame_index] = aov_paths
        
        # DWAB compress and copy to the output path
        _headline('Copying files to network')
        for aov_paths in temp_output_paths.values():
            for temp_aov_path, output_aov_path in aov_paths.values():
                print(f'Copying file: {output_aov_path}')
                output_aov_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(temp_aov_path, output_aov_path)
        
        # Create the output receipts
        _headline('Creating output receipts')
        for frame_index, aov_paths in temp_output_paths.items():
            current_receipt_path = _get_frame_path(receipt_path, frame_index)
            print(f'Creating receipt: {current_receipt_path}')
            store_json(current_receipt_path, {
                aov_name: path_str(output_aov_path)
                for aov_name, (_, output_aov_path) in aov_paths.items()
            })

    # Done
    _headline('Done')
    return 0

"""
config = {
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 2, 3, 4, 5],
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'node_path': '/path/to/node',
    'output_paths': {
        'diffuse': 'path/to/diffuse.####.exr',
        'depth': 'path/to/depth.####.exr'
    }
}
"""

def _is_valid_config(config):

    def _is_valid_layer(layer):
        if not isinstance(layer, dict): return False
        for aov_name, aov_path in layer.items():
            if not isinstance(aov_name, str): return False
            if not isinstance(aov_path, str): return False
        return True

    if 'first_frame' not in config: return False
    if not isinstance(config['first_frame'], int): return False
    if 'last_frame' not in config: return False
    if not isinstance(config['last_frame'], int): return False
    if 'frames' not in config: return False
    if not isinstance(config['frames'], list): return False
    for frame in config['frames']:
        if not isinstance(frame, int): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'node_path' not in config: return False
    if not isinstance(config['node_path'], str): return False
    if 'output_paths' not in config: return False
    if not _is_valid_layer(config['output_paths']): return False
    return True

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script_path', type=str)
    parser.add_argument('config_path', type=str)
    args = parser.parse_args()

    # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')
    
    # Print config
    _headline('Config')
    print(json.dumps(config, indent=4))

    # Get the render range
    render_range = BlockRange(
        config['first_frame'],
        config['last_frame']
    )
    frames = config['frames']

    # Get the receipt path
    receipt_path = _fix_frames_pattern(Path(config['receipt_path']))

    # Get the input path
    input_path = Path(config['input_path'])

    # Get the node path
    node_path = config['node_path']

    # Get the output paths
    output_paths = {
        aov_name: Path(aov_path)
        for aov_name, aov_path in config['output_paths'].items()
    }
    
    # Run the main function
    return main(
        render_range,
        frames,
        receipt_path,
        input_path,
        node_path,
        output_paths
    )

if __name__ == '__main__':
    hou.exit(cli())