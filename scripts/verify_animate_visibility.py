#!/usr/bin/env hython
"""Verify th::animate carries a shape's animated visibility to the stage.

A rig shows and hides a shape with ``usdvisibility`` on its primitives
(TumbleRig's TH Visibility writes it every frame: ``invisible`` or
``inherited``), which a SOP Import turns into the prim's USD ``visibility``.
th::animate imports the first frame once and copies only what moves after
that, and visibility was not among it: the first frame's held for the whole
shot, so a shape hidden at the first frame never appeared and one hidden
later never hid (Remy, tumblepipe__bugs 2026-11-18). A shape missing on some
frames (TH Visibility's Points and All modes delete it) was made invisible
on those frames only, with no sample anywhere else, so a saved range read it
as hidden on every frame. Now ``animate_points.apply`` samples every prim's
visibility every frame (``read_visibility``, ``author_visibility``);
``tests/test_animate_points.py`` pins that on plain layers, this cooks the
real HDA and saves it with the real ROP.

The dive is plain SOPs, no rig: ``/CHAR/hero/geo/body``, a sphere, and
``/CHAR/hero/geo/hat``, a box that says nothing of its visibility; both move
every frame. Unpacked and as packed folders (the Scene Invoke's output), the
body is hidden on some frames, in the middle of the range or from its first
frame, by ``usdvisibility`` on its primitives every frame, on the hidden
frames only, on the detail, or by being left out of the dive. For each, on
the node's stage at every frame and in the files a USD ROP saves of the
whole range and of a range starting inside it:

- the body is invisible exactly on its hidden frames;
- the hat is visible on every frame;
- a hat the incoming stage hides stays hidden (the dive says nothing of it);
- with no ``usdvisibility`` anywhere both are visible on every frame.

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
BODY, HAT = '/CHAR/hero/geo/body', '/CHAR/hero/geo/hat'
MIDDLE, START = (4, 5, 6), (1, 2, 3)

# The dive's geometry. HOW the body is hidden on the frames in HIDDEN:
# 'attr' (usdvisibility on its primitives, every frame), 'when' (only on the
# hidden frames), 'detail' (on its detail, every frame; packed only: merged
# flat, a detail attribute is every shape's), 'vanish' (left out of the dive)
# or 'never'. LAYOUT 'packed' gives packed folders, 'flat' the shapes merged.
DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = int(hou.frame())
hidden = frame in {hidden!r}
how = {how!r}

def shape(verb_name, parms, path, offset):
    verb = hou.sopNodeTypeCategory().nodeVerb(verb_name)
    verb.setParms(parms)
    out = hou.Geometry()
    verb.execute(out, [])
    out.transform(hou.hmath.buildTranslate(offset + 0.1 * frame, 0.05 * frame, 0))
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', [path] * len(out.prims()))
    return out

shapes = []
if not (how == 'vanish' and hidden):
    body = shape('sphere', {{'type': 2, 'rows': 6, 'cols': 6}}, '{body}', 0.0)
    value = 'invisible' if hidden else 'inherited'
    if how == 'attr' or (how == 'when' and hidden):
        body.addAttrib(hou.attribType.Prim, 'usdvisibility', '')
        body.setPrimStringAttribValues('usdvisibility', [value] * len(body.prims()))
    elif how == 'detail':
        body.addAttrib(hou.attribType.Global, 'usdvisibility', '')
        body.setGlobalAttribValue('usdvisibility', value)
    shapes.append(body)
shapes.append(shape('box', {{'type': 1}}, '{hat}', 3.0))
if {layout!r} == 'flat':
    for one in shapes:
        geo.merge(one)
else:
    inner = hou.Geometry()
    for one in shapes:
        inner.createPacked('PackedGeometry').setEmbeddedGeometry(one)
    geo.createPacked('PackedGeometry').setEmbeddedGeometry(inner)
'''

# A layer under th::animate that hides the hat.
UNDER = '''
def Xform "CHAR"
{
    def Xform "hero"
    {
        def Xform "geo"
        {
            def Mesh "hat"
            {
                token visibility = "invisible"
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


def build(label, how, hidden, layout, under):
    """A th::animate whose dive outputs DIVE_GEO, nothing else, over a
    stage that hides the hat with `under`."""
    animate = hou.node('/stage').createNode('th::animate::2.0', f'animate_{label}')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(DIVE_GEO.format(
        how=how, hidden=tuple(hidden), layout=layout, body=BODY, hat=HAT))
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


def visibility(stage, path, frame):
    """The prim's own visibility at `frame`; None when there is no prim."""
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return None
    return UsdGeom.Imageable(prim).GetVisibilityAttr().Get(frame)


def wanted(path, frame, hidden, under):
    if path == BODY:
        return 'invisible' if frame in hidden else 'inherited'
    return 'invisible' if under else 'inherited'


def verify(label, how, hidden, layout, work, under=False):
    from pxr import Usd
    animate = build(label, how, hidden, layout, under)
    frames = list(range(FIRST, LAST + 1))
    for path in (BODY, HAT):
        live = []
        for frame in frames:
            hou.setFrame(frame)
            live.append(visibility(animate.stage(), path, frame))
        want = [wanted(path, frame, hidden, under) for frame in frames]
        check(f'{label}: {path} on the node\'s stage, frame by frame', live == want, live)
    check(f'{label}: the node cooks without errors', not animate.errors(), animate.errors())
    for first in (FIRST, 5):
        out = work / f'{label}_{first}.usd'
        rop = save(animate, first, LAST, out)
        if not out.exists():
            check(f'{label} {first}-{LAST}: the ROP saves', False, rop.errors())
            continue
        stage = Usd.Stage.Open(str(out))
        for path in (BODY, HAT):
            saved = [visibility(stage, path, frame) for frame in range(first, LAST + 1)]
            want = [wanted(path, frame, hidden, under) for frame in range(first, LAST + 1)]
            check(f'{label} {first}-{LAST}: {path} in the saved file', saved == want, saved)


def main():
    install()
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for layout in ('flat', 'packed'):
            for how in ('attr', 'when', 'vanish'):
                for name, hidden in (('middle', MIDDLE), ('start', START)):
                    if how == 'vanish' and hidden == START:
                        continue  # the first frame is what the others are imported onto
                    verify(f'{layout}_{how}_{name}', how, hidden, layout, work)
            verify(f'{layout}_never', 'never', (), layout, work)
            verify(f'{layout}_under', 'attr', MIDDLE, layout, work, under=True)
            verify(f'{layout}_under_never', 'never', (), layout, work, under=True)
        verify('packed_detail_middle', 'detail', MIDDLE, 'packed', work)
        verify('packed_detail_start', 'detail', START, 'packed', work)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
