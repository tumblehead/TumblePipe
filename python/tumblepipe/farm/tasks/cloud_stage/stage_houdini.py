from functools import partial
from pathlib import Path
import json

import hou

from tumblepipe.api import (
    path_str
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import render_stage
from tumblepipe.pipe.houdini.lops import archive

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
    variant_name: str,
    render_settings_path: Path,
    output_path: Path
    ) -> int:
    _headline('Stage Shot')

    # Prepare scene
    scene_node = hou.node('/stage')

    # Build the shared render-stage graph for this variant
    prev_node = render_stage.build_render_stage_graph(
        scene_node,
        shot_uri,
        variant_name,
        render_settings_path
    )

    # Setup archive node
    archive_node = archive.create(scene_node, '__export')
    archive_node.parm('range_type').set('from_settings')
    archive_node.parm('frame_settingsx').deleteAllKeyframes()
    archive_node.parm('frame_settingsy').deleteAllKeyframes()
    _connect(prev_node, archive_node)

    # Export the USD stage
    archive_node.parm('archive_path').set(path_str(output_path))
    archive_node.parm('frame_settingsx').set(render_range.first_frame)
    archive_node.parm('frame_settingsy').set(render_range.last_frame)
    archive_node.parm('export').pressButton()

    # Verify the USD file was created
    if not output_path.exists():
        return _error(f'Failed to export USD stage: {output_path}')

    print(f'Successfully exported USD stage: {output_path}')

    # Done
    return 0

"""
config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'first_frame': 'int',
    'last_frame': 'int',
    'variant_name': 'string',
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
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if not _check_int(config, 'first_frame'): return False
    if not _check_int(config, 'last_frame'): return False
    if not _check_str(config, 'variant_name'): return False
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
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    if entity_uri.segments[0] != 'shots':
        return _error(f'Invalid entity: {entity_uri}')

    # Get the parameters
    render_range = BlockRange(
        config['first_frame'],
        config['last_frame']
    )
    variant_name = config['variant_name']
    render_settings_path = Path(config['render_settings_path'])
    output_path = Path(config['output_path'])

    # Run main
    return main(
        entity_uri,
        render_range,
        variant_name,
        render_settings_path,
        output_path
    )

if __name__ == '__main__':
    hou.exit(cli())