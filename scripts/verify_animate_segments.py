#!/usr/bin/env hython
"""Verify th::animate's per-character Frames and Segments on the real HDA.

A shot that pans across rooms is animated in one dive: one rig import
feeding a Scene Animate per room, side by side (Gitea TumblePipe #98).
Frames on the Characters list hide a character outside them
(``lops.animate_frames``); Segments rows take characters from another of the
dive's Output SOPs on some frames (``lops.animate_segments``). The rules are
pinned without Houdini in ``tests/test_animate_frames.py`` and
``tests/test_animate_segments.py``; this cooks the real nodes.

TumbleRig's example figure stands in for two published rigs, ``hero`` and
``pal`` (its render shape given the paths ``/CHAR/<name>/geo/body``). The
dive adds both to one scene, which feeds three Scene Animates: the default
stream (``output0``) keys both characters' ``Main`` x from 0, ``kitchen_out``
(Output Index 1) from 10 and ``bath_out`` (2) from 20, each moving 0.2 a
frame; ``bad_out`` (3) is the bath scene invoked already. The stage under
th::animate holds a ``groom`` curve under each character, which the rig
does not output.

- ``plain``: no Frames, no Segments. The node cooks as before: the default
  invoke's own Characters pattern, no stream invoke ever cooked, nothing
  authored on the characters' prims. Its positions are the reference.
- ``segments``: rows ``4-6 -> 1 *``, ``5 -> 2 hero``, and three that are
  wrong (Output 3 is not a scene, Frames ``abc``, Output 7 missing). Hero
  comes from stream 0 on 1-3 and 7-10, 1 on 4 and 6, 2 on 5 (the last row
  wins); pal from 1 on 4-6. Each frame its body is exactly where the
  reference is plus that stream's offset, hero's skeleton too; the point
  count never changes (nothing frozen or dropped); the wrong rows are
  warnings on ``apply`` naming each row, and change nothing. Hero has
  Frames ``3-5 8``: its body, skeleton and groom are invisible on every
  other frame and visible on those, on the node's stage and in files a USD
  ROP saves of the range; pal's Frames ``1-`` are a warning naming the row,
  and pal shows on every frame.
- ``invoked``: Output 0 is invoked geometry (its own Scene Invoke) with a
  Segments row: they do not apply (positions are the reference) and the
  node says so.

Needs a Houdini license and TumbleRig (its checks are skipped without it);
preflight's hython stage runs it. Prints one line per check and exits 1 on
any failure.
"""

import sys
import tempfile
import time
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
FIRST, LAST = 1, 10
SHAPE = 'Primitives'
CHARACTERS = ('hero', 'pal')
OFFSETS = {0: 0.0, 1: 10.0, 2: 20.0}
HERO_FRAMES = (3, 4, 5, 8)

