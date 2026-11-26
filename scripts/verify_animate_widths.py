#!/usr/bin/env hython
"""Verify th::animate carries curve and point widths, saved over a range.

Remy 2026-11-16: "The th animate node doesn't transfer width attributes", so
hair and curves in the animation scene got the default width. th::animate's
three SOP Imports (static, remainder, full) read only the attributes their
Attributes pattern names, and ``width`` and ``pscale`` were not among them:
every curve got the Set Missing Widths value (0.01), whatever its rig said.
And ``apply`` copied only points and normals every frame, so widths that
change would have kept the first frame's even with the pattern fixed. Now
the imports read ``width pscale`` and ``apply`` copies each frame's widths
(``animate_widths.read_widths``, ``animate_points.width_offsets``);
``tests/test_animate_points.py`` pins the copy on plain layers, this saves
the real HDA with the real ROP.

The dive is plain SOPs, no rig: a character's curves with widths of each
kind a SOP Import reads (point ``width``, point ``pscale``, primitive
``width``, detail ``width``, vertex ``width``), curves with none and a mesh, each with its
pipeline path, every width changing every frame and every shape gaining a
curve from frame 6 (a topology change: that frame on is ``full_import``'s).
Laid out as packed folders (the Scene Invoke's output, read leaf by leaf)
one kind per node and all kinds in one, and as unpacked geometry (SOPs
after the Scene Invoke). For each, the range 1-10 saved by a USD ROP:

- every curves prim has a widths sample on every frame, with the
  interpolation a plain SOP Import gives that geometry (per point, per
  curve, one), the same on every frame;
- each frame's widths are those a plain SOP Import of the dive's
  geometry at that frame authors (unpacked as th::animate unpacks it), value
  for value, and for the kinds with widths of their own, the frame's own
  widths, never the missing-width default;
- the extent holds the widest curve.

Needs a Houdini license; preflight's hython stage runs it. Prints one line
per check and exits 1 on any failure.
"""

import sys
import tempfile
import traceback
from pathlib import Path

import hou
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
CHECKED = []
CHANGE = 6
FIRST, LAST = 1, 10
GEO = '/CHAR/hero/geo'
# What a SOP Import authors without any width: the HDA's Set Missing Widths.
MISSING = 0.01
ATTRIBS = 'P N visibility bounds Cd Alpha transform width pscale'
KINDS = ('point', 'pscale', 'prim', 'detail', 'vertex', 'bare', 'body')
# 'joint': vertex widths on curves chained end to start (a joint is one point
# of two vertices, which a SOP Import splits into one USD point per curve);
# only in its own cases.
ALL_KINDS = KINDS + ('joint',)

# The dive's geometry: one shape per kind in KINDS, each with its path,
# moving every frame, every width changing every frame, one more curve each
# from CHANGE. LAYOUT 'packed' gives packed folders (one character folder of
# packed shapes), 'flat' the shapes merged unpacked.
DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = hou.frame()
grow = 1.0 + 0.1 * frame
curves = 2 if frame < {change} else 3

