"""Layout in Animate end to end in hython: th::layout_rigs, the layout
network, its publish branch and the layout keys.

    hython scripts/verify_layout_on_stage.py

Builds a scratch layout stage (a hero asset placed at (1, 0, 2), a lamp
prop, the shot camera), stands Houdini's Electra test character in for the
hero's published rig (``import_rig.rig_file_path`` is pointed at it), and
checks:

1. Build: the rig loads under /layout_rigs/hero, the lamp gets a control,
   the camera SideFX's rig (no package registered one), the hero's own
   prim is hidden;
2. the publish branch, written over frames 1-24: the lamp moves with its
   layout keys, /cameras/render_camera is a Camera, no /layout_rigs and no
   active APEX scene (layout_publish passes);
3. the layout keys: an Animation Catalog with the lamp's clip, read back
   on the same frames.

Prints one line per check and exits 1 on any failure.
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

import hou

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
hou.hda.installFile(str(ROOT / 'otls' / 'lop_th.layout_rigs.1.0'))

from pxr import Usd, UsdGeom  # noqa: E402

from tumblepipe.pipe.houdini import layout_keys  # noqa: E402
from tumblepipe.pipe.houdini.lops import layout_rigs  # noqa: E402
from tumblepipe.pipe.houdini.sops import import_rig  # noqa: E402
from tumblepipe.pipe.houdini.validators import validate_layout_publish  # noqa: E402

FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


def world_x(stage, path, frame):
    prim = stage.GetPrimAtPath(path)
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(frame))
    return round(matrix.ExtractTranslation()[0], 3)


def main(tmp: Path):
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)

    # the hero's "published rig": Houdini's Electra as an APEX character
    rig_file = tmp / 'electra.bgeo.sc'
    geo = hou.node('/obj').createNode('geo', 'rig')
    electra = geo.createNode('testgeometry_electra')
    for parm, value in (('output', 'character'), ('outputtype', 'apexchar')):
        if electra.parm(parm) is not None:
            try:
                electra.parm(parm).set(value)
            except hou.OperationFailed:
                pass
    electra.geometry().saveToFile(str(rig_file))
    import_rig.rig_file_path = lambda uri, channel, version='latest': rig_file

    stage = hou.node('/stage')
    hero = stage.createNode('sopcreate', 'hero')
    hero.parm('pathprefix').set('/CHAR/hero')
    hero.node('sopnet/create').createNode('output').setInput(
        0, hero.node('sopnet/create').createNode('box'))
    lamp = stage.createNode('sopcreate', 'lamp')
    lamp.parm('pathprefix').set('/PROP/lamp')
    lamp.node('sopnet/create').createNode('output').setInput(
        0, lamp.node('sopnet/create').createNode('tube'))
    lamp.setInput(0, hero)
    placed = stage.createNode('xform', 'hero_layout')
    placed.setInput(0, lamp)
    placed.parm('primpattern').set('/CHAR/hero')
    placed.parmTuple('t').set((1, 0, 2))
    camera = stage.createNode('camera', 'render_camera')
    camera.setInput(0, placed)
    camera.parm('primpath').set('/cameras/render_camera')

    graft = layout_rigs.add_layout_animate(stage, camera, None)
    rigs = layout_rigs.LayoutRigs(stage.node('layout_rigs'))
    rigs.set_rows([
        {'prim': '/CHAR/hero', 'entity': 'entity:/assets/CHAR/hero', 'mode': 'rig',
         'channel': 'default', 'version': 'latest'},
        {'prim': '/PROP/lamp', 'entity': 'entity:/assets/PROP/lamp', 'mode': 'control',
         'channel': 'default', 'version': 'latest'},
    ])
    rigs.build()
    animate = stage.node('layout_animate')

    # 1. the layout stage
    out = animate.stage()
    characters = sorted(
        str(p.GetPath()) for p in out.Traverse()
        if 'HoudiniApexCharacterAPI' in [str(s) for s in p.GetAppliedSchemas()]
    )
    check('build: rig, control and camera rig in the scene',
          characters == ['/PROP/lamp', '/cameras/render_camera', '/layout_rigs/hero'], characters)
    check('build: the rigged asset is hidden',
          UsdGeom.Imageable(out.GetPrimAtPath('/CHAR/hero')).ComputeVisibility() == 'invisible')
    check('build: no cook errors', not animate.errors() and not rigs.native().errors(),
          list(animate.errors()) + list(rigs.native().errors()))

    # layout's keys on the lamp (its control's tx: 0 -> 4 over 1..24)
    entry = hou.Geometry()
    name = entry.addAttrib(hou.attribType.Prim, 'name', '')
    entry.addAttrib(hou.attribType.Global, 'rig_path', '')
    entry.setGlobalAttribValue('rig_path', '/lamp.char/Base.rig')
    channel = entry.createChannelPrim()
    channel.setAttribValue(name, 'lamp_tx')
    channel.insertKey(1)
    channel.insertKey(24)
    channel.setKeyValues([0.0, 4.0])
    keys = hou.Geometry()
    keys.packToFolder('/animation/default.clip/_lamp.char_Base', entry)
    animate.parm('animation').set(keys)

    # 2. the publish branch, written as the export writes it
    rop = stage.createNode('usd_rop', 'publish')
    rop.setInput(0, graft)
    layer = tmp / 'layout.usd'
    rop.parm('lopoutput').set(str(layer).replace('\\', '/'))
    rop.parm('trange').set('normal')
    rop.parmTuple('f').set((1, 24, 1))
    rop.render()
    published = Usd.Stage.Open(str(layer))
    lamp_x = [world_x(published, '/PROP/lamp', f) for f in (1, 24)]
    check('publish: the lamp moves with its layout keys', lamp_x == [0.0, 4.0], lamp_x)
    cam = published.GetPrimAtPath('/cameras/render_camera')
    check('publish: the shot camera is a Camera', bool(cam) and cam.GetTypeName() == 'Camera')
    check('publish: no rigs in the layer', not published.GetPrimAtPath('/layout_rigs'))
    result = validate_layout_publish(published.GetPseudoRoot())
    check('publish: layout_publish passes', result.passed,
          [i.message for i in result.issues])

    # 3. the layout keys beside the layer
    info = layout_keys.write_layout_keys(animate, tmp, 'layout.usd')
    check('keys: a clip for the lamp', info is not None and 'lamp' in info['characters'], info)
    back = layout_keys.read_catalog(tmp / info['file'])
    check('keys: read back on the same frames',
          layout_keys.key_range(back['lamp']) == (1.0, 24.0), layout_keys.key_range(back['lamp']))


if __name__ == '__main__':
    os.environ.setdefault('HOUDINI_LMINFO_VERBOSE', '1')
    with tempfile.TemporaryDirectory() as tmp:
        try:
            main(Path(tmp))
        except Exception:
            traceback.print_exc()
            FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