UNDER = '''#usda 1.0
def Xform "CHAR"
{
    def Xform "hero"
    {
        def BasisCurves "groom"
        {
            int[] curveVertexCounts = [2]
            point3f[] points = [(0, 0, 0), (0, 1, 0)]
        }
    }
    def Xform "pal"
    {
        def BasisCurves "groom"
        {
            int[] curveVertexCounts = [2]
            point3f[] points = [(0, 0, 0), (0, 1, 0)]
        }
    }
}
'''


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}", flush=True)
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages (the APEX Scene SOPs) and the expanded HDA
    sources, which must win over any compiled .hda."""
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


def body(character):
    return f'/CHAR/{character}/geo/body'


def path_shapes(path):
    return f'''
geo = hou.pwd().geometry()
for prim in geo.prims():
    if prim.attribValue('name') != '{SHAPE}.shp':
        continue
    inner = prim.getEmbeddedGeometry().freeze()
    if inner.findPrimAttrib('path') is None:
        inner.addAttrib(hou.attribType.Prim, 'path', '')
    inner.setPrimStringAttribValues('path', ['{path}'] * len(inner.prims()))
    prim.setEmbeddedGeometry(inner)
'''


def keys(start):
    """Every character's Main_t keyed as Animate keys a control: x from
    `start` over frames FIRST..LAST, 0.2 a frame; y and z 0."""
    geo = hou.Geometry()
    for character in CHARACTERS:
        entry = hou.Geometry()
        name = entry.addAttrib(hou.attribType.Prim, 'name', '')
        entry.addAttrib(hou.attribType.Global, 'rig_path', '')
        entry.setGlobalAttribValue('rig_path', f'/{character}.char/Base.rig')
        for axis in 'xyz':
            channel = entry.createChannelPrim()
            channel.setAttribValue(name, f'Main_t{axis}')
            channel.insertKey(FIRST)
            channel.insertKey(LAST)
            end = start + 0.2 * (LAST - FIRST)
            channel.setKeyValues([start, end] if axis == 'x' else [0.0, 0.0])
        geo.packToFolder(f'/animation/default.clip/_{character}.char_Base', entry)
    return geo


def rigs():
    """One pathed figure per character, in /obj."""
    obj = hou.node('/obj').createNode('geo', 'rigs')
    figure = obj.createNode(example_type('figure'), 'figure')
    pathed = {}
    for character in CHARACTERS:
        node = obj.createNode('python', f'pathed_{character}')
        node.setInput(0, figure)
        put(node.parm('python'), path_shapes(body(character)))
        pathed[character] = node
    return pathed


def build(label, pathed, invoked=False):
    """A th::animate over the groom stage: both characters in one scene,
    three Scene Animates on it, an Output SOP each (0, 1, 2) and the bath
    scene invoked as Output 3; `invoked` gives Output 0 its own Scene
    Invoke, as dives from before th::animate invoked the scene."""
    stage = hou.node('/stage')
    under = stage.createNode('inlineusd', f'under_{label}')
    put(under.parm('usdsource'), UNDER)
    animate = stage.createNode('th::animate::2.0', f'animate_{label}')
    animate.setInput(0, under)
    put(animate.parm('shapes'), SHAPE)
    dive = animate.node('anim_dive')
    output0 = dive.node('output0')
    for child in dive.children():
        if child != output0:
            child.destroy()
    last = None
    for character in CHARACTERS:
        merge = dive.createNode('object_merge', f'rig_{character}')
        put(merge.parm('objpath1'), pathed[character].path())
        add = dive.createNode('apex::sceneaddcharacter', f'add_{character}')
        if last is not None:
            add.setInput(0, last)
        add.setInput(1, merge)
        put(add.parm('charactername'), character)
        last = add
    streams = {}
    for index, room in ((0, 'bedroom'), (1, 'kitchen'), (2, 'bath')):
        scene_animate = dive.createNode('apex::sceneanimate', room)
        scene_animate.setInput(0, last)
        put(scene_animate.parm('animation'), keys(OFFSETS[index]))
        streams[index] = scene_animate
    if invoked:
        own = dive.createNode('apex::sceneinvoke::2.0', 'own_invoke')
        own.setInput(0, streams[0])
        put(own.parm('outputmode'), 'packedfolders')
        put(own.parm('shapepattern'), SHAPE)
        output0.setInput(0, own)
    else:
        output0.setInput(0, streams[0])
    for index, name in ((1, 'kitchen_out'), (2, 'bath_out')):
        out = dive.createNode('output', name)
        put(out.parm('outputidx'), index)
        out.setInput(0, streams[index])
    bad = dive.createNode('apex::sceneinvoke::2.0', 'bath_invoked')
    bad.setInput(0, streams[2])
    put(bad.parm('outputmode'), 'packedfolders')
    bad_out = dive.createNode('output', 'bad_out')
    put(bad_out.parm('outputidx'), 3)
    bad_out.setInput(0, bad)
    return animate


def add_character_row(animate, character, frames, skeleton=False):
    count = animate.parm('characters')
    index = count.eval() + 1
    count.set(index)
    put(animate.parm(f'character{index}'), character)
    put(animate.parm(f'geometry{index}'), 1)
    put(animate.parm(f'skeleton{index}'), int(skeleton))
    put(animate.parm(f'frames{index}'), frames)


def add_segment_row(animate, frames, output, characters='*'):
    count = animate.parm('segments')
    index = count.eval() + 1
    count.set(index)
    put(animate.parm(f'segment_frames{index}'), frames)
    put(animate.parm(f'segment_output{index}'), output)
    put(animate.parm(f'segment_characters{index}'), characters)


def put(parm, value):
    """Set a parm's value on every frame: Scene Add Character's Character
    Name (and others) are animated by default ($OS on a key), and a plain
    set() on an animated parm only adds a key at the current frame."""
    parm.deleteAllKeyframes()
    parm.set(value)


SKEL = '/CHAR/hero/skel/Base'
SEEN = (body('hero'), '/CHAR/hero/groom', SKEL, body('pal'), '/CHAR/pal/groom')


def read(animate, frame):
    """At `frame`: {path: (centre x, point count)} of the bodies and hero's
    skeleton, and {path: computed visibility} of `SEEN`. Read while the
    node is cooked at the frame: its stage holds that frame's samples."""
    from pxr import UsdGeom
    hou.setFrame(frame)
    stage = animate.stage()
    found = {}
    for path in (body('hero'), body('pal'), SKEL):
        prim = stage.GetPrimAtPath(path)
        points = UsdGeom.PointBased(prim).GetPointsAttr().Get(frame) if prim.IsValid() else None
        found[path] = None if not points else (
            (min(p[0] for p in points) + max(p[0] for p in points)) / 2, len(points))
    return found, {path: visibility(stage, path, frame) for path in SEEN}


