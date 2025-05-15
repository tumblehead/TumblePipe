from tempfile import TemporaryDirectory
from pathlib import Path
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

def _should_denoise_aov(aov_name):
    name = aov_name.lower()
    if name == 'beauty': return True
    if name.startswith('beauty_'): return True
    return False

def _create_file_node(parent, name):
    file_node = parent.createNode('file', name)
    file_node.parm('videoframestart').deleteAllKeyframes()
    file_node.parm('videoframestart').set(1001)
    file_node.parm('filename').set('')
    file_node.parm('colorspace').set(1)
    return file_node

def _create_denoise_node(parent, name):
    denoise_node = parent.createNode('denoiseai', name)
    denoise_node.parm('denoiser').set('oidn')
    return denoise_node

def _create_render_node(parent, name, target):
    render_node = parent.createNode('rop_image', name)
    render_node.parm('trange').set(1)
    render_node.parm('f1').deleteAllKeyframes()
    render_node.parm('f2').deleteAllKeyframes()
    render_node.parm('f1').set(1001)
    render_node.parm('f2').set(1001)
    render_node.parm('coppath').set(render_node.relativePathTo(target))
    render_node.parm('copoutput').set('')
    return render_node

def _set_file_node(file_node, file_path, aov_name):
    file_node.parm('filename').set(path_str(file_path))
    file_node.parm('aovs').set(0)
    file_node.parm('addaovs').pressButton()

def main(
    render_range: BlockRange,
    frames: list[int],
    receipt_path: Path,
    input_paths: dict[str, dict[str, Path]],
    output_paths: dict[str, dict[str, Path]]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Check if the enough aovs are available
    if not 'normal' in input_paths:
        return _error('Normal AOV not found')
    if not 'albedo' in input_paths:
        return _error('Albedo AOV not found')
    
    # Find the AOVs to denoise
    target_aov_paths = {
        aov_name: aov_path
        for aov_name, aov_path in input_paths.items()
        if _should_denoise_aov(aov_name)
    }

    # Check that target AOVs are available
    for aov_name in target_aov_paths.keys():
        if aov_name in input_paths: continue
        return _error(f'No output path given for {aov_name}')

    # Set the playback range
    util.set_block_range(render_range)

    # Create denoising network
    scene = hou.node('/stage')
    cops_node = scene.createNode('copnet', 'cops')
    source_file_node = _create_file_node(cops_node, 'source')
    normal_file_node = _create_file_node(cops_node, 'normal')
    albedo_file_node = _create_file_node(cops_node, 'albedo')
    denoise_node = _create_denoise_node(cops_node, 'denoise')
    render_node = _create_render_node(cops_node, 'render', denoise_node)
    denoise_node.setInput(0, source_file_node, 0)
    denoise_node.setInput(1, normal_file_node)
    denoise_node.setInput(2, albedo_file_node)

    # Set the util aov paths
    normal_aov_path = _fix_frames_pattern(input_paths['normal'])
    albedo_aov_path = _fix_frames_pattern(input_paths['albedo'])
    _set_file_node(normal_file_node, normal_aov_path, 'normal')
    _set_file_node(albedo_file_node, albedo_aov_path, 'albedo')

    # Denoise the input frames
    _headline('Denoising')

    output_frame_paths = {
        frame_index: dict()
        for frame_index in render_range
    }

    root_temp_path = to_windows_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        for aov_name, aov_path in log_progress(target_aov_paths.items()):
            print(f'Denoising AOV: {aov_name}')

            # Set the output path
            temp_frames_path = temp_path / aov_name / f'{aov_name}.$F4.exr'
            output_frames_path = _fix_frames_pattern(output_paths[aov_name])

            # Set the frame on the playback range
            hou.setFrame(render_range.first_frame)

            # Set the input and output parameters
            source_file_path = _fix_frames_pattern(aov_path)
            _set_file_node(source_file_node, source_file_path, aov_name)
            render_node.parm('copoutput').set(path_str(temp_frames_path))
            render_node.parm('aov1').set(aov_name)

            # Denoise the input frames
            temp_frames_path.parent.mkdir(parents = True, exist_ok = True)
            for frame_index in render_range:

                # Skip if already rendered
                if frame_index not in frames: continue

                # Render the frame
                output_frame_path = _get_frame_path(
                    output_frames_path, frame_index
                )
                output_frame_paths[frame_index][aov_name] = output_frame_path
                render_node.parm('f1').set(frame_index)
                render_node.parm('f2').set(frame_index)
                render_node.parm('execute').pressButton()
            
            # DWAB compress and copy to the output path
            output_frames_path.parent.mkdir(parents = True, exist_ok = True)
            for frame_index in render_range:

                # Skip if already rendered
                if frame_index not in frames: continue

                # Compress the frame
                temp_frame_path = _get_frame_path(
                    temp_frames_path, frame_index
                )
                output_frame_path = _get_frame_path(
                    output_frames_path, frame_index
                )
                exr.dwab_encode(temp_frame_path, output_frame_path)
            
            # Check that all frames were rendered
            for frame_index in render_range:
                output_frame_path = _get_frame_path(
                    output_frames_path, frame_index
                )
                if output_frame_path.exists(): continue
                return _error(f'Frame not rendered: {output_frame_path}')
        
    # Create the frame receipts
    _headline('Creating frame receipts')
    receipt_path = _fix_frames_pattern(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    for frame_index, aov_paths in output_frame_paths.items():
        if len(aov_paths) == 0: continue
        current_receipt_path = _get_frame_path(receipt_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(current_receipt_path, {
            aov_name: path_str(output_aov_path)
            for aov_name, output_aov_path in aov_paths.items()
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
    if 'input_paths' not in config: return False
    if not _is_valid_layer(config['input_paths']): return False
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
    receipt_path = Path(config['receipt_path'])

    # Get the input paths
    input_paths = {
        aov_name: Path(aov_path)
        for aov_name, aov_path in config['input_paths'].items()
    }

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
        input_paths,
        output_paths
    )

if __name__ == '__main__':
    hou.exit(cli())