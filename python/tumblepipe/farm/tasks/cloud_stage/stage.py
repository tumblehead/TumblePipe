from tempfile import TemporaryDirectory
from pathlib import Path
import tarfile
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
from tumblepipe.farm.jobs.houdini.cloud_render import job as render_job
from tumblepipe.farm.tasks.cloud_stage import _spec
from tumblepipe.farm.tasks.env import get_hython_env, print_env

_error = _common.error

def _walk_path(path: Path):
    if path.is_file(): yield path; return
    for subpath in path.iterdir():
        for subsubpath in _walk_path(subpath):
            yield subsubpath

SCRIPT_PATH = Path(__file__).parent / 'stage_houdini.py'
def main(config):

    # Print environment variables for debugging
    print_env()

    # Get config data
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    tile_count = config['settings']['tile_count']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    batch_size = config['settings']['batch_size']

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create output path
        export_path = temp_path / 'export'
        stage_path = export_path / 'stage.usd'
        archive_path = temp_path / 'archive.tar.gz'
        relative_stage_path = stage_path.relative_to(export_path)
        relative_archive_path = archive_path.relative_to(temp_path)

        # Store stage config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            entity = dict(
                uri = str(entity_uri),
                department = department_name
            ),
            first_frame = first_frame,
            last_frame = last_frame,
            variant_name = variant_name,
            render_settings_path = path_str(render_settings_path),
            output_path = path_str(to_windows_path(stage_path))
        ))
    
        # Export the USD stage
        result = hython.run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(config_path))
            ],
            env = get_hython_env(api)
        )

        # Check if hython process succeeded
        if result != 0:
            return _error(f'Hython export failed with return code: {result}')

        # Check if temp stage was generated
        if not stage_path.exists():
            return _error(f'Stage not exported: {stage_path}')
        
        # Compress the stage
        with tarfile.open(archive_path, 'w:gz') as archive_file:
            for file_path in _walk_path(export_path):
                archive_file_path = file_path.relative_to(export_path)
                archive_file.add(file_path, archive_file_path)
        
        # Submit the render pipe tasks
        tasks = config['tasks'].copy()
        tasks.pop('stage')
        render_job.submit(dict(
            entity = dict(
                uri = str(entity_uri),
                department = department_name
            ),
            settings = dict(
                user_name = user_name,
                purpose = purpose,
                priority = priority,
                pool_name = pool_name,
                variant_name = variant_name,
                render_department_name = render_department_name,
                render_settings_path = path_str(render_settings_path),
                archive_path = path_str(relative_archive_path),
                input_path = path_str(relative_stage_path),
                tile_count = tile_count,
                first_frame = first_frame,
                last_frame = last_frame,
                step_size = step_size,
                batch_size = batch_size
            ),
            tasks = tasks
        ), {
            render_settings_path: render_settings_path,
            archive_path: relative_archive_path
        })

    # Done
    print('Success')
    return 0

def cli():
    return _common.run_task_cli(_spec.is_valid_config, main)

if __name__ == '__main__':
    _common.configure_logging()
    sys.exit(cli())