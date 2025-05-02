from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    fix_path,
    get_user_name,
    to_wsl_path,
    to_windows_path,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config import BlockRange
from tumblehead.apps.houdini import Hython

api = default_client()

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    logging.error(msg)
    return 1

def _fix_frame_pattern(frame_path, frame_pattern):
    name, _, ext = frame_path.name.split('.')
    return (
        frame_path.parent /
        f'{name}.{frame_pattern}.{ext}'
    )

def _get_frame_path(framestack_path, frame_index):
    frame_name = f'{frame_index:04}'
    return (
        framestack_path.parent /
        framestack_path.name.replace('*', frame_name)
    )

SCRIPT_PATH = Path(__file__).parent / 'slapcomp_houdini.py'

def main(
    render_range,
    input_paths,
    receipt_path,
    output_path
    ):
    _headline('Running slapcomp')

    # Receipt files
    receipt_paths = [
        _get_frame_path(receipt_path, frame_index)
        for frame_index in render_range
    ]

    # Find missing receipt files
    missing_receipt_paths = [
        output_frame_path
        for output_frame_path in receipt_paths
        if not to_wsl_path(output_frame_path).exists()
    ]

    # Check if all receipts already exist
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0

    # Get hython ready
    hython = Hython('20.5.550')

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Store slapcomp config
        slapcomp_config_path = temp_path / 'slapcomp.json'
        store_json(slapcomp_config_path, dict(
            first_frame = render_range.first_frame,
            last_frame = render_range.last_frame,
            input_paths = {
                layer_name: {
                    aov_name: path_str(input_path)
                    for aov_name, input_path in aovs.items()
                }
                for layer_name, aovs in input_paths.items()
            },
            receipt_path = path_str(receipt_path),
            output_path = path_str(output_path)
        ))
    
        # Run script in hython
        hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(slapcomp_config_path))
            ],
            env = dict(
                TH_USER = get_user_name(),
                TH_CONFIG_PATH = path_str(to_windows_path(api.CONFIG_PATH)),
                TH_PROJECT_PATH = path_str(to_windows_path(api.PROJECT_PATH)),
                TH_PIPELINE_PATH = path_str(to_windows_path(api.PIPELINE_PATH)),
                HOUDINI_PACKAGE_DIR = ';'.join([
                    path_str(to_windows_path(api.storage.resolve('pipeline:/houdini'))),
                    path_str(to_windows_path(api.storage.resolve('project:/_pipeline/houdini')))
                ])
            )
        )

    # Check if outputs were generated
    for frame_index in render_range:
        frame_path = _get_frame_path(output_path, frame_index)
        if to_wsl_path(frame_path).exists(): continue
        return _error(f'Output frame not found: {frame_path}')

    # Create output receipts
    _headline('Creating output receipts')
    for frame_index in render_range:
        current_receipt_path = _get_frame_path(receipt_path, frame_index)
        current_output_path = _get_frame_path(output_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(to_wsl_path(current_receipt_path), dict(
            slapcomp = path_str(current_output_path)
        ))

    # Done
    print('Success')
    return 0

"""
config = {
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
    'receipt_path': 'path/to/receipt.####.json',
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

    if 'input_paths' not in config: return False
    if not isinstance(config['input_paths'], dict): return False
    for layer_name, layer in config['input_paths'].items():
        if not isinstance(layer_name, str): return False
        if not _is_valid_layer(layer): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'output_path' not in config: return False
    if not isinstance(config['output_path'], str): return False
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

    # Get the input paths
    input_paths = {
        layer_name: {
            aov_name: _fix_frame_pattern(Path(aov_path), '*')
            for aov_name, aov_path in aovs.items()
        }
        for layer_name, aovs in config['input_paths'].items()
    }

    # Get the receipt path
    receipt_path = _fix_frame_pattern(Path(config['receipt_path']), '*')

    # Get the output path
    output_path = _fix_frame_pattern(Path(config['output_path']), '*')
    
    # Run main
    return main(
        render_range,
        input_paths,
        receipt_path,
        output_path
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())