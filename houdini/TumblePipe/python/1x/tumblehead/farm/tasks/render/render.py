from tempfile import TemporaryDirectory
from typing import Optional
from pathlib import Path
import logging
import shutil
import json
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
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
from tumblehead.apps.houdini import Husk
from tumblehead.apps import exr

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

def _get_frame_path(frame_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return (
        frame_path.parent /
        frame_path.name.replace('*', frame_name)
    )

def main(
    render_range: BlockRange,
    input_path: Path,
    receipt_path: Path,
    slapcomp_path: Optional[Path],
    output_paths: dict[str, Path]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Output receipt paths
    receipt_paths = [
        _get_frame_path(receipt_path, frame_index)
        for frame_index in render_range
    ]

    # Find missing output receipts
    missing_receipt_paths = [
        output_receipt_path
        for output_receipt_path in receipt_paths
        if not to_wsl_path(output_receipt_path).exists()
    ]

    # Check if all receipts already exist
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0

    # Get husk ready
    husk = Husk()

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_frame_path = temp_path / 'render' / 'render.*.exr'

        # Render with husk and Karama XPU
        _headline('Rendering')
        temp_frame_path.parent.mkdir(parents=True, exist_ok=True)
        husk.run(
            to_windows_path(input_path),
            [
                '--make-output-path',
                '--no-mplay',
                '--core',
                '--list-license-checks',
                '--threads', '-1',
                '--engine', 'xpu',
                '--frame', str(render_range.first_frame),
                '--frame-count', str(len(render_range))
            ] + (
                [] if slapcomp_path is None else
                ['--slap-comp', to_windows_path(slapcomp_path)]
            ) + [
                '--output', path_str(to_windows_path(
                    _fix_frame_pattern(temp_frame_path, '$F4')
                ))
            ],
            env = dict(
                OCIO = path_str(to_windows_path(Path(os.environ['OCIO']))),
            )
        )

        # Check that the frames were generated
        for frame_index in render_range:
            current_frame_path = _get_frame_path(temp_frame_path, frame_index)
            if to_wsl_path(current_frame_path).exists(): continue
            return _error(f'Frame not generated: {current_frame_path}')

        # Split AOVs into separate stacks
        _headline('Splitting AOVs')
        framestack_aov_paths = dict()
        for frame_index in render_range:

            # Split the frame
            current_frame_path = _get_frame_path(temp_frame_path, frame_index)
            print(f'Splitting frame: {current_frame_path}')
            split_frame_paths = exr.split_subimages(
                current_frame_path, temp_path
            )

            # Check splitter output
            if split_frame_paths is None:
                return _error(f'Failed to split frame: {current_frame_path}')

            # Store the paths
            aov_paths = dict()
            for aov_name, temp_aov_path in split_frame_paths.items():
                output_aov_path = output_paths.get(aov_name)
                if output_aov_path is None: continue
                aov_paths[aov_name] = (
                    temp_aov_path,
                    _get_frame_path(output_aov_path, frame_index)
                )
            framestack_aov_paths[frame_index] = aov_paths

        # Copy frames to network
        _headline('Copying files to network')
        for aov_paths in framestack_aov_paths.values():
            for temp_aov_path, output_aov_path in aov_paths.values():
                print(f'Copying file: {output_aov_path}')
                output_aov_path = to_wsl_path(output_aov_path)
                output_aov_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(temp_aov_path, output_aov_path)
        
        # Create the output receipts
        _headline('Creating output receipts')
        for frame_index, aov_paths in framestack_aov_paths.items():
            current_receipt_path = _get_frame_path(receipt_path, frame_index)
            print(f'Creating receipt: {current_receipt_path}')
            store_json(to_wsl_path(current_receipt_path), {
                aov_name: path_str(output_aov_path)
                for aov_name, (_, output_aov_path) in aov_paths.items()
            })

    # Check if output receipts were generated
    for receipt_path in receipt_paths:
        if to_wsl_path(receipt_path).exists(): continue
        return _error(f'Output receipt not found: {receipt_path}')

    # Done
    print('Success')
    return 0

"""
config = {
    'receipt_path': 'path/to/receipt.####.json',
    'slapcomp_path': None | 'path/to/slapcomp.bgeo.sc',
    'input_path': 'path/to/input.usd',
    'output_paths': {
        'diffuse': 'path/to/diffuse.####.exr',
        'specular': 'path/to/specular.####.exr'
    }
}
"""

def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'slapcomp_path' not in config: return False
    if not (
        config['slapcomp_path'] == None or
        isinstance(config['slapcomp_path'], str)): return False
    if 'output_paths' not in config: return False
    if not isinstance(config['output_paths'], dict): return False
    for key, value in config['output_paths'].items():
        if not isinstance(key, str): return False
        if not isinstance(value, str): return False
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
    
    # Print config
    _headline('Config')
    print(json.dumps(config, indent=4))

    # Check render range
    first_frame = args.first_frame
    last_frame = args.last_frame
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame)

    # Get the receipt path
    receipt_path = _fix_frame_pattern(Path(config['receipt_path']), '*')

    # Get the slapcomp path
    slapcomp_path = config.get('slapcomp_path')
    if slapcomp_path is not None:
        slapcomp_path = Path(slapcomp_path)
        if not slapcomp_path.exists():
            return _error(f'Slapcomp path not found: {slapcomp_path}')

    # Check the input path
    input_path = Path(config['input_path'])
    if not input_path.exists():
        return _error(f'Input path not found: {input_path}')

    # Get the output path
    output_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['output_paths'].items()
    }

    # Run main
    return main(
        render_range,
        input_path,
        receipt_path,
        slapcomp_path,
        output_paths
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())