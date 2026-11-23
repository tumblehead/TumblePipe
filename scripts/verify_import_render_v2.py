"""Audit: th::import_render 2.0, the cleaner import (Remy, 2026-11-22).

    hython scripts/verify_import_render_v2.py

Remy (Discord rfe, PRIORITY): "Can we prio getting a cleaner, working
import render node? It will be needed very soon and it's still messy." His
node showed rgba, rgba 2 .. rgba 8, the cables, geometry and camera, and a
checker for a thumbnail: an import of one channel has seven empty beauty
outputs (1.0's are fixed by its type), a node dropped by hand read nothing
until Update, and a node that could not read said so only on its Reads
label. In a throwaway project (staged as scripts/verify_comp.py stages
renders, with hoiiotool):

- **Outputs.** import_render.create makes a 2.0, whose outputs are beauty,
  lpe, aov, matte, cryptomaterials, cryptoprimitives, layers, geometry,
  camera, extra (no rgba N); every output cooks.
- **One channel per layer.** beauty has the layer's pixels; the lpe, aov,
  matte and extra cables carry its passes by name (a light group and an
  LPE split on lpe, depth on aov as one channel, transmission on extra;
  Antoine, 2026-11-22); layers carries its beauty
  named after the channel; the comment under the node names what it reads
  (``chars: denoise v0001``).
- **All channels.** beauty is the layers laid over each other in the shot's
  order, the last on top (premultiplied over); layers carries each by name.
- **Not rendered yet.** A channel with no render: no error on the node
  (a comp is built before its renders), every output empty, the comment
  says ``<channel>: not rendered yet``; an all-channels import names it.
- **Cannot comp.** A channel the shot does not have, and a beauty with no
  alpha: the node fails its cook with the reason, so does a node reading
  any of its outputs, and the comment says so; picking a good channel
  clears it. A read never leaves an earlier one on the outputs.
- **Dropped by hand.** createNode with the scene's shot as context: it has
  read the shot's latest render, on the shot's first rendered channel when
  its (implicit) ``default`` has no render, with no Update.
- **Outputs named after the passes.** AOV Outputs keeps a Cable Unpack per
  cable, layers too, whose outputs are labelled with the passes (channels).
- **A light mix** wired from a 2.0's aov moves to lpe and gets the beauty.
- **1.0 still cooks.** An explicit th::import_render::1.0 reads as before:
  rgba .. rgba 8, the beauty on rgba.
- **A 1.0 made a 2.0** (``change_version``) keeps a wire on aov on aov, and
  one on rgba 2 lands on a layers Cable Unpack at that output's channel.

Needs Houdini with the comp HDAs compiled and on HOUDINI_OTLSCAN_PATH, and
this checkout's python/ on PYTHONPATH (preflight's hython stage sets both).
Exits 1 on any failure; nothing outside a temp dir is written. The network
editor itself (the comment drawn under the node, the error badge) needs a
GUI and is not driven here.
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
from tumblepipe.pipe.houdini import nodes as ns  # noqa: E402
from tumblepipe.pipe.houdini.cops import import_render, light_mix  # noqa: E402

COLOURS = {
    'chars': (0.8, 0.2, 0.1, 1.0),
    'bg': (0.1, 0.3, 0.9, 1.0),
    'fx': (0.2, 0.9, 0.1, 0.5),
}
LAYERS = list(COLOURS)
V2_LABELS = ['beauty', 'lpe', 'aov', 'matte', 'cryptomaterials', 'cryptoprimitives',
             'layers', 'geometry', 'camera', 'extra']
check = vc.check


# A light th::lpe_tags named with a capital: its AOV's folder is
# lowercased by the farm's split, its channels kept the light's case
# (diffuse_Fill.r in diffuse_fill/), and the File COP, which matches a plane
# case for case, read it black (2026-11-23). It rides lpe whatever the
# light is called.
RECASED_LPE, RECASED_PLANE, RECASED_COLOUR = 'diffuse_fill', 'diffuse_Fill', (0.3, 0.6, 0.9)


def stage_recased(shot_uri, department, channel, version, aov, plane, colour):
    """One AOV of a render whose channels are ``<plane>.r/g/b`` in folder
    ``aov`` (verify_comp.stage names both after the AOV)."""
    root = vc.api.storage.resolve(
        vc.Uri.parse('render:/render') / shot_uri.segments / department / channel / version
    )
    stem = '_'.join(shot_uri.segments[1:])
    (root / aov).mkdir(parents=True, exist_ok=True)
    names = [f'{plane}.{n}' for n in ('r', 'g', 'b')]
    for frame in range(vc.FIRST, vc.LAST + 1):
        out = root / aov / f'{stem}_{channel}_{aov}_{version}.{frame}.exr'
        vc.oiiotool('--pattern', 'constant:color=' + ','.join(map(str, colour)),
                    '32x16', 3, '--chnames', ','.join(names),
                    *vc.CHROMA, '-d', 'float', '-o', out)


def stage_shot():
    shot = vc.add_shot('SH100', LAYERS)
    for layer, colour in COLOURS.items():
        aovs = {'beauty': colour, 'beauty_key': colour[:3], 'depth': (5.0, 5.0, 5.0),
                f'objid_{layer}': (1.0,), 'diffuse_key': colour[:3],
                'transmission': (0.25, 0.25, 0.25)}
        vc.stage(shot, 'render', layer, 'v0001', True, aovs)
        stage_recased(shot, 'render', layer, 'v0001', RECASED_LPE, RECASED_PLANE, RECASED_COLOUR)
        if layer == 'chars':
            # A denoise: the beauty bare R,G,B,A (the C plane).
            vc.stage(shot, 'denoise', layer, 'v0001', False, {'beauty': colour})
            vc.stage(shot, 'denoise', layer, 'v0001', True,
                     {k: v for k, v in aovs.items() if k != 'beauty'})
            stage_recased(shot, 'denoise', layer, 'v0001', RECASED_LPE, RECASED_PLANE,
                          RECASED_COLOUR)
    return shot


def layer_average(native, output):
    layer = native.layer(output)
    if layer is None: return None
    return [layer.computeAverage(c) for c in range(layer.channelCount())]


def empty(got):
    return got is None or max(abs(c) for c in got) < 1e-6


def close(got, want, tolerance=2e-3):
    return got is not None and len(got) == len(want) and all(
        abs(g - w) < tolerance for g, w in zip(got, want))


def over(front, back):
    """Premultiplied over."""
    return tuple(f + b * (1.0 - front[3]) for f, b in zip(front, back))


def cable_names(native, output):
    cable = native.cable(output)
    return None if cable is None else list(cable.wireNames())


def cook_errors(native):
    try:
        native.cook(force=True)
    except hou.OperationFailed:
        pass
    return list(native.errors())


def check_outputs(node, label, layers):
    """``layers``: the layers the node reads, in the shot's order."""
    native = node.native()
    check(f'{label}: the node cooks with no error', not cook_errors(native),
          str(native.errors()))
    check(f'{label}: the outputs are beauty, the cables, layers, geometry, camera, extra',
          list(native.outputLabels()) == V2_LABELS, str(native.outputLabels()))
    want = COLOURS[layers[0]]
    for layer in layers[1:]:
        want = over(COLOURS[layer], want)
    got = layer_average(native, import_render.RGBA_OUTPUT)
    check(f'{label}: beauty is {" under ".join(layers)}' if len(layers) > 1
          else f'{label}: beauty is the {layers[0]} beauty', close(got, want),
          f'got {got}, want {want}')
    prefix = (lambda layer: f'{layer}_') if node.is_all_channels() else (lambda layer: '')
    want_cables = {
        import_render.LPES_OUTPUT: [f'{prefix(layer)}{name}' for layer in layers
                                    for name in ('beauty_key', 'diffuse_key', RECASED_LPE)],
        import_render.AOVS_OUTPUT: [f'{prefix(layer)}depth' for layer in layers],
        import_render.MATTES_OUTPUT: [f'{prefix(layer)}objid_{layer}' for layer in layers],
        import_render.LAYERS_OUTPUT: list(layers),
        import_render.EXTRA_OUTPUT: [f'{prefix(layer)}transmission' for layer in layers],
    }
    for output, fields in want_cables.items():
        name = import_render.OUTPUTS[output]
        names = cable_names(native, output)
        check(f'{label}: the {name} cable carries each pass by name',
              sorted(names or []) == sorted(fields), f'got {names}, want {fields}')
    aovs = native.cable(import_render.AOVS_OUTPUT)
    for layer in layers:
        depth = f'{prefix(layer)}depth'
        got = None
        if aovs is not None and depth in aovs.wireNames():
            got = [aovs.layer(depth).computeAverage(c)
                   for c in range(aovs.layer(depth).channelCount())]
        check(f'{label}: {depth} is Mono, its depth', close(got, (5.0,)), f'got {got}')
    lpes = native.cable(import_render.LPES_OUTPUT)
    for layer in layers:
        name = f'{prefix(layer)}{RECASED_LPE}'
        got = None
        if lpes is not None and name in lpes.wireNames():
            got = [lpes.layer(name).computeAverage(c) for c in range(3)]
        check(f'{label}: lpe field {name} (channels {RECASED_PLANE}.r/g/b) is its colour, '
              'not black', close(got, RECASED_COLOUR), f'got {got}')
    extra = native.cable(import_render.EXTRA_OUTPUT)
    for layer in layers:
        name = f'{prefix(layer)}transmission'
        got = None
        if extra is not None and name in extra.wireNames():
            got = [extra.layer(name).computeAverage(c) for c in range(3)]
        check(f'{label}: extra field {name} is its transmission',
              close(got, (0.25, 0.25, 0.25)), f'got {got}')
    cable = native.cable(import_render.LAYERS_OUTPUT)
    for layer in layers:
        got = None
        if cable is not None and layer in cable.wireNames():
            got = [cable.layer(layer).computeAverage(c) for c in range(4)]
        check(f'{label}: layers field {layer} is its beauty', close(got, COLOURS[layer]),
              f'got {got}')


