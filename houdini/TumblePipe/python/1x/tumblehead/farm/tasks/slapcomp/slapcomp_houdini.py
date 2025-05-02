from pathlib import Path
import json

import hou

from tumblehead.api import default_client
from tumblehead.config import BlockRange
from tumblehead.util.io import load_json
from tumblehead.pipe.houdini.cops import slapcomp
from tumblehead.pipe.paths import AOV

api = default_client()

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _fix_frame_pattern(frame_path, expected_frame_pattern):
    frame_name, frame_pattern, frame_suffix = frame_path.name.rsplit('.', 2)
    if frame_pattern == expected_frame_pattern: return frame_path
    return frame_path.with_name(
        f'{frame_name}.'
        f'{expected_frame_pattern}.'
        f'{frame_suffix}'
    )

def main(
    render_range: BlockRange,
    input_paths: dict[str, dict[str, Path]],
    output_path: Path
    ):

    # Get the layer names
    layer_names = list(input_paths.keys())

    # Map to AOV objects
    layer_aovs = {
        layer_name: {
            aov_name: AOV(
                path = aov_path.parent,
                label = aov_name,
                name = aov_path.stem.rsplit('.', 1)[0],
                suffix = aov_path.suffix.lstrip('.')
            )
            for aov_name, aov_path in aov_paths.items()
        }
        for layer_name, aov_paths in input_paths.items()
    }

    # Create slapcomp node
    scene = hou.node('/stage')
    cops_node = scene.createNode('copnet', 'cops')
    slapcomp_node = slapcomp.create(cops_node, 'slapcomp')

    # Set shot and render
    _headline('Rendering')
    slapcomp_node._export(
        render_range,
        layer_names,
        layer_aovs,
        _fix_frame_pattern(output_path, '$F4')
    )

    # Done
    return 0

"""
config = {
    'first_frame': 1,
    'last_frame': 100,
    'input_paths': {
        'layer1': {
            'diffuse': 'path/to/diffuse.####.exr',
            'depth': 'path/to/depth.####.exr'
        },
        'layer2': {
            'diffuse': 'path/to/diffuse.####.exr',
            'depth': 'path/to/depth.####.exr'
        }
    },
    'output_path': 'path/to/output.####.jpg'
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
    if 'input_paths' not in config: return False
    if not isinstance(config['input_paths'], dict): return False
    for layer_name, layer in config['input_paths'].items():
        if not isinstance(layer_name, str): return False
        if not _is_valid_layer(layer): return False
    if 'output_path' not in config: return False
    if not isinstance(config['output_path'], str): return False
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

    # Get the input paths
    input_paths = {
        layer_name: {
            aov_name: Path(aov_path)
            for aov_name, aov_path in layer.items()
        }
        for layer_name, layer in config['input_paths'].items()
    }

    # Get the output path
    output_path = Path(config['output_path'])

    # Run main
    return main(
        render_range,
        input_paths,
        output_path
    )

if __name__ == '__main__':
    hou.exit(cli())