"""Publish a department from a bundled workfile in hython
(``publish_houdini.py``), then queue the staged build a renderable asset
publish calls for."""

from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.config.channels import DEFAULT_CHANNEL
from tumblepipe.config.department import is_renderable
from tumblepipe.config.entities import get_entity_type as _get_entity_type
from tumblepipe.config.timeline import BlockRange
from tumblepipe.pipe.paths import next_export_path
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_hython_env, job_data_dir, print_env
from tumblepipe.farm.pipelines import build as build_pipeline
from tumblepipe.farm.specs import publish as publish_spec
from tumblepipe.farm.worker import error


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
    return Uri.parse(f'entity:/assets/{entity_uri.segments[1]}/{entity_uri.segments[2]}')


def _trigger_asset_build(entity_uri: Uri, settings: dict, channel_name: str):
    """
    Trigger asset build job after successful renderable department publish.

    Raises on failure — a renderable publish whose staged build never
    submits leaves the asset stale while the task reports success.

    Args:
        entity_uri: The asset entity URI
        settings: Settings from config (priority/pool_name guaranteed by the spec)
        channel_name: The channel to build
    """

    # Get asset base URI (without department)
    asset_uri = _get_asset_uri(entity_uri)
    if asset_uri is None:
        raise RuntimeError(f'Cannot determine asset URI from: {entity_uri}')

    # Submit asset build job
    build_config = {
        'entity_uri': str(asset_uri),
        'channel_name': channel_name,
        'priority': settings['priority'],
        'pool_name': settings['pool_name']
    }

    logging.info(f'Triggering asset build for: {asset_uri} channel: {channel_name}')
    build_pipeline.submit(build_config)
    logging.info(f'Asset build job submitted successfully for: {asset_uri} channel: {channel_name}')


# A farm publish job carries no channel (pipelines.publish.create_publish_job never
# writes one): it verifies, and rebuilds, the default channel. The workfile's
# export nodes publish whichever channels they name.
PUBLISH_CHANNEL = DEFAULT_CHANNEL

def _next_export_path(entity):
    entity_uri = Uri.parse(entity['uri'])
    department_name = entity['department']
    return next_export_path(entity_uri, PUBLISH_CHANNEL, department_name)

SCRIPT_PATH = Path(__file__).parent / 'publish_houdini.py'
def main(config: dict, _frame_span: BlockRange) -> int:

    # Print environment variables for debugging
    print_env()

    # Decide on the next export path
    export_path = _next_export_path(config['entity'])

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create a temporary config file
        temp_config_path = temp_path / 'config.json'
        store_json(temp_config_path, config)
    
        # Run script in hython
        hython.run(
            SCRIPT_PATH,
            [
                path_str(temp_config_path),
            ],
            env = {
                **get_hython_env(),
                # Forward the job data dir so publish_houdini can resolve the
                # bundled workfile (it runs in hython with CWD = the hpm
                # manifest dir, not the data dir).
                'TH_FARM_DATA': path_str(job_data_dir()),
            }
        )

    # Check if the export was generated (skip for groups - they export individual members)
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    if entity_uri.purpose != 'groups' and not export_path.exists():
        return error(f'Export not found: {export_path}')

    # Auto-trigger asset build if this is a renderable asset department
    entity_type = _get_entity_type(entity_uri)
    if entity_type == 'asset' and is_renderable('assets', department_name):
        logging.info(f'Renderable asset department published: {department_name}')
        _trigger_asset_build(entity_uri, config['settings'], PUBLISH_CHANNEL)

    # Done
    print('Success')
    return 0

if __name__ == '__main__':
    sys.exit(worker.run(publish_spec.is_valid_context, main))