def check_reads_nothing(node, label, want_comment):
    native = node.native()
    errors = cook_errors(native)
    check(f'{label}: no error on the node (nothing rendered yet is no error)',
          not errors, str(errors))
    check(f'{label}: beauty is empty',
          empty(layer_average(native, import_render.RGBA_OUTPUT)))
    check(f'{label}: every cable is empty',
          all(not cable_names(native, import_render.OUTPUTS.index(name))
              for name in import_render.CABLE_OUTPUT_NAMES
              + (import_render.LAYERS, import_render.EXTRA)))
    check(f'{label}: the comment says so', native.comment() == want_comment,
          repr(native.comment()))
    check(f'{label}: the network editor shows the comment',
          native.isGenericFlagSet(hou.nodeFlag.DisplayComment))


def check_refused(node, label, reason):
    native = node.native()
    errors = cook_errors(native)
    check(f'{label}: the node fails its cook, saying why ({reason})',
          any(reason in e for e in errors), str(errors))
    probe = native.parent().createNode('null', f'{native.name()}_probe')
    probe.setInput(0, native, import_render.AOVS_OUTPUT)
    probe_errors = cook_errors(probe)
    check(f'{label}: a node reading its aov fails too', bool(probe_errors), str(probe_errors))
    probe.destroy()
    check(f'{label}: the comment says it cannot comp',
          native.comment().endswith(import_render.CANNOT_COMP), repr(native.comment()))


