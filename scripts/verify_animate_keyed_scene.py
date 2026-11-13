#!/usr/bin/env hython
"""Verify th::animate with a keyed Scene Animate, with and without a
Scene Invoke in its dive.

A dive that ends in its Scene Animate is invoked by th::animate itself
(``lops.animate_invoke``). Once the Scene Animate has keys, the scene it
outputs holds a root ``animation`` folder (the clip) beside its
``<name>.char`` folders, and ``is_scene`` read that as "invoked already":
the raw scene went to the stage, the rigs' control shape library (a ball,
cubes, circles) with it, and nothing moved (Remy, tumblepipe__bugs
2026-11-13). An unkeyed scene has no such folder, so a fresh node looked
fine.

A dive with its own Scene Invoke on Houdini's default Shapes (``*``), as
Remy added to get going, output every shape of the rig: the joint and
spline viz too, which TumbleRig's noodle and figure draw as spheres, not
points, so they reached a root ``/Geometry``. th::animate's Shapes is the
one rule for both dives now (``animate_points.placed_shapes``).
``tests/test_animate_invoke.py`` and ``tests/test_animate_points.py`` pin the
rules without Houdini; this cooks the real nodes.

TumbleRig's example figure and noodle stand in for published rigs (each
one's render shape given a pipeline path, ``/CHAR/hero/geo/body``, and
th::animate's Shapes set to it), their ``Main`` control keyed x: 0 -> 2 over
frames 1..24. For each rig and dive:

1. ``default``: what a hand-placed th::animate builds (the HDA default:
   Import Rigs -> Scene Animate -> output, no Scene Invoke);
2. ``invoke``: the same with its own Scene Invoke on Houdini's default
   Shapes ``*`` (dives from before th::animate invoked the scene, old
   project templates, Remy's);
3. ``bypassed``: that Scene Invoke bypassed;

the checks: the keyed scene has its ``animation`` folder (else this proves
nothing), th::animate cooks without errors, only ``/CHAR/hero/geo/body``
reaches the stage (no control shapes, no joint viz, no root ``/Geometry``),
and the body moves by 2 in x from frame 1 to 24.

Without TumbleRig the checks are skipped. Needs a Houdini license; run with
an empty package dir so Houdini's own APEX nodes load, and TumbleRig
installed once (two TumbleRig installs on the paths load their CUDA SOPs
twice, and the noodle's delta mush crashes hython):

    HOUDINI_PACKAGE_DIR=<empty dir> HOUDINI_PATH="<repo>;&" \
        PYTHONPATH=<repo>/python TH_PIPELINE_PATH=<repo> \
        hython scripts/verify_animate_keyed_scene.py

Prints one line per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

# TumbleRig example -> its render shape (the rest is helpers and controls).
RIGS = {'figure': 'Primitives', 'noodle': 'Base'}
BODY = '/CHAR/hero/geo/body'
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages (apex: the Scene SOPs th::animate uses) and
    the expanded HDA sources, which must win over any compiled .hda."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def example_type(kind):
    prefix = f'th::apex_rigExample_{kind}'
    names = sorted(n for n in hou.sopNodeTypeCategory().nodeTypes() if n.startswith(prefix))
    return names[-1] if names else None


def path_shapes(shape):
    return f'''
geo = hou.pwd().geometry()
for prim in geo.prims():
    if prim.attribValue('name') != '{shape}.shp':
        continue
    inner = prim.getEmbeddedGeometry().freeze()
    if inner.findPrimAttrib('path') is None:
        inner.addAttrib(hou.attribType.Prim, 'path', '')
    inner.setPrimStringAttribValues('path', ['{BODY}'] * len(inner.prims()))
    prim.setEmbeddedGeometry(inner)
'''


def keys():
    """Main_t keyed as Animate keys a control: x 0 -> 2, y and z 0."""
    entry = hou.Geometry()
    name = entry.addAttrib(hou.attribType.Prim, 'name', '')
    entry.addAttrib(hou.attribType.Global, 'rig_path', '')
    entry.setGlobalAttribValue('rig_path', '/hero.char/Base.rig')
    for axis in 'xyz':
        channel = entry.createChannelPrim()
        channel.setAttribValue(name, f'Main_t{axis}')
        channel.insertKey(1)
        channel.insertKey(24)
        channel.setKeyValues([0.0, 2.0] if axis == 'x' else [0.0, 0.0])
    geo = hou.Geometry()
    geo.packToFolder('/animation/default.clip/_hero.char_Base', entry)
    return geo


def build(stage, rig, shape, label, mode):
    """A th::animate as placed by hand (OnCreated builds its dive), the
    rig in place of Import Rigs, the Scene Animate keyed."""
    animate = stage.createNode('th::animate::2.0', f'animate_{label}_{mode}')
    animate.parm('shapes').set(shape)
    dive = animate.node('anim_dive')
    scene_animate = dive.node('scene_animate')
    output = dive.node('output0')
    rigs = dive.node('import_rigs')
    check(f'{label} {mode}: a new th::animate builds Import Rigs -> Scene Animate -> output',
          rigs is not None and scene_animate is not None and output is not None
          and scene_animate.input(0) == rigs and output.input(0) == scene_animate)
    rigs.destroy()
    merge = dive.createNode('object_merge', 'rig')
    merge.parm('objpath1').set(rig.path())
    add = dive.createNode('apex::sceneaddcharacter', 'add')
    add.setInput(1, merge)
    add.parm('charactername').set('hero')
    scene_animate.setInput(0, add)
    scene_animate.parm('animation').set(keys())
    if mode != 'default':
        # Houdini's defaults, Shapes `*` among them, as an artist adds one
        invoke = dive.createNode('apex::sceneinvoke::2.0', 'sceneinvoke1')
        invoke.setInput(0, scene_animate)
        output.setInput(0, invoke)
        invoke.bypass(mode == 'bypassed')
    return animate, scene_animate


def gprims(stage_):
    return sorted(
        str(prim.GetPath()) for prim in stage_.Traverse()
        if prim.GetTypeName() in ('Mesh', 'BasisCurves', 'Points', 'NurbsCurves')
    )


def centre_x(animate, frame):
    from pxr import UsdGeom
    hou.setFrame(frame)
    prim = animate.stage().GetPrimAtPath(BODY)
    if not prim.IsValid():
        return None
    points = UsdGeom.PointBased(prim).GetPointsAttr().Get(frame)
    if not points:
        return None
    xs = [p[0] for p in points]
    return (min(xs) + max(xs)) / 2


def run_rig(stage, label, shape):
    from tumblepipe.pipe.houdini.lops import animate_invoke
    type_name = example_type(label)
    if type_name is None:
        print(f'SKIP  TumbleRig has no {label} example: nothing to stand in')
        return
    obj = hou.node('/obj').createNode('geo', f'rig_{label}')
    pathed = obj.createNode('python', 'pathed')
    pathed.setInput(0, obj.createNode(type_name))
    pathed.parm('python').set(path_shapes(shape))
    for mode in ('default', 'invoke', 'bypassed'):
        animate, scene_animate = build(stage, pathed, shape, label, mode)
        name = f'{label} {mode}'
        scene = scene_animate.geometry()
        folders = [p.attribValue('name') for p in scene.prims()] if scene is not None else []
        check(f'{name}: the keyed scene holds its clip folder beside the character',
              'animation' in folders and 'hero.char' in folders, folders)
        check(f'{name}: is_scene reads the keyed scene as a scene to invoke',
              animate_invoke.is_scene(scene)
              and animate_invoke.scene_characters(scene) == ['hero'])
        first = centre_x(animate, 1)
        errors = animate.errors()
        check(f'{name}: th::animate cooks', not errors, errors[:1])
        prims = gprims(animate.stage())
        check(f'{name}: only the character reaches the stage', prims == [BODY],
              f'{len(prims)} prims: {prims[:6]}')
        last = centre_x(animate, 24)
        moved = None if first is None or last is None else round(last - first, 3)
        check(f'{name}: the character moves with its keys',
              moved is not None and abs(moved - 2.0) < 0.05, f'moved {moved}')
        hou.setFrame(1)


def main():
    install()
    if not any(example_type(kind) for kind in RIGS):
        print('SKIP  TumbleRig is not installed: no TumbleRig rig to stand in')
        return
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)
    hou.setFrame(1)
    stage = hou.node('/stage')
    for label, shape in RIGS.items():
        run_rig(stage, label, shape)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
