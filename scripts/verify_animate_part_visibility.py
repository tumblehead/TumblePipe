#!/usr/bin/env hython
"""Verify th::animate hides part of a prim when its shapes disagree.

One USD prim can be made of shapes the rig switches one by one: a
``/geo/arms`` mesh holding both arms, each with its own ``usdvisibility``
(Remy, tumblepipe__bugs 2026-11-19). A prim has one ``visibility``, and up
to 1.114 the shape holding the prim's first point decided for both arms.
Now ``animate_points.apply`` reads every primitive's word
(``shape_visibility``) and, where a prim's do not agree, leaves the prim
visible and takes the hidden part out (``author_parts``): a mesh's hidden
faces are that frame's ``holeIndices``, hidden curves get width 0.
``tests/test_animate_points.py`` pins that on plain layers; this cooks the
real HDA and saves it with the real ROP.

The dive is plain SOPs, no rig, moving every frame: ``/CHAR/hero/geo/arms``,
a left and a right box under one path; ``/CHAR/hero/geo/hair``, a left and a
right curve under one path, with point ``width``; ``/CHAR/hero/geo/hat``, a
box that says nothing. The right arm and the right curve are hidden on
frames 4-6, both arms on frame 8. The arms come as two primitives' worth of
one shape (``flat``, and ``packed`` in one packed shape) and as two packed
shapes with the same path (``shapes``). For each, on the node's stage at
every frame and in the files a USD ROP saves of the whole range and of a
range starting inside it:

- the arms are visible on every frame but 8, where they are invisible;
- on frames 4-6 ``holeIndices`` names exactly the right arm's faces, and no
  face on every other frame;
- the right curve's widths are 0 on frames 4-6 and the left curve's never,
  and the hair is invisible on frame 8;
- the hat is visible, with no holes, on every frame;
- holes the incoming stage has on the arms stay, with the rig's added;
- no stamp primvar (``__th_prim``) is left on the stage.

With a folder as its argument the saved files are kept there (for a husk
render: ``<folder>/<layout>_1.usd``).

Needs a Houdini license; preflight's hython stage runs it. Prints one line
per check and exits 1 on any failure.
"""

import sys
import tempfile
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
FIRST, LAST = 1, 10
ARMS, HAIR, HAT = '/CHAR/hero/geo/arms', '/CHAR/hero/geo/hair', '/CHAR/hero/geo/hat'
RIGHT, BOTH = (4, 5, 6), (8,)

# The dive's geometry: LAYOUT 'flat' merges everything, 'packed' packs the
# arms as one shape, 'shapes' as two shapes with the same path.
DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = int(hou.frame())
right_hidden = frame in {right!r} or frame in {both!r}
left_hidden = frame in {both!r}
move = hou.hmath.buildTranslate(0.1 * frame, 0.05 * frame, 0)

def said(out, hidden):
    out.addAttrib(hou.attribType.Prim, 'usdvisibility', '')
    out.setPrimStringAttribValues(
        'usdvisibility', ['invisible' if hidden else 'inherited'] * len(out.prims()))

def box(path, x, hidden=None):
    verb = hou.sopNodeTypeCategory().nodeVerb('box')
    verb.setParms({{'type': 1, 'divrate': (3, 3, 3)}})
    out = hou.Geometry()
    verb.execute(out, [])
    out.transform(hou.hmath.buildTranslate(x, 0, 0) * move)
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', [path] * len(out.prims()))
    if hidden is not None:
        said(out, hidden)
    return out

def curve(x, hidden):
    out = hou.Geometry()
    points = out.createPoints([(x, 1.0 + 0.5 * i, 0.0) for i in range(4)])
    line = out.createPolygon(is_closed=False)
    for point in points:
        line.addVertex(point)
    out.transform(move)
    out.addAttrib(hou.attribType.Point, 'width', 0.2)
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', ['{hair}'])
    said(out, hidden)
    return out

def merged(parts):
    out = hou.Geometry()
    for part in parts:
        out.merge(part)
    return out

arms = [box('{arms}', -2.0, left_hidden), box('{arms}', 2.0, right_hidden)]
hair = [curve(-2.0, left_hidden), curve(2.0, right_hidden)]
hat = box('{hat}', 0.0)
layout = {layout!r}
if layout == 'flat':
    for one in arms + hair + [hat]:
        geo.merge(one)
else:
    shapes = (arms + hair if layout == 'shapes' else [merged(arms), merged(hair)]) + [hat]
    inner = hou.Geometry()
    for one in shapes:
        inner.createPacked('PackedGeometry').setEmbeddedGeometry(one)
    geo.createPacked('PackedGeometry').setEmbeddedGeometry(inner)
