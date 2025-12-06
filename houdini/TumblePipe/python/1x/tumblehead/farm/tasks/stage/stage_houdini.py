from functools import partial
from pathlib import Path
import json

import hou

from tumblehead.api import (
    path_str,
    default_client
)
from tumblehead.config.timeline import BlockRange
from tumblehead.config.department import list_departments
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_variant
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
    shot_uri: Uri,
    render_range: BlockRange,
    variant_names: list[str],
    render_department_name: str,
    render_settings_path: Path,
    output_path: Path
    ) -> int:
    _headline('Stage Shot')

    # Prepare scene
    scene_node = hou.node('/stage')

    # Config
    included_department_names = [
        d.name for d in list_departments('shots') if d.renderable
    ]

    # Create build shot node
    shot_node = build_shot.create(scene_node, '__build_shot')
    shot_node.set_shot_uri(shot_uri)
    shot_node.set_include_procedurals(True)
    shot_node.set_include_downstream_departments(True)
    shot_node.execute()
    prev_node = shot_node.native()

    # Loop over each variant to create separate variant subnets
    for variant_name in variant_names:

        # Prepare import variant layers
        variant_subnet = scene_node.createNode('subnet', f'__variant_{variant_name}')
        variant_subnet.node('output0').destroy()
        variant_subnet_input = variant_subnet.indirectInputs()[0]
        variant_subnet_output = variant_subnet.createNode(
            'output', 'output'
        )

        # Connect build shot to subnet
        _connect(prev_node, variant_subnet)
        subnet_prev_node = variant_subnet_input

        # Setup variant node for this specific variant
        for included_department_name in included_department_names:
            variant_node = import_variant.create(
                variant_subnet,
                included_department_name
            )
            variant_node.set_entity_uri(shot_uri)
            variant_node.set_department_name(included_department_name)
            variant_node.set_variant_name(variant_name)
            variant_node.latest()
            variant_node.execute()
            _connect(subnet_prev_node, variant_node.native())
            subnet_prev_node = variant_node.native()

        # Connect last node to subnet output
        _connect(subnet_prev_node, variant_subnet_output)
        prev_node = variant_subnet

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

    # Verify the USD file was created
    if not output_path.exists():
        return _error(f'Failed to export USD stage: {output_path}')

    print(f'Successfully exported USD stage: {output_path}')

    # Done
    return 0

"""
config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'first_frame': 'int',
        'last_frame': 'int',
        'variant_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'path/to/render_settings.json'
    },
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
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if 'variant_names' not in settings: return False
        if not isinstance(settings['variant_names'], list): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        return True

    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
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
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    if entity_uri.segments[0] != 'shots':
        return _error(f'Invalid entity: {entity_uri}')

    # Get the parameters
    settings = config['settings']
    render_range = BlockRange(
        settings['first_frame'],
        settings['last_frame']
    )
    variant_names = settings['variant_names']
    render_department_name = settings['render_department_name']
    render_settings_path = Path(settings['render_settings_path'])
    output_path = Path(config['output_path'])

    # Run main
    return main(
        entity_uri,
        render_range,
        variant_names,
        render_department_name,
        render_settings_path,
        output_path
    )

if __name__ == '__main__':
    hou.exit(cli())