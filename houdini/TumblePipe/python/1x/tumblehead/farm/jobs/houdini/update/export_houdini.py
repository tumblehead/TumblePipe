from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import hou

from tumblehead.api import path_str, fix_path, default_client
from tumblehead.config.timeline import BlockRange, get_frame_range
from tumblehead.config.shots import list_render_layers
from tumblehead.config.department import list_departments
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_render_layer
)

api = default_client()

# Helpers
def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'Error: {msg}')
    return 1

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def main(
    shot_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    render_range: BlockRange,
    output_stage_path: Path
    ) -> int:

    _headline('Parameters')
    print(f'Shot URI: {shot_uri}')
    print(f'Render department name: {render_department_name}')
    print(f'Render layer name: {render_layer_name}')
    print(f'Render range: {render_range}')
    print(f'Output stage path: {output_stage_path}')

    # Nodes
    context = hou.node('/stage')

    # Config
    included_asset_departments = [d.name for d in list_departments('assets') if d.renderable]
    included_kit_departments = []
    included_shot_departments = [d.name for d in list_departments('shots') if d.renderable]
    shot_department_name = included_shot_departments[-1]

    # Setup build shot
    shot_node = build_shot.create(context, 'build_shot')
    shot_node.set_shot_uri(shot_uri)
    shot_node.set_asset_department_names(included_asset_departments)
    shot_node.set_kit_department_names(included_kit_departments)
    shot_node.set_shot_department_names(included_shot_departments)
    shot_node.execute()
    prev_node = shot_node.native()

    # Setup import render layer
    layer_node = import_render_layer.create(context, 'import_render_layer')
    layer_node.set_shot_uri(shot_uri)
    layer_node.set_department_name(shot_department_name)
    layer_node.set_render_layer_name(render_layer_name)
    layer_node.latest()
    layer_node.execute()
    _connect(prev_node, layer_node.native())
    prev_node = layer_node.native()

    # Setup export
    export_node = context.createNode('usd_rop', 'export')
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('striplayerbreaks').set(0)
    _connect(prev_node, export_node)

    # Export
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_stage_path = temp_path / output_stage_path.name

        # Export the stage
        export_node.parm('lopoutput').set(path_str(temp_stage_path))
        export_node.parm('execute').pressButton()

        # Check if stage was exported
        if not temp_stage_path.exists():
            return _error(f'Failed to export USD stage: {temp_stage_path}')
        
        # Copy stage to network
        _headline('Copying files to output')
        shutil.copytree(
            temp_path,
            output_stage_path.parent,
            dirs_exist_ok = True
        )

    # Done
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script_path', type=str)
    parser.add_argument('entity_uri', type=str)
    parser.add_argument('render_department_name', type=str)
    parser.add_argument('render_layer_name', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    parser.add_argument('output_stage_path', type=str)
    args = parser.parse_args()

    # Parse and validate entity URI
    shot_uri = Uri.parse_unsafe(args.entity_uri)
    if shot_uri.purpose != 'entity':
        return _error(f'Invalid entity URI: {shot_uri}')
    if shot_uri.segments[0] != 'shots':
        return _error(f'Invalid entity type: {shot_uri.segments[0]} (expected shots)')

    # Check the render department name
    render_department_names = api.config.list_render_department_names()
    render_department_name = args.render_department_name
    if render_department_name not in render_department_names:
        return _error(
            f'Invalid render department name: '
            f'{render_department_name}'
        )

    # Check the render layer name
    render_layer_name = args.render_layer_name
    render_layer_names = list_render_layers(shot_uri)
    if render_layer_name not in render_layer_names:
        return _error(
            f'Invalid render layer name: '
            f'{render_layer_name}'
        )

    # Check the render range
    frame_range = get_frame_range(shot_uri)
    render_range = BlockRange(args.first_frame, args.last_frame)
    if render_range not in frame_range:
        return _error(
            f'Invalid render range: '
            f'{render_range} '
            f'not in '
            f'{frame_range}'
        )

    # Check the output stage path
    output_stage_path = Path(args.output_stage_path)

    # Run the main function
    return main(
        shot_uri,
        render_department_name,
        render_layer_name,
        render_range,
        output_stage_path
    )

if __name__ == '__main__':
    hou.exit(cli())