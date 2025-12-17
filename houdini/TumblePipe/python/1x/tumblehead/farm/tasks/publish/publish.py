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
    default_client,
    refresh_global_cache
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.util.uri import Uri
from tumblehead.apps.houdini import Hython
from tumblehead.config.department import is_renderable
from tumblehead.pipe.paths import (
    next_export_path
)

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1


def _get_entity_type(entity_uri: Uri) -> str | None:
    """Get entity type from URI ('shot' or 'asset')."""
    if entity_uri.purpose != 'entity':
        return None
    if len(entity_uri.segments) < 1:
        return None
    context = entity_uri.segments[0]
    if context == 'shots':
        return 'shot'
    if context == 'assets':
        return 'asset'
    return None


def _get_asset_uri(entity_uri: Uri) -> Uri | None:
    """
    Get asset URI (without department) from entity URI.

    For entity:/assets/CHAR/Steen -> returns entity:/assets/CHAR/Steen
    """
    if _get_entity_type(entity_uri) != 'asset':
        return None
    # Asset URIs have format: entity:/assets/category/asset
    if len(entity_uri.segments) < 3:
        return None
    return Uri.parse_unsafe(f'entity:/assets/{entity_uri.segments[1]}/{entity_uri.segments[2]}')


def _trigger_asset_build(entity_uri: Uri, settings: dict):
    """
    Trigger asset build job after successful renderable department publish.

    Args:
        entity_uri: The asset entity URI
        settings: Settings from config containing priority and pool_name
    """
    try:
        from tumblehead.farm.jobs.houdini.build import job as build_job

        # Get asset base URI (without department)
        asset_uri = _get_asset_uri(entity_uri)
        if asset_uri is None:
            logging.warning(f'Cannot determine asset URI from: {entity_uri}')
            return

        # Submit asset build job
        build_config = {
            'entity_uri': str(asset_uri),
            'priority': settings.get('priority', 50),
            'pool_name': settings.get('pool_name', 'general')
        }

        logging.info(f'Triggering asset build for: {asset_uri}')
        result = build_job.submit(build_config)
        if result != 0:
            logging.warning(f'Asset build job submission returned non-zero: {result}')
        else:
            logging.info(f'Asset build job submitted successfully for: {asset_uri}')

    except Exception as e:
        logging.warning(f'Failed to trigger asset build: {e}')


def _next_export_path(entity):
    # Convert entity JSON to Uri, variant, and department
    entity_uri = Uri.parse_unsafe(entity['uri'])
    variant_name = entity.get('variant', 'default')
    department_name = entity['department']
    return next_export_path(entity_uri, variant_name, department_name)

SCRIPT_PATH = Path(__file__).parent / 'publish_houdini.py'
def main(config):

    # Decide on the next export path
    export_path = _next_export_path(config['entity'])
    if export_path is None:
        return _error('Invalid entity type in config')

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create a temporary config file
        temp_config_path = temp_path / 'config.json'
        store_json(temp_config_path, config)
    
        # Run script in hython
        hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(temp_config_path)),
            ],
            env = dict(
                TH_USER = get_user_name(),
                TH_PROJECT_PATH = path_str(to_windows_path(api.PROJECT_PATH)),
                TH_PIPELINE_PATH = path_str(to_windows_path(api.PIPELINE_PATH)),
                TH_CONFIG_PATH = path_str(to_windows_path(api.CONFIG_PATH)),
                HOUDINI_PACKAGE_DIR = ';'.join([
                    path_str(to_windows_path(api.storage.resolve(Uri.parse_unsafe('pipeline:/houdini')))),
                    path_str(to_windows_path(api.storage.resolve(Uri.parse_unsafe('project:/_pipeline/houdini'))))
                ]),
                OCIO = path_str(to_windows_path(Path(os.environ['OCIO'])))
            )
        )

    # Check if the export was generated (skip for groups - they export individual members)
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    if entity_uri.purpose != 'groups' and not export_path.exists():
        return _error(f'Export not found: {export_path}')

    # Auto-trigger asset build if this is a renderable asset department
    entity_type = _get_entity_type(entity_uri)
    refresh_global_cache('departments')
    if entity_type == 'asset' and is_renderable('assets', department_name):
        logging.info(f'Renderable asset department published: {department_name}')
        _trigger_asset_build(entity_uri, config.get('settings', {}))

    # Done
    print('Success')
    return 0

"""
config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'priority': 'int',
        'pool_name': 'string',
        'first_frame': 'int',
        'last_frame': 'int'
    },
    'tasks': {
        'publish': {
            'downstream_departments': 'list[string]'
        }
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)
    
    def _is_int(datum):
        return isinstance(datum, int)
    
    def _is_list(datum):
        return isinstance(datum, list)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_list = partial(_check, _is_list)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_publish(publish):
            if not isinstance(publish, dict): return False
            if 'downstream_departments' in publish:
                if not _check_list(publish, 'downstream_departments'): return False
                for dept in publish['downstream_departments']:
                    if not isinstance(dept, str): return False
            return True

        if not isinstance(tasks, dict): return False
        if 'publish' in tasks:
            if not _valid_publish(tasks['publish']): return False
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