def shape(kind, index):
    out = hou.Geometry()
    n_curves = curves
    if kind == 'joint':
        # curves chained end to start: each joint is one point, two vertices
        pts = [out.createPoint() for _ in range(3 * n_curves + 1)]
        for n, pt in enumerate(pts):
            pt.setPosition((0.3 * (n // 3) + (n % 3) / 3.0, 0.2 * (n % 2), 0.1 * index))
        for c in range(n_curves):
            poly = out.createPolygon()
            poly.setIsClosed(False)
            for v in range(4):
                poly.addVertex(pts[3 * c + v])
    elif kind == 'body':
        verb = hou.sopNodeTypeCategory().nodeVerb('box')
        verb.setParms({{'type': 1}})
        verb.execute(out, [])
    else:
        verb = hou.sopNodeTypeCategory().nodeVerb('line')
        verb.setParms({{'points': 4, 'dist': 1.0}})
        for c in range(curves):
            line = hou.Geometry()
            verb.execute(line, [])
            line.transform(hou.hmath.buildTranslate(0.3 * c, 0, 0.1 * index))
            out.merge(line)
    out.transform(hou.hmath.buildTranslate(2.0 * index + 0.05 * frame, 0.02 * frame, 0))
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', ['{geo}/' + kind] * len(out.prims()))
    count = len(out.points())
    if kind == 'point':
        out.addAttrib(hou.attribType.Point, 'width', 0.0)
        out.setPointFloatAttribValues('width', [0.02 * (1 + n % 3) * grow for n in range(count)])
    elif kind == 'pscale':
        out.addAttrib(hou.attribType.Point, 'pscale', 0.0)
        out.setPointFloatAttribValues('pscale', [0.015 * (1 + n % 2) * grow for n in range(count)])
    elif kind == 'prim':
        out.addAttrib(hou.attribType.Prim, 'width', 0.0)
        out.setPrimFloatAttribValues('width', [0.03 * (1 + p) * grow for p in range(len(out.prims()))])
    elif kind in ('vertex', 'joint'):
        out.addAttrib(hou.attribType.Vertex, 'width', 0.0)
        out.setVertexFloatAttribValues('width', [
            0.02 * (1 + (2 * c + v) % 4) * grow for c in range(n_curves) for v in range(4)])
    elif kind == 'detail':
        out.addAttrib(hou.attribType.Global, 'width', 0.0)
        out.setGlobalAttribValue('width', 0.04 * grow)
    return out

shapes = [shape(kind, KINDS.index(kind)) for kind in {kinds!r}]
if {layout!r} == 'flat':
    for one in shapes:
        geo.merge(one)
else:
    inner = hou.Geometry()
    for one in shapes:
        inner.createPacked('PackedGeometry').setEmbeddedGeometry(one)
    geo.createPacked('PackedGeometry').setEmbeddedGeometry(inner)
'''


def check(name, ok, info=''):
    """One check: only a failure prints (each scenario prints one line when
    done), so the stage's tail of the output shows what failed."""
    CHECKED.append(name)
    if not ok:
        print(f'FAIL  {name}  {info}', flush=True)
        FAILED.append(name)


def install():
    """Houdini's own packages and the expanded HDA sources."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def build(label, kinds, layout):
    """A th::animate whose dive outputs DIVE_GEO for `kinds`, nothing else,
    and a plain SOP Import of what it imports (points_sops/placed, unpacked
    twice as th::animate unpacks it)."""
    animate = hou.node('/stage').createNode('th::animate::2.0', f'animate_{label}')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(
        f'KINDS = {ALL_KINDS!r}\n'
        + DIVE_GEO.format(change=CHANGE, geo=GEO, kinds=tuple(kinds), layout=layout))
    output.setInput(0, source)
    obj = hou.node('/obj').createNode('geo', f'ref_{label}')
    merge = obj.createNode('object_merge', 'placed')
    merge.parm('objpath1').set(animate.path() + '/points_sops/placed')
    last = merge
    for name in ('unpack1', 'unpack2'):
        unpack = obj.createNode('unpack', name)
        unpack.setInput(0, last)
        last = unpack
    ref = hou.node('/stage').createNode('sopimport', f'ref_{label}')
    ref.parm('soppath').set(last.path())
    ref.parm('enable_attribs').set(1)
    ref.parm('attribs').set(ATTRIBS)
    ref.parm('enable_setmissingwidths').set(1)
    ref.parm('setmissingwidths').set(MISSING)
    ref.parm('enable_topologyhandling').set(1)
    ref.parm('topologyhandling').set('animated')
    return animate, ref


def save(animate, out):
    rop = hou.node('/out').createNode('usd', f'save_{animate.name()}')
    rop.parm('loppath').set(animate.path())
    rop.parm('lopoutput').set(str(out))
    rop.parm('trange').set(1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((FIRST, LAST, 1))
    rop.render()
    return rop


def vertex_widths(kind, frame):
    """The widths, sorted, of the USD points `kind` (vertex or joint) authors
    at `frame`, one per vertex. A joint's point is two vertices of different
    widths: a SOP Import gives each curve its own copy of the point, holding
    that curve's vertex width, and so must th::animate on every frame, before
    and after a topology change (it averaged them up to 2026-11-16, while the
    full import of a changed frame kept each vertex's own)."""
    grow = 1.0 + 0.1 * frame
    curves = 2 if frame < CHANGE else 3
    values = [[0.02 * (1 + (2 * c + v) % 4) * grow for v in range(4)] for c in range(curves)]
    return np.sort(np.asarray(values, np.float32).ravel())


def own_widths(kind, frame):
    """The distinct widths `kind` puts on its curves at `frame`, as USD
    widths (pscale doubled); None for a kind with none of its own."""
    grow = 1.0 + 0.1 * frame
    curves = 2 if frame < CHANGE else 3
    return {
        'point': {round(0.02 * k * grow, 5) for k in (1, 2, 3)},
        'pscale': {round(2 * 0.015 * k * grow, 5) for k in (1, 2)},
        'prim': {round(0.03 * (1 + p) * grow, 5) for p in range(curves)},
        'detail': {round(0.04 * grow, 5)},
        'vertex': {round(float(w), 5) for w in vertex_widths('vertex', frame)},
        'joint': {round(float(w), 5) for w in vertex_widths('joint', frame)},
    }.get(kind)


def verify(label, kinds, layout, work, merged_widths=True):
    """Save `kinds` laid out as `layout` and check every frame. With
    `merged_widths` the widths are a plain SOP Import's of the dive's
    geometry; without (packed shapes reading different attributes, whose
    merge loses some) every shape has its own, per point."""
    from pxr import Sdf, Usd, UsdGeom
    animate, ref = build(label, kinds, layout)
    out = work / f'{label}.usd'
    rop = save(animate, out)
    check(f'{label}: the ROP saves without errors', not rop.errors() and out.exists(),
          rop.errors())
    if not out.exists():
        return
    layer = Sdf.Layer.FindOrOpen(str(out))
    stage = Usd.Stage.Open(layer)
    frames = list(range(FIRST, LAST + 1))
    curve_kinds = [kind for kind in kinds if kind != 'body']
    for kind in curve_kinds:
        path = Sdf.Path(f'{GEO}/{kind}').AppendProperty('widths')
        samples = [int(t) for t in layer.ListTimeSamplesForPath(path)]
        check(f'{label}: {kind} has a widths sample on every frame', samples == frames, samples)
    # a flat dive merged its shapes itself: what that lost, nothing gives back
    own = not merged_widths or len(curve_kinds) == 1
    for frame in frames:
        hou.setFrame(frame)
        ref_stage = ref.stage()
        for kind in curve_kinds:
            path = f'{GEO}/{kind}'
            got_prim = stage.GetPrimAtPath(path)
            want_prim = ref_stage.GetPrimAtPath(path)
            if not got_prim or not want_prim:
                check(f'{label} frame {frame}: {kind} is on both stages', False,
                      (bool(got_prim), bool(want_prim)))
                continue
            curves = UsdGeom.Curves(got_prim)
            got = np.asarray(curves.GetWidthsAttr().Get(frame) or [], np.float32)
            want = np.asarray(UsdGeom.Curves(want_prim).GetWidthsAttr().Get(frame) or [],
                              np.float32)
            got_interp = curves.GetWidthsInterpolation()
            want_interp = UsdGeom.Curves(want_prim).GetWidthsInterpolation()
            points = np.asarray(curves.GetPointsAttr().Get(frame))
            if merged_widths:
                check(f'{label} frame {frame}: {kind} widths interpolation is a SOP Import\'s',
                      got_interp == want_interp, (got_interp, want_interp))
                check(f'{label} frame {frame}: {kind} widths are a SOP Import\'s, value for value',
                      got.shape == want.shape and np.allclose(got, want, atol=1e-6),
                      (got[:6].tolist(), want[:6].tolist()))
            else:
                check(f'{label} frame {frame}: {kind} has a width per point',
                      got_interp == 'vertex' and len(got) == len(points),
                      (got_interp, len(got), len(points)))
            if kind in ('vertex', 'joint'):
                want_sorted = vertex_widths(kind, frame)
                check(f'{label} frame {frame}: {kind} widths are its vertices\' per point',
                      got_interp == 'vertex' and got.shape == want_sorted.shape
                      and np.allclose(np.sort(got), want_sorted, atol=1e-6),
                      (got_interp, np.sort(got).tolist(), want_sorted.tolist()))
            expected = own_widths(kind, frame) if own else None
            if expected is None and not merged_widths:
                expected = {round(MISSING, 5)}  # no width of its own: the missing width
            if expected is not None:
                distinct = {round(float(w), 5) for w in got}
                check(f'{label} frame {frame}: {kind} has its own widths of the frame',
                      distinct == expected, (sorted(distinct), sorted(expected)))
            extent = np.asarray(curves.GetExtentAttr().Get(frame))
            widest = float(got.max()) if len(got) else 0.0
            holds = (len(extent) == 2 and len(points) > 0
                     and np.all(extent[0] <= points.min(axis=0) - widest / 2 + 1e-5)
                     and np.all(extent[1] >= points.max(axis=0) + widest / 2 - 1e-5))
            check(f'{label} frame {frame}: {kind} extent holds its widest curve', holds,
                  (extent.tolist(), widest))
    print(f'{label}: checked', flush=True)


def main():
    install()
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for kind in ('point', 'pscale', 'prim', 'detail', 'vertex'):
            verify(f'packed_{kind}', (kind, 'body'), 'packed', work)
        verify('packed_mixed', KINDS, 'packed', work, merged_widths=False)
        verify('packed_vertex_point', ('vertex', 'point', 'body'), 'packed', work,
               merged_widths=False)
        verify('flat_point', ('point', 'body'), 'flat', work)
        verify('flat_vertex', ('vertex', 'body'), 'flat', work)
        verify('packed_vertex_pscale', ('vertex', 'pscale', 'body'), 'packed', work,
               merged_widths=False)
        verify('packed_joint', ('joint', 'body'), 'packed', work)
        verify('packed_joint_point', ('joint', 'point', 'body'), 'packed', work,
               merged_widths=False)
        verify('flat_joint', ('joint', 'body'), 'flat', work)
        verify('flat_mixed', KINDS, 'flat', work)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f'{len(CHECKED) - len(FAILED)}/{len(CHECKED)} checks passed', flush=True)
    print(f"{'FAILED: ' + ', '.join(FAILED[:10]) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