'''

# A layer under th::animate with a hole of its own in the arms (face 0).
UNDER = '''
def Xform "CHAR"
{
    def Xform "hero"
    {
        def Xform "geo"
        {
            def Mesh "arms"
            {
                int[] holeIndices = [0]
            }
        }
    }
}
'''


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}", flush=True)
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages and the expanded HDA sources."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def build(label, layout, under):
    animate = hou.node('/stage').createNode('th::animate::2.0', f'animate_{label}')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(DIVE_GEO.format(
        right=RIGHT, both=BOTH, layout=layout, arms=ARMS, hair=HAIR, hat=HAT))
    output.setInput(0, source)
    if under:
        below = hou.node('/stage').createNode('inlineusd', f'under_{label}')
        below.parm('usdsource').set(UNDER)
        animate.setInput(0, below)
    return animate


def save(animate, first, last, out):
    rop = hou.node('/out').createNode('usd', f'save_{animate.name()}_{first}_{last}')
    rop.parm('loppath').set(animate.path())
    rop.parm('lopoutput').set(str(out))
    rop.parm('trange').set(1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((first, last, 1))
    rop.render()
    return rop


def read(stage, frame, under):
    """What `stage` has at `frame`, as the checks below name it; None for
    a value that is as wanted, else what was found."""
    from pxr import UsdGeom
    wrong = {}
    hidden = frame in BOTH
    centre = 0.1 * frame  # the arms' and curves' x is this -2 or +2

    def visibility(path):
        return UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr().Get(frame)

    for path in (ARMS, HAIR):
        want = 'invisible' if hidden else 'inherited'
        if visibility(path) != want:
            wrong[f'{path} visibility'] = visibility(path)
    if visibility(HAT) != 'inherited':
        wrong['hat visibility'] = visibility(HAT)

    arms = UsdGeom.Mesh(stage.GetPrimAtPath(ARMS))
    points = arms.GetPointsAttr().Get(frame)
    counts = arms.GetFaceVertexCountsAttr().Get(frame)
    indices = arms.GetFaceVertexIndicesAttr().Get(frame)
    right, start = set(), 0
    for face, count in enumerate(counts):
        if sum(points[i][0] for i in indices[start:start + count]) / count > centre:
            right.add(face)
        start += count
    if len(right) * 2 != len(counts):
        wrong['arms faces'] = (len(right), len(counts))
    want = set(right) if frame in RIGHT else set()
    if under:
        want.add(0)
    holes = arms.GetHoleIndicesAttr().Get(frame)
    if holes is None or set(holes) != want or len(holes) != len(want):
        wrong['arms holeIndices'] = None if holes is None else sorted(holes)
    hat_holes = UsdGeom.Mesh(stage.GetPrimAtPath(HAT)).GetHoleIndicesAttr().Get(frame)
    if hat_holes is None or len(hat_holes):
        wrong['hat holeIndices'] = hat_holes

    hair = UsdGeom.BasisCurves(stage.GetPrimAtPath(HAIR))
    widths = hair.GetWidthsAttr().Get(frame)
    hair_points = hair.GetPointsAttr().Get(frame)
    if widths is None or len(widths) != len(hair_points):
        wrong['hair widths'] = widths
    else:
        for point, width in zip(hair_points, widths):
            zero = frame in RIGHT and point[0] > centre
            if abs(width - (0.0 if zero else 0.2)) > 1e-6:
                wrong['hair widths'] = list(widths)
                break
    for path in (ARMS, HAIR, HAT):
        names = [name for name in stage.GetPrimAtPath(path).GetPropertyNames() if '__th_' in name]
        if names:
            wrong[f'{path} stamps'] = names
    return wrong


def verify(label, layout, work, under=False):
    from pxr import Usd
    animate = build(label, layout, under)
    frames = list(range(FIRST, LAST + 1))
    wrong = {}
    for frame in frames:
        hou.setFrame(frame)
        found = read(animate.stage(), frame, under)
        if found:
            wrong[frame] = found
    check(f'{label}: the node\'s stage, frame by frame', not wrong, wrong)
    check(f'{label}: the node cooks without errors or warnings',
          not animate.errors() and not animate.warnings(),
          (animate.errors(), animate.warnings()))
    for first in (FIRST, 5):
        out = work / f'{label}_{first}.usd'
        rop = save(animate, first, LAST, out)
        if not out.exists():
            check(f'{label} {first}-{LAST}: the ROP saves', False, rop.errors())
            continue
        stage = Usd.Stage.Open(str(out))
        wrong = {}
        for frame in range(first, LAST + 1):
            found = read(stage, frame, under)
            if found:
                wrong[frame] = found
        check(f'{label} {first}-{LAST}: the saved file, frame by frame', not wrong, wrong)


def main():
    install()
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    keep = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        for layout in ('flat', 'packed', 'shapes'):
            verify(layout, layout, work)
        verify('shapes_under', 'shapes', work, under=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
