from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    get_user_name,
    path_str,
    fix_path,
    to_wsl_path,
    to_windows_path,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config.timeline import BlockRange
from tumblehead.util.uri import Uri
from tumblehead.apps.houdini import Hython
from tumblehead.farm.tasks.env import print_env

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

def _fix_frame_pattern(frame_path, frame_pattern):
    name, _, ext = frame_path.name.split('.')
    return (
        frame_path.parent /
        f'{name}.{frame_pattern}.{ext}'
    )

def _get_frame_path(frame_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return (
        frame_path.parent /
        frame_path.name.replace('*', frame_name)
    )

def _get_frame_index(frame_path):
    return int(frame_path.stem.rsplit('.', 1)[-1])

SCRIPT_PATH = Path(__file__).parent / 'composite_houdini.py'
def main(
    render_range: BlockRange,
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

    # Print environment variables for debugging
    print_env()

    # Output receipt paths
    receipt_paths = [
        _get_frame_path(receipt_path, frame_index)
        for frame_index in render_range
    ]

    # Find missing output receipts
    missing_receipt_paths = [
        output_receipt_path
        for output_receipt_path in receipt_paths
        if not output_receipt_path.exists()
    ]
    missing_frames = [
        _get_frame_index(missing_receipt_path)
        for missing_receipt_path in missing_receipt_paths
    ]

    # Check if all receipts already exist
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Store composite config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            entity = entity_json,
            first_frame = render_range.first_frame,
            last_frame = render_range.last_frame,
            frames = missing_frames,
            receipt_path = path_str(receipt_path),
            input_path = path_str(input_path),
            node_path = node_path,
            layer_names = layer_names,
            output_paths = {
                aov_name: path_str(output_path)
                for aov_name, output_path in output_paths.items()
            }
        ))

        # Run script in hython
        hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(config_path))
            ],
            env = dict(
                TH_USER = get_user_name(),
                TH_CONFIG_PATH = path_str(to_windows_path(api.CONFIG_PATH)),
                TH_PROJECT_PATH = path_str(to_windows_path(api.PROJECT_PATH)),
                TH_PIPELINE_PATH = path_str(to_windows_path(api.PIPELINE_PATH)),
                HOUDINI_PACKAGE_DIR = ';'.join([
                    path_str(to_windows_path(api.storage.resolve(Uri.parse_unsafe('pipeline:/houdini')))),
                    path_str(to_windows_path(api.storage.resolve(Uri.parse_unsafe('project:/_pipeline/houdini'))))
                ]),
                OCIO = path_str(to_windows_path(Path(os.environ['OCIO'])))
            )
        )

    # Check if output receipts were generated
    for receipt_path in missing_receipt_paths:
        if to_wsl_path(receipt_path).exists(): continue
        return _error(f'Output receipt not found: {receipt_path}')

    # Done
    print('Success')
    return 0

"""
config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'node_path': 'path/to/node',
    'layer_names': ['main', 'mirror'],
    'output_paths': {
        'main': 'path/to/main.####.exr',
        'mirror': 'path/to/mirror.####.exr'
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

    if 'entity' not in config: return False
    if not isinstance(config['entity'], dict): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'node_path' not in config: return False
    if not isinstance(config['node_path'], str): return False
    if 'layer_names' not in config: return False
    if not isinstance(config['layer_names'], list): return False
    if 'output_paths' not in config: return False
    if not _is_valid_layer(config['output_paths']): return False
    return True

def cli():
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

    # Check render range
    first_frame = args.first_frame
    last_frame = args.last_frame
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame)

    # Get the receipt path
    receipt_path = _fix_frame_pattern(Path(config['receipt_path']), '*')

    # Get the input path
    input_path = Path(config['input_path'])

    # Get the node path
    node_path = config['node_path']

    # Get the layer names
    layer_names = config['layer_names']

    # Get the output paths
    output_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['output_paths'].items()
    }

    # Get the entity
    entity_json = config['entity']

    # Run the main function
    return main(
        render_range,
        receipt_path,
        input_path,
        node_path,
        layer_names,
        output_paths,
        entity_json
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())