"""Export a workfile's stage in hython (``export_houdini.py``) and submit
the render pipeline over it: the farm half of ``th::lookdev_studio``."""

from tempfile import TemporaryDirectory
from pathlib import Path
import sys

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_hython_env, job_data_dir, print_env
from tumblepipe.farm.pipelines import render as render_pipeline
from tumblepipe.farm.specs import export as export_spec
from tumblepipe.farm.worker import error

SCRIPT_PATH = Path(__file__).parent / 'export_houdini.py'


def main(config: dict, _frame_span: BlockRange) -> int:

    # Print environment variables for debugging
    print_env()

    # Get config data. The workfile and the render settings are bundled into
    # the job's data dir and addressed relative to it.
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    channel_name = settings['channel_name']
    render_settings_path = job_data_dir() / settings['render_settings_path']
    first_frame = settings['first_frame']
    last_frame = settings['last_frame']
    batch_size = settings['batch_size']
    input_path = job_data_dir() / config['tasks']['export']['input_path']
    node_path = config['tasks']['export']['node_path']

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create output path
        export_path = temp_path / 'export'
        stage_path = export_path / 'stage.usd'
        relative_export_path = export_path.relative_to(temp_path)
        relative_stage_path = stage_path.relative_to(temp_path)

        # Store the hython half's config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            first_frame = first_frame,
            last_frame = last_frame,
            batch_size = batch_size,
            render_settings_path = path_str(render_settings_path),
            input_path = path_str(input_path),
            node_path = node_path,
            output_path = path_str(stage_path)
        ))

        # Export the USD stage
        hython.run(
            SCRIPT_PATH,
            [path_str(config_path)],
            env = get_hython_env()
        )

        # Check if stage was exported
        if not stage_path.exists():
            return error(f'Stage not exported: {stage_path}')

        # Submit the render pipeline over the exported stage
        tasks = config['tasks'].copy()
        tasks.pop('export')
        render_pipeline.submit(dict(
            entity = dict(
                uri = str(entity_uri),
                department = department_name
            ),
            settings = dict(
                user_name = settings['user_name'],
                purpose = settings['purpose'],
                pool_name = settings['pool_name'],
                channel_names = [channel_name],
                render_department_name = settings['render_department_name'],
                render_settings_path = path_str(render_settings_path),
                input_paths = {channel_name: path_str(relative_stage_path)},
                tile_count = settings['tile_count'],
                first_frame = first_frame,
                last_frame = last_frame,
                step_size = settings['step_size'],
                batch_size = batch_size,
                # An exported (turntable) render is never synced to edit
                copy_to_edit = False
            ),
            tasks = tasks
        ), {
            render_settings_path: Path(settings['render_settings_path']),
            export_path: relative_export_path
        })

    # Done
    print('Success')
    return 0


if __name__ == '__main__':
    sys.exit(worker.run(export_spec.is_valid_context, main))
