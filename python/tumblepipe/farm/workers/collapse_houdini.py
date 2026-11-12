"""The hython half of the collapse worker: snapshot the staged stage.

Runs inside hython because collapsing composes the staged build through USD
and the project's resolver. Writes the collapsed inputs into ``output_dir``
and a ``result.json`` (``preview.COLLAPSE_RESULT_NAME``) naming them; ``collapse.py`` reads that back and submits
the preview jobs. Nothing is submitted from here: Deadline stays on the
plain-python side, as it does for the stage worker.

result.json = {
    'render_inputs': {'channel': 'collapsed_stage_<channel>.usda'},
    'playblast_input': 'collapsed_playblast.usda',
    'turntables': {                      # an asset's render (#53), instead
        'channel': {                     # of render_inputs
            'folder': 'turntable_<channel>',            # bundled whole
            'stage': 'turntable_<channel>/stage.usd',
            'render_settings': 'render_settings_<channel>.json'
        }
    }
}

An asset's turntable is exported here too, not by an export job: the
exported stage sublayers the asset snapshot by a relative path, so the two
must sit in one folder that travels into the render job's data dir together.
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
from tumblepipe.pipe.houdini.nodes import NodeError


def _turntables(entity_uri: Uri, render: dict, output_dir: Path) -> dict:
    """Snapshot each channel of the asset, build its turntable and export it.

    The export is the export job's own (``export_houdini.main``): the
    render settings' overrides applied on the studio's settings prim, the
    AOVs pruned to the studio's, the frames exported in batches, and the
    studio's embedded HDRI and textures copied beside the stage.
    """
    from tumblepipe.config.timeline import BlockRange, require_fps
    from tumblepipe.farm.workers import export_houdini
    from tumblepipe.pipe import turntable
    from tumblepipe.pipe.duplicate import prim_path_for
    from tumblepipe.pipe.houdini import turntable_workfile

    inputs = preview.collapse_turntable_inputs(
        entity_uri, render['channel_names'], render['department'],
        output_dir, {},
    )
    turntables = {}
    for channel_name, asset_name in inputs.items():
        workfile = output_dir / turntable.workfile_name(channel_name)
        aov_names = turntable_workfile.build(
            asset_name, prim_path_for(entity_uri), workfile,
        )
        settings_name = turntable.render_settings_name(channel_name)
        store_json(output_dir / settings_name, dict(
            channel_names=[turntable.render_layer(channel_name)],
            aov_names=aov_names,
            overrides=render['overrides'],
        ))
        stage = turntable.stage_name(channel_name)
        code = export_houdini.main(
            BlockRange(render['first_frame'], render['last_frame']),
            render['batch_size'],
            require_fps(entity_uri),
            output_dir / settings_name,
            workfile,
            turntable.export_node_path(),
            output_dir / stage,
        )
        if code != 0:
            raise preview.PreviewError(
                f'Could not export the {channel_name} turntable of {entity_uri}'
            )
        turntables[channel_name] = dict(
            folder=turntable.folder_name(channel_name),
            stage=stage,
            render_settings=settings_name,
        )
    return turntables


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
        if 'render' in config and config['render'].get('turntable'):
            headline('Collapse the asset and build its turntables')
            result['turntables'] = _turntables(entity_uri, config['render'], output_dir)
        elif 'render' in config:
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
                light=collapse_spec.playblast_look(config['playblast'])['light'],
            )
    except preview.PreviewError as failure:
        return error(str(failure))
    except NodeError as failure:
        # The turntable's studio would not cook (its own words).
        return error(f'The turntable of {entity_uri} does not cook: {failure}')
    store_json(output_dir / preview.COLLAPSE_RESULT_NAME, result)
    print(json.dumps(result, indent=4))
    return 0


if __name__ == '__main__':
    hou.exit(worker.run(
        collapse_spec.is_valid_houdini_config, main, frames=False
    ))
