"""The hython half of the stage worker: build and export one render stage
per channel (``pipe.houdini.render_stage``), each carrying a RenderPass per
render layer (``pipe.usd.render_passes``)."""

from pathlib import Path
import json

import hou

from tumblepipe.api import path_str
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import list_channel_layers
from tumblepipe.pipe.houdini import render_stage
from tumblepipe.pipe.render_layers import RenderPassError
from tumblepipe.pipe.usd.render_settings import (
    RenderSettingsError,
    find_render_settings_prim_path,
)
from tumblepipe.farm import worker
from tumblepipe.farm.specs import stage as stage_spec
from tumblepipe.farm.worker import error, headline

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _author_render_passes(stage_path: Path, layers) -> None:
    """Author the render layers' passes into the exported stage's root layer."""
    from pxr import Usd
    from tumblepipe.pipe.usd.render_passes import apply_render_layers
    stage = Usd.Stage.Open(path_str(stage_path), Usd.Stage.LoadAll)
    apply_render_layers(stage, layers, find_render_settings_prim_path(stage))
    stage.GetRootLayer().Save()

def main(
    shot_uri: Uri,
    render_range: BlockRange,
    render_settings_path: Path,
    output_paths: dict[str, Path],
    render_department_name: str
    ) -> int:
    headline('Stage Shot')

    # Prepare scene
    scene_node = hou.node('/stage')
    layers = list_channel_layers(shot_uri)

    # Build and export one independent graph per channel. Chaining the
    # channel graphs would compose every channel into a single stage, so
    # each render layer would get the last channel's opinions.
    for channel_name, output_path in output_paths.items():
        headline(f'Channel {channel_name}')

        prev_node = render_stage.build_render_stage_graph(
            scene_node,
            shot_uri,
            channel_name,
            render_settings_path,
            name_prefix = f'__{channel_name}_',
            render_department_name = render_department_name
        )

        # Setup export node
        export_node = scene_node.createNode(
            'filecache', f'__{channel_name}_export'
        )
        export_node.parm('filemethod').set(1)
        export_node.parm('trange').set(1)
        export_node.parm('f1').deleteAllKeyframes()
        export_node.parm('f2').deleteAllKeyframes()
        export_node.parm('f3').set(1)
        export_node.parm('striplayerbreaks').set(False)
        _connect(prev_node, export_node)

        # Export the USD stage
        export_node.parm('file').set(path_str(output_path))
        export_node.parm('f1').set(render_range.first_frame)
        export_node.parm('f2').set(render_range.last_frame)
        export_node.parm('execute').pressButton()

        # Verify the USD file was created
        if not output_path.exists():
            return error(f'Failed to export USD stage: {output_path}')

        # Render layers: a RenderPass per layer, for husk --pass.
        if layers:
            try:
                _author_render_passes(output_path, layers)
            except (RenderPassError, RenderSettingsError) as failure:
                return error(f'Cannot make the render layers of {shot_uri}: {failure}')

        print(f'Successfully exported USD stage: {output_path}')

    # Done
    return 0

def from_config(config: dict) -> int:
    """Stage as the worker's config describes."""
    headline('Config')
    print(json.dumps(config, indent=4))

    entity_uri = Uri.parse(config['entity']['uri'])
    if entity_uri.segments[0] != 'shots':
        return error(f'Invalid entity: {entity_uri}')

    settings = config['settings']
    return main(
        entity_uri,
        BlockRange(settings['first_frame'], settings['last_frame']),
        Path(settings['render_settings_path']),
        {
            channel_name: Path(output_path)
            for channel_name, output_path in config['output_paths'].items()
        },
        settings['render_department_name']
    )

if __name__ == '__main__':
    hou.exit(worker.run(
        stage_spec.is_valid_houdini_config, from_config, frames=False
    ))
