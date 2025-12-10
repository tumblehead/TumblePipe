from functools import partial
from pathlib import Path
import json

import hou

from tumblehead.api import (
    path_str,
    default_client
)
from tumblehead.util.io import load_json
from tumblehead.config.timeline import BlockRange, get_fps
from tumblehead.pipe.houdini import util

api = default_client()

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _get_aov_names(render_settings_path: Path) -> set:

    # Load render settings
    render_settings_data = load_json(render_settings_path)
    if render_settings_data is None: return None

    # Get AOV names
    if 'aov_names' not in render_settings_data: return None
    return set(render_settings_data['aov_names'])

def _get_render_settings_script(render_settings_path: Path) -> str:
    _render_settings_path = path_str(render_settings_path)
    return (
        'from pathlib import Path\n'
        'import json\n'
        'import hou\n'
        '\n'
        'def load_json(path):\n'
        '    if not path.exists(): return None\n'
        '    with path.open("r") as file:\n'
        '        return json.load(file)\n'
        '\n'
        'def _edit_render_settings():\n'
        '    \n'
        '    # Get context\n'
        '    node = hou.pwd()\n'
        '    stage = node.editableStage()\n'
        '    root = stage.GetPseudoRoot()\n'
        '    \n'
        '    # Load render settings\n'
        f'    render_settings_path = Path("{_render_settings_path}")\n'
        '    render_settings_data = load_json(render_settings_path)\n'
        '    if render_settings_data is None: return\n'
        '    if "overrides" not in render_settings_data: return\n'
        '    \n'    
        '    # Get render settings prim\n'
        '    render_settings_prim = root.GetPrimAtPath(\n'
        '        "/Render/rendersettings"\n'
        '    )\n'
        '    if not render_settings_prim.IsValid(): return\n'
        '    \n'    
        '    # Edit the render settings\n'
        '    overrides = render_settings_data["overrides"]\n'
        '    for property, value in overrides.items():\n'
        '        attribute = render_settings_prim.GetAttribute(property)\n'
        '        if not attribute.IsValid(): continue\n'
        '        attribute.Set(value)\n'
        '\n'
        '_edit_render_settings()\n'
    )

def main(
    render_range: BlockRange,
    render_settings_path: Path,
    input_path: Path,
    node_path: str,
    output_path: Path
    ) -> int:

    # Open the input scene
    if not input_path.exists():
        return _error(f'Input file not found: {input_path}')
    hou.hipFile.load(path_str(input_path))

    # Find the node to export
    input_node = hou.node(node_path)
    if input_node is None:
        return _error(f'Node not found: {node_path}')
    
    # Prepare scene
    scene_node = input_node.parent()
    prev_node = input_node
    
    # Setup edit render settings
    edit_render_settings_node = scene_node.createNode(
        'pythonscript',
        '__edit_settings'
    )
    edit_render_settings_node.parm('python').set(
        _get_render_settings_script(render_settings_path)
    )
    _connect(prev_node, edit_render_settings_node)
    prev_node = edit_render_settings_node

    # Setup AOV pruning
    included_aov_names = _get_aov_names(render_settings_path)
    if included_aov_names is not None:
        root = input_node.stage().GetPseudoRoot()
        aov_paths = {
            aov_path.rsplit('/', 1)[1]: aov_path
            for aov_path in util.list_render_vars(root)
        }
        excluded_aov_names = set(aov_paths.keys()) - included_aov_names
        prune_aovs_node = scene_node.createNode('prune', '__prune_aovs')
        prune_aovs_node.parm('primpattern1').set(
            ' '.join([
                aov_paths[aov_name]
                for aov_name in excluded_aov_names
            ])
        )
        _connect(prev_node, prune_aovs_node)
        prev_node = prune_aovs_node
    
    # Prepare the export
    export_node = scene_node.createNode('filecache', '__export')
    export_node.parm('filemethod').set(1)
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f3').set(1)
    export_node.parm('striplayerbreaks').set(False)
    _connect(prev_node, export_node)

    # Set the frame range and FPS
    util.set_block_range(render_range)
    fps = get_fps()
    if fps is not None:
        util.set_fps(fps)

    # Export the USD stage
    export_node.parm('file').set(path_str(output_path))
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('execute').pressButton()

    # Verify the USD file was created
    if not output_path.exists():
        return _error(f'Failed to export USD stage: {output_path}')

    print(f'Successfully exported USD stage: {output_path}')

    # Done
    return 0

"""
config = {
    'first_frame': 'int',
    'last_frame': 'int',
    'render_settings_path': 'path/to/render_settings.json',
    'input_path': 'path/to/input.hip',
    'node_path': '/stage/path/to/node',
    'output_path': 'path/to/stage.usd'
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)
    
    def _is_int(datum):
        return isinstance(datum, int)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    
    if not isinstance(config, dict): return False
    if not _check_int(config, 'first_frame'): return False
    if not _check_int(config, 'last_frame'): return False
    if not _check_str(config, 'render_settings_path'): return False
    if not _check_str(config, 'input_path'): return False
    if not _check_str(config, 'node_path'): return False
    if not _check_str(config, 'output_path'): return False
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
    
    # Get the parameters
    render_range = BlockRange(
        config['first_frame'],
        config['last_frame']
    )
    render_settings_path = Path(config['render_settings_path'])
    input_path = Path(config['input_path'])
    node_path = config['node_path']
    output_path = Path(config['output_path'])

    # Run main
    return main(
        render_range,
        render_settings_path,
        input_path,
        node_path,
        output_path
    )

if __name__ == '__main__':
    hou.exit(cli())