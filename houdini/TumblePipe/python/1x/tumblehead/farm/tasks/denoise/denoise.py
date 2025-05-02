from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys

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
from tumblehead.config import BlockRange
from tumblehead.apps.houdini import Hython

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

SCRIPT_PATH = Path(__file__).parent / 'denoise_houdini.py'
def main(
    render_range: BlockRange,
    receipt_path: Path,
    input_paths: dict[str, Path],
    output_paths: dict[str, Path]
    ) -> int:

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
    hython = Hython('20.5.550')

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Store denoise config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            first_frame = render_range.first_frame,
            last_frame = render_range.last_frame,
            frames = missing_frames,
            receipt_path = path_str(receipt_path),
            input_paths = {
                aov_name: path_str(input_path)
                for aov_name, input_path in input_paths.items()
            },
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
                    path_str(to_windows_path(api.storage.resolve('pipeline:/houdini'))),
                    path_str(to_windows_path(api.storage.resolve('project:/_pipeline/houdini')))
                ])
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
    'receipt_path': 'path/to/receipt.####.json',
    'input_paths': {
        'diffuse': 'path/to/diffuse.####.exr',
        'depth': 'path/to/depth.####.exr'
    },
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
    
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'input_paths' not in config: return False
    if not _is_valid_layer(config['input_paths']): return False
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

    # Get the input paths
    input_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['input_paths'].items()
    }

    # Get the output paths
    output_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['output_paths'].items()
    }

    # Run the main function
    return main(
        render_range,
        receipt_path,
        input_paths,
        output_paths
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())