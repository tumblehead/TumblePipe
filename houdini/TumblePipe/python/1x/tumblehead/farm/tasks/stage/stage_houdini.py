from functools import partial
from pathlib import Path
import json

import hou

from tumblehead.api import (
    path_str,
    default_client
)
from tumblehead.config import BlockRange
from tumblehead.util.io import load_json
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_render_layer
)
from tumblehead.pipe.paths import (
    Entity,
    ShotEntity
)

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
        'def _load_json(path):\n'
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
        '    render_settings_data = _load_json(render_settings_path)\n'
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
    sequence_name: str,
    shot_name: str,
    render_range: BlockRange,
    render_layer_name: str,
    render_department_name: str,
    render_settings_path: Path,
    output_path: Path
    ) -> int:
    _headline('Stage Shot')
    
    # Prepare scene
    scene_node = hou.node('/stage')

    # Config
    included_department_names = api.render.list_included_shot_department_names(
        render_department_name
    )

    # Create build shot node
    shot_node = build_shot.create(scene_node, '__build_shot')
    shot_node.set_sequence_name(sequence_name)
    shot_node.set_shot_name(shot_name)
    shot_node.set_include_procedurals(True)
    shot_node.set_include_downstream_departments(True)
    shot_node.execute()
    prev_node = shot_node.native()

    # Prepare import render layers
    render_layer_subnet = scene_node.createNode('subnet', '__render_layers')
    render_layer_subnet.node('output0').destroy()
    render_layer_subnet_input = render_layer_subnet.indirectInputs()[0]
    render_layer_subnet_output = render_layer_subnet.createNode(
        'output', 'output'
    )

    # Connect build shot to subnet
    _connect(prev_node, render_layer_subnet)
    prev_node = render_layer_subnet_input

    # Setup render layers node
    for included_department_name in included_department_names:
        layer_node = import_render_layer.create(
            render_layer_subnet,
            included_department_name
        )
        layer_node.set_sequence_name(sequence_name)
        layer_node.set_shot_name(shot_name)
        layer_node.set_department_name(included_department_name)
        layer_node.set_render_layer_name(render_layer_name)
        layer_node.latest()
        layer_node.execute()
        _connect(prev_node, layer_node.native())
        prev_node = layer_node.native()
    
    # Connect last node to subnet output
    _connect(prev_node, render_layer_subnet_output)
    prev_node = render_layer_subnet

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
        root = shot_node.native().stage().GetPseudoRoot()
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

    # Setup export node
    export_node = scene_node.createNode('filecache', '__export')
    export_node.parm('filemethod').set(1)
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f3').set(1)
    export_node.parm('striplayerbreaks').set(False)
    _connect(prev_node, export_node)

    # Export the USD stage
    export_node.parm('file').set(path_str(output_path))
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('execute').pressButton()

    # Done
    return 0

"""
config = {
    'entity': {
        'tag': 'asset',
        'category_name': 'string',
        'asset_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'shot',
        'sequence_name': 'string',
        'shot_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'kit',
        'category_name': 'string',
        'kit_name': 'string',
        'department_name': 'string'
    },
    'first_frame': 'int',
    'last_frame': 'int',
    'render_layer_name': 'string',
    'render_department_name': 'string',
    'render_settings_path': 'path/to/render_settings.json',
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

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if 'tag' not in entity: return False
        match entity['tag']:
            case 'asset':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'asset_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'shot':
                if not _check_str(entity, 'sequence_name'): return False
                if not _check_str(entity, 'shot_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'kit':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'kit_name'): return False
                if not _check_str(entity, 'department_name'): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if not _check_int(config, 'first_frame'): return False
    if not _check_int(config, 'last_frame'): return False
    if not _check_str(config, 'render_layer_name'): return False
    if not _check_str(config, 'render_department_name'): return False
    if not _check_str(config, 'render_settings_path'): return False
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

    # Get the entity
    entity = Entity.from_json(config['entity'])
    if not isinstance(entity, ShotEntity):
        return _error(f'Invalid entity: {entity}')
    
    # Get the parameters
    render_range = BlockRange(
        config['first_frame'],
        config['last_frame']
    )
    render_layer_name = config['render_layer_name']
    render_department_name = config['render_department_name']
    render_settings_path = Path(config['render_settings_path'])
    output_path = Path(config['output_path'])

    # Run main
    return main(
        entity.sequence_name,
        entity.shot_name,
        render_range,
        render_layer_name,
        render_department_name,
        render_settings_path,
        output_path
    )

if __name__ == '__main__':
    hou.exit(cli())