"""Config schemas of the composite job (``jobs/composite.py``), its worker
(``workers/composite.py``) and the worker's hython half
(``workers/composite_houdini.py``), which pins a comp workfile's
``th::import_render`` nodes and renders each ``th::export_comp``.

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
    'title': 'composite',
    'priority': 50,
    'pool_name': 'general',
    'first_frame': 1,
    'last_frame': 100,
    'frames': [1, 50, 100],         # empty => the whole first..last range
    'step_size': 1,
    'batch_size': 10,
    'receipt_path': 'path/to/receipt.####.json',
    'input_path': 'path/to/input.hip',
    'exports': [
        {'node_path': '/stage/comp/export_comp', 'layer': 'comp',
         'output_path': 'path/to/slapcomp/v0001/shot_slapcomp_v0001.####.exr'}
    ],
    'imports': {
        '/stage/comp/import_chars': {
            'channel': 'chars', 'department': 'denoise', 'version': 'v0004'
        }
    },
    # optional (#80): the render of the same submit the comp reads through
    # its Slap Comp Import nodes, in the render's channel order; with it,
    # imports is empty
    'renders': [{'channel': 'chars', 'department': 'denoise', 'version': 'v0004'}]
}

The worker's context is the config without the scheduling keys, keeping
``step_size`` (Deadline hands the worker a span; the step is the config's).
The hython config replaces it with the span the worker was handed and the
``frames`` of it still missing their receipts.
"""

from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_int_list,
    check_str,
    check_str_list,
    valid_entity,
    valid_frame_batches,
    valid_frame_range,
    valid_job_header,
)

_IMPORT_KEYS = ('channel', 'department', 'version')


def _valid_exports(exports) -> bool:
    if not isinstance(exports, list) or len(exports) == 0: return False
    for export in exports:
        if not isinstance(export, dict): return False
        for key in ('node_path', 'layer', 'output_path'):
            if not check_str(export, key): return False
    layers = [export['layer'] for export in exports]
    return len(set(layers)) == len(layers)


def _valid_imports(imports) -> bool:
    if not isinstance(imports, dict): return False
    for node_path, pin in imports.items():
        if not isinstance(node_path, str) or not node_path: return False
        if not isinstance(pin, dict): return False
        for key in _IMPORT_KEYS:
            if not check_str(pin, key): return False
    return True


def valid_renders(renders) -> bool:
    """A comp's ``renders`` (#80): the render of its own submit, a
    non-empty list of ``{channel, department, version}``, one per channel."""
    if not isinstance(renders, list) or len(renders) == 0: return False
    for render in renders:
        if not isinstance(render, dict): return False
        for key in _IMPORT_KEYS:
            if not check_str(render, key) or not render[key]: return False
    channels = [render['channel'] for render in renders]
    return len(set(channels)) == len(channels)


def valid_scene(scene) -> bool:
    """A re-comp block's optional ``scene`` (#80): the workfile comp to
    render instead of the shot's newest composite workfile, ``{path,
    exports: [{node_path, layer}], aovs: [str]}`` (``aovs`` optional), each
    export a node of its own, one per layer."""
    if not isinstance(scene, dict): return False
    if not check_str(scene, 'path') or not scene['path']: return False
    exports = scene.get('exports')
    if not isinstance(exports, list) or len(exports) == 0: return False
    for export in exports:
        if not isinstance(export, dict): return False
        for key in ('node_path', 'layer'):
            if not check_str(export, key) or not export[key]: return False
    layers = [export['layer'] for export in exports]
    if len(set(layers)) != len(layers): return False
    if 'aovs' in scene and not check_str_list(scene, 'aovs'): return False
    return True


def _valid_composite(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not check_str(config, 'receipt_path'): return False
    if not check_str(config, 'input_path'): return False
    if not _valid_exports(config.get('exports')): return False
    if not _valid_imports(config.get('imports')): return False
    if 'renders' in config:
        if not valid_renders(config['renders']): return False
        # The render of its submit is all it reads: nothing is frozen.
        if config['imports']: return False
    return True


def valid_recomp(block) -> bool:
    """A render config's optional ``composite`` block: the re-comp that
    follows the render (Farm Submit's Comp column; ``pipelines/composite.
    recomp_config``) and how to schedule it.

    block = {
        'pool_name': 'string', 'priority': 'int', 'batch_size': 'int',
        'copy_to_edit': 'bool', 'channel_name': 'string',
        'step_size': 'int',         # optional, default 1
        'preview_frames': 'bool',   # optional, default False: first/middle/last first
        'preview_frame_list': ['int'],  # optional: the preview's frames instead
                                    # of first/middle/last (farm.specs.preview_frames)
        'first_frame': 'int',       # optional: the comp's own first frame
        'last_frame': 'int',        # optional: the comp's own last frame
        'scene': {...},             # optional (#80): a workfile comp with no
                                    # th::import_render, comping this
                                    # render (valid_scene)
    }

    The optional keys arrived after the block first shipped (v1.98.0), so
    a block without them (a job queued by that version) still validates
    and means what it did: every frame of the range it follows, no preview
    frames. A missing first or last frame follows that range's.
    """
    if not isinstance(block, dict): return False
    if not check_str(block, 'pool_name'): return False
    if not check_int(block, 'priority'): return False
    if not check_int(block, 'batch_size'): return False
    if block['batch_size'] <= 0: return False
    if not check_bool(block, 'copy_to_edit'): return False
    if not check_str(block, 'channel_name'): return False
    if 'step_size' in block:
        if not check_int(block, 'step_size'): return False
        if block['step_size'] <= 0: return False
    if 'preview_frames' in block and not check_bool(block, 'preview_frames'): return False
    if 'preview_frame_list' in block:
        # Its range is the comp's, known once the block meets the range it
        # follows (pipelines.composite.recomp_preview_frames): here, the shape.
        frames = block['preview_frame_list']
        if not check_int_list(block, 'preview_frame_list') or not frames: return False
        if len(set(frames)) != len(frames): return False
    for key in ('first_frame', 'last_frame'):
        if key in block and not check_int(block, key): return False
    if 'first_frame' in block and 'last_frame' in block:
        if block['first_frame'] > block['last_frame']: return False
    if 'scene' in block and not valid_scene(block['scene']): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    if not valid_frame_batches(config): return False
    return _valid_composite(config)


def is_valid_context(config) -> bool:
    if not _valid_composite(config): return False
    # The step reached the Deadline job but never the worker once, so the
    # worker rebuilt a step-1 range and composited every in-between frame.
    if not check_int(config, 'step_size'): return False
    if config['step_size'] <= 0: return False
    return True


def is_valid_houdini_config(config) -> bool:
    if not _valid_composite(config): return False
    if not valid_frame_range(config, step=False): return False
    if not check_int_list(config, 'frames'): return False
    return True
