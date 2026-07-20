from functools import partial
from pathlib import Path
import json

import hou

from tumblepipe.api import path_str
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import render_stage

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def main(
    shot_uri: Uri,
    render_range: BlockRange,
    render_settings_path: Path,
    output_paths: dict[str, Path],
    render_department_name: str | None = None
    ) -> int:
    _headline('Stage Shot')

    # Prepare scene
    scene_node = hou.node('/stage')

    # Build and export one independent graph per variant. Chaining the
    # variant graphs would compose every variant into a single stage, so
    # each render layer would get the last variant's opinions.
    for variant_name, output_path in output_paths.items():
        _headline(f'Variant {variant_name}')

        prev_node = render_stage.build_render_stage_graph(
            scene_node,
            shot_uri,
            variant_name,
            render_settings_path,
            name_prefix = f'__{variant_name}_',
            render_department_name = render_department_name
        )

        # Setup export node
        export_node = scene_node.createNode(
            'filecache', f'__{variant_name}_export'
        )
        export_node.parm('filemethod').set(1)
        export_node.parm('trange').set(1)
        export_node.parm('f1').deleteAllKeyframes()
        export_node.parm('f2').deleteAllKeyframes()
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
        'render_settings_path': 'path/to/render_settings.json',
        'render_department_name': 'string'
    },
    'output_paths': {
        'variant_name': 'path/to/stage_variant_name.usd'
    }
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
        if not _check_str(settings, 'render_settings_path'): return False
        return True

    def _valid_output_paths(output_paths):
        if not isinstance(output_paths, dict): return False
        if len(output_paths) == 0: return False
        for variant_name, output_path in output_paths.items():
            if not isinstance(variant_name, str): return False
            if not isinstance(output_path, str): return False
        return True

    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'output_paths' not in config: return False
    if not _valid_output_paths(config['output_paths']): return False
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
    render_settings_path = Path(settings['render_settings_path'])
    output_paths = {
        variant_name: Path(output_path)
        for variant_name, output_path in config['output_paths'].items()
    }
    # Optional, not required by _is_valid_config: a job submitted before this
    # key existed is still in flight on the farm, and it means 'compose every
    # department' — the behaviour it was submitted under.
    render_department_name = settings.get('render_department_name') or None

    # Run main
    return main(
        entity_uri,
        render_range,
        render_settings_path,
        output_paths,
        render_department_name
    )

if __name__ == '__main__':
    hou.exit(cli())