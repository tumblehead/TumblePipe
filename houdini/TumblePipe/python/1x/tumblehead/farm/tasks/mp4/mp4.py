from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil
import time
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    fix_path,
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import BlockRange, get_fps
from tumblehead.apps import exr, mp4
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

def _out_of_date(path: Path) -> bool:
    if not path.exists(): return True
    return path.stat().st_mtime < time.time()

def main(
    render_range: BlockRange,
    input_path: Path,
    output_paths: list[Path]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Print environment variables for debugging
    print_env()

    # Find output paths that are not generated
    missing_output_paths = [
        output_path
        for output_path in output_paths
        if _out_of_date(to_wsl_path(output_path))
    ]
    if len(missing_output_paths) == 0:
        print('Output files already exist')
        return 0
    
    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Convert EXRs to JPGs
        temp_input_path = temp_path / 'temp.*.jpg'
        temp_input_path.parent.mkdir(parents=True, exist_ok=True)
        for frame_index in render_range:
            input_frame_path = _get_frame_path(input_path, frame_index)
            temp_frame_path = _get_frame_path(temp_input_path, frame_index)
            exr.to_jpeg(
                to_wsl_path(input_frame_path),
                temp_frame_path
            )
    
        # Encode to mp4
        temp_output_path = temp_path / 'temp.mp4'
        mp4.from_jpg(
            to_wsl_path(temp_input_path),
            render_range,
            get_fps(),
            temp_output_path
        )

        # Check that the temporary output exists
        if not temp_output_path.exists():
            return _error(f'Temp output not generated: {temp_output_path}')
        
        # Copy to output paths
        for output_path in missing_output_paths:
            output_path = to_wsl_path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(temp_output_path, output_path)

    # Check that all the missing outputs exists
    for output_path in missing_output_paths:
        if to_wsl_path(output_path).exists(): continue
        return _error(f'Output not generated: {output_path}')

    # Done
    print('Success')
    return 0

"""
config = {
    'first_frame': 1,
    'last_frame': 100,
    'step_size': 1,
    'input_path': 'path/to/input.####.exr',
    'output_paths': [
        'path/to/output1.mp4',
        'path/to/output2.mp4'
    ]
}
"""

def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'first_frame' not in config: return False
    if not isinstance(config['first_frame'], int): return False
    if 'last_frame' not in config: return False
    if not isinstance(config['last_frame'], int): return False
    if 'step_size' not in config: return False
    if not isinstance(config['step_size'], int): return False
    if 'input_path' not in config: return False
    if not isinstance(config['input_path'], str): return False
    if 'output_paths' not in config: return False
    if not isinstance(config['output_paths'], list): return False
    for output_path in config['output_paths']:
        if not isinstance(output_path, str): return False
    return True

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()

    # Load config data
    config_path = Path.cwd() / Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')

    # Get the render range
    first_frame = config['first_frame']
    last_frame = config['last_frame']
    step_size = config['step_size']
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame, step_size)
    
    # Check the input path
    input_path = _fix_frame_pattern(Path(config['input_path']), '*')
    for frame_index in render_range:
        frame_path = _get_frame_path(input_path, frame_index)
        if to_wsl_path(frame_path).exists(): continue
        return _error(f'Input frame not found: {frame_path}')
    
    # Get the output paths
    output_paths = list(map(Path, config['output_paths']))
    
    # Run main
    return main(
        render_range,
        input_path,
        output_paths
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())