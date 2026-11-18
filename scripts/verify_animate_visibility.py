#!/usr/bin/env hython
"""Verify th::animate carries animated visibility, saved over a range.

2026-11-18: a rig hooked a replacement head to TumbleRig's Apex Visibility
with its switch keyed. Scene Animate showed and hid it, but after th::animate
the head was hidden on every frame. Apex Visibility writes the
``usdvisibility`` prim attribute every frame (a SOP Import maps it to USD
``visibility``); th::animate imports the rigs' geometry once, at the first
frame (``static_import``), and ``apply`` copied only points, normals, widths
and extents after that, while ``sample_defaults`` sampled the first frame's
visibility on every frame. A piece hidden at the first frame stayed hidden
all shot; one shown at the first frame was never hidden. Now ``apply`` reads
each frame's ``usdvisibility`` (``animate_points.read_visibility``) and
samples ``visibility`` (``author_visibility``).

The dive is plain SOPs, no rig: a body with no ``usdvisibility``, a head
hidden from frame SWITCH and a replacement head shown from it (hidden
before), each with its pipeline path, laid out as packed folders (the Scene
Invoke's output) and as unpacked geometry. For each, the range FIRST-LAST
saved by a USD ROP:

- each head's ``visibility`` is that frame's ``usdvisibility``;
- the body reads ``inherited`` on every frame.

``TH_VERIFY_UNFIXED=1`` turns the per-frame visibility off, to show the bug.
Needs a Houdini license. Prints one line per failure and exits 1 on any.
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
CHECKED = []
SWITCH = 5
FIRST, LAST = 1, 10
GEO = '/CHAR/hero/geo'
PIECES = ('body', 'head', 'head_alt')

DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = hou.frame()
pieces = {pieces!r}

def shape(name, index):
    out = hou.Geometry()
    verb = hou.sopNodeTypeCategory().nodeVerb('box')
    verb.execute(out, [])
    out.transform(hou.hmath.buildTranslate(2.0 * index, 0.05 * frame, 0))
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', ['{geo}/' + name] * len(out.prims()))
    if name != 'body':
        hidden = (frame >= {switch}) if name == 'head' else (frame < {switch})
        out.addAttrib(hou.attribType.Prim, 'usdvisibility', '')
        out.setPrimStringAttribValues(
            'usdvisibility', ['invisible' if hidden else 'inherited'] * len(out.prims()))
    return out

shapes = [shape(name, i) for i, name in enumerate(pieces)]
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
    CHECKED.append(name)
    if not ok:
        print(f'FAIL  {name}  {info}', flush=True)
        FAILED.append(name)


def install():
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def build(label, layout):
    animate = hou.node('/stage').createNode('th::animate::2.0', f'animate_{label}')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(DIVE_GEO.format(
        pieces=PIECES, geo=GEO, switch=SWITCH, layout=layout))
    output.setInput(0, source)
    return animate


def save(animate, out):
    rop = hou.node('/out').createNode('usd', f'save_{animate.name()}')
    rop.parm('loppath').set(animate.path())
    rop.parm('lopoutput').set(str(out))
    rop.parm('trange').set(1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((FIRST, LAST, 1))
    rop.render()
    errors = rop.errors()
    if errors:
        raise RuntimeError('\n'.join(errors))


def expected(name, frame):
    if name == 'body':
        return 'inherited'
    hidden = (frame >= SWITCH) if name == 'head' else (frame < SWITCH)
    return 'invisible' if hidden else 'inherited'


def verify(label, layout, work):
    from pxr import Usd, UsdGeom
    animate = build(label, layout)
    out = work / f'{label}.usd'
    save(animate, out)
    stage = Usd.Stage.Open(str(out))
    rows = []
    for name in PIECES:
        prim = stage.GetPrimAtPath(f'{GEO}/{name}')
        check(f'{label}/{name} exists', prim.IsValid(), str(out))
        if not prim.IsValid():
            continue
        vis = UsdGeom.Imageable(prim).GetVisibilityAttr()
        got = [str(vis.Get(frame)) for frame in range(FIRST, LAST + 1)]
        want = [expected(name, frame) for frame in range(FIRST, LAST + 1)]
        rows.append(f'  {name:9s} ' + ' '.join('H' if v == 'invisible' else '.' for v in got))
        check(f'{label}/{name} visibility per frame', got == want, f'got {got}')
    print(f'{label}: frames {FIRST}-{LAST} (H = invisible)', flush=True)
    for row in rows:
        print(row, flush=True)


def main():
    install()
    if os.environ.get('TH_VERIFY_UNFIXED') == '1':
        from tumblepipe.pipe.houdini.lops import animate_points
        animate_points.author_visibility = lambda *args, **kwargs: None
        print('UNFIXED: per-frame visibility off', flush=True)
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        verify('packed', 'packed', work)
        verify('flat', 'flat', work)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f'{len(CHECKED) - len(FAILED)}/{len(CHECKED)} checks passed', flush=True)
    print(f"{'FAILED: ' + ', '.join(FAILED[:10]) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
