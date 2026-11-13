"""Audit: th::import_render's outputs on a three-layer shot, each cooked.

    hython scripts/verify_import_render_outputs.py

Remy, 2026-11-13: an import of a shot with three layers showed rgba ..
rgba 8 and he could not tell which layer was which, and the outputs did
not preview in the Composite View. In a throwaway project (staged as
scripts/verify_comp.py stages renders, with hoiiotool) a shot with three
layers, ``chars`` (a denoise: bare R,G,B,A, the ``C`` plane), ``bg`` and
``fx`` (raw renders: ``beauty.R`` ..), each with a light group, depth and a
matte. Then:

- an all-channels import and a one-channel import per layer: every output
  is cooked (``layer()`` / ``cable()``); each beauty output holding a layer
  has that layer's pixels, every other beauty output is empty and cooks; the
  ``lpe``, ``aov`` and ``matte`` cables carry each layer's fields by name,
  with pixels;
- the outputs' labels are the type's (rgba .. rgba 8, the AOV cables,
  geometry, camera: a digital asset cannot relabel them per node), and the
  node's comment, shown under it in the network editor, names the layer on
  each output (``rgba: chars``, ``rgba 2: bg``, ``rgba 3: fx``), as Reads
  does; switching a one-channel import to another layer renames it; a
  comment someone typed is left alone;
- the network editor shows the comment (its Display Comment flag).

Needs Houdini with the comp HDAs compiled (``hython .ci/compile_hdas.py``)
and on HOUDINI_OTLSCAN_PATH, and this checkout's python/ on PYTHONPATH; do
not set HOUDINI_USER_PREF_DIR to a scratch dir named with __HVER__ (COP
cooks then fail). Exits 1 on any failure; nothing outside a temp dir is
written.

The Composite View itself needs a GUI: its layer menu lists C (the first
output), then each output that holds something by its label, and shows the
picked one; checked by hand on 22.0.429 (docs/nodes/comp.md).
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
sys.argv = [sys.argv[0]]
import verify_comp as vc  # noqa: E402  (its throwaway project and staging)

import hou  # noqa: E402

from tumblepipe.pipe import comp  # noqa: E402
from tumblepipe.pipe.houdini.cops import import_render  # noqa: E402

COLOURS = {
    'chars': (0.8, 0.2, 0.1, 1.0),
    'bg': (0.1, 0.3, 0.9, 1.0),
    'fx': (0.2, 0.9, 0.1, 0.5),
}
LAYERS = list(COLOURS)
check = vc.check


def stage_shot():
    shot = vc.add_shot('SH100', LAYERS)
    for layer, colour in COLOURS.items():
        aovs = {'beauty': colour, 'beauty_key': colour[:3], 'depth': (5.0, 5.0, 5.0),
                f'objid_{layer}': (1.0,)}
        raw = layer != 'chars'
        if raw:
            vc.stage(shot, 'render', layer, 'v0001', True, aovs)
        else:
            # A denoise: the beauty is bare R,G,B,A (the C plane), the
            # other AOVs as rendered.
            vc.stage(shot, 'render', layer, 'v0001', True, aovs)
            vc.stage(shot, 'denoise', layer, 'v0001', False, {'beauty': colour})
            vc.stage(shot, 'denoise', layer, 'v0001', True,
                     {k: v for k, v in aovs.items() if k != 'beauty'})
    return shot


def layer_average(native, output):
    """The output's mean pixel, per channel; None for an empty output."""
    layer = native.layer(output)
    if layer is None: return None
    return [layer.computeAverage(c) for c in range(layer.channelCount())]


def close(got, want, tolerance=2e-3):
    return got is not None and len(got) == len(want) and all(
        abs(g - w) < tolerance for g, w in zip(got, want))


def check_outputs(node, label, held):
    """Cook every output of ``node``; ``held`` maps an output index to the
    layer whose beauty it carries."""
    native = node.native()
    native.cook(force=True)
    check(f'{label}: the node cooks with no error', not native.errors(), str(native.errors()))
    want_labels = ([import_render.output_label(n) for n in import_render.CHANNEL_OUTPUT_NAMES]
                   + list(import_render.CABLE_OUTPUT_NAMES) + ['geometry', 'camera'])
    check(f'{label}: the outputs are the type\'s, rgba .. rgba 8 then the cables, geometry, camera',
          list(native.outputLabels()) == want_labels, str(native.outputLabels()))
    for index in range(import_render.CHANNEL_OUTPUTS):
        name = import_render.output_label(import_render.CHANNEL_OUTPUT_NAMES[index])
        got = layer_average(native, index)
        if index in held:
            check(f'{label}: {name} has the {held[index]} beauty',
                  close(got, COLOURS[held[index]]), f'got {got}')
        else:
            check(f'{label}: {name} holds no layer: empty, and cooks',
                  got is None or max(abs(c) for c in got) < 1e-6, f'got {got}')
    prefix = (lambda layer: f'{layer}_') if node.is_all_channels() else (lambda layer: '')
    read = list(held.values())
    want_cables = {
        import_render.LPES_OUTPUT: [f'{prefix(layer)}beauty_key' for layer in read],
        import_render.AOVS_OUTPUT: [f'{prefix(layer)}depth' for layer in read],
        import_render.MATTES_OUTPUT: [f'{prefix(layer)}objid_{layer}' for layer in read],
    }
    for output, fields in want_cables.items():
        name = import_render.OUTPUTS[output]
        cable = native.cable(output)
        names = sorted(cable.wireNames()) if cable is not None else None
        check(f'{label}: the {name} cable carries each layer\'s field by name',
              names == sorted(fields), f'got {names}, want {sorted(fields)}')
        if cable is None: continue
        for wire in fields:
            layer = cable.layer(wire) if wire in (names or []) else None
            check(f'{label}: {name} field {wire} has pixels',
                  layer is not None and layer.computeAverage() > 0.0,
                  str(layer and layer.computeAverage()))


def check_names(node, label, want_comment):
    native = node.native()
    check(f'{label}: the comment names the layer on each output',
          native.comment() == want_comment, repr(native.comment()))
    check(f'{label}: the network editor shows the comment',
          native.isGenericFlagSet(hou.nodeFlag.DisplayComment))


def main():
    shot = stage_shot()
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(vc.FIRST, vc.LAST)
    hou.setFrame(vc.FIRST)
    vc.use_context(shot)
    copnet = hou.node('/stage').createNode('copnet', 'comp')

    print('--- all channels in one import')
    node = import_render.create(copnet, 'import_all')
    node.set_channel_name(comp.ALL_CHANNELS)
    node.execute()
    check('all channels: each layer on an output of its own, in the shot\'s order',
          node.get_channel_outputs()[:4] == ('chars', 'bg', 'fx', None),
          str(node.get_channel_outputs()))
    check_outputs(node, 'all channels', {0: 'chars', 1: 'bg', 2: 'fx'})
    check_names(node, 'all channels', 'rgba: chars\nrgba 2: bg\nrgba 3: fx')
    check('all channels: Reads names them too',
          'outputs: rgba chars, rgba 2 bg, rgba 3 fx' in node.parm('version_label').eval(),
          node.parm('version_label').eval())
    node.native().setComment('my note')
    node.execute()
    check('all channels: a comment someone typed stays',
          node.native().comment() == 'my note', repr(node.native().comment()))

    print('--- one import per layer')
    one = import_render.create(copnet, 'import_one')
    for layer in LAYERS:
        one.set_channel_name(layer)
        one.execute()
        check_outputs(one, f'{layer} alone', {0: layer})
        check_names(one, f'{layer} alone', f'rgba: {layer}')

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
