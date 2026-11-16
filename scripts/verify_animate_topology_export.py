#!/usr/bin/env hython
"""Verify th::animate saved over a frame range whose topology changes partway.

A rig can change its own topology as it poses (shot 010's lilGuy cuts its
head mesh from frame 1151: 2 more points, other faces). th::animate imports
the first frame once and copies only the points after that; a frame with
another point count is imported whole by ``full_import``. That frame went to
a layer of its own (``topology_switch``), and a USD ROP saving a range across
the change dropped it: the file had samples up to the frame before and the
render held the first pose to the end. A range starting after the change
was fine, so it looked like "render from 1151 and it works". Now ``apply``
copies the full import into its own layer and samples the static import's
topology every frame (``animate_points.author_full_frame``,
``sample_defaults``); ``tests/test_animate_points.py`` pins that on plain
layers, this saves the real HDA with the real ROP.

The dive is plain SOPs, no rig: ``/CHAR/hero/geo/body``, a sphere that moves
every frame and has more faces from frame 6, and ``/CHAR/hero/geo/hat``, a
box that moves and never changes. For a range across the change (1-10), one
before it (1-5) and one after it (6-10):

- every frame of the range has its own points sample, on both prims;
- every frame's topology fits its points (indices within the points, the
  counts summing to the indices), with the body's own face count of that
  frame (before 6 the first frame's, from 6 the new one's);
- each prim's points at each frame are the dive's at that frame;
- a range without a change is no bigger than before the fix by more than a
  little (the topology sampled every frame is the same value each time,
  which the crate file stores once).

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
CHANGE = 6
BODY, HAT = '/CHAR/hero/geo/body', '/CHAR/hero/geo/hat'

# The dive's geometry: the body a sphere that gets more faces from CHANGE,
# both shapes moving every frame, each with its pipeline path.
DIVE_GEO = f'''
node = hou.pwd()
geo = node.geometry()
frame = hou.frame()
def shape(verb_name, parms, path, offset):
    verb = hou.sopNodeTypeCategory().nodeVerb(verb_name)
    verb.setParms(parms)
    out = hou.Geometry()
    verb.execute(out, [])
    out.transform(hou.hmath.buildTranslate(offset + 0.1 * frame, 0.05 * frame, 0))
    out.addAttrib(hou.attribType.Prim, 'path', '')
    for prim in out.prims():
        prim.setAttribValue('path', path)
    return out
rows = 8 if frame < {CHANGE} else 12
geo.merge(shape('sphere', {{'type': 2, 'rows': rows, 'cols': rows}}, '{BODY}', 0.0))
geo.merge(shape('box', {{'type': 1}}, '{HAT}', 3.0))
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


def build():
    """A th::animate whose dive outputs DIVE_GEO, nothing else."""
    animate = hou.node('/stage').createNode('th::animate::2.0', 'animate_topology')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(DIVE_GEO)
    output.setInput(0, source)
    return animate, source


def save(animate, first, last, out):
    rop = hou.node('/out').createNode('usd', f'save_{first}_{last}')
    rop.parm('loppath').set(animate.path())
    rop.parm('lopoutput').set(str(out))
    rop.parm('trange').set(1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((first, last, 1))
    rop.render()
    return rop


def dive_points(source, frame):
    """{path: (points, face count)} of the dive at `frame`."""
    hou.setFrame(frame)
    geo = source.geometry()
    out = {}
    for path in (BODY, HAT):
        prims = [prim for prim in geo.prims() if prim.attribValue('path') == path]
        numbers = sorted({vertex.point().number() for prim in prims for vertex in prim.vertices()})
        out[path] = (np.array([geo.point(n).position() for n in numbers]), len(prims))
    return out


def verify_range(animate, source, first, last, work):
    from pxr import Sdf, Usd, UsdGeom
    out = work / f'anim_{first}_{last}.usd'
    rop = save(animate, first, last, out)
    label = f'{first}-{last}'
    check(f'{label}: the ROP saves without errors', not rop.errors() and out.exists(),
          rop.errors())
    if not out.exists():
        return None
    layer = Sdf.Layer.FindOrOpen(str(out))
    stage = Usd.Stage.Open(layer)
    frames = list(range(first, last + 1))
    for path in (BODY, HAT):
        samples = [int(t) for t in layer.ListTimeSamplesForPath(Sdf.Path(path).AppendProperty('points'))]
        check(f'{label}: {path} has a points sample on every frame', samples == frames, samples)
    for frame in frames:
        want = dive_points(source, frame)
        for path in (BODY, HAT):
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(path))
            points = np.asarray(mesh.GetPointsAttr().Get(frame))
            counts = list(mesh.GetFaceVertexCountsAttr().Get(frame))
            indices = list(mesh.GetFaceVertexIndicesAttr().Get(frame))
            fits = sum(counts) == len(indices) and (not indices or max(indices) < len(points))
            check(f'{label} frame {frame}: {path} topology fits its points', fits,
                  (len(points), sum(counts), max(indices) if indices else None))
            check(f'{label} frame {frame}: {path} has the frame\'s face count',
                  len(counts) == want[path][1], (len(counts), want[path][1]))
            # the SOP Import's point order is its own: compare as sets of rows
            same = (points.shape == want[path][0].shape and np.allclose(
                np.sort(points, axis=0), np.sort(want[path][0], axis=0), atol=1e-4))
            check(f'{label} frame {frame}: {path} points are the dive\'s', same,
                  (points.shape, want[path][0].shape))
    return out.stat().st_size


def main():
    install()
    hou.playbar.setFrameRange(1, 10)
    hou.playbar.setPlaybackRange(1, 10)
    hou.setFrame(1)
    animate, source = build()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        verify_range(animate, source, 1, 10, work)
        before = verify_range(animate, source, 1, CHANGE - 1, work)
        verify_range(animate, source, CHANGE, 10, work)
        if before is not None:
            # one frame's topology is the most a range without a change may add
            check('a range without a change stays small (topology stored once)',
                  before < 200_000, before)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
