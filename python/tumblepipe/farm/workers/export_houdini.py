"""The hython half of the export worker: export a workfile node's stage,
pruned to the render settings' AOVs, in batches stitched back together."""

from pathlib import Path
import json

import hou

from tumblepipe.api import path_str
from tumblepipe.util.io import load_json
from tumblepipe.pipe.houdini.render_stage import get_render_settings_script as _get_render_settings_script
from tumblepipe.config.timeline import BlockRange, require_fps
from tumblepipe.pipe.houdini import playbar, prim_queries
from tumblepipe.pipe.usd.stitch import (
    stitch_usd_directories,
    calculate_chunks,
    flatten_sidecar_directories,
)
from tumblepipe.farm import worker
from tumblepipe.farm.specs import export as export_spec
from tumblepipe.farm.worker import error, headline

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _get_aov_names(render_settings_path: Path) -> set:
    render_settings_data = load_json(render_settings_path)
    return set(render_settings_data['aov_names'])

def main(
    render_range: BlockRange,
    batch_size: int,
    render_settings_path: Path,
    input_path: Path,
    node_path: str,
    output_path: Path
    ) -> int:

    # Open the input scene
    if not input_path.exists():
        return error(f'Input file not found: {input_path}')
    hou.hipFile.load(path_str(input_path))

    # Find the node to export
    input_node = hou.node(node_path)
    if input_node is None:
        return error(f'Node not found: {node_path}')
    
    # Prepare scene
    scene_node = input_node.parent()
    prev_node = input_node
    
    # Setup edit render settings
    edit_render_settings_node = scene_node.createNode(
        'pythonscript',
        '__edit_settings'
    )
    edit_render_settings_node.parm('python').set(
        _get_render_settings_script(render_settings_path)
    )
    _connect(prev_node, edit_render_settings_node)
    prev_node = edit_render_settings_node

    # Setup AOV pruning
    included_aov_names = _get_aov_names(render_settings_path)
    stage = input_node.stage()
    if stage is None:
        # Same disconnected-node case the interactive executor skips over.
        # A worker has nobody watching, so it fails - but it must say why
        # rather than dying on None.GetPseudoRoot().
        return error(
            f'Node has no stage input connected: {node_path}. '
            'Check for a disconnected export node in the workfile.'
        )
    root = stage.GetPseudoRoot()
    aov_paths = {
        aov_path.rsplit('/', 1)[1]: aov_path
        for aov_path in prim_queries.list_render_vars(root)
    }
    excluded_aov_names = set(aov_paths.keys()) - included_aov_names
    prune_aovs_node = scene_node.createNode('prune', '__prune_aovs')
    prune_aovs_node.parm('primpattern1').set(
        ' '.join([
            aov_paths[aov_name]
            for aov_name in excluded_aov_names
        ])
    )
    _connect(prev_node, prune_aovs_node)
    prev_node = prune_aovs_node

    # Prepare the export
    export_node = scene_node.createNode('filecache', '__export')
    export_node.parm('filemethod').set(1)
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f3').set(1)
    export_node.parm('striplayerbreaks').set(False)
    _connect(prev_node, export_node)

    # Set FPS before the range so the range can't be shifted by a later
    # fps change (see playbar.set_fps; consistent with import_shot).
    playbar.set_fps(require_fps())
    playbar.set_block_range(render_range)

    # Export the USD stage
    if batch_size > 0:
        # Batched export: export chunks to separate directories then stitch
        import shutil
        chunks = calculate_chunks(render_range.first_frame, render_range.last_frame, batch_size)
        output_file_name = output_path.name  # e.g., "stage.usd"
        output_dir = output_path.parent

        # Create chunks directory
        chunks_dir = output_dir / 'chunks'
        chunks_dir.mkdir(exist_ok=True)
        chunk_dirs = []

        for chunk_start, chunk_end in chunks:
            # Each chunk exports to its own subdirectory
            chunk_name = f"{chunk_start:04d}-{chunk_end:04d}"
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir(exist_ok=True)
            chunk_main_path = chunk_dir / output_file_name

            export_node.parm('file').set(path_str(chunk_main_path))
            export_node.parm('f1').set(chunk_start)
            export_node.parm('f2').set(chunk_end)
            export_node.parm('execute').pressButton()

            if not chunk_main_path.exists():
                return error(f'Failed to export USD chunk: {chunk_main_path}')

            # Flatten .usd.textures sidecars before stitching, matching the
            # interactive exporter, so the stitch carries textures through to
            # the output beside the main layer instead of dropping them.
            flatten_sidecar_directories(chunk_dir)

            print(f'Exported chunk: {chunk_dir}')
            chunk_dirs.append(chunk_dir)

        # Stitch all chunks (main file + sidecar directories)
        print(f'Stitching {len(chunk_dirs)} chunks into: {output_dir}')
        stitch_usd_directories(chunk_dirs, output_file_name, output_dir)

        # Clean up chunks directory
        shutil.rmtree(chunks_dir)
        print('Cleaned up chunks directory')
    else:
        # Standard export: export all frames at once
        export_node.parm('file').set(path_str(output_path))
        export_node.parm('f1').set(render_range.first_frame)
        export_node.parm('f2').set(render_range.last_frame)
        export_node.parm('execute').pressButton()

    # Verify the USD file was created
    if not output_path.exists():
        return error(f'Failed to export USD stage: {output_path}')

    print(f'Successfully exported USD stage: {output_path}')

    # Done
    return 0

def from_config(config: dict) -> int:
    """Export as the worker's config describes."""
    headline('Config')
    print(json.dumps(config, indent=4))
    return main(
        BlockRange(config['first_frame'], config['last_frame']),
        config['batch_size'],
        Path(config['render_settings_path']),
        Path(config['input_path']),
        config['node_path'],
        Path(config['output_path'])
    )

if __name__ == '__main__':
    hou.exit(worker.run(
        export_spec.is_valid_houdini_config, from_config, frames=False
    ))
