#!/usr/bin/env hython
"""Verify th::animate's stage normals follow the animation, whatever kind
of N the rigs put out.

th::animate imports the rigs' first frame once and then copies only what
moves onto it every frame (``lops.animate_points``). It copied point N and
nothing else, so a rig putting out vertex N (a Normal SOP's default, and
what TumbleRig's figure graph and some published rigs put out) kept the
first frame's normals while its points moved: the shading turned with the
world, not the character. Subdivided meshes ignore authored normals, which
hid it until th::animate stopped subdividing everything (v1.106.0).

TumbleRig's example figure (its graph puts out vertex N) and noodle (point
N) stand in for published rigs, their render shape given a pipeline path
(``/CHAR/hero/geo/body``) and their ``Main`` control keyed to turn 180
degrees in Y over frames 1..24. The cases:

- ``figure own``: the HDA's own Scene Invoke (a hand-placed th::animate's
  dive: Import Rigs -> Scene Animate -> output), vertex N in packed
  folders;
- ``figure dive``: a dive with its own Scene Invoke (packed folders);
- ``noodle own``: point N in packed folders;
- ``after <kinds>``: a dive whose Scene Invoke (unpacked) is followed by a
  Python SOP that puts N on the points, the vertices, both, or the
  primitives (the unpacked read path);
- ``moved``: packed folders moved by a Transform SOP after the Scene
  Invoke (th::animate then reads its unpacked copy, points_sops/CURRENT).

Checked at frames 1, 12 and 24: th::animate cooks; the character turned;
its normals are those a plain SOP Import of the same geometry authors at
that frame (the full conversion th::animate stands in for), interpolation
and values; no stamp primvar is left. Primitive N cannot follow; for that
case th::animate must warn instead.

Without TumbleRig the checks are skipped. Needs a Houdini license; run as
verify_animate_keyed_scene.py says (preflight's hython stage does).
Prints one line per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

BODY = '/CHAR/hero/geo/body'
FRAMES = (1, 12, 24)
ATTRIBS = 'P N visibility bounds Cd Alpha transform'
STAMPS = ('primvars:__th_pt', 'primvars:__th_vtx')
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages (apex) and the expanded HDA sources."""
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


# N of the given kinds, computed by the Normal SOP, on unpacked geometry.
NORMALS_AFTER = '''
geo = hou.pwd().geometry()
for attrib in (geo.findPointAttrib('N'), geo.findVertexAttrib('N'), geo.findPrimAttrib('N')):
    if attrib is not None:
        attrib.destroy()
plain = geo.freeze()
verb = hou.sopNodeTypeCategory().nodeVerb('normal')
for kind, index, of, setter, getter in (
    ('point', 0, hou.attribType.Point, 'setPointFloatAttribValuesFromString', 'pointFloatAttribValuesAsString'),
    ('vertex', 1, hou.attribType.Vertex, 'setVertexFloatAttribValuesFromString', 'vertexFloatAttribValuesAsString'),
    ('primitive', 2, hou.attribType.Prim, 'setPrimFloatAttribValuesFromString', 'primFloatAttribValuesAsString'),
):
    if kind not in {kinds!r}:
        continue
    verb.setParms({{'type': index}})
    out = hou.Geometry()
    verb.execute(out, [plain])
    geo.addAttrib(of, 'N', (0.0, 0.0, 0.0), transform_as_normal=True)
    getattr(geo, setter)('N', getattr(out, getter)('N'))
'''


def keys():
    """Main turned 0 -> 180 degrees in Y over 1..24."""
    entry = hou.Geometry()
    name = entry.addAttrib(hou.attribType.Prim, 'name', '')
    entry.addAttrib(hou.attribType.Global, 'rig_path', '')
    entry.setGlobalAttribValue('rig_path', '/hero.char/Base.rig')
    for axis in 'xyz':
        channel = entry.createChannelPrim()
        channel.setAttribValue(name, f'Main_r{axis}')
        channel.insertKey(1)
        channel.insertKey(24)
        channel.setKeyValues([0.0, 180.0] if axis == 'y' else [0.0, 0.0])
    geo = hou.Geometry()
    geo.packToFolder('/animation/default.clip/_hero.char_Base', entry)
    return geo


def rig_node(label, tag, shape):
    obj = hou.node('/obj').createNode('geo', f'rig_{label}_{tag}')
    pathed = obj.createNode('python', 'pathed')
    pathed.setInput(0, obj.createNode(example_type(label)))
    pathed.parm('python').set(path_shapes(shape))
    return pathed


