from tempfile import TemporaryDirectory
from pathlib import Path
import sys

# Add tumblepipe python packages path
tumblepipe_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblepipe_packages_path not in sys.path:
    sys.path.append(str(tumblepipe_packages_path))

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
from tumblepipe.farm.jobs.houdini.render import job as render_job
from tumblepipe.farm.tasks.env import get_hython_env, print_env
from tumblepipe.farm.tasks.stage import _spec

_error = _common.error

SCRIPT_PATH = Path(__file__).parent / 'stage_houdini.py'
def main(config):

    # Print environment variables for debugging
    print_env()

    # Get config data
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_names = config['settings']['variant_names']
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

        # Create one output stage per variant. A single stage composing
        # every variant would render the last variant's opinions for all
        # of them (husk has no variant selection).
        export_path = temp_path / 'export'
        relative_export_path = export_path.relative_to(temp_path)
        stage_paths = {
            variant_name: export_path / f'stage_{variant_name}.usd'
            for variant_name in variant_names
        }
        relative_stage_paths = {
            variant_name: stage_path.relative_to(temp_path)
            for variant_name, stage_path in stage_paths.items()
        }

        # Store stage config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            entity = dict(
                uri = str(entity_uri),
                department = department_name
            ),
            settings = dict(
                first_frame = first_frame,
                last_frame = last_frame,
                render_settings_path = path_str(render_settings_path),
                render_department_name = render_department_name
            ),
            output_paths = {
                variant_name: path_str(to_windows_path(stage_path))
                for variant_name, stage_path in stage_paths.items()
            }
        ))

        # Export the USD stages
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

        # Check if temp stages were generated
        for variant_name, stage_path in stage_paths.items():
            if not stage_path.exists():
                return _error(f'Stage not exported: {stage_path}')

        # Submit the render pipe tasks with all render layers
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
                pool_name = pool_name,
                variant_names = variant_names,
                render_department_name = render_department_name,
                render_settings_path = path_str(render_settings_path),
                input_paths = {
                    variant_name: path_str(relative_stage_path)
                    for variant_name, relative_stage_path in relative_stage_paths.items()
                },
                tile_count = tile_count,
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
    return 0

def cli():
    return _common.run_task_cli(_spec.is_valid_config, main)

if __name__ == '__main__':
    _common.configure_logging()
    sys.exit(cli())