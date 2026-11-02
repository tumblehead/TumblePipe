"""Layout keys from layout to animation in hython: th::layout_keys.

    hython scripts/verify_layout_keys.py

Layout: TumbleRig's example figure (``th::apex_rigExample_figure``, a
TumbleRig rig as a pipeline rig export is) stands in for a published rig on
a scratch stage (th::layout_rigs, an APEX Animate LOP) and its ``Main``
control is keyed (x: 0 -> 2 over 1..24). The keys are written as the layout
export writes them (layout_keys.write_layout_keys, a context.json beside
them) into a scratch version folder. Without TumbleRig installed the
checks are skipped.

Animation: the figure again, added to a SOP APEX scene under the same name,
then th::layout_keys (its version lookup pointed at the scratch folder),
then a Scene Animate. Checks:

1. Discover gives a row for the character, pinned to the version, its
   frame range the shot's;
2. Copy All puts layout's keys in the Scene Animate: the character moves;
3. the animator edits them (the same channel set to 5): the keys are the
   animator's;
4. Copy from Layout with Controls ``Main`` replaces only the Main channels
   and keeps the animator's other keys; layout's keys play again.

Prints one line per check and exits 1 on any failure.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import hou

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
for hda in ('lop_th.layout_rigs.1.0', 'sop_th.layout_keys.1.0'):
    hou.hda.installFile(str(ROOT / 'otls' / hda))

from tumblepipe.pipe.houdini import layout_keys, shot_rigs  # noqa: E402
from tumblepipe.pipe.houdini.lops import layout_rigs  # noqa: E402
from tumblepipe.pipe.houdini.sops import import_rig, layout_keys_rows  # noqa: E402

FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


FIGURE = 'th::apex_rigExample_figure'


def figure_available() -> bool:
    return any(name.startswith(FIGURE) for name in hou.sopNodeTypeCategory().nodeTypes())


def stand_in(geo):
    """The stand-in rig: TumbleRig's example figure."""
    return geo.createNode(FIGURE)


def translate_parm(rig_geo) -> str:
    """The placement control's translate parm of a TumbleRig rig."""
    import apex
    name = rig_geo.findPrimAttrib('name')
    rig = [p for p in rig_geo.prims() if p.attribValue(name).endswith('Base.rig')][0]
    graph = apex.Graph()
    graph.loadFromGeometry(rig.getEmbeddedGeometry())
    if not graph.matchGraphInputs('Main_t'):
        raise RuntimeError("the figure's rig has no Main_t")
    return 'Main_t'


def keyed_entry(character, parm, values):
    entry = hou.Geometry()
    name = entry.addAttrib(hou.attribType.Prim, 'name', '')
    entry.addAttrib(hou.attribType.Global, 'rig_path', '')
    entry.setGlobalAttribValue('rig_path', f'/{character}.char/Base.rig')
    # every component, as Animate keys a control (x moves, y and z hold 0)
    for axis in 'xyz':
        channel = entry.createChannelPrim()
        channel.setAttribValue(name, f'{parm}{axis}')
        channel.insertKey(1)
        channel.insertKey(24)
        channel.setKeyValues(values if axis == 'x' else [0.0, 0.0])
    return entry


