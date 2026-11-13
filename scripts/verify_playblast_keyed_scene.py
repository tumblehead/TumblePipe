#!/usr/bin/env hython
"""Verify th::playblast (SOP) with a keyed APEX scene wired into it.

th::playblast invokes an APEX scene itself (``objects/from_scene``: its
``scene`` Switch asks ``lops.animate_invoke.is_scene`` of its input and
takes the node's own Scene Invoke, Shapes ``Base``, when it is one). That
is the check that read a keyed scene as "invoked already" (a keyed Scene
Animate adds a root ``animation`` folder beside the ``<name>.char`` ones,
and the old check wanted every root folder to be a character): the
playblast then drew the raw scene, the rigs' control shape library with it,
and nothing moved. ``scripts/verify_animate_keyed_scene.py`` cooks the same
scene through th::animate; ``tests/test_animate_invoke.py`` pins the rule
without Houdini.

TumbleRig's example noodle and figure stand in for published rigs, each
one's render shape given a pipeline path, ``/CHAR/hero/geo/body``, its
``Main`` control keyed x: 0 -> 2 over frames 1..24. The noodle's render
shape is ``Base``, as a published rig's is, and runs through the HDA as
shipped. The figure's is ``Primitives`` (renaming it to ``Base`` unbinds it
from the rig: it stays put), so its playblast's own invoke is set to
Shapes ``Primitives``; the scene check under test is the same. Each goes Scene Add Character -> Scene Animate (keyed) ->
th::playblast, and the checks read what the playblast would draw, without
a TumbleView render:

* the keyed scene has its ``animation`` folder (else this proves nothing);
* the playblast's ``scene`` Switch takes its own invoke (input 1);
* the geometry it draws is the character's render shape alone: no
  control shape library, no joint or spline viz, no rig;
* its ``live`` stage (the input on top at ``/live``) holds that one body,
  and the body moves by 2 in x from frame 1 to 24.

Without TumbleRig the checks are skipped. Needs a Houdini license; run with
TumbleRig installed once (``.ci/preflight.py --only hython`` sets this up):

    HOUDINI_PACKAGE_DIR=<dir with only a tumblerig package>
    HOUDINI_USER_PREF_DIR=<scratch>/houdini22.0 HOUDINI_USE_HFS_OCL=0
    PYTHONPATH=<repo>/python TH_PIPELINE_PATH=<repo>
    hython scripts/verify_playblast_keyed_scene.py

Prints one line per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_animate_keyed_scene as animate  # noqa: E402  (install, keys, example types)

# TumbleRig example -> its render shape.
RIGS = {'noodle': 'Base', 'figure': 'Primitives'}
BODY = animate.BODY
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)




def build(label, shape):
    """Rig -> Scene Add Character -> keyed Scene Animate -> th::playblast,
    in one SOP network."""
    obj = hou.node('/obj').createNode('geo', f'playblast_{label}')
    rig = obj.createNode(animate.example_type(label), 'rig')
    pathed = obj.createNode('python', 'published')
    pathed.setInput(0, rig)
    pathed.parm('python').set(animate.path_shapes(shape))
    add = obj.createNode('apex::sceneaddcharacter', 'add')
    add.setInput(1, pathed)
    add.parm('charactername').set('hero')
    scene_animate = obj.createNode('apex::sceneanimate', 'scene_animate')
    scene_animate.setInput(0, add)
    scene_animate.parm('animation').set(animate.keys())
    playblast = obj.createNode('th::playblast::2.0', 'playblast')
    playblast.setInput(0, scene_animate)
    playblast.setDisplayFlag(True)
    if shape != 'Base':
        # Not a published rig's render shape: point the HDA's invoke at it.
        playblast.allowEditingOfContents()
        playblast.node('objects/from_scene/invoke').parm('shapepattern').set(shape)
    return scene_animate, playblast


def drawn_names(geo):
    """Every shape the drawn geometry holds, folder by folder: the names of
    packed folders and of what is inside each, as ``folder/name``."""
    names = []
    for prim in geo.prims():
        name = prim.attribValue('name') if geo.findPrimAttrib('name') else ''
        if prim.type() == hou.primType.PackedGeometry:
            inner = prim.getEmbeddedGeometry()
            if inner is not None and inner.findPrimAttrib('name') is not None:
                names += [f'{name}/{n}' for n in inner.primStringAttribValues('name')]
                continue
        names.append(name)
    return names


def shape_stem(name):
    """The rig shape an invoke output is: ``Base`` for ``Base_output_Base.shp``."""
    stem = name[:-len('.shp')] if name.endswith('.shp') else name
    return stem.split('_output_', 1)[-1]


def gprims(stage_):
    return sorted(
        str(prim.GetPath()) for prim in stage_.Traverse()
        if prim.GetTypeName() in ('Mesh', 'BasisCurves', 'Points', 'NurbsCurves')
    )


def centre_x(live, path, frame):
    from pxr import UsdGeom
    hou.setFrame(frame)
    prim = live.stage().GetPrimAtPath(path)
    if not prim.IsValid():
        return None
    points = UsdGeom.PointBased(prim).GetPointsAttr().Get(frame)
    if not points:
        return None
    xs = [p[0] for p in points]
    return (min(xs) + max(xs)) / 2


def run_rig(label, shape):
    if animate.example_type(label) is None:
        print(f'SKIP  TumbleRig has no {label} example: nothing to stand in')
        return
    scene_animate, playblast = build(label, shape)
    scene = scene_animate.geometry()
    folders = [p.attribValue('name') for p in scene.prims()] if scene is not None else []
    check(f'{label}: the keyed scene holds its clip folder beside the character',
          'animation' in folders and 'hero.char' in folders, folders)
    switch = playblast.node('objects/from_scene/scene')
    hou.setFrame(1)
    drawn = switch.geometry()
    check(f'{label}: th::playblast takes its own invoke for the keyed scene',
          switch.evalParm('input') == 1, f"input {switch.evalParm('input')}")
    errors = list(switch.errors()) + list(playblast.errors())
    check(f'{label}: th::playblast cooks', not errors, errors[:1])
    names = drawn_names(drawn) if drawn is not None else []
    stems = [shape_stem(n.rsplit('/', 1)[-1]) for n in names]
    check(f'{label}: the playblast draws the render shape alone',
          stems == [shape] and all(n.startswith('hero.char/') for n in names), names[:8])
    live = playblast.node('stage/live')
    prims = gprims(live.stage()) if live is not None else []
    bodies = [p for p in prims if p.endswith(BODY)]
    check(f'{label}: its stage holds the one body under /live',
          len(prims) == 1 and len(bodies) == 1 and prims[0].startswith('/live'),
          f'{len(prims)} prims: {prims[:6]}')
    path = bodies[0] if bodies else BODY
    first = centre_x(live, path, 1)
    last = centre_x(live, path, 24)
    moved = None if first is None or last is None else round(last - first, 3)
    check(f'{label}: the body moves with its keys',
          moved is not None and abs(moved - 2.0) < 0.05, f'moved {moved}')
    hou.setFrame(1)


def main():
    animate.install()
    if not any(animate.example_type(kind) for kind in RIGS):
        print('SKIP  TumbleRig is not installed: no TumbleRig rig to stand in')
        return
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)
    hou.setFrame(1)
    for label, shape in RIGS.items():
        run_rig(label, shape)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
