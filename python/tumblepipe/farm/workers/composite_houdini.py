"""The hython half of the composite worker: load the comp workfile and
cook its render node once per channel for the frames still missing."""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import json
import os

import hou

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.config.channels import read_channel_names
from tumblepipe.config.timeline import BlockRange, require_fps
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import playbar
from tumblepipe.farm import worker
from tumblepipe.farm.frames import frame_file, HOUDINI_FRAME_TOKEN
from tumblepipe.farm.specs import composite as composite_spec
from tumblepipe.apps.deadline import log_progress
from tumblepipe.farm.worker import ensure_dir, error, headline

def main(
    render_range: BlockRange,
    frames: list[int],
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

    # Open the input file
    if not input_path.exists():
        return error(f'Input not found: {input_path}')
    hou.hipFile.load(path_str(input_path))

    # Find the render node
    render_node = hou.node(node_path)
    if render_node is None:
        return error(f'Node not found: {node_path}')

    # Set FPS before the range so the range can't be shifted by a later
    # fps change (see playbar.set_fps; consistent with import_shot).
    entity_uri = Uri.parse(entity_json['uri'])
    playbar.set_fps(require_fps(entity_uri))
    playbar.set_block_range(render_range)

    # HACK: Make sure the graph is cooked
    render_node.parm('f1').set(render_range.first_frame)
    render_node.parm('f2').set(render_range.first_frame)
    render_node.parm('execute').pressButton()
    # HACK: Make sure the graph is cooked

    # Get channel names.
    #
    # Reads CHANNEL_PROPERTY rather than the literal 'channels' so the key has
    # exactly one definition. It deliberately does NOT go through
    # channels.list_channels(): that prepends the implicit 'default', which
    # would shift every subsequent index by one and so change which output port
    # `port1` selects below. Whether this list *should* include 'default' is a
    # real open question -- list_channels() is the pipeline's answer everywhere
    # else -- but resolving it changes render output, so it is deliberately not
    # bundled into a rename. See the port1 index use ~10 lines down.
    properties = api.config.get_properties(entity_uri)
    channel_names = read_channel_names(properties)

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Render each layer separately
        all_layer_aov_paths = {}  # {frame_index: {layer_name: {aov_name: (temp_path, output_path)}}}

        for layer_name in layer_names:
            headline(f'Rendering layer: {layer_name}')

            # Set the dive node output port to select the channel
            layer_index = channel_names.index(layer_name)
            render_node.parm('port1').set(layer_index + 1)

            # Set the output path for this layer
            temp_layer_path = temp_path / layer_name
            temp_layer_path.mkdir(parents=True, exist_ok=True)
            temp_frames_path = temp_layer_path / f'composite.{HOUDINI_FRAME_TOKEN}.exr'
            render_node.parm('copoutput').set(path_str(temp_frames_path))

            # Render the frames for this layer
            print(f'  Rendering frames for layer: {layer_name}')
            for frame_index in log_progress(render_range):

                # Skip if already rendered
                if frame_index not in frames: continue
                print(f'  Rendering frame {frame_index} for layer {layer_name}')

                # Render the frame
                render_node.parm('f1').set(frame_index)
                render_node.parm('f2').set(frame_index)
                render_node.parm('execute').pressButton()

            # Store RGBA beauty frames for this layer
            print(f'  Processing rendered frames for layer: {layer_name}')
            for frame_index in render_range:

                # Skip if already rendered
                if frame_index not in frames: continue
                print(f'  Processing frame {frame_index} for layer {layer_name}')

                # Get the RGBA frame path
                rgba_frame_path = frame_file(temp_frames_path, frame_index, HOUDINI_FRAME_TOKEN)
                if not rgba_frame_path.exists():
                    return error(f'RGBA frame not found: {rgba_frame_path}')

                # Store the paths for this layer
                if frame_index not in all_layer_aov_paths:
                    all_layer_aov_paths[frame_index] = {}
                if layer_name not in all_layer_aov_paths[frame_index]:
                    all_layer_aov_paths[frame_index][layer_name] = {}

                # Get the output path for this layer's beauty (RGBA)
                output_beauty_path = output_paths.get(layer_name)
                if output_beauty_path is None:
                    return error(f'No output path for layer: {layer_name}')

                all_layer_aov_paths[frame_index][layer_name] = (
                    rgba_frame_path,
                    frame_file(output_beauty_path, frame_index)
                )

        # Copy to the output path
        headline('Copying files to network')
        for frame_index, layer_paths in all_layer_aov_paths.items():
            for layer_name, (temp_path, output_path) in layer_paths.items():
                print(f'Copying file: {output_path}')
                ensure_dir(output_path.parent)
                shutil.copyfile(temp_path, output_path)

        # Create the output receipts
        headline('Creating output receipts')
        for frame_index, layer_paths in all_layer_aov_paths.items():
            current_receipt_path = frame_file(receipt_path, frame_index)
            print(f'Creating receipt: {current_receipt_path}')

            receipt_data = {
                layer_name: path_str(output_path)
                for layer_name, (_, output_path) in layer_paths.items()
            }
            store_json(current_receipt_path, receipt_data)

    # Done
    headline('Done')
    return 0

def from_config(config: dict) -> int:
    """Composite as the worker's config describes."""
    headline('Config')
    print(json.dumps(config, indent=4))
    return main(
        BlockRange(config['first_frame'], config['last_frame']),
        config['frames'],
        Path(config['receipt_path']),
        Path(config['input_path']),
        config['node_path'],
        config['layer_names'],
        {
            output_key: Path(output_path)
            for output_key, output_path in config['output_paths'].items()
        },
        config['entity']
    )

if __name__ == '__main__':
    hou.exit(worker.run(
        composite_spec.is_valid_houdini_config, from_config, frames=False
    ))
