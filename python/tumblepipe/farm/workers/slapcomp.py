"""Stack each channel's RGBA beauty (or the sum of its LPEs, with the
beauty's alpha) over the channels below it: the quick comp of a render."""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import sys
import os

from tumblepipe.api import path_str
from tumblepipe.util.io import store_json
from tumblepipe.config.timeline import BlockRange
from tumblepipe.apps import exr
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.frames import frame_file
from tumblepipe.farm.specs import slapcomp as slapcomp_spec
from tumblepipe.farm.worker import ensure_dir, error, headline


def _get_ocio_env():
    """Get OCIO environment dict for the oiiotool subprocess."""
    return dict(OCIO=os.environ['OCIO'])

def rgba_command(
    rgb_path: Path,
    beauty_path: Path,
    output_path: Path,
    beauty_alpha_index: int
    ) -> list[str]:
    """The oiiotool command that makes one layer's RGBA for the slapcomp.

    The colour is ``rgb_path``'s first three channels (the beauty itself, or
    the sum of its LPEs); the alpha is the RGBA beauty's own, its channel
    ``beauty_alpha_index`` in oiiotool's order. Channels are selected by
    position, never by name: per-AOV files carry `<aov>.R`-style names and a
    by-name `--ch` silently zero-fills what it can't find (solid black).
    """
    command = [
        'oiiotool', path_str(rgb_path), '--ch', '0,1,2',
        path_str(beauty_path), '--ch', str(beauty_alpha_index), '--chappend',
        
        '--chnames', 'R,G,B,A',
        *exr.ACESCG_ATTRIB_ARGS,
        '-o', path_str(output_path)
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
        image_infos = exr.get_image_info(beauty_path)
        if not image_infos:
            raise ValueError(f'oiiotool lists no image in {beauty_path}')
        _BEAUTY_ALPHA_INDEX[key] = exr.alpha_channel_index(
            image_infos[0].channels or []
        )
    return _BEAUTY_ALPHA_INDEX[key]

def _composite_frame(
    frame_index: int,
    input_paths: dict[str, dict[str, Path]],
    output_path: Path
    ) -> int:
    """Composite a single frame using oiiotool"""

    # Check if output already exists
    output_frame_path = frame_file(output_path, frame_index)
    if output_frame_path.exists():
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

            # Every layer has a beauty (the render fails without it). A
            # layer skipped here used to leave the slapcomp a layer short,
            # with nothing but a log line to say so.
            if 'beauty' not in layer_aovs:
                return error(f'No beauty AOV for layer {layer_name}')
            beauty_path = frame_file(layer_aovs['beauty'], frame_index)
            if not beauty_path.exists():
                return error(f'Beauty AOV frame not found: {beauty_path}')

            # The layer's alpha is its RGBA beauty's. An RGB beauty was
            # rendered before beauty went RGBA (config migration v10) and
            # has no coverage to comp with.
            beauty_alpha_index = _beauty_alpha_index(layer_aovs['beauty'], beauty_path)
            if beauty_alpha_index is None:
                return error(
                    f'The beauty of layer {layer_name} has no alpha channel '
                    f'(an RGB beauty; re-render it): {beauty_path}'
                )

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
                    # A missing LPE would drop its light from the sum (and,
                    # as the first operand, misplace every --add after it).
                    lpe_frame_path = frame_file(lpe_path, frame_index)
                    if not lpe_frame_path.exists():
                        return error(f'LPE AOV {lpe_name} frame not found: {lpe_frame_path}')

                    # Add this LPE to the command
                    oiiotool_cmd.append(path_str(lpe_frame_path))

                    # Add previous result if not first LPE
                    if lpe_index > 0:
                        oiiotool_cmd.append('--add')

                # Save composited LPEs to temp file
                rgb_source_path = temp_path / f'layer_{layer_index}_lpe_composite.exr'
                oiiotool_cmd.extend([
                    *exr.ACESCG_ATTRIB_ARGS,
                    '-o', path_str(rgb_source_path)
                ])

                exr._run(oiiotool_cmd, env=_get_ocio_env())
            else:
                # No LPE AOVs, use beauty directly as RGB source
                print(f'  Layer {layer_name}: Using beauty AOV directly')
                rgb_source_path = beauty_path

            # Make the layer RGBA
            layer_rgba_path = temp_path / f'layer_{layer_index}_rgba.exr'
            exr._run(
                rgba_command(
                    rgb_source_path, beauty_path, layer_rgba_path,
                    beauty_alpha_index
                ),
                env=_get_ocio_env()
            )
            layer_output_paths.append((layer_name, layer_rgba_path, beauty_path))

        # Composite all layers together using "over" operation
        if not layer_output_paths:
            return error('No valid layers to composite')

        if len(layer_output_paths) == 1:
            # Only one layer, copy it directly
            _, layer_path, _ = layer_output_paths[0]
            ensure_dir(output_frame_path.parent)
            shutil.copyfile(layer_path, output_frame_path)
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
            oiiotool_cmd.append(path_str(last_layer_path))

            # Composite the result over each lower layer, top to bottom
            for layer_name, layer_path, beauty_path in reversed(layer_output_paths[:-1]):
                oiiotool_cmd.append(path_str(layer_path))
                oiiotool_cmd.append('--over')

            print(f'    Composite order (top first): {" over ".join([name for name, _, _ in reversed(layer_output_paths)])}')

            # Write final output with proper colorspace metadata
            ensure_dir(output_frame_path.parent)
            oiiotool_cmd.extend([
                *exr.ACESCG_ATTRIB_ARGS,
                '-o', path_str(output_frame_path)
            ])

            exr._run(oiiotool_cmd, env=_get_ocio_env())

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
        frame_file(receipt_path, frame_index)
        for frame_index in render_range
    ]

    # Find missing receipt files
    missing_receipt_paths = [
        output_frame_path
        for output_frame_path in receipt_paths
        if not output_frame_path.exists()
    ]

    # Check if all receipts already exist
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0

    # Composite frames
    headline('Compositing frames')
    for frame_index in render_range:
        result = _composite_frame(frame_index, input_paths, output_path)
        if result != 0:
            return result

    # Check if outputs were generated
    for frame_index in render_range:
        frame_path = frame_file(output_path, frame_index)
        if frame_path.exists(): continue
        return error(f'Output frame not found: {frame_path}')

    # Create output receipts
    headline('Creating output receipts')
    for frame_index in render_range:
        current_receipt_path = frame_file(receipt_path, frame_index)
        current_output_path = frame_file(output_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(current_receipt_path, dict(
            slapcomp = path_str(current_output_path)
        ))

    # Done
    print('Success')
    return 0

def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Comp the configured range (the one Deadline task spans it)."""
    return main(
        BlockRange(
            config['first_frame'],
            config['last_frame'],
            config['step_size']
        ),
        {
            layer_name: {
                aov_name: Path(aov_path)
                for aov_name, aov_path in aovs.items()
            }
            for layer_name, aovs in config['input_paths'].items()
        },
        Path(config['receipt_path']),
        Path(config['output_path'])
    )

if __name__ == '__main__':
    sys.exit(worker.run(slapcomp_spec.is_valid_context, from_config))