def main(tmp: Path):
    if not figure_available():
        print(f'SKIP  TumbleRig is not installed ({FIGURE}): no TumbleRig rig to stand in')
        return
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)
    rig_file = tmp / 'figure.bgeo.sc'
    geo = hou.node('/obj').createNode('geo', 'rig')
    rig_geo = stand_in(geo).geometry()
    rig_geo.saveToFile(str(rig_file))
    parm = translate_parm(rig_geo)
    import_rig.rig_file_path = lambda uri, channel, version='latest': rig_file

    # layout
    stage = hou.node('/stage')
    hero = stage.createNode('sopcreate', 'hero')
    hero.parm('pathprefix').set('/CHAR/hero')
    hero.node('sopnet/create').createNode('output').setInput(
        0, hero.node('sopnet/create').createNode('box'))
    rigs = layout_rigs.create(stage, 'layout_rigs')
    rigs.native().setInput(0, hero)
    rigs.set_rows([{'prim': '/CHAR/hero', 'entity': 'entity:/assets/CHAR/hero', 'mode': 'rig',
                    'channel': 'default', 'version': 'latest'}])
    rigs.build()
    animate = stage.createNode('apexanimate', 'layout_animate')
    animate.setInput(0, rigs.native())
    keys = hou.Geometry()
    keys.packToFolder('/animation/default.clip/_hero.char_Base', keyed_entry('hero', parm, [0.0, 2.0]))
    animate.parm('animation').set(keys)
    version = tmp / 'layout' / 'v0007'
    version.mkdir(parents=True)
    info = layout_keys.write_layout_keys(animate, version, 'sh010_main_layout_v0007.usd')
    (version / 'context.json').write_text(json.dumps(
        {'inputs': [], 'outputs': [{'parameters': {'layout_keys': info}}]}))

    # animation
    layout_keys_rows.LayoutKeys.resolved = lambda self, v: version
    anim = hou.node('/obj').createNode('geo', 'anim')
    add = anim.createNode('apex::sceneaddcharacter', 'add')
    add.setInput(1, stand_in(anim))
    add.parm('charactername').set('hero')
    node = layout_keys_rows.create(anim, 'layout_keys')
    node.native().setInput(0, add)
    scene_animate = anim.createNode('apex::sceneanimate', 'scene_animate')
    scene_animate.setInput(0, node.native())
    invoke = anim.createNode('apex::sceneinvoke::2.0', 'invoke')
    invoke.setInput(0, scene_animate)
    # unpacked, as the animation template has it: a packed character's
    # bounds in hython can be a frame the Scene Invoke cooked before
    invoke.parm('outputmode').set('unpacked')

    def centre_x(frame):
        hou.setFrame(frame)
        invoke.cook(force=True)
        return round(invoke.geometry().boundingBox().center()[0], 3)

    node.discover()
    rows = node.get_rows()
    check('discover: a row for the character, pinned, the shot range ready',
          [(r['character'], r['version'], r['on'], r['first'], r['last']) for r in rows]
          == [('hero', 'v0007', True, 1, 24)], rows)
    rest = centre_x(1)
    copied = node.copy_all()
    moved = [round(centre_x(f) - rest, 3) for f in (1, 24)]
    check('Copy All: the character moves with layout keys',
          copied == ['hero'] and moved[0] == 0.0 and abs(moved[1] - 2.0) < 0.05
          and node.copied() == {'hero': 'v0007'}, f'{copied} {parm}x: {moved}')

    own = hou.Geometry()
    entry = keyed_entry('hero', parm, [5.0, 5.0])
    other = entry.createChannelPrim()
    other.setAttribValue('name', 'extra_tx')
    other.insertKey(1)
    own.packToFolder('/animation/default.clip/_hero.char_Base', entry)
    scene_animate.parm('animation').set(own)
    animator = [round(centre_x(f) - rest, 3) for f in (1, 24)]
    check("the animator's edits are the keys", all(abs(v - 5.0) < 0.05 for v in animator), animator)

    rows = node.get_rows()
    rows[0]['controls'] = 'Main'
    node.set_rows(rows)
    node.copy_row(1)
    back = [round(centre_x(f) - rest, 3) for f in (1, 24)]
    stored = scene_animate.parm('animation').eval()
    names = sorted(p.attribValue('name') for p in
                   stored.unpackFromFolder('/animation/default.clip/_hero.char_Base').prims())
    check('Copy from Layout (Main): layout keys again, other keys kept',
          back[0] == 0.0 and abs(back[1] - 2.0) < 0.05 and 'extra_tx' in names, f'{back} {names}')

    # a layout published with a package's camera rig, opened in animation
    # without that package (not loaded, or its hook fails): the workfile
    # still builds, no camera rig, the hero's row as before
    other = tmp / 'layout' / 'v0008'
    other.mkdir(parents=True)
    (other / version.joinpath(info['file']).name).write_bytes(version.joinpath(info['file']).read_bytes())
    (other / 'context.json').write_text(json.dumps({'inputs': [], 'outputs': [{'parameters': {
        'layout_keys': dict(info, camera_rig={'name': 'NotLoaded', 'settings': {'type': 'x'}})}}]}))
    layout_keys_rows.LayoutKeys.resolved = lambda self, v: other

    def broken(*args):
        raise RuntimeError('camera rig package not loaded')
    for name, register in (('not loaded', False), ('failing', True)):
        if register:
            shot_rigs.register_camera_rig('NotLoaded', animation=broken)
        dive = hou.node('/obj').createNode('geo', f'anim_{name.replace(" ", "_")}')
        start = dive.createNode('null', 'import_rigs')
        out = layout_keys_rows.add_to_dive(dive, start)
        children = sorted(c.name() for c in dive.children())
        check(f'animation: camera rig package {name}, the workfile still builds',
              out is not None and out.input(0) == start and children == ['import_rigs', 'layout_keys'],
              children)
    shot_rigs.unregister_camera_rig('NotLoaded')


if __name__ == '__main__':
    os.environ.setdefault('HOUDINI_LMINFO_VERBOSE', '1')
    with tempfile.TemporaryDirectory() as tmp:
        try:
            main(Path(tmp))
        except Exception:
            traceback.print_exc()
            FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
