"""Collapse worker: snapshot a freshly published stage, then submit its previews.

Runs after the submission's publish chain and staged build (``specs.collapse``).
The snapshot is taken by hython (``collapse_houdini.py``); this plain-python
side then submits the render and playblast jobs with those snapshots bundled,
exactly as ``batch_submit`` does for an entity it does not publish.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_hython_env, print_env
from tumblepipe.farm.pipelines import preview
from tumblepipe.farm.pipelines import render as render_pipeline
from tumblepipe.farm.pipelines import playblast as playblast_pipeline
from tumblepipe.farm.specs import collapse as collapse_spec
from tumblepipe.farm.worker import error

SCRIPT_PATH = Path(__file__).parent / 'collapse_houdini.py'


def _submit_render(config, result, output_dir) -> None:
    entity_uri = Uri.parse(config['entity']['uri'])
    render = config['render']
    channels = render['channel_names']
    paths = {}
    # AOV names are read now, after the publish, so an AOV the publish added
    # is rendered.
    settings_path = preview.write_render_settings(
        output_dir, paths, channels, preview.aov_names(entity_uri, channels),
    )
    input_paths = result['render_inputs']
    for name in input_paths.values():
        paths[output_dir / name] = Path(name)
    render_pipeline.submit(preview.render_config(
        entity_uri,
        department=render['department'],
        channels=channels,
        input_paths=input_paths,
        render_settings_path=settings_path,
        user_name=config['settings']['user_name'],
        pool_name=render['pool_name'],
        priority=render['priority'],
        tile_count=render['tile_count'],
        first_frame=render['first_frame'],
        last_frame=render['last_frame'],
        batch_size=render['batch_size'],
        denoise=render['denoise'],
        copy_to_edit=render['copy_to_edit'],
        task_keys=render['task_keys'],
    ), paths)


def _submit_playblast(config, result, output_dir) -> None:
    entity_uri = Uri.parse(config['entity']['uri'])
    playblast = config['playblast']
    name = result['playblast_input']
    paths = {output_dir / name: Path(name)}
    playblast_pipeline.submit(preview.playblast_config(
        entity_uri,
        department=playblast['department'],
        input_path=name,
        user_name=config['settings']['user_name'],
        pool_name=playblast['pool_name'],
        priority=playblast['priority'],
        res=playblast['res'],
    ), paths)


def main(config: dict, _frame_span: BlockRange) -> int:
    print_env()

    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / 'config.json'
        output_dir = temp_path / 'collapsed'
        store_json(config_path, dict(config, output_dir = path_str(output_dir)))

        # Snapshot in hython
        Hython().run(
            SCRIPT_PATH,
            [path_str(config_path)],
            env = get_hython_env()
        )
        result = load_json(output_dir / preview.COLLAPSE_RESULT_NAME)

        # Submit the previews with the snapshots bundled. Both are attempted
        # even if the first fails, so one bad preview does not cost the other.
        failed = []
        for kind, submit in (('render', _submit_render), ('playblast', _submit_playblast)):
            if kind not in config:
                continue
            try:
                submit(config, result, output_dir)
            except Exception as failure:
                logging.exception(f'Submitting the {kind} failed: {failure}')
                failed.append(kind)
        if failed:
            return error(f"Could not submit: {', '.join(failed)}")

    return 0


if __name__ == '__main__':
    sys.exit(worker.run(collapse_spec.is_valid_context, main))