def build(stage, rig, shape, tag, after=None):
    """A hand-placed th::animate (OnCreated builds its dive), the rig in
    place of Import Rigs, keyed. `after`: None (th::animate invokes),
    'invoke' (a Scene Invoke in the dive), ('normals', kinds) or 'moved'."""
    animate = stage.createNode('th::animate::2.0', f'animate_{tag}')
    animate.parm('shapes').set(shape)
    dive = animate.node('anim_dive')
    scene_animate = dive.node('scene_animate')
    output = dive.node('output0')
    dive.node('import_rigs').destroy()
    merge = dive.createNode('object_merge', 'rig')
    merge.parm('objpath1').set(rig.path())
    add = dive.createNode('apex::sceneaddcharacter', 'add')
    add.setInput(1, merge)
    add.parm('charactername').set('hero')
    scene_animate.setInput(0, add)
    scene_animate.parm('animation').set(keys())
    if after is not None:
        invoke = dive.createNode('apex::sceneinvoke::2.0', 'sceneinvoke1')
        invoke.setInput(0, scene_animate)
        invoke.parm('shapepattern').set(shape)
        last = invoke
        if isinstance(after, tuple):
            invoke.parm('outputmode').set('unpacked')
            last = dive.createNode('python', 'normals')
            last.parm('python').set(NORMALS_AFTER.format(kinds=after[1]))
            last.setInput(0, invoke)
        elif after == 'moved':
            invoke.parm('outputmode').set('packedfolders')
            last = dive.createNode('xform', 'moved')
            last.parm('ty').set(0.25)
            last.setInput(0, invoke)
        output.setInput(0, last)
    return animate


def reference(stage, animate, tag):
    """A plain SOP Import of what th::animate imports (points_sops/placed),
    unpacked, at the current frame."""
    obj = hou.node('/obj').createNode('geo', f'ref_{tag}')
    merge = obj.createNode('object_merge', 'placed')
    merge.parm('objpath1').set(animate.path() + '/points_sops/placed')
    one = obj.createNode('unpack', 'unpack1')
    one.setInput(0, merge)
    two = obj.createNode('unpack', 'unpack2')
    two.setInput(0, one)
    two.setDisplayFlag(True)
    ref = stage.createNode('sopimport', f'ref_{tag}')
    ref.parm('soppath').set(two.path())
    ref.parm('enable_attribs').set(1)
    ref.parm('attribs').set(ATTRIBS)
    return ref


def mesh(stage, frame):
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(BODY)
    if not prim.IsValid() or not prim.IsA(UsdGeom.Mesh):
        return None
    geo = UsdGeom.Mesh(prim)
    normals = geo.GetNormalsAttr()
    return {
        'points': np.asarray(geo.GetPointsAttr().Get(frame), np.float64),
        'normals': (np.asarray(normals.Get(frame), np.float64)
                    if normals.HasAuthoredValue() else None),
        'interp': geo.GetNormalsInterpolation() if normals.HasAuthoredValue() else None,
        'stamps': [name for name in STAMPS if prim.GetAttribute(name)],
    }


def run(stage, label, shape, tag, after=None, follows=True):
    animate = build(stage, rig_node(label, tag, shape), shape, f'{label}_{tag}', after)
    ref = reference(stage, animate, f'{label}_{tag}')
    name = f'{label} {tag}'
    first = None
    for frame in FRAMES:
        hou.setFrame(frame)
        got, want = mesh(animate.stage(), frame), mesh(ref.stage(), frame)
        errors = animate.errors()
        if frame == FRAMES[0]:
            check(f'{name}: th::animate cooks and puts out the character',
                  not errors and got is not None and want is not None, errors[:1])
            if got is None or want is None:
                return
            first = got['points']
            check(f'{name}: the static import authors normals as the SOP Import does',
                  got['interp'] == want['interp'] and want['interp'] is not None,
                  f"{got['interp']} vs {want['interp']}")
            check(f'{name}: no stamp primvar is left', not got['stamps'], got['stamps'])
            continue
        if got is None or want is None:
            check(f'{name} f{frame}: the character is on both stages', False)
            continue
        turned = float(np.abs(got['points'] - first).max())
        same_points = got['points'].shape == want['points'].shape and \
            float(np.abs(got['points'] - want['points']).max()) < 1e-4
        check(f'{name} f{frame}: the character turned, its points as the SOP Import\'s',
              turned > 0.1 and same_points, f'moved {turned:.3f}')
        if got['normals'] is None or want['normals'] is None \
                or got['normals'].shape != want['normals'].shape:
            check(f'{name} f{frame}: the normals follow', False,
                  f"{None if got['normals'] is None else got['normals'].shape} vs "
                  f"{None if want['normals'] is None else want['normals'].shape}")
            continue
        diff = float(np.abs(got['normals'] - want['normals']).max())
        if follows:
            check(f'{name} f{frame}: the normals follow ({got["interp"]})', diff < 1e-4,
                  f'max difference {diff:.4f}')
    if not follows:
        warnings = animate.node('apply').warnings()
        check(f'{name}: normals that cannot follow are named in a warning',
              any(BODY in w and 'first frame' in w for w in warnings), warnings[:1])
    hou.setFrame(1)


def main():
    install()
    if example_type('figure') is None or example_type('noodle') is None:
        print('SKIP  TumbleRig is not installed: no TumbleRig rig to stand in')
        return
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)
    hou.setFrame(1)
    stage = hou.node('/stage')
    run(stage, 'figure', 'Primitives', 'own')
    run(stage, 'figure', 'Primitives', 'dive', after='invoke')
    run(stage, 'noodle', 'Base', 'own')
    for kinds in (('point',), ('vertex',), ('point', 'vertex')):
        run(stage, 'figure', 'Primitives', 'after_' + '_'.join(kinds), after=('normals', kinds))
    run(stage, 'figure', 'Primitives', 'after_primitive', after=('normals', ('primitive',)),
        follows=False)
    run(stage, 'figure', 'Primitives', 'moved', after='moved')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
