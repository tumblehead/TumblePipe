"""The hython half of the collapse task: snapshot the staged stage.

Runs inside hython because collapsing composes the staged build through USD
and the project's resolver. Writes the collapsed inputs into ``output_dir``
and a ``result.json`` naming them; ``collapse.py`` reads that back and submits
the preview jobs. Nothing is submitted from here: Deadline stays on the
plain-python side, as it does for the stage task.

result.json = {
    'render_inputs': {'channel': 'collapsed_stage_<channel>.usda'},
    'playblast_input': 'collapsed_playblast.usda'
}
"""

from pathlib import Path
import json

import hou

from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import read_channel_name_list
from tumblepipe.farm.jobs.houdini import _preview
from tumblepipe.farm.tasks.collapse._spec import is_valid_config

RESULT_NAME = 'result.json'


def _headline(title):
    print(f' {title} '.center(80, '='))


def _error(msg):
    print(f'ERROR: {msg}')
    return 1


def main(config: dict, output_dir: Path) -> int:
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    output_dir.mkdir(parents=True, exist_ok=True)
    # The collapse functions register what they write for bundling; this
    # side only needs the names, so the paths dict is discarded.
    paths = {}
    result = {}
    try:
        if 'render' in config:
            _headline('Collapse render inputs')
            render = config['render']
            result['render_inputs'] = _preview.collapse_render_inputs(
                entity_uri,
                read_channel_name_list(render, where='collapse render config'),
                render['department'],
                render.get('overrides', {}),
                output_dir,
                paths,
            )
        if 'playblast' in config:
            _headline('Collapse playblast input')
            result['playblast_input'] = _preview.collapse_playblast_input(
                entity_uri,
                config['playblast']['department'],
                output_dir,
                paths,
            )
    except _preview.PreviewError as error:
        return _error(str(error))
    store_json(output_dir / RESULT_NAME, result)
    print(json.dumps(result, indent=4))
    return 0


def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script_path', type=str)
    parser.add_argument('config_path', type=str)
    parser.add_argument('output_dir', type=str)
    args = parser.parse_args()

    config = load_json(Path(args.config_path))
    if config is None:
        return _error(f'Config file not found: {args.config_path}')
    if not is_valid_config(config):
        return _error(f'Invalid config file: {args.config_path}')

    _headline('Config')
    print(json.dumps(config, indent=4))
    return main(config, Path(args.output_dir))


if __name__ == '__main__':
    hou.exit(cli())
