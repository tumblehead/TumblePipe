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
from tumblehead.config.timeline import BlockRange, get_fps
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini import util
from tumblehead.apps.deadline import log_progress

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
    layer_names: list[str],
    output_paths: dict[str, Path],
    entity_json: dict
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

    # Set the playback range and FPS
    util.set_block_range(render_range)
    entity_uri = Uri.parse_unsafe(entity_json['uri']) if entity_json else None
    fps = get_fps(entity_uri)
    if fps is not None:
        util.set_fps(fps)

    # HACK: Make sure the graph is cooked
    render_node.parm('f1').set(render_range.first_frame)
    render_node.parm('f2').set(render_range.first_frame)
    render_node.parm('execute').pressButton()
    # HACK: Make sure the graph is cooked

    # Get variant names
    entity_uri = Uri.parse_unsafe(entity_json['uri'])
    properties = api.config.get_properties(entity_uri)
    variant_names = properties.get('variants', [])

    # Open a temporary directory
    root_temp_path = to_windows_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Render each layer separately
        all_layer_aov_paths = {}  # {frame_index: {layer_name: {aov_name: (temp_path, output_path)}}}

        for layer_name in layer_names:
            _headline(f'Rendering layer: {layer_name}')

            # Set the dive node output port to select the variant
            layer_index = variant_names.index(layer_name)
            render_node.parm('port1').set(layer_index + 1)

            # Set the output path for this layer
            temp_layer_path = temp_path / layer_name
            temp_layer_path.mkdir(parents=True, exist_ok=True)
            temp_frames_path = temp_layer_path / f'composite.$F4.exr'
            render_node.parm('copoutput').set(path_str(temp_frames_path))

            # Render the frames for this layer
            print(f'  Rendering frames for layer: {layer_name}')
            for frame_index in log_progress(render_range):

                # Skip if already rendered
                if frame_index not in frames: continue
                print(f'  Rendering frame {frame_index} for layer {layer_name}')

                # Render the frame
                render_node.parm('f1').set(frame_index)
                render_node.parm('f2').set(frame_index)
                render_node.parm('execute').pressButton()

            # Store RGBA beauty frames for this layer
            print(f'  Processing rendered frames for layer: {layer_name}')
            for frame_index in render_range:

                # Skip if already rendered
                if frame_index not in frames: continue
                print(f'  Processing frame {frame_index} for layer {layer_name}')

                # Get the RGBA frame path
                rgba_frame_path = _get_frame_path(temp_frames_path, frame_index)
                if not rgba_frame_path.exists():
                    return _error(f'RGBA frame not found: {rgba_frame_path}')

                # Store the paths for this layer
                if frame_index not in all_layer_aov_paths:
                    all_layer_aov_paths[frame_index] = {}
                if layer_name not in all_layer_aov_paths[frame_index]:
                    all_layer_aov_paths[frame_index][layer_name] = {}

                # Get the output path for this layer's beauty (RGBA)
                output_beauty_path = output_paths.get(layer_name)
                if output_beauty_path is None:
                    return _error(f'No output path for layer: {layer_name}')

                output_beauty_path = _fix_frames_pattern(output_beauty_path)
                all_layer_aov_paths[frame_index][layer_name] = (
                    rgba_frame_path,
                    _get_frame_path(output_beauty_path, frame_index)
                )

        # Copy to the output path
        _headline('Copying files to network')
        for frame_index, layer_paths in all_layer_aov_paths.items():
            for layer_name, (temp_path, output_path) in layer_paths.items():
                print(f'Copying file: {output_path}')
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(temp_path, output_path)

        # Create the output receipts
        _headline('Creating output receipts')
        for frame_index, layer_paths in all_layer_aov_paths.items():
            current_receipt_path = _get_frame_path(receipt_path, frame_index)
            print(f'Creating receipt: {current_receipt_path}')

            receipt_data = {
                layer_name: path_str(output_path)
                for layer_name, (_, output_path) in layer_paths.items()
            }
            store_json(current_receipt_path, receipt_data)

    # Done
    _headline('Done')
    return 0

"""
config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 2, 3, 4, 5],
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'node_path': '/path/to/node',
    'layer_names': ['main', 'mirror'],
    'output_paths': {
        'main': 'path/to/main/beauty.####.exr',
        'mirror': 'path/to/mirror/beauty.####.exr'
    }
}
"""

def _is_valid_config(config):

    def _is_valid_output_paths(output_paths):
        if not isinstance(output_paths, dict): return False
        for layer_name, output_path in output_paths.items():
            if not isinstance(layer_name, str): return False
            if not isinstance(output_path, str): return False
        return True

    if 'entity' not in config: return False
    if not isinstance(config['entity'], dict): return False
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
    if 'layer_names' not in config: return False
    if not isinstance(config['layer_names'], list): return False
    if len(config['layer_names']) == 0: return False
    for layer_name in config['layer_names']:
        if not isinstance(layer_name, str): return False
    if 'output_paths' not in config: return False
    if not _is_valid_output_paths(config['output_paths']): return False
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

    # Get the layer names
    layer_names = config['layer_names']

    # Get the output paths
    output_paths = {
        output_key: Path(output_path)
        for output_key, output_path in config['output_paths'].items()
    }

    # Get the entity
    entity_json = config['entity']

    # Run the main function
    return main(
        render_range,
        frames,
        receipt_path,
        input_path,
        node_path,
        layer_names,
        output_paths,
        entity_json
    )

if __name__ == '__main__':
    hou.exit(cli())