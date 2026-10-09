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

from tumblepipe.api import (
    path_str,
    local_path
)
from tumblepipe.util.io import (
    load_json,
    store_json
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.farm._common import ensure_dir, is_int
from tumblepipe.apps import exr
from tumblepipe.farm.tasks.env import print_env
from tumblepipe.pipe.aovs import (
    comp_alpha_source,
    ALPHA_FROM_BEAUTY,
    ALPHA_FROM_AOV,
    LEGACY_ALPHA_AOV
)


def _get_ocio_env():
    """Get OCIO environment dict for the oiiotool subprocess."""
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

def rgba_command(
    rgb_path: Path,
    alpha_source,
    beauty_path: Path,
    alpha_path,
    output_path: Path,
    beauty_alpha_index: int = 3
    ) -> list[str]:
    """The oiiotool command that makes one layer's RGBA for the slapcomp.

    ``alpha_source`` is :func:`tumblepipe.pipe.aovs.comp_alpha_source`'s
    answer: the RGBA beauty's own alpha (its channel ``beauty_alpha_index``,
    in oiiotool's order), a legacy render's separate ``alpha`` AOV, or None
    for a constant 1. Channels are selected by position, never by name:
    per-AOV files carry `<aov>.R`-style names and a by-name `--ch` silently
    zero-fills what it can't find (solid black).
    """
    command = ['oiiotool', path_str(local_path(rgb_path)), '--ch', '0,1,2']
    if alpha_source == ALPHA_FROM_BEAUTY:
        command += [
            path_str(local_path(beauty_path)),
            '--ch', str(beauty_alpha_index), '--chappend'
        ]
    elif alpha_source == ALPHA_FROM_AOV:
        command += [
            path_str(local_path(alpha_path)), '--ch', '0', '--chappend'
        ]
    else:
        command += ['--ch', '0,1,2,A=1.0']
    command += [
        '--chnames', 'R,G,B,A',
        *exr.ACESCG_ATTRIB_ARGS,
        '-o', path_str(local_path(output_path))
    ]
    return command

# Beauty frame pattern -> index of its alpha channel (None: no alpha). Every
# frame of a render shares one layout, so one oiiotool probe per beauty.
_BEAUTY_ALPHA_INDEX = dict()

def _beauty_alpha_index(beauty_pattern: Path, beauty_path: Path):
    """Index of the beauty's alpha channel in oiiotool's channel order.

    oiiotool's order, not the EXR header's: the header lists channels
    alphabetically (`A,B,G,R`) while oiiotool, whose `--ch` takes the index,
    puts them `R,G,B,A`.
    """
    key = str(beauty_pattern)
    if key not in _BEAUTY_ALPHA_INDEX:
        image_infos = exr.get_image_info(local_path(beauty_path))
        channels = image_infos[0].channels if image_infos else []
        _BEAUTY_ALPHA_INDEX[key] = exr.alpha_channel_index(channels or [])
    return _BEAUTY_ALPHA_INDEX[key]

def _composite_frame(
    frame_index: int,
    input_paths: dict[str, dict[str, Path]],
    output_path: Path
    ) -> int:
    """Composite a single frame using oiiotool"""

    # Check if output already exists
    output_frame_path = _get_frame_path(output_path, frame_index)
    if local_path(output_frame_path).exists():
        print(f'Frame {frame_index} already exists, skipping')
        return 0

    print(f'Processing frame {frame_index}')

    # Composite layers using oiiotool
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        layer_output_paths = []

        # Process each render layer, bottom of the stack first: the channels
        # in the render's channel order (``default`` first, as every channel
        # list starts with it)
        for layer_index, (layer_name, layer_aovs) in enumerate(input_paths.items()):

            # Get beauty AOV path for this layer
            if 'beauty' not in layer_aovs:
                print(f'  Warning: No beauty AOV found for layer {layer_name}, skipping')
                continue

            beauty_path = _get_frame_path(layer_aovs['beauty'], frame_index)
            if not local_path(beauty_path).exists():
                print(f'  Warning: Beauty AOV not found at {beauty_path}, skipping')
                continue

            # Where the layer's alpha comes from: the RGBA beauty, or the
            # separate alpha AOV of a render made before beauty went RGBA
            alpha_path = None
            available_aovs = set(layer_aovs.keys())
            if LEGACY_ALPHA_AOV in layer_aovs:
                alpha_path = _get_frame_path(layer_aovs[LEGACY_ALPHA_AOV], frame_index)
                if not local_path(alpha_path).exists():
                    print(f'  Warning: Alpha AOV specified but not found at {alpha_path}')
                    available_aovs.discard(LEGACY_ALPHA_AOV)
            beauty_alpha_index = _beauty_alpha_index(layer_aovs['beauty'], beauty_path)
            alpha_source = comp_alpha_source(beauty_alpha_index, available_aovs)

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
                    if not local_path(lpe_frame_path).exists():
                        print(f'    Warning: LPE AOV {lpe_name} not found, skipping')
                        continue

                    # Add this LPE to the command
                    oiiotool_cmd.append(path_str(local_path(lpe_frame_path)))

                    # Add previous result if not first LPE
                    if lpe_index > 0:
                        oiiotool_cmd.append('--add')

                # Save composited LPEs to temp file
                rgb_source_path = temp_path / f'layer_{layer_index}_lpe_composite.exr'
                oiiotool_cmd.extend([
                    *exr.ACESCG_ATTRIB_ARGS,
                    '-o', path_str(local_path(rgb_source_path))
                ])

                result = exr._run(oiiotool_cmd, env=_get_ocio_env())
                if result != 0:
                    return _error(f'Failed to composite LPE AOVs for layer {layer_name}')
            else:
                # No LPE AOVs, use beauty directly as RGB source
                print(f'  Layer {layer_name}: Using beauty AOV directly')
                rgb_source_path = beauty_path

            # Make the layer RGBA
            if alpha_source == ALPHA_FROM_BEAUTY:
                print(f'  Layer {layer_name}: Alpha from the RGBA beauty')
            elif alpha_source == ALPHA_FROM_AOV:
                print(f'  Layer {layer_name}: Alpha from the alpha AOV')
            else:
                print(f'  Layer {layer_name}: Adding constant alpha=1.0')
            layer_rgba_path = temp_path / f'layer_{layer_index}_rgba.exr'
            result = exr._run(
                rgba_command(
                    rgb_source_path, alpha_source,
                    beauty_path, alpha_path, layer_rgba_path,
                    beauty_alpha_index if beauty_alpha_index is not None else 3
                ),
                env=_get_ocio_env()
            )
            if result != 0:
                return _error(f'Failed to make RGBA for layer {layer_name}')
            layer_output_paths.append((layer_name, layer_rgba_path, beauty_path))

        # Composite all layers together using "over" operation
        if not layer_output_paths:
            return _error('No valid layers to composite')

        if len(layer_output_paths) == 1:
            # Only one layer, copy it directly
            _, layer_path, _ = layer_output_paths[0]
            ensure_dir(output_frame_path.parent)
            shutil.copyfile(local_path(layer_path), local_path(output_frame_path))
        else:
            # Multiple layers, composite them
            print(f'  Compositing {len(layer_output_paths)} layers')

            # Print layer order for debugging
            for i, (layer_name, _, _) in enumerate(layer_output_paths):
                print(f'    Layer {i}: {layer_name}')

            # Build oiiotool command to composite layers. The stack is
            # bottom-up: layer 0 (`default`) at the bottom, the last layer on
            # top. oiiotool's "A B --over" is A over B
            # (the first operand is the foreground), so start from the top
            # layer and composite it over each layer below in turn:
            # top L1 --over L0 --over == top over L1 over L0.
            oiiotool_cmd = ['oiiotool']

            # Start with the LAST layer (the top)
            _, last_layer_path, _ = layer_output_paths[-1]
            oiiotool_cmd.append(path_str(local_path(last_layer_path)))

            # Composite the result over each lower layer, top to bottom
            for layer_name, layer_path, beauty_path in reversed(layer_output_paths[:-1]):
                oiiotool_cmd.append(path_str(local_path(layer_path)))
                oiiotool_cmd.append('--over')

            print(f'    Composite order (top first): {" over ".join([name for name, _, _ in reversed(layer_output_paths)])}')

            # Write final output with proper colorspace metadata
            ensure_dir(output_frame_path.parent)
            oiiotool_cmd.extend([
                *exr.ACESCG_ATTRIB_ARGS,
                '-o', path_str(local_path(output_frame_path))
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
        if not local_path(output_frame_path).exists()
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
        if local_path(frame_path).exists(): continue
        return _error(f'Output frame not found: {frame_path}')

    # Create output receipts
    _headline('Creating output receipts')
    for frame_index in render_range:
        current_receipt_path = _get_frame_path(receipt_path, frame_index)
        current_output_path = _get_frame_path(output_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(local_path(current_receipt_path), dict(
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

    if not isinstance(config, dict): return False
    if 'first_frame' not in config: return False
    if not is_int(config['first_frame']): return False
    if 'last_frame' not in config: return False
    if not is_int(config['last_frame']): return False
    if 'step_size' not in config: return False
    if not is_int(config['step_size']): return False
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