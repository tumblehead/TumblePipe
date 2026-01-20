from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import shutil
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config.timeline import BlockRange
from tumblehead.apps import exr
from tumblehead.farm.tasks.env import print_env

api = default_client()


def _get_ocio_env():
    """Get OCIO environment dict for WSL subprocess calls."""
    return dict(OCIO=os.environ['OCIO'])

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

def _merge_rgba(
    beauty_path: Path,
    alpha_path: Path,
    output_path: Path
    ) -> int:
    """Merge RGB beauty with single-channel alpha using oiiotool --chappend"""
    oiiotool_cmd = [
        'oiiotool',
        path_str(to_wsl_path(beauty_path)),
        path_str(to_wsl_path(alpha_path)),
        '--chappend',
        '--chnames', 'R,G,B,A',
        '--attrib:type=string', 'oiio:ColorSpace', 'ACEScg',
        '-o', path_str(to_wsl_path(output_path))
    ]
    print(f'    Merging RGBA: beauty + alpha -> temp')
    return exr._run(oiiotool_cmd, env=_get_ocio_env())

def _composite_frame(
    frame_index: int,
    input_paths: dict[str, dict[str, Path]],
    output_path: Path
    ) -> int:
    """Composite a single frame using oiiotool"""

    # Check if output already exists
    output_frame_path = _get_frame_path(output_path, frame_index)
    if to_wsl_path(output_frame_path).exists():
        print(f'Frame {frame_index} already exists, skipping')
        return 0

    print(f'Processing frame {frame_index}')

    # Composite layers using oiiotool
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        layer_output_paths = []

        # Process each render layer
        for layer_index, (layer_name, layer_aovs) in enumerate(input_paths.items()):

            # Get beauty AOV path for this layer
            if 'beauty' not in layer_aovs:
                print(f'  Warning: No beauty AOV found for layer {layer_name}, skipping')
                continue

            beauty_path = _get_frame_path(layer_aovs['beauty'], frame_index)
            if not to_wsl_path(beauty_path).exists():
                print(f'  Warning: Beauty AOV not found at {beauty_path}, skipping')
                continue

            # Check if we need to merge alpha channel
            needs_alpha_merge = 'alpha' in layer_aovs
            if needs_alpha_merge:
                alpha_path = _get_frame_path(layer_aovs['alpha'], frame_index)
                if not to_wsl_path(alpha_path).exists():
                    print(f'  Warning: Alpha AOV specified but not found at {alpha_path}')
                    needs_alpha_merge = False

            # Find all LPE AOVs (beauty_*)
            lpe_aovs = {
                aov_name: aov_path
                for aov_name, aov_path in layer_aovs.items()
                if aov_name.lower().startswith('beauty_') and aov_name.lower() != 'beauty'
            }

            # Determine the RGB source (either LPE composite or beauty)
            if lpe_aovs:
                print(f'  Layer {layer_name}: Compositing {len(lpe_aovs)} LPE AOVs')

                # Build oiiotool command to add all LPE AOVs together
                oiiotool_cmd = ['oiiotool']

                for lpe_index, (lpe_name, lpe_path) in enumerate(lpe_aovs.items()):
                    lpe_frame_path = _get_frame_path(lpe_path, frame_index)
                    if not to_wsl_path(lpe_frame_path).exists():
                        print(f'    Warning: LPE AOV {lpe_name} not found, skipping')
                        continue

                    # Add this LPE to the command
                    oiiotool_cmd.append(path_str(to_wsl_path(lpe_frame_path)))

                    # Add previous result if not first LPE
                    if lpe_index > 0:
                        oiiotool_cmd.append('--add')

                # Save composited LPEs to temp file
                rgb_source_path = temp_path / f'layer_{layer_index}_lpe_composite.exr'
                oiiotool_cmd.extend([
                    '--attrib:type=string', 'oiio:ColorSpace', 'ACEScg',
                    '-o', path_str(to_wsl_path(rgb_source_path))
                ])

                result = exr._run(oiiotool_cmd, env=_get_ocio_env())
                if result != 0:
                    return _error(f'Failed to composite LPE AOVs for layer {layer_name}')
            else:
                # No LPE AOVs, use beauty directly as RGB source
                print(f'  Layer {layer_name}: Using beauty AOV directly')
                rgb_source_path = beauty_path

            # Merge alpha if needed
            if needs_alpha_merge:
                print(f'  Layer {layer_name}: Merging alpha channel')
                layer_rgba_path = temp_path / f'layer_{layer_index}_rgba.exr'
                result = _merge_rgba(rgb_source_path, alpha_path, layer_rgba_path)
                if result != 0:
                    return _error(f'Failed to merge RGBA for layer {layer_name}')
                layer_output_paths.append((layer_name, layer_rgba_path, beauty_path))
            else:
                # No alpha, add constant alpha=1.0 to make RGBA
                print(f'  Layer {layer_name}: Adding constant alpha=1.0')
                layer_rgba_path = temp_path / f'layer_{layer_index}_rgba.exr'
                add_alpha_cmd = [
                    'oiiotool',
                    path_str(to_wsl_path(rgb_source_path)),
                    '--ch', 'R,G,B,A=1.0',
                    '--attrib:type=string', 'oiio:ColorSpace', 'ACEScg',
                    '-o', path_str(to_wsl_path(layer_rgba_path))
                ]
                result = exr._run(add_alpha_cmd, env=_get_ocio_env())
                if result != 0:
                    return _error(f'Failed to add constant alpha for layer {layer_name}')
                layer_output_paths.append((layer_name, layer_rgba_path, beauty_path))

        # Composite all layers together using "over" operation
        if not layer_output_paths:
            return _error('No valid layers to composite')

        if len(layer_output_paths) == 1:
            # Only one layer, copy it directly
            _, layer_path, _ = layer_output_paths[0]
            output_frame_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(to_wsl_path(layer_path), to_wsl_path(output_frame_path))
        else:
            # Multiple layers, composite them
            print(f'  Compositing {len(layer_output_paths)} layers')

            # Print layer order for debugging
            for i, (layer_name, _, _) in enumerate(layer_output_paths):
                print(f'    Layer {i}: {layer_name}')

            # Build oiiotool command to composite layers
            # NOTE: oiiotool "A B --over" means "B over A", so we need to
            # process layers from back to front to get correct layering
            oiiotool_cmd = ['oiiotool']

            # Start with the LAST layer (bottom/background)
            _, last_layer_path, _ = layer_output_paths[-1]
            oiiotool_cmd.append(path_str(to_wsl_path(last_layer_path)))

            # Composite each layer from second-to-last to first (back to front)
            # This way: background ... middle --over foreground --over
            for layer_name, layer_path, beauty_path in reversed(layer_output_paths[:-1]):
                oiiotool_cmd.append(path_str(to_wsl_path(layer_path)))
                oiiotool_cmd.append('--over')

            print(f'    Composite order: {" --over ".join([name for name, _, _ in reversed(layer_output_paths)])}')

            # Write final output with proper colorspace metadata
            output_frame_path.parent.mkdir(parents=True, exist_ok=True)
            oiiotool_cmd.extend([
                '--attrib:type=string', 'oiio:ColorSpace', 'ACEScg',
                '-o', path_str(to_wsl_path(output_frame_path))
            ])

            result = exr._run(oiiotool_cmd, env=_get_ocio_env())
            if result != 0:
                return _error(f'Failed to composite layers for frame {frame_index}')

    print(f'  Completed frame {frame_index}')
    return 0

def main(
    render_range,
    input_paths,
    receipt_path,
    output_path
    ):

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Print environment variables for debugging
    print_env()

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

    # Composite frames
    _headline('Compositing frames')
    for frame_index in render_range:
        result = _composite_frame(frame_index, input_paths, output_path)
        if result != 0:
            return result

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
    'first_frame': 1,
    'last_frame': 10,
    'step_size': 1,
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
    'output_path': 'path/to/output.####.exr'
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
    if 'step_size' not in config: return False
    if not isinstance(config['step_size'], int): return False
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
    first_frame = config['first_frame']
    last_frame = config['last_frame']
    step_size = config['step_size']
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame, step_size)

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