def execute(node):
    try:
        node.execute()
        return None
    except ns.NodeError as error:
        return str(error)


def main():
    shot = stage_shot()
    late = vc.add_shot('SH200', ['chars', 'late'])
    vc.stage(late, 'render', 'chars', 'v0001', True,
             {'beauty': COLOURS['chars'], 'beauty_key': COLOURS['chars'][:3],
              'depth': (5.0, 5.0, 5.0), 'objid_chars': (1.0,)})
    no_alpha = vc.add_shot('SH300', ['default'])
    vc.stage(no_alpha, 'render', 'default', 'v0001', True, {'beauty': (0.5, 0.5, 0.5)})
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(vc.FIRST, vc.LAST)
    hou.setFrame(vc.FIRST)
    vc.use_context(shot)
    copnet = hou.node('/stage').createNode('copnet', 'comp')

    print('--- the type')
    one = import_render.create(copnet, 'import_one')
    check('create makes a th::import_render::2.0',
          one.native().type().name() == 'th::import_render::2.0', one.native().type().name())
    check('a 2.0 is on the 2.0 layout', one.get_outputs_layout() == import_render.LAYOUT_V2)

    print('--- one import per layer')
    for layer in LAYERS:
        one.set_channel_name(layer)
        error = execute(one)
        check(f'{layer} alone: resolves', error is None, str(error))
        check_outputs(one, f'{layer} alone', [layer])
        department = 'denoise' if layer == 'chars' else 'render'
        check(f'{layer} alone: the comment names what it reads',
              one.native().comment() == f'{layer}: {department} v0001',
              repr(one.native().comment()))

    print('--- all channels in one import')
    every = import_render.create(copnet, 'import_all')
    every.set_channel_name(comp.ALL_CHANNELS)
    error = execute(every)
    check('all channels: resolves', error is None, str(error))
    check_outputs(every, 'all channels', LAYERS)
    check('all channels: the comment names each layer',
          every.native().comment() == ('chars: denoise v0001\nbg: render v0001\nfx: render v0001'
                                       '\ndefault: not rendered yet'),
          repr(every.native().comment()))
    every.native().setComment('my note')
    execute(every)
    check('all channels: a comment someone typed stays',
          every.native().comment() == 'my note', repr(every.native().comment()))

    print('--- not rendered yet')
    one.set_channel_name('fx')
    execute(one)
    pending = import_render.create(copnet, 'import_late')
    pending.set_entity_uri(late)
    pending.set_channel_name('late')
    error = execute(pending)
    check('late: Update says nothing is rendered', error is not None and 'no complete render' in error,
          str(error))
    check_reads_nothing(pending, 'late', 'late: not rendered yet')
    check('late: Reads says why', 'no complete render' in pending.parm('version_label').eval(),
          pending.parm('version_label').eval())
    pending.set_channel_name('chars')
    execute(pending)
    check('late -> chars: reads chars', pending.native().comment() == 'chars: render v0001',
          repr(pending.native().comment()))
    pending.set_channel_name('late')
    execute(pending)
    check_reads_nothing(pending, 'chars -> late (an earlier read never stays)',
                        'late: not rendered yet')
    partial = import_render.create(copnet, 'import_partial')
    partial.set_entity_uri(late)
    partial.set_channel_name(comp.ALL_CHANNELS)
    execute(partial)
    check('all channels, one not rendered: the comment names both',
          partial.native().comment() == ('chars: render v0001\ndefault: not rendered yet'
                                         '\nlate: not rendered yet'),
          repr(partial.native().comment()))
    check('all channels, one not rendered: no error', not cook_errors(partial.native()))

    print('--- cannot comp')
    wrong = import_render.create(copnet, 'import_wrong')
    wrong.set_channel_name('nosuch')
    execute(wrong)
    check_refused(wrong, 'a channel the shot does not have', 'is not a channel')
    wrong.set_channel_name('bg')
    execute(wrong)
    check_outputs(wrong, 'nosuch -> bg (the error clears)', ['bg'])
    flat = import_render.create(copnet, 'import_flat')
    flat.set_entity_uri(no_alpha)
    flat.set_channel_name('default')
    execute(flat)
    check_refused(flat, 'a beauty with no alpha', 'no alpha')

    print('--- dropped by hand')
    native = copnet.createNode('th::import_render', 'import_dropped')
    dropped = import_render.ImportRender(native)
    check('dropped: on the shot\'s first rendered channel (its default has none)',
          dropped.get_channel_name() == 'chars', dropped.get_channel_name())
    check('dropped: it has read the latest render with no Update',
          native.comment() == 'chars: denoise v0001', repr(native.comment()))
    # Every output, cached ones too: a Cache COP wired inside an OnCreated
    # got no output, so all of them were empty.
    check_outputs(dropped, 'dropped', ['chars'])

    print('--- outputs named after the passes')
    one.set_channel_name('chars')
    execute(one)
    one.parm(import_render.UNPACK_PARM).set(1)
    execute(one)
    for name, want in ((import_render.LPE, ['beauty_key', 'diffuse_key', RECASED_LPE]),
                       (import_render.AOV, ['depth']),
                       (import_render.MATTE, ['objid_chars']), (import_render.LAYERS, ['chars']),
                       (import_render.EXTRA, ['transmission'])):
        unpack = one.find_unpack(name)
        labels = list(unpack.outputLabels()) if unpack is not None else None
        check(f'AOV Outputs: a Cable Unpack on {name}, its outputs named after the passes',
              labels is not None and sorted(labels) == sorted(want), str(labels))
    layers_unpack = one.find_unpack(import_render.LAYERS)
    if layers_unpack is not None:
        check('AOV Outputs: the layers unpack\'s chars output is the chars beauty',
              close(layer_average(layers_unpack, 0), COLOURS['chars']))

    print('--- a light mix on a 2.0')
    mix = light_mix.create(copnet, 'mix')
    mix.native().setInput(light_mix.CABLE_INPUT, every.native(), import_render.AOVS_OUTPUT)
    mixed = import_render.create(copnet, 'import_mixed')
    mixed.set_channel_name('chars')
    execute(mixed)
    mix.native().setInput(light_mix.CABLE_INPUT, mixed.native(), import_render.AOVS_OUTPUT)
    mix.sync_rows()
    wires = sorted((c.inputIndex(), c.outputIndex()) for c in mix.native().inputConnections())
    check('light mix: the cable moved from aov to lpe, the beauty wired from beauty',
          wires == [(light_mix.BEAUTY_INPUT, import_render.RGBA_OUTPUT),
                    (light_mix.CABLE_INPUT, import_render.LPES_OUTPUT)], str(wires))
    check('light mix: a row for the light group', mix.rows() == ['beauty_key'], str(mix.rows()))

    print('--- 1.0 still cooks')
    old_native = copnet.createNode('th::import_render::1.0', 'import_old')
    old = import_render.ImportRender(old_native)
    old.set_channel_name('bg')
    execute(old)
    check('1.0: on the 1.0 layout', old.get_outputs_layout() == import_render.LAYOUT_V1)
    check('1.0: its outputs are rgba .. rgba 8, the cables, geometry, camera',
          list(old_native.outputLabels()) == list(import_render.output_labels(import_render.LAYOUT_V1)),
          str(old_native.outputLabels()))
    check('1.0: cooks with no error', not cook_errors(old_native), str(old_native.errors()))
    check('1.0: the bg beauty on rgba', close(layer_average(old_native, 0), COLOURS['bg']))
    check('1.0: rgba 2 is empty', empty(layer_average(old_native, 1)))
    check('1.0: its comment names the layer on rgba', old_native.comment() == 'rgba: bg',
          repr(old_native.comment()))

    print('--- a 1.0 made a 2.0')
    old.set_channel_name(comp.ALL_CHANNELS)
    execute(old)
    slots = old.get_channel_outputs()
    on_second = slots[1]
    reads_rgba2 = copnet.createNode('null', 'reads_rgba2')
    reads_rgba2.setInput(0, old_native, 1)
    reads_aov = copnet.createNode('null', 'reads_aov')
    reads_aov.setInput(0, old_native, old.output(import_render.AOV))
    new_native = import_render.change_version(old_native, '2.0')
    check('1.0 -> 2.0: the node is a 2.0 of the same name',
          new_native.type().name() == 'th::import_render::2.0' and new_native.name() == 'import_old',
          new_native.type().name())
    wired = reads_aov.inputConnections()
    check('1.0 -> 2.0: a wire on aov stays on aov',
          len(wired) == 1 and wired[0].inputNode() == new_native
          and wired[0].outputIndex() == import_render.AOVS_OUTPUT,
          str([(c.inputNode().path(), c.outputIndex()) for c in wired]))
    wired = reads_rgba2.inputConnections()
    unpack = wired[0].inputNode() if wired else None
    fields = [unpack.parm(f'fieldname{i}').eval() for i in range(1, unpack.parm('fields').eval() + 1)] \
        if unpack is not None and unpack.type().name() == 'cableunpack' else []
    check(f'1.0 -> 2.0: a wire on rgba 2 ({on_second}) is on a layers unpack at {on_second}',
          unpack is not None and fields and fields[wired[0].outputIndex()] == on_second,
          f'{unpack and unpack.path()} {fields}')
    check(f'1.0 -> 2.0: that wire reads the {on_second} beauty',
          close(layer_average(reads_rgba2, 0), COLOURS[on_second]),
          str(layer_average(reads_rgba2, 0)))

    print('--- a dive saved short of an output (before extras, v1.121.0)')
    saved = import_render.create(copnet, 'import_saved')
    saved.set_channel_name('chars')
    execute(saved)
    saved_path = saved.native().path()
    saved.native().node(import_render.DIVE).parm('outputs').set(len(import_render.DIVE_OUTPUTS) - 1)
    hip = Path(vc.TEMP_ROOT) / 'short_dive.hip'
    hou.hipFile.save(str(hip))
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.hipFile.load(str(hip), suppress_save_prompt=True, ignore_load_warnings=True)
    loaded = hou.node(saved_path)
    dive = loaded.node(import_render.DIVE) if loaded is not None else None
    check('saved short: the load gives the dive every output',
          dive is not None and dive.parm('outputs').eval() == len(import_render.DIVE_OUTPUTS),
          str(dive and dive.parm('outputs').eval()))
    feeds = {}
    if loaded is not None:
        for c in loaded.node('outputs').inputConnections():
            source = c.inputNode()
            parm = source.parm(f'outputlabel{c.outputIndex() + 1}')
            feeds[c.inputIndex()] = (source.name(), parm.eval() if parm else None)
    want = {index: ('dive', import_render.DIVE_SOURCES[name])
            for index, name in enumerate(import_render.OUTPUTS)
            if name not in import_render.SCENE_OUTPUT_NAMES}
    check('saved short: every output is fed from its dive output, extra from extras',
          all(feeds.get(index) == source for index, source in want.items()), str(feeds))
    if loaded is not None:
        names = cable_names(loaded, import_render.EXTRA_OUTPUT)
        check('saved short: extra carries the transmission', names == ['transmission'],
              str(names))

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
