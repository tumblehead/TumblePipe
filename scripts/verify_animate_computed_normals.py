#!/usr/bin/env hython
"""Verify th::animate's computed normals on real Houdini geometry.

A shot can mix rigs with point N, vertex N and none; the static import
merges them, so ``apply`` asks every rig for one kind, and a rig without it
gets normals computed in its place (``animate_points._computed_normals``,
PR #88: zeros there rendered as a flat cut-out). The unit tests
(``tests/test_animate_points.py``) stand a fake in for that function, a
nonzero normal per element, so what Houdini's SOP verbs actually give is
pinned here:

- per vertex from point N: each vertex gets its own point's N, in the order
  HOM lists vertices, one row per vertex; the shape itself is left as it was
  (the promote runs on a frozen copy). Houdini keeps one N per name, so a
  vertex N added to a shape with point N replaces it: a shape never has both;
- per vertex without N, and per point: one row per vertex / point, nonzero
  on a closed mesh, as a Normal SOP with its defaults computes;
- what the Normal SOP gives where there is no surface (an open curve, bare
  points, a degenerate face): the row count still matches, nothing raises,
  and ``_filled`` / ``_concat_or_none`` keep the shape, zeros left only
  where nothing better exists;
- ``_filled`` replaces exactly the zero rows of a mesh's N and leaves the
  rest bit for bit;
- ``_concat_or_none`` over a mesh without N, one with point N and one with
  vertex N (the customer shot's mix) gives one row per vertex of all three
  and no zero normal on any of them.

Needs a Houdini license; preflight's hython stage runs it. Prints one line
per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

from tumblepipe.pipe.houdini.lops import animate_points as ap  # noqa: E402

FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}", flush=True)
    if not ok:
        FAILED.append(name)


def verb(name, parms, inputs=()):
    node_verb = hou.sopNodeTypeCategory().nodeVerb(name)
    node_verb.setParms(parms)
    out = hou.Geometry()
    node_verb.execute(out, list(inputs))
    return out


def box():
    return verb('box', {'divrate': (3, 3, 3), 'type': 1})  # polygon mesh, shared points


def with_point_n(geo, seed):
    rng = np.random.default_rng(seed)
    n = rng.normal(size=(len(geo.points()), 3)).astype(np.float32)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    geo.addAttrib(hou.attribType.Point, 'N', (0.0, 0.0, 0.0))
    geo.setPointFloatAttribValuesFromString('N', n.tobytes())
    return n


def with_vertex_n(geo, value=(0.0, 0.0, 1.0)):
    geo.addAttrib(hou.attribType.Vertex, 'N', value)


def vertex_points(geo):
    """The point number of each vertex, in HOM's vertex order."""
    return np.array([v.point().number() for prim in geo.prims() for v in prim.vertices()])


def nonzero(rows):
    return rows is not None and bool(np.all(np.einsum('ij,ij->i', rows, rows) > 1e-12))


def count(geo, per_vertex):
    return geo.intrinsicValue('vertexcount' if per_vertex else 'pointcount')


def main():
    # 1. per vertex from point N
    geo = box()
    point_n = with_point_n(geo, 1)
    got = ap._computed_normals(geo, True)
    check('point N per vertex: one row per vertex',
          got is not None and got.shape == (count(geo, True), 3),
          None if got is None else got.shape)
    check('point N per vertex: each vertex has its own point\'s N',
          got is not None and np.allclose(got, point_n[vertex_points(geo)], atol=1e-6))

    check('point N per vertex: the shape is left as it was (point N, no vertex N)',
          geo.findVertexAttrib('N') is None
          and np.array_equal(ap._float3(geo, 'N'), point_n))

    # 1b. one name, one class: adding a vertex N drops the point N, so a shape
    # never has both and "point N wins over vertex N" cannot arise.
    geo = box()
    with_point_n(geo, 2)
    with_vertex_n(geo, (0.0, 0.0, 1.0))
    check('point then vertex N: Houdini keeps only the vertex N',
          geo.findPointAttrib('N') is None and geo.findVertexAttrib('N') is not None)

    # 2. no N: the Normal SOP's
    geo = box()
    for per_vertex in (True, False):
        kind = 'vertex' if per_vertex else 'point'
        got = ap._computed_normals(geo, per_vertex)
        reference = verb('normal', {'type': 1 if per_vertex else 0}, [geo])
        want = ap._vertex_float3(reference, 'N') if per_vertex else ap._float3(reference, 'N')
        check(f'no N per {kind}: one row per {kind}, nonzero on a closed mesh',
              got is not None and got.shape == (count(geo, per_vertex), 3) and nonzero(got),
              None if got is None else got.shape)
        check(f'no N per {kind}: what a Normal SOP with its defaults computes',
              got is not None and np.allclose(got, want, atol=1e-6))

    # 3. no surface: an open curve, bare points, a degenerate face
    curve = hou.Geometry()
    poly = curve.createPolygon(is_closed=False)
    for p in curve.createPoints([(0, 0, 0), (1, 0, 0), (2, 1, 0)]):
        poly.addVertex(p)
    points = hou.Geometry()
    points.createPoints([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    flat = hou.Geometry()
    face = flat.createPolygon()
    for p in flat.createPoints([(0, 0, 0), (1, 0, 0), (2, 0, 0)]):
        face.addVertex(p)
    for label, geo in (('open curve', curve), ('bare points', points), ('degenerate face', flat)):
        for per_vertex in (True, False):
            kind = 'vertex' if per_vertex else 'point'
            try:
                got = ap._computed_normals(geo, per_vertex)
                filled = ap._filled(np.zeros((count(geo, per_vertex), 3), np.float32), geo, per_vertex)
                ok = (got is not None and got.shape == (count(geo, per_vertex), 3)
                      and filled.shape == got.shape)
                info = None if got is None else got.shape
            except Exception as exc:  # noqa: BLE001  (the check is that nothing raises)
                ok, info = False, repr(exc)
            check(f'{label} per {kind}: rows match, nothing raises', ok, info)

    # 4. _filled replaces the zero rows only
    geo = box()
    point_n = with_point_n(geo, 3)
    holes = point_n.copy()
    zero = np.zeros(len(holes), bool)
    zero[::3] = True
    holes[zero] = 0
    got = ap._filled(holes, geo, False)
    computed = ap._computed_normals(geo, False)
    check('_filled: zero rows get computed normals, the rest stay bit for bit',
          np.array_equal(got[~zero], holes[~zero]) and np.allclose(got[zero], computed[zero])
          and nonzero(got))

    # 5. the customer shot's mix, through _concat_or_none
    bare, pointed, vertexed = box(), box(), box()
    with_point_n(pointed, 4)
    with_vertex_n(vertexed, (0.0, 1.0, 0.0))
    leaves = [bare, pointed, vertexed]
    got = ap._concat_or_none(leaves, lambda leaf: ap._vertex_float3(leaf, 'N'), True)
    total = sum(count(leaf, True) for leaf in leaves)
    check('mixed rigs per vertex: one row per vertex of all three',
          got is not None and got.shape == (total, 3), None if got is None else got.shape)
    check('mixed rigs per vertex: no zero normal anywhere', nonzero(got))
    tail = count(vertexed, True)
    check('mixed rigs per vertex: the rig with vertex N keeps its own',
          got is not None and np.allclose(got[-tail:], (0.0, 1.0, 0.0)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
