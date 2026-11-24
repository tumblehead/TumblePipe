#!/usr/bin/env hython
"""Verify th::animate gives a rig that simulates the same frames in every
process (TumbleRig's deterministic Start, `TumbleRig.sim_start`, called from
`lops.animate_points.apply` through `pipe.houdini.sim_start`).

Remy, tumblepipe__bugs 2026-11-18: "tumblerig motion effects are not
rendering on the farm". A sim inside a rig (Motion Effects, Collisions...)
only steps when frames come one after another; a process that starts
mid-shot (a farm or local-farm task, an export of part of the range, husk
reading the stage at one frame) started the sim cold there. Now th::animate
replays the rig from the shot's start when its sims are not at the frame
before.

The rig: a hips + four-link tail with TumbleRig's newest Motion Effects
(Jiggle on the tail, Update Mode Live Tick, the default), a ball on its tip
(Apex Primitives) given a pipeline path (``/CHAR/hero/geo/body``), the hips
keyed to swing and walk over 1..48. One part per process:

    ref     the stage read at 1..48 in order
    fresh   the stage read at 25..48 only (a task starting mid-shot)
    ooo     the stage read in a shuffled order
    rop     a USD ROP saves 25..48 (an export of part of the range), read back
    plain   as fresh with the replay off (TUMBLERIG_NO_SIM_REPLAY=1): must differ
    cfx_ref a stock SOP Import LOP on th::animate's SOPs (`points_sops/placed`),
            read at 1..48 in order
    cfx     the same SOP Import read at 25..48 only: a CFX scene whose sim
            starts mid-shot (wifi_retina, tumblerig__help 2026-11-21: Motion
            Effects missing on a SOP Import of the character). `placed`
            catches the rigs up in its own cook, so it equals cfx_ref.

    hython scripts/verify_animate_sim_start.py <part> <out.json>
    python scripts/verify_animate_sim_start.py --compare <dir>

Run with an empty package dir, this repo on HOUDINI_PATH and TumbleRig
installed once (as verify_animate_keyed_scene.py says). Without TumbleRig
it prints SKIP. TumbleRig 1.25+ takes its sim system from the shared kit
(tumbletrove/kit, `ttkit`): with an empty package dir the kit's `python`
folder has to be on PYTHONPATH too, or nothing counts as a sim, nothing is
replayed and every part but `plain` fails.
"""

import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BODY = '/CHAR/hero/geo/body'
FIRST, LAST, MID = 1, 48, 25
TOL = 1e-5


def compare(folder):
    folder = Path(folder)
    ref = json.loads((folder / 'ref.json').read_text())
    failed = False
    for part in ('fresh', 'ooo', 'rop', 'plain', 'cfx'):
        got = json.loads((folder / f'{part}.json').read_text())
        if part == 'cfx':
            ref = json.loads((folder / 'cfx_ref.json').read_text())
        worst, at = 0.0, None
        for f, pts in got['frames'].items():
            r = ref['frames'].get(f)
            if r is None or pts is None:
                continue
            d = max((abs(a - b) for a, b in zip(r, pts)), default=0.0) if len(r) == len(pts) else float('inf')
            if d > worst:
                worst, at = d, f
        ok = worst > 10 * TOL if part == 'plain' else worst <= TOL
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}  {part:6s} worst {worst:.6g} at {at}  {got.get('info')}")
    return 1 if failed else 0


if len(sys.argv) > 1 and sys.argv[1] == '--compare':
    sys.exit(compare(sys.argv[2]))

import hou  # noqa: E402

sys.path.insert(0, str(REPO / 'python'))
PART, OUT = sys.argv[1], sys.argv[2]


def install():
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def newest(family):
    names = [n for n in hou.sopNodeTypeCategory().nodeTypes() if n.startswith(f'th::{family}::')]
    return max(names, key=lambda n: tuple(int(x) for x in n.split('::')[-1].split('.'))) if names else None


PATH_SHAPES = f'''
geo = hou.pwd().geometry()
for prim in geo.prims():
    if prim.attribValue('name') != 'Primitives.shp':
        continue
    inner = prim.getEmbeddedGeometry().freeze()
    if inner.findPrimAttrib('path') is None:
        inner.addAttrib(hou.attribType.Prim, 'path', '')
    inner.setPrimStringAttribValues('path', ['{BODY}'] * len(inner.prims()))
    prim.setEmbeddedGeometry(inner)
'''


