from tempfile import TemporaryDirectory
from typing import Optional
from pathlib import Path
import logging
import tarfile
import shutil
import math
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
from tumblehead.apps.houdini import Husk, ITileStitch
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
    tile_count: int,
    render_range: BlockRange,
    archive_path: Path,
    relative_input_path: Path,
    receipt_path: Path,
    output_paths: dict[str, Path]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Get apps ready
    husk = Husk()
    itilestitch = ITileStitch()

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_frame_path = temp_path / 'render' / 'render.*.exr'
        temp_archive_path = temp_path / 'archive.tar.gz'
        temp_input_path = temp_path / relative_input_path

        # Copy the input archive to the temp path
        _headline('Unpack workspace archive')
        print(f'Transfering archive: {temp_archive_path}')
        shutil.copyfile(to_wsl_path(archive_path), temp_archive_path)

        # Unpack the input archive
        with tarfile.open(temp_archive_path, 'r:gz') as archive_file:
            for archive_member in archive_file:
                archive_file.extract(archive_member, temp_path)
                relative_file_path = Path(archive_member.name)
                file_path = temp_path / relative_file_path
                print(f'Unpacked: {file_path}')
        
        # Check that the input file exists
        if not temp_input_path.exists():
            return _error(f'Input file not found: {temp_input_path}')

        # Render with husk and Karama XPU
        _headline('Rendering tiles')
        x_tiles = y_tiles = int(math.sqrt(tile_count))
        temp_frame_path.parent.mkdir(parents=True, exist_ok=True)

        # Build base husk arguments
        base_args = [
            '--make-output-path',
            '--no-mplay',
            '--check-licenses', 'Karma Renderer',
            '--threads', '-1',
            '--engine', 'xpu',
            '--frame', str(render_range.first_frame),
            '--frame-count', str(len(render_range))
        ]

        # Render tiles
        if tile_count == 1:
            # Single tile - render directly without tile flags
            husk.run(
                to_windows_path(temp_input_path),
                base_args + [
                    '--output', path_str(to_windows_path(
                        _fix_frame_pattern(temp_frame_path, '$F4')
                    ))
                ],
                env = dict(
                    OCIO = path_str(to_windows_path(Path(os.environ['OCIO']))),
                )
            )
        else:
            # Multiple tiles - render with tile flags
            for tile_index in range(tile_count):
                husk.run(
                    to_windows_path(temp_input_path),
                    base_args + [
                        '--tile-count', str(x_tiles), str(y_tiles),
                        '--tile-index', str(tile_index),
                        '--tile-suffix', '.%04d',
                        '--output', path_str(to_windows_path(
                            _fix_frame_pattern(temp_frame_path, '$F4')
                        ))
                    ],
                    env = dict(
                        OCIO = path_str(to_windows_path(Path(os.environ['OCIO']))),
                    )
                )

            # Stitch the tiles together to create the frames
            _headline('Stitching tiles')
            for frame_index in render_range:
                frame_path = _get_frame_path(temp_frame_path, frame_index)
                tile_paths = [
                    temp_frame_path.parent / f'render.{frame_index:04d}.{tile_index:04d}.exr'
                    for tile_index in range(tile_count)
                ]
                print(f'Stitching tiles for frame {frame_index}: {frame_path}')
                itilestitch.run(
                    [ path_str(to_windows_path(frame_path)) ] +
                    [ path_str(to_windows_path(tile_path)) for tile_path in tile_paths ],
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

    # Done
    print('Success')
    return 0

"""
config = {
    'tile_count': 1,
    'receipt_path': 'path/to/receipt.####.json',
    'archive_path': 'path/to/archive.tar.gz',
    'input_path': 'path/to/input.usd',
    'output_paths': {
        'diffuse': 'path/to/diffuse.####.exr',
        'specular': 'path/to/specular.####.exr'
    }
}
"""

def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'tile_count' not in config: return False
    if not isinstance(config['tile_count'], int): return False
    if 'receipt_path' not in config: return False
    if not isinstance(config['receipt_path'], str): return False
    if 'archive_path' not in config: return False
    if not isinstance(config['archive_path'], str): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
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

    # Check tile count
    tile_count = config['tile_count']
    if tile_count not in (1, 4, 16, 64, 256):
        return _error(f'Invalid tile count: {tile_count}')

    # Check render range
    first_frame = args.first_frame
    last_frame = args.last_frame
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame)

    # Get the receipt path
    receipt_path = _fix_frame_pattern(Path(config['receipt_path']), '*')

    # Get the archive path
    archive_path = Path.cwd() / Path(config['archive_path'])
    if not archive_path.exists():
        return _error(f'Archive path not found: {archive_path}')

    # Check the input path
    relative_input_path = Path(config['input_path'])

    # Get the output path
    output_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['output_paths'].items()
    }

    # Run main
    return main(
        tile_count,
        render_range,
        archive_path,
        relative_input_path,
        receipt_path,
        output_paths
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())