def visibility(stage, path, frame):
    from pxr import Usd, UsdGeom
    prim = stage.GetPrimAtPath(path)
    return UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(frame)) if prim else None


def owner(character, frame):
    """The stream each character comes from in `segments` (the rows in
    `run`): `4-6 -> 1 *`, then `5 -> 2 hero`."""
    if character == 'hero' and frame == 5:
        return 2
    return 1 if 4 <= frame <= 6 else 0


def save(animate, first, last, out):
    rop = hou.node('/out').createNode('usd', f'save_{animate.name()}_{first}')
    put(rop.parm('loppath'), animate.path())
    put(rop.parm('lopoutput'), str(out))
    put(rop.parm('trange'), 1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((first, last, 1))
    rop.render()
    return rop


def run(work):
    from tumblepipe.pipe.houdini.lops import animate_segments
    pathed = rigs()
    frames = list(range(FIRST, LAST + 1))

    # --- plain: no Frames, no Segments --------------------------------------
    plain = build('plain', pathed)
    add_character_row(plain, 'hero', '', skeleton=True)
    started = time.monotonic()
    reference, plain_seen = {}, {}
    for frame in frames:
        reference[frame], plain_seen[frame] = read(plain, frame)
    plain_seconds = time.monotonic() - started
    shown = {path: [plain_seen[f][path] for f in frames] for path in SEEN}
    check('plain: everything shows on every frame',
          all(values == ['inherited'] * len(frames) for values in shown.values()), shown)
    sops = plain.node('points_sops')
    check('plain: th::animate cooks', not plain.errors(), plain.errors()[:1])
    check('plain: every body and the skeleton are there on every frame',
          all(value is not None for found in reference.values() for value in found.values()),
          reference[FIRST])
    check('plain: the default invoke keeps its own Characters pattern',
          sops.node('invoke').parm('characterpattern').eval() == '*',
          sops.node('invoke').parm('characterpattern').eval())
    check('plain: the segments switch stays on the default invoke',
          sops.node('segments').parm('input').eval() == 0)
    # (The streams' Object Merges may cook, empty: TumbleRig's sim replay
    # reads the input of every Scene Invoke above `placed`.)
    cooked = [k for k in range(1, animate_segments.MAX_STREAMS + 1)
              if sops.node(f'stream{k}_invoke').cookCount()]
    check('plain: no stream is invoked', not cooked, cooked)
    paths = [sops.node(f'stream{k}').parm('objpath1').eval()
             for k in range(1, animate_segments.MAX_STREAMS + 1)]
    check('plain: no stream merges anything from the dive', not any(paths), paths)
    hou.setFrame(5)
    hero_vis = plain.stage().GetPrimAtPath('/CHAR/hero').GetAttribute('visibility')
    check('plain: nothing authored on the character prim',
          not (hero_vis and hero_vis.GetNumTimeSamples()))
    check('plain: no warning on apply', not plain.node('apply').warnings(),
          plain.node('apply').warnings())

    # --- segments + Frames --------------------------------------------------
    animate = build('segments', pathed)
    add_character_row(animate, 'hero', '3-5 8', skeleton=True)
    add_character_row(animate, 'pal', '1-')
    add_segment_row(animate, '4-6', '1')
    add_segment_row(animate, '5', '2', 'hero')
    add_segment_row(animate, '', '3')
    add_segment_row(animate, 'abc', '1')
    add_segment_row(animate, '7', '7')
    check('segments: the Output menu lists the dive\'s Output SOPs 1 and up',
          animate_segments.output_menu(animate)
          == ['1', '1  kitchen_out', '2', '2  bath_out', '3', '3  bad_out'],
          animate_segments.output_menu(animate))
    started = time.monotonic()
    got, seen = {}, {}
    for frame in frames:
        got[frame], seen[frame] = read(animate, frame)
        check(f'segments {frame}: th::animate cooks', not animate.errors(), animate.errors()[:1])
    segment_seconds = time.monotonic() - started
    for character in CHARACTERS:
        path = body(character)
        offsets = []
        for frame in frames:
            mine, theirs = got[frame][path], reference[frame][path]
            offsets.append(None if mine is None or theirs is None else round(mine[0] - theirs[0], 3))
        want = [OFFSETS[owner(character, frame)] for frame in frames]
        check(f'segments: {character} comes from its stream on each frame (last row wins, '
              'Output 0 elsewhere)',
              all(o is not None and abs(o - w) < 0.05 for o, w in zip(offsets, want)),
              f'offsets {offsets} want {want}')
        counts = {got[frame][path][1] for frame in frames if got[frame][path]}
        check(f'segments: {character} keeps its points on every frame, never frozen or dropped',
              len(counts) == 1 and all(got[frame][path] for frame in frames)
              and counts == {reference[FIRST][path][1]}, counts)
    skel = SKEL
    skel_offsets = [None if got[f][skel] is None else round(got[f][skel][0] - reference[f][skel][0], 3)
                    for f in frames]
    want = [OFFSETS[owner('hero', f)] for f in frames]
    check("segments: hero's skeleton comes from the same stream as its body",
          all(o is not None and abs(o - w) < 0.05 for o, w in zip(skel_offsets, want)),
          f'offsets {skel_offsets}')
    hou.setFrame(5)
    warnings = '\n'.join(animate.node('apply').warnings())
    for number, words in ((3, 'not an APEX scene'), (4, 'are not frames'), (5, 'no Output SOP')):
        check(f'segments: row {number} is a warning naming it ({words})',
              f'Segments row {number} is ignored' in warnings and words in warnings, warnings)
    check("segments: pal's Frames are a warning naming its row",
          'Characters row 2 (pal)' in warnings, warnings)
    for path in (body('hero'), '/CHAR/hero/groom', skel):
        live = [seen[f][path] for f in frames]
        want = ['inherited' if f in HERO_FRAMES else 'invisible' for f in frames]
        check(f'frames: {path} on the node\'s stage, frame by frame', live == want, live)
    for path in (body('pal'), '/CHAR/pal/groom'):
        live = [seen[f][path] for f in frames]
        check(f'frames: {path} shows on every frame (its Frames are not frames)',
              live == ['inherited'] * len(frames), live)
    for first in (FIRST, 5):
        out = work / f'segments_{first}.usd'
        rop = save(animate, first, LAST, out)
        if not out.exists():
            check(f'frames {first}-{LAST}: the ROP saves', False, rop.errors())
            continue
        from pxr import Usd
        saved = Usd.Stage.Open(str(out))
        for path in (body('hero'), '/CHAR/hero/groom', skel, body('pal')):
            got_vis = [visibility(saved, path, f) for f in range(first, LAST + 1)]
            want = ['inherited' if f in HERO_FRAMES or 'pal' in path else 'invisible'
                    for f in range(first, LAST + 1)]
            check(f'frames {first}-{LAST}: {path} in the saved file', got_vis == want, got_vis)
    print(f'INFO  {len(frames)} frames: plain {plain_seconds:.2f} s, '
          f'segments {segment_seconds:.2f} s', flush=True)

    # --- Output 0 invoked already -------------------------------------------
    invoked = build('invoked', pathed, invoked=True)
    add_segment_row(invoked, '4-6', '1')
    offsets = []
    for frame in frames:
        found, _stage = read(invoked, frame)
        mine, theirs = found[body('hero')], reference[frame][body('hero')]
        offsets.append(None if mine is None or theirs is None else round(mine[0] - theirs[0], 3))
    check('invoked: th::animate cooks', not invoked.errors(), invoked.errors()[:1])
    check('invoked: Segments do not apply over an invoked Output 0',
          all(o is not None and abs(o) < 0.05 for o in offsets), offsets)
    warnings = '\n'.join(invoked.node('apply').warnings())
    check('invoked: the node says Segments do not apply', 'Segments do not apply' in warnings,
          warnings)


def main():
    install()
    if example_type('figure') is None:
        print('SKIP  TumbleRig is not installed: no TumbleRig rig to stand in')
        return
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
