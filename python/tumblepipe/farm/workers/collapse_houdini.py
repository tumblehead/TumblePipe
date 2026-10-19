"""The hython half of the collapse worker: snapshot the staged stage.

Runs inside hython because collapsing composes the staged build through USD
and the project's resolver. Writes the collapsed inputs into ``output_dir``
and a ``result.json`` (``preview.COLLAPSE_RESULT_NAME``) naming them; ``collapse.py`` reads that back and submits
the preview jobs. Nothing is submitted from here: Deadline stays on the
plain-python side, as it does for the stage worker.

result.json = {
    'render_inputs': {'channel': 'collapsed_stage_<channel>.usda'},
    'playblast_input': 'collapsed_playblast.usda'
}
"""

from pathlib import Path
import json

import hou

from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
from tumblepipe.farm import worker
from tumblepipe.farm.pipelines import preview
from tumblepipe.farm.specs import collapse as collapse_spec
from tumblepipe.farm.worker import error, headline


def main(config: dict) -> int:
    headline('Config')
    print(json.dumps(config, indent=4))
    output_dir = Path(config['output_dir'])
    entity_uri = Uri.parse(config['entity']['uri'])
    output_dir.mkdir(parents=True, exist_ok=True)
    # The collapse functions register what they write for bundling; this
    # side only needs the names, so the paths dict is discarded.
    paths = {}
    result = {}
    try:
        if 'render' in config:
            headline('Collapse render inputs')
            render = config['render']
            result['render_inputs'] = preview.collapse_render_inputs(
                entity_uri,
                render['channel_names'],
                render['department'],
                render['overrides'],
                output_dir,
                paths,
            )
        if 'playblast' in config:
            headline('Collapse playblast input')
            result['playblast_input'] = preview.collapse_playblast_input(
                entity_uri,
                config['playblast']['department'],
                output_dir,
                paths,
            )
    except preview.PreviewError as failure:
        return error(str(failure))
    store_json(output_dir / preview.COLLAPSE_RESULT_NAME, result)
    print(json.dumps(result, indent=4))
    return 0


if __name__ == '__main__':
    hou.exit(worker.run(
        collapse_spec.is_valid_houdini_config, main, frames=False
    ))
