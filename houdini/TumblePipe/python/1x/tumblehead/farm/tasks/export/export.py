from tempfile import TemporaryDirectory
from functools import partial
from pathlib import Path
import logging
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    fix_path,
    get_user_name,
    to_windows_path,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.pipe.paths import Entity
from tumblehead.apps.houdini import Hython
from tumblehead.farm.jobs.houdini.render import job as render_job

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

SCRIPT_PATH = Path(__file__).parent / 'export_houdini.py'
def main(config):

    # Get config data
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    render_layer_name = config['settings']['render_layer_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    batch_size = config['settings']['batch_size']
    input_path = Path(config['tasks']['export']['input_path'])
    node_path = config['tasks']['export']['node_path']

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create output path
        export_path = temp_path / 'export'
        stage_path = export_path / 'stage.usd'
        relative_export_path = export_path.relative_to(temp_path)
        relative_stage_path = stage_path.relative_to(temp_path)

        # Store stage config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            first_frame = first_frame,
            last_frame = last_frame,
            render_settings_path = path_str(render_settings_path),
            input_path = path_str(input_path),
            node_path = node_path,
            output_path = path_str(to_windows_path(stage_path))
        ))
    
        # Export the USD stage
        hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(config_path))
            ],
            env = dict(
                TH_USER = get_user_name(),
                TH_CONFIG_PATH = path_str(to_windows_path(api.CONFIG_PATH)),
                TH_PROJECT_PATH = path_str(to_windows_path(api.PROJECT_PATH)),
                TH_PIPELINE_PATH = path_str(to_windows_path(api.PIPELINE_PATH)),
                HOUDINI_PACKAGE_DIR = ';'.join([
                    path_str(to_windows_path(api.storage.resolve('pipeline:/houdini'))),
                    path_str(to_windows_path(api.storage.resolve('project:/_pipeline/houdini')))
                ]),
                OCIO = path_str(to_windows_path(Path(os.environ['OCIO'])))
            )
        )

        # Check if stage was exported
        if not stage_path.exists():
            return _error(f'Stage not exported: {stage_path}')
        
        # Submit the render pipe tasks
        tasks = config['tasks'].copy()
        tasks.pop('export')
        render_job.submit(dict(
            entity = entity.to_json(),
            settings = dict(
                user_name = user_name,
                purpose = purpose,
                priority = priority,
                pool_name = pool_name,
                render_layer_name = render_layer_name,
                render_department_name = render_department_name,
                render_settings_path = path_str(render_settings_path),
                input_path = path_str(relative_stage_path),
                first_frame = first_frame,
                last_frame = last_frame,
                step_size = step_size,
                batch_size = batch_size
            ),
            tasks = tasks
        ), {
            render_settings_path: render_settings_path,
            export_path: relative_export_path
        })

    # Done
    print('Success')
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
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'render_layer_name': 'string',
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'export': {
            'input_path': 'string',
            'node_path': 'string',
            'channel_name': 'string'
        },
        'partial_render': {
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
            'denoise': 'bool',
            'channel_name': 'string'
        }
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)
    
    def _is_int(datum):
        return isinstance(datum, int)
    
    def _is_bool(datum):
        return isinstance(datum, bool)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_bool = partial(_check, _is_bool)

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
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_str(settings, 'user_name'): return False
        if not _check_str(settings, 'purpose'): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_str(settings, 'render_layer_name'): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_stage(stage):
            if not isinstance(stage, dict): return False
            if not _check_str(stage, 'input_path'): return False
            if not _check_str(stage, 'node_path'): return False
            if not _check_str(stage, 'channel_name'): return False
            return True

        def _valid_partial_render(partial_render):
            if not isinstance(partial_render, dict): return False
            if not _check_bool(partial_render, 'denoise'): return False
            if not _check_str(partial_render, 'channel_name'): return False
            return True
    
        def _valid_full_render(full_render):
            if not isinstance(full_render, dict): return False
            if not _check_bool(full_render, 'denoise'): return False
            if not _check_str(full_render, 'channel_name'): return False
            return True
        
        if not isinstance(tasks, dict): return False
        if 'stage' in tasks:
            if not _valid_stage(tasks['stage']): return False
        if 'partial_render' in tasks:
            if not _valid_partial_render(tasks['partial_render']): return False
        if 'full_render' in tasks:
            if not _valid_full_render(tasks['full_render']): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    return True

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('start_frame', type=int)
    parser.add_argument('end_frame', type=int)
    args = parser.parse_args()

    # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')
    
    # Run main
    return main(config)

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())