def rig():
    """A hips + four-link tail, Motion Effects Jiggle on the tail, a ball
    (Apex Primitives) on its tip: the shape th::animate places."""
    obj = hou.node('/obj').createNode('geo', 'tail')
    sk = obj.createNode('attribwrangle', 'skeleton')
    sk.parm('class').set(0)
    sk.parm('snippet').set(
        'matrix3 m = ident(); int prev = -1;\n'
        'for (int i = 0; i < 5; i++) {\n'
        '  int p = addpoint(0, set(0, 1, -0.3 * i));\n'
        '  setpointattrib(0, "name", p, i == 0 ? "hips" : "tail" + itoa(i));\n'
        '  setpointattrib(0, "transform", p, m);\n'
        '  if (prev >= 0) addprim(0, "polyline", prev, p);\n'
        '  prev = p;\n'
        '}\n')
    jn = obj.createNode(newest('apex_joints'), 'apex_joints')
    jn.setInput(0, sk)
    me = obj.createNode(newest('apex_motion_effects'), 'jiggle')
    me.setInput(0, jn)
    me.parm('updatemode').set('live')
    for k, v in {'effect': 'jiggle', 'freq': 1.5, 'damp': 0.25, 'strength': 1.2}.items():
        me.parm(k).set(v)
    me.parm('targets').set(1)
    me.parm('target_1').set('tail*')
    me.parm('follows_1').set('')
    me.parm('strength_1').set(0.5)
    ball = obj.createNode(newest('apex_primitives'), 'ball')
    ball.setInput(0, me)
    ball.parm('prim_parent_1').set('tail4')
    pathed = obj.createNode('python', 'pathed')
    pathed.setInput(0, ball)
    pathed.parm('python').set(PATH_SHAPES)
    return pathed


def keys():
    """The hips swing and walk: a key on every frame 1..48."""
    import math
    entry = hou.Geometry()
    name = entry.addAttrib(hou.attribType.Prim, 'name', '')
    entry.addAttrib(hou.attribType.Global, 'rig_path', '')
    entry.setGlobalAttribValue('rig_path', '/hero.char/Base.rig')
    for chan, fn in (('hips_ry', lambda f: 30.0 * math.sin(f * 0.3)),
                     ('hips_tx', lambda f: 0.4 * math.sin(f * 0.2))):
        c = entry.createChannelPrim()
        c.setAttribValue(name, chan)
        frames = list(range(FIRST, LAST + 1))
        for f in frames:
            c.insertKey(float(f))
        c.setKeyValues([fn(f) for f in frames])
    geo = hou.Geometry()
    geo.packToFolder('/animation/default.clip/_hero.char_Base', entry)
    return geo


def build(stage, r):
    animate = stage.createNode('th::animate::2.0', 'animate')
    animate.parm('shapes').set('Primitives')
    dive = animate.node('anim_dive')
    dive.node('import_rigs').destroy()
    merge = dive.createNode('object_merge', 'rig')
    merge.parm('objpath1').set(r.path())
    add = dive.createNode('apex::sceneaddcharacter', 'add')
    add.setInput(1, merge)
    add.parm('charactername').set('hero')
    dive.node('scene_animate').setInput(0, add)
    dive.node('scene_animate').parm('animation').set(keys())
    return animate


def points(stage, frame):
    from pxr import UsdGeom, Usd
    pts = []
    for prim in Usd.PrimRange(stage.GetPrimAtPath('/CHAR')) if stage.GetPrimAtPath('/CHAR') else []:
        if prim.IsA(UsdGeom.PointBased):
            v = UsdGeom.PointBased(prim).GetPointsAttr().Get(frame)
            if v is not None:
                pts += [round(float(c), 6) for p in v for c in p]
    return pts


def _flat(g, depth=0):
    o = list(g.pointFloatAttribValues('P')) if g.findPointAttrib('P') else []
    for pr in g.prims():
        if isinstance(pr, hou.PackedPrim) and depth < 6:
            try:
                eg = pr.getEmbeddedGeometry()
            except Exception:
                eg = None
            if eg is None:
                continue
            t = pr.fullTransform()
            sub = _flat(eg, depth + 1)
            for i in range(0, len(sub), 3):
                v = hou.Vector3(sub[i:i + 3]) * t
                o += [round(v[0], 6), round(v[1], 6), round(v[2], 6)]
    return o


def main():
    install()
    try:
        from TumbleRig import sim_start  # noqa: F401
    except ImportError:
        print('SKIP  TumbleRig is not installed')
        return 0
    from TumbleRig import sim_start
    if PART == 'plain':
        os.environ['TUMBLERIG_NO_SIM_REPLAY'] = '1'
    hou.hscript(f'frange {FIRST} {LAST}')
    hou.playbar.setFrameRange(FIRST, LAST)
    stage = hou.node('/stage')
    r = rig()
    if PART in ('bypass', 'invoke_bypass'):
        r.parent().node('jiggle').bypass(True)
    animate = build(stage, r)
    res, info = {}, {'part': PART}
    if PART in ('invoke', 'invoke_bypass'):
        # the same rig and keys through a plain Scene Invoke (no th::animate)
        dive = animate.node('anim_dive')
        inv = hou.node('/obj').createNode('geo', 'plain').createNode('object_merge', 'm')
        inv.parm('objpath1').set(dive.node('scene_animate').path())
        si = inv.parent().createNode('apex::sceneinvoke::2.0', 'si')
        si.setInput(0, inv)
        si.parm('frame').setExpression('$FF')
        si.parm('shapepattern').set('Primitives')
        for f in range(FIRST, LAST + 1):
            hou.setFrame(f)
            g = si.geometry()
            res[str(f)] = _flat(g)

    if PART == 'dump':
        for n in animate.node('points_sops').children():
            if n.type().name().startswith('apex::sceneinvoke'):
                info[n.name()] = {p.name(): p.evalAsString() for p in n.parms() if not p.isAtDefault()}
        info['scene_input'] = animate.node('points_sops/scene').evalParm('input')
    elif PART.startswith('sop:'):
        placed = animate.node('points_sops/placed')
        for f in [int(x) for x in PART[4:].split(',')]:
            hou.setFrame(f)
            res[str(f)] = _flat(sim_start.cook(placed, start=1))
    elif PART.startswith('list:'):
        inv = animate.node('points_sops/invoke')
        info['cooks'] = []
        for f in [int(x) for x in PART[5:].split(',')]:
            hou.setFrame(f)
            res[str(f)] = points(animate.stage(), f)
            info['cooks'].append((f, inv.cookCount() if inv is not None else None))
            res['sop' + str(f)] = _flat(animate.node('points_sops/placed').geometry())
            res['cur' + str(f)] = _flat(animate.node('points_sops/CURRENT').geometry())
        from tumblepipe.pipe.houdini.lops import animate_points
        info['shot_start'] = animate_points.sim_start_frame(animate.node('points_sops'))
        info['replay_start'] = sim_start.replay_start(info['shot_start'])
    elif PART in ('ref', 'fresh', 'plain', 'ooo', 'bypass'):
        frames = list(range(FIRST if PART in ('ref', 'ooo', 'bypass') else MID, LAST + 1))
        if PART == 'ooo':
            random.Random(3).shuffle(frames)
        for f in frames:
            hou.setFrame(f)
            res[str(f)] = points(animate.stage(), f)
    elif PART in ('cfx_ref', 'cfx'):
        from pxr import UsdGeom
        character = stage.createNode('sopimport', 'cfx_character')
        character.parm('soppath').set(animate.node('points_sops/placed').path())
        for f in range(FIRST if PART == 'cfx_ref' else MID, LAST + 1):
            hou.setFrame(f)
            pts = []
            for prim in character.stage().Traverse():
                if prim.IsA(UsdGeom.PointBased):
                    v = UsdGeom.PointBased(prim).GetPointsAttr().Get(f)
                    if v is not None:
                        pts += [round(float(c), 6) for p in v for c in p]
            res[str(f)] = pts
    elif PART == 'rop':
        out = Path(OUT).with_suffix('.usd')
        rop = stage.createNode('usd_rop', 'export')
        rop.setInput(0, animate)
        rop.parm('lopoutput').set(str(out).replace('\\', '/'))
        rop.parm('trange').set(1)
        rop.parmTuple('f').deleteAllKeyframes()
        rop.parmTuple('f').set((MID, LAST, 1))
        rop.render()
        from pxr import Usd
        s = Usd.Stage.Open(str(out))
        for f in range(MID, LAST + 1):
            res[str(f)] = points(s, f)
    info['stats'] = sim_start.stats()
    info['errors'] = list(animate.errors())
    info['n'] = len(next(iter(res.values()), []))
    if not info['n']:
        info['prims'] = [str(p.GetPath()) for p in animate.stage().Traverse()][:30]
        info['warnings'] = list(animate.warnings())[:5]
        for nm in ('points_sops/scene', 'points_sops/placed'):
            n = animate.node(nm)
            if n is not None:
                info[nm] = [list(n.warnings())[:3], list(n.errors())[:3], [p.attribValue('name') if n.geometry().findPrimAttrib('name') else '' for p in n.geometry().prims()][:8]]
        try:
            sh = [p.attribValue('name') for p in animate.node('anim_dive/output0').geometry().prims()]
            info['dive'] = sh[:10]
        except Exception as e:
            info['dive'] = repr(e)
    Path(OUT).write_text(json.dumps({'info': info, 'frames': res}))
    print(json.dumps(info))
    return 0


try:
    code = main()
except Exception:
    import traceback
    traceback.print_exc()
    code = 1
sys.exit(code)
