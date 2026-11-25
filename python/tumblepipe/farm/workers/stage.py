"""Build a shot's render stages in hython (``stage_houdini.py``), one per
channel, and submit the render pipeline over them: a standalone render."""

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
from tumblepipe.farm.pipelines import preview
from tumblepipe.farm.pipelines import render as render_pipeline
from tumblepipe.farm.specs import stage as stage_spec
from tumblepipe.farm.worker import error

SCRIPT_PATH = Path(__file__).parent / 'stage_houdini.py'


def main(config: dict, _frame_span: BlockRange) -> int:

    # Print environment variables for debugging
    print_env()

    # Get config data
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']
    settings = config['settings']
    channel_names = settings['channel_names']
    # Bundled into the job's data dir and addressed relative to it.
    relative_render_settings_path = Path(settings['render_settings_path'])
    render_settings_path = job_data_dir() / relative_render_settings_path

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Create one output stage per channel. A single stage composing
        # every channel would render the last channel's opinions for all
        # of them: a channel is different content, which husk cannot
        # select. (What a render pass selects, visibility and holdouts,
        # each stage carries for every render layer: stage_houdini.)
        export_path = temp_path / 'export'
        relative_export_path = export_path.relative_to(temp_path)
        stage_paths = {
            channel_name: export_path / f'stage_{channel_name}.usd'
            for channel_name in channel_names
        }
        relative_stage_paths = {
            channel_name: stage_path.relative_to(temp_path)
            for channel_name, stage_path in stage_paths.items()
        }

        # Store stage config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            entity = dict(
                uri = str(entity_uri),
                department = department_name
            ),
            settings = dict(
                first_frame = settings['first_frame'],
                last_frame = settings['last_frame'],
                render_settings_path = path_str(render_settings_path),
                render_department_name = settings['render_department_name']
            ),
            output_paths = {
                channel_name: path_str(stage_path)
                for channel_name, stage_path in stage_paths.items()
            }
        ))

        # Export the USD stages
        hython.run(
            SCRIPT_PATH,
            [
                path_str(config_path)
            ],
            env = get_hython_env()
        )

        # Check if temp stages were generated
        for channel_name, stage_path in stage_paths.items():
            if not stage_path.exists():
                return error(f'Stage not exported: {stage_path}')

        # Submit the render pipeline over the stages: the render request
        # this job carries (specs.stage), without its own stage task.
        tasks = {key: task for key, task in config['tasks'].items() if key != 'stage'}
        render_pipeline.submit(preview.render_config(
            dict(config, tasks = tasks),
            {
                channel_name: path_str(relative_stage_path)
                for channel_name, relative_stage_path in relative_stage_paths.items()
            },
            render_settings_path,
        ), {
            render_settings_path: relative_render_settings_path,
            export_path: relative_export_path
        })

    # Done
    return 0

if __name__ == '__main__':
    sys.exit(worker.run(stage_spec.is_valid_context, main))
