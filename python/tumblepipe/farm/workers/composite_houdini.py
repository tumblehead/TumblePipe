"""The hython half of the composite worker: load the comp workfile, pin its
imports to the versions the submit froze, and render each th::export_comp
for the frames still missing.

Every frame goes through ``exr.stamp_acescg`` on its way to the network: a
COP ROP writes no colour stamp, and an unstamped frame reads as Rec.709 in
RV and Nuke.

A re-comp (Farm Submit's Comp column) froze nothing: ``imports`` is empty,
so every import of the shot resolves on its own settings now, and its
export has no node path, so the shot's export_comp on that layer is found.
Either way each written layer's ``context.json`` records what was read.

A comp of its submit's render (#80: a workfile comp with no
th::import_render, ``renders`` set) points the Slap Comp Import nodes its
exports read at the frames that render wrote (``cops.slap_comp``), and
records those as what it read.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import json
import os

import hou

from tumblepipe.api import path_str, api
from tumblepipe.apps import exr
from tumblepipe.util.io import load_json_if_exists, store_json
from tumblepipe.pipe import comp
from tumblepipe.config.timeline import BlockRange, require_fps
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import playbar
from tumblepipe.farm import worker
from tumblepipe.farm.frames import frame_file, HOUDINI_FRAME_TOKEN
from tumblepipe.farm.specs import composite as composite_spec
from tumblepipe.apps.deadline import log_progress
from tumblepipe.farm.worker import ensure_dir, error, headline


def _point_at_shot(native, entity_uri: Uri):
    """A node on 'from_context' addresses the job's shot: the worker loads
    a copy of the workfile from the job's data folder, where 'from_context'
    resolves no shot."""
    if native.parm('entity').eval() == 'from_context':
        native.parm('entity').set(str(entity_uri))


def _own_frames(native):
    """An import reads the shot's renders on the shot's own frames: the
    worker comps and writes those, whatever shared timeline the workfile's
    Multi lays the shot on (``cops.import_render``'s Time Offset)."""
    from tumblepipe.pipe.houdini.time_offset import TimeOffsetSource
    parm = native.parm('time_offset_mode')
    if parm is not None:
        parm.set(TimeOffsetSource.NONE)


def no_frame_cache():
    """Turn every import's frame cache off (#73): the worker cooks each
    frame once, so the Cache COPs would only hold frames (up to Max
    Frames per read, gigabytes at 1080p) that are never asked for again.
    The cache never changes a pixel."""
    from tumblepipe.pipe.houdini.cops import import_render
    import tumblepipe.pipe.houdini.nodes as ns
    for native in ns.list_by_node_type(import_render.TYPE_NAME, 'Cop'):
        parm = native.parm(import_render.CACHE_PARM)
        if parm is not None:
            parm.set(0)


def resolve_imports(entity_uri: Uri) -> tuple[dict | None, str | None]:
    """A re-comp's imports: every import_render of the shot resolved on
    its own settings now. ``(plan, None)`` with what each read, or
    ``(None, error)``."""
    from tumblepipe.pipe.houdini.cops import import_render
    import tumblepipe.pipe.houdini.nodes as ns
    for native in ns.list_by_node_type(import_render.TYPE_NAME, 'Cop'):
        _point_at_shot(native, entity_uri)
        _own_frames(native)
    states = []
    for node in import_render.find_imports(entity_uri):
        try:
            node_states = node.import_states()
        except ns.NodeError as exc:
            return None, str(exc)
        for state in node_states:
            print(f'  {node.path()}: {state.channel} {state.resolved_department} {state.resolved_version}')
        states += node_states
    if not states:
        return None, f'the workfile has no th::import_render for {entity_uri}'
    return comp.freeze_plan(states), None


def report_uncovered(entity_uri: Uri, reads: dict, frames) -> list[str]:
    """Print, and return, a warning line per channel whose render does not
    cover all of ``frames``."""
    from tumblepipe.pipe.paths import get_render_context
    try:
        renders = get_render_context(entity_uri, purpose='render').renders
        ranges = comp.read_ranges(renders, reads)
    except Exception as exc:
        print(f'WARNING: could not read the rendered ranges: {exc}')
        return []
    lines = comp.uncovered_message(comp.uncovered_frames(frames, ranges), ranges)
    for line in lines:
        print(f'WARNING: {line}; those frames comp over empty input')
    return lines


def find_export(layer: str, entity_uri: Uri):
    """The shot's export_comp on ``layer``; ``(node, None)`` or ``(None,
    error)``."""
    from tumblepipe.pipe.houdini.cops import export_comp
    import tumblepipe.pipe.houdini.nodes as ns
    found = []
    for node in export_comp.find_exports():
        _point_at_shot(node.native(), entity_uri)
        try:
            if node.get_entity_uri() != entity_uri: continue
        except ns.NodeError:
            continue
        if node.get_layer_name() == layer:
            found.append(node.native())
    if len(found) != 1:
        return None, (
            f'the workfile has {len(found)} th::export_comp nodes on layer '
            f'"{layer}" for {entity_uri}, not one'
        )
    return found[0], None


def record_reads(exports: list[dict], reads: dict):
    """Write what the comp read (``{channel: {department, version}}``,
    ``comp.provenance``) into each written layer's context.json (the
    version folder: the merged comp's frames sit in it, a channel's in its
    ``beauty`` folder)."""
    for export in exports:
        context_path = comp.layer_context_dir(export['output_path'], export['layer']) / 'context.json'
        context = load_json_if_exists(context_path) or dict()
        context['reads'] = reads
        store_json(context_path, context)


def pin_imports(imports: dict, entity_uri: Uri) -> str | None:
    """Set every import_render to the version the submit froze and point it
    there; the error text when one cannot be.

    The worker loads a copy of the workfile from the job's data folder, where
    'from_context' resolves no shot, so an import on it is pointed at the
    job's shot (one job comps one shot; a Multi's imports are pinned to
    their member already)."""
    from tumblepipe.pipe.houdini.cops.import_render import ImportRender
    import tumblepipe.pipe.houdini.nodes as ns
    # An all-channels import is frozen per channel (plan key
    # "<node path>#<channel>"): gather its pins first.
    for node_path, pins in comp.pins_by_node(imports).items():
        native = hou.node(node_path)
        if native is None:
            return f'Import not found: {node_path}'
        node = ImportRender(native)
        _point_at_shot(native, entity_uri)
        _own_frames(native)
        if node.is_all_channels():
            node.pin_channels(pins)
        else:
            (channel, (department, version)), = pins.items()
            if node.get_channel_name() != channel:
                return (
                    f'{node_path} reads channel "{node.get_channel_name()}", '
                    f'the submit froze "{channel}"'
                )
            node.pin(department, version)
        try:
            reads = node.execute()
        except ns.NodeError as exc:
            return str(exc)
        frozen = set(pins)
        if set(reads) != frozen:
            return (
                f'{node_path} reads channels {sorted(reads)}, the submit '
                f'froze {sorted(frozen)}'
            )
        for channel, resolved in reads.items():
            print(f'  {node_path}: {channel} {resolved.department} {resolved.version}')
    return None


def main(
    render_range: BlockRange,
    frames: list[int],
    receipt_path: Path,
    input_path: Path,
    exports: list[dict],
    imports: dict,
    entity_json: dict,
    renders: list | None = None
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Open the input file
    if not input_path.exists():
        return error(f'Input not found: {input_path}')
    hou.hipFile.load(path_str(input_path), suppress_save_prompt=True, ignore_load_warnings=True)

    # Set FPS before the range so the range can't be shifted by a later
    # fps change (see playbar.set_fps; consistent with import_shot).
    entity_uri = Uri.parse(entity_json['uri'])
    playbar.set_fps(require_fps(entity_uri))
    playbar.set_block_range(render_range)
    no_frame_cache()

    # Find each export's render node
    targets = []
    for export in exports:
        if export['node_path']:
            export_node = hou.node(export['node_path'])
        else:
            export_node, problem = find_export(export['layer'], entity_uri)
            if problem is not None:
                return error(problem)
        if export_node is None:
            return error(f'Export not found: {export["node_path"]}')
        render_node = export_node.node('render')
        if render_node is None:
            return error(f'{export_node.path()} has no render node')
        targets.append((export, export_node, render_node))

    # A comp of its submit's render reads that render through its Slap
    # Comp Imports (#80); otherwise pin the imports to what the submit
    # froze, or (a re-comp) resolve them now.
    if renders:
        headline('Reading the render of this submit')
        from tumblepipe.pipe.houdini.cops import slap_comp
        try:
            for line in slap_comp.swap_imports(
                entity_uri, renders, [node for _, node, _ in targets]
            ):
                print(f'  {line}')
        except (slap_comp.SceneCompError, comp.CompError) as exc:
            return error(str(exc))
        reads = comp.render_reads(renders)
    else:
        if imports:
            headline('Pinning imports')
            problem = pin_imports(imports, entity_uri)
            if problem is not None:
                return error(problem)
            plan = imports
        else:
            headline('Resolving imports')
            plan, problem = resolve_imports(entity_uri)
            if problem is not None:
                return error(problem)
        try:
            reads = comp.provenance(plan)
        except comp.CompError as exc:
            return error(str(exc))
    # A read's File COPs fill a frame it has no render of with empty
    # input (import_render.MISSING_EMPTY), so such a frame comps to
    # transparent black without failing: say so in the log (#43).
    report_uncovered(entity_uri, reads, render_range)

    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # {frame: {layer: (temp frame, output frame)}}
        written = {}
        for export, export_node, render_node in targets:
            layer = export['layer']
            headline(f'Rendering layer: {layer}')
            temp_frames_path = temp_path / layer / f'composite.{HOUDINI_FRAME_TOKEN}.exr'
            ensure_dir(temp_frames_path.parent)
            # The ROP sits inside the locked export_comp and follows its
            # render_output / render_range parms.
            export_node.parm('render_output').set(path_str(temp_frames_path))
            for frame_index in log_progress(render_range):
                if frame_index not in frames: continue
                print(f'  Rendering frame {frame_index} of layer {layer}')
                export_node.parmTuple('render_range').set((frame_index, frame_index))
                render_node.parm('execute').pressButton()
                temp_frame = frame_file(temp_frames_path, frame_index, HOUDINI_FRAME_TOKEN)
                if not temp_frame.exists():
                    errors = '; '.join(render_node.errors()) or 'no error reported'
                    return error(
                        f'Layer {layer} wrote no frame {frame_index}: {errors}'
                    )
                written.setdefault(frame_index, {})[layer] = (
                    temp_frame,
                    frame_file(Path(export['output_path']), frame_index),
                )

        # Stamp the colour space on the way to the network
        headline('Copying frames to the network')
        for frame_index, layers in written.items():
            for layer, (temp_frame, output_frame) in layers.items():
                print(f'  {output_frame}')
                ensure_dir(output_frame.parent)
                exr.stamp_acescg(temp_frame, output_frame)

        # What the comp read, then the receipts: a frame is done when every
        # layer of it is written
        record_reads(exports, reads)
        headline('Creating output receipts')
        for frame_index, layers in written.items():
            current_receipt_path = frame_file(receipt_path, frame_index)
            print(f'  {current_receipt_path}')
            store_json(current_receipt_path, {
                layer: path_str(output_frame)
                for layer, (_, output_frame) in layers.items()
            })

    headline('Done')
    return 0


def from_config(config: dict) -> int:
    """Composite as the worker's config describes."""
    headline('Config')
    print(json.dumps(config, indent=4))
    return main(
        BlockRange(config['first_frame'], config['last_frame']),
        config['frames'],
        Path(config['receipt_path']),
        Path(config['input_path']),
        config['exports'],
        config['imports'],
        config['entity'],
        config.get('renders'),
    )


if __name__ == '__main__':
    hou.exit(worker.run(
        composite_spec.is_valid_houdini_config, from_config, frames=False
    ))
