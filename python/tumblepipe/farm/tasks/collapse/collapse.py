"""Collapse task: snapshot a freshly published stage, then submit its previews.

Runs after the submission's publish chain and staged build (see ``_spec``).
The snapshot is taken by hython (``collapse_houdini.py``); this plain-python
side then submits the render and playblast jobs with those snapshots bundled,
exactly as ``batch_submit`` does for an entity it does not publish.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import logging
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
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import _common
from tumblepipe.farm.jobs.houdini import _preview
from tumblepipe.farm.jobs.houdini.render import job as render_job
from tumblepipe.farm.jobs.houdini.playblast import job as playblast_job
from tumblepipe.farm.tasks.env import get_hython_env, print_env
from tumblepipe.farm.tasks.collapse import _spec
from tumblepipe.config.channels import read_channel_name_list

_error = _common.error

SCRIPT_PATH = Path(__file__).parent / 'collapse_houdini.py'
RESULT_NAME = 'result.json'


def _submit_render(config, result, output_dir) -> int:
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    render = config['render']
    channels = read_channel_name_list(render, where='collapse render config')
    paths = {}
    # AOV names are read now, after the publish, so an AOV the publish added
    # is rendered.
    settings_path = _preview.write_render_settings(
        output_dir, paths, channels, _preview.aov_names(entity_uri, channels),
    )
    input_paths = result['render_inputs']
    for name in input_paths.values():
        paths[output_dir / name] = Path(name)
    return render_job.submit(_preview.render_config(
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
        task_key=render['task_key'],
    ), paths)


def _submit_playblast(config, result, output_dir) -> int:
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    playblast = config['playblast']
    name = result['playblast_input']
    paths = {output_dir / name: Path(name)}
    try:
        playblast_config = _preview.playblast_config(
            entity_uri,
            department=playblast['department'],
            input_path=name,
            user_name=config['settings']['user_name'],
            pool_name=playblast['pool_name'],
            priority=playblast['priority'],
            res=playblast['res'],
        )
    except _preview.PreviewError as error:
        return _error(str(error))
    return playblast_job.submit(playblast_config, paths)


def main(config):
    print_env()

    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / 'config.json'
        store_json(config_path, config)
        output_dir = temp_path / 'collapsed'

        # Snapshot in hython
        result_code = Hython().run(
            to_windows_path(SCRIPT_PATH),
            [
                path_str(to_windows_path(config_path)),
                path_str(to_windows_path(output_dir)),
            ],
            env = get_hython_env(api)
        )
        if result_code != 0:
            return _error(f'Hython collapse failed with return code: {result_code}')
        result = load_json(output_dir / RESULT_NAME)
        if result is None:
            return _error(f'Collapse wrote no result: {output_dir / RESULT_NAME}')

        # Submit the previews with the snapshots bundled. Both are attempted
        # even if the first fails, so one bad preview does not cost the other.
        failed = []
        for kind, submit in (('render', _submit_render), ('playblast', _submit_playblast)):
            if kind not in config:
                continue
            try:
                code = submit(config, result, output_dir)
            except Exception as error:
                logging.exception(f'Submitting the {kind} failed: {error}')
                code = 1
            if code != 0:
                failed.append(kind)
        if failed:
            return _error(f"Could not submit: {', '.join(failed)}")

    return 0


def cli():
    return _common.run_task_cli(_spec.is_valid_config, main)


if __name__ == '__main__':
    _common.configure_logging()
    sys.exit(cli())
