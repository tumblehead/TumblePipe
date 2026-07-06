from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblepipe.api import (
    path_str,
    local_path,
    to_windows_path,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import _common
from tumblepipe.farm.tasks.env import get_hython_env, job_data_dir
from tumblepipe.farm.tasks.publish import _spec
from tumblepipe.config.department import is_renderable
from tumblepipe.config.variants import get_entity_type as _get_entity_type
from tumblepipe.pipe.paths import (
    next_export_path
)
from tumblepipe.farm.tasks.env import print_env

_error = _common.error


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


def _trigger_asset_build(entity_uri: Uri, settings: dict, variant_name: str = 'default'):
    """
    Trigger asset build job after successful renderable department publish.

    Raises on failure — a renderable publish whose staged build never
    submits leaves the asset stale while the task reports success.

    Args:
        entity_uri: The asset entity URI
        settings: Settings from config (priority/pool_name guaranteed by _spec)
        variant_name: The variant to build (defaults to 'default')
    """
    from tumblepipe.farm.jobs.houdini.build import job as build_job

    # Get asset base URI (without department)
    asset_uri = _get_asset_uri(entity_uri)
    if asset_uri is None:
        raise RuntimeError(f'Cannot determine asset URI from: {entity_uri}')

    # Submit asset build job
    build_config = {
        'entity_uri': str(asset_uri),
        'variant_name': variant_name,
        'priority': settings['priority'],
        'pool_name': settings['pool_name']
    }

    logging.info(f'Triggering asset build for: {asset_uri} variant: {variant_name}')
    result = build_job.submit(build_config)
    if result != 0:
        raise RuntimeError(f'Asset build job submission failed ({result}) for: {asset_uri}')
    logging.info(f'Asset build job submitted successfully for: {asset_uri} variant: {variant_name}')


def _next_export_path(entity):
    # Convert entity JSON to Uri, variant, and department
    entity_uri = Uri.parse_unsafe(entity['uri'])
    variant_name = entity.get('variant', 'default')
    department_name = entity['department']
    return next_export_path(entity_uri, variant_name, department_name)

SCRIPT_PATH = Path(__file__).parent / 'publish_houdini.py'
def main(config):

    # Print environment variables for debugging
    print_env()

    # Decide on the next export path
    export_path = _next_export_path(config['entity'])
    if export_path is None:
        return _error('Invalid entity type in config')

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create a temporary config file
        temp_config_path = temp_path / 'config.json'
        store_json(temp_config_path, config)
    
        # Run script in hython
        hython_result = hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(temp_config_path)),
            ],
            env = {
                **get_hython_env(api),
                # Forward the job data dir so publish_houdini can resolve the
                # bundled workfile (it runs in hython with CWD = the hpm
                # manifest dir, not the data dir).
                'TH_FARM_DATA': path_str(job_data_dir()),
            }
        )
        if hython_result != 0:
            return _error(f'publish_houdini failed with exit code {hython_result}')

    # Check if the export was generated (skip for groups - they export individual members)
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    if entity_uri.purpose != 'groups' and not export_path.exists():
        return _error(f'Export not found: {export_path}')

    # Auto-trigger asset build if this is a renderable asset department
    entity_type = _get_entity_type(entity_uri)
    variant_name = config['entity'].get('variant', 'default')
    if entity_type == 'asset' and is_renderable('assets', department_name):
        logging.info(f'Renderable asset department published: {department_name}')
        try:
            _trigger_asset_build(entity_uri, config['settings'], variant_name)
        except Exception as e:
            return _error(f'Publish succeeded but asset build trigger failed: {e}')

    # Done
    print('Success')
    return 0

def cli():
    return _common.run_task_cli(_spec.is_valid_config, main)

if __name__ == '__main__':
    _common.configure_logging()
    sys.exit(cli())
