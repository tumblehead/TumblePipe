#!/usr/bin/env hython
"""Verify th::animate carries the UVs of geometry made in its dive, and
leaves the asset's own UVs alone, live and saved over a range.

Remy 2026-11-18: "uvs are not transfered in the th animate to the stage. Any
geometry added internally does not get uvs in the stage, breaking materials".
th::animate's three SOP Imports (static, remainder, full) read only the
attributes their Attributes pattern names, and ``uv`` was not among them: an
asset's meshes have their ``primvars:st`` from the asset's own layers, but a
mesh made in the dive has no prim under it and got none. Now the imports read
``uv*``, and ``apply`` takes back out what the asset already says
(``animate_primvars.asset_primvars``) and what only a merge filled in
(``filler_primvars``); ``tests/test_animate_points.py`` pins those on plain
stages, this cooks and saves the real HDA over a real asset layer.

The dive is plain SOPs, no rig, each shape with its pipeline path:

- ``new_vertex`` / ``new_point`` / ``new_curve``: geometry made in the dive,
  no prim under it, ``uv`` on the vertices / the points / a curve's points;
- ``new_multi``: the same with ``uv2`` and ``Cd`` beside ``uv``;
- ``new_none``: made in the dive without UVs;
- ``asset_same``: an asset's mesh the dive outputs with other UVs than the
  asset's (a rig carries the UVs its mesh had when the rig was built);
- ``asset_none``: an asset's mesh the dive outputs without UVs (merged with
  the others it gets zeros: a Merge fills a missing attribute);
- ``asset_point``: an asset's mesh whose ``st`` is per point, the dive's per
  vertex;
- ``asset_cut``: an asset's mesh the dive changed the topology of, so the
  asset's ``st`` does not fit it;
- ``asset_bare``: an asset's mesh the asset gave no ``st``.

The ``new_*`` shapes and ``asset_cut`` gain a row from frame 6 (a topology
change: that frame on is ``full_import``'s). Laid out as packed folders (the
Scene Invoke's output), as unpacked geometry (SOPs after the Scene Invoke)
and as packed folders with unpacked geometry merged in beside them. The
asset is a USD file sublayered into th::animate's input. Checked on the live
stage (frames cooked out of order) and in the range 1-10 saved by a USD ROP:

- every shape with UVs of its own to give (``new_*`` with UVs,
  ``asset_cut``, ``asset_bare``) has ``primvars:st`` on every frame, a
  ``texCoord2f[]`` with the interpolation and the values a plain SOP Import
  of the dive's geometry at that frame authors, value for value, and the
  values the dive computed (worked out again here from the points);
  ``uv2`` and ``displayColor`` likewise;
- ``asset_same``, ``asset_none`` and ``asset_point`` read the asset's own
  ``st``, values, indices and interpolation, and th::animate says nothing
  of it;
- in packed folders ``new_none`` gets no ``st`` at all;
- none of these primvars reads as indexed (a blocked time sample on a
  primvar's ``:indices`` did that to ``displayColor`` up to 1.112: indexed,
  with no indices, which no reader can use);
- no stamp primvar is left.

Needs a Houdini license; preflight's hython stage runs it. Prints one line
per scenario and each failed check, and exits 1 on any failure.
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
LIVE_FRAMES = (1, 8, 3, 6, 2)
GEO = '/CHAR/hero/geo'
ATTRIBS = 'P N visibility bounds Cd Alpha transform width pscale uv*'
STAMPS = ('primvars:__th_pt', 'primvars:__th_vtx')
KINDS = ('new_vertex', 'new_point', 'new_curve', 'new_multi', 'new_none',
         'asset_same', 'asset_none', 'asset_point', 'asset_cut', 'asset_bare')
# Which shapes gain a row at CHANGE, which have UVs in the dive (and of which
# class), which get their st from the dive and which keep the asset's.
GROWS = ('new_vertex', 'new_point', 'new_curve', 'asset_cut')
VERTEX_UV = ('new_vertex', 'new_multi', 'asset_same', 'asset_point', 'asset_cut')
POINT_UV = ('new_point', 'new_curve', 'asset_bare')
FROM_DIVE = ('new_vertex', 'new_point', 'new_curve', 'new_multi', 'asset_cut', 'asset_bare')
FROM_ASSET = ('asset_same', 'asset_none', 'asset_point')
LOOSE = ('new_vertex', 'new_point', 'new_curve', 'new_multi', 'new_none')

# The dive's geometry: one shape per kind, each at its own place (index along
# X), moving every frame. UVs come from where a point is in its shape: per
# point (x + 0.5, z + 0.5); per vertex that plus a tenth of its face's centre,
# so faces do not share UVs at a point (a seam everywhere).
DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = hou.frame()

def shape(kind, index):
    out = hou.Geometry()
    rows = 4 if kind in GROWS and frame >= CHANGE else 3
    if kind == 'new_curve':
        verb = hou.sopNodeTypeCategory().nodeVerb('line')
        verb.setParms({'points': rows + 1, 'dist': 1.0, 'dir': (1, 0, 0), 'origin': (-0.5, 0, 0)})
    else:
        verb = hou.sopNodeTypeCategory().nodeVerb('grid')
        verb.setParms({'rows': rows, 'cols': 3, 'size': (1, 1)})
    verb.execute(out, [])
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', [GEO + '/' + kind] * len(out.prims()))
    if kind in POINT_UV:
        out.addAttrib(hou.attribType.Point, 'uv', (0.0, 0.0, 0.0))
        for point in out.points():
            x, _, z = point.position()
            point.setAttribValue('uv', (x + 0.5, z + 0.5, 0.0))
    if kind in VERTEX_UV:
        out.addAttrib(hou.attribType.Vertex, 'uv', (0.0, 0.0, 0.0))
        if kind == 'new_multi':
            out.addAttrib(hou.attribType.Vertex, 'uv2', (0.0, 0.0, 0.0))
            out.addAttrib(hou.attribType.Point, 'Cd', (1.0, 1.0, 1.0))
            for point in out.points():
                x, _, z = point.position()
                point.setAttribValue('Cd', (x + 0.5, 0.25, z + 0.5))
        for prim in out.prims():
            centre = sum((v.point().position() for v in prim.vertices()), hou.Vector3()) / prim.numVertices()
            for vertex in prim.vertices():
                x, _, z = vertex.point().position()
                u, v = x + 0.5 + 0.1 * centre[0], z + 0.5 + 0.1 * centre[2]
                vertex.setAttribValue('uv', (u, v, 0.0))
                if kind == 'new_multi':
                    vertex.setAttribValue('uv2', (1.0 - u, 1.0 - v, 0.0))
    if kind in POINT_UV and kind not in packed:
        # merged by hand: on the vertices, as a Merge SOP puts point UVs
        # that meet vertex UVs (hou.Geometry.merge keeps one value a point)
        promote = hou.sopNodeTypeCategory().nodeVerb('attribpromote')
        promote.setParms({'inname': 'uv', 'inclass': 2, 'outclass': 3})
        promoted = hou.Geometry()
        promote.execute(promoted, [out])
        out = promoted
    out.transform(hou.hmath.buildTranslate(2.0 * index + 0.05 * frame, 0.02 * frame, 0))
    return out

packed = [kind for kind in KINDS if LAYOUT == 'packed' or (LAYOUT == 'mixed' and kind not in LOOSE)]
shapes = {kind: shape(kind, KINDS.index(kind)) for kind in KINDS}
if packed:
    inner = hou.Geometry()
    for kind in packed:
        inner.createPacked('PackedGeometry').setEmbeddedGeometry(shapes[kind])
    geo.createPacked('PackedGeometry').setEmbeddedGeometry(inner)
for kind in KINDS:
    if kind not in packed:
        geo.merge(shapes[kind])
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


# --- the asset ---------------------------------------------------------------

# A 3 x 3 grid as USD, the topology the dive's grids have at the first frame
# (9 points, 4 quads, 16 face-vertices); only the counts matter here.
GRID_COUNTS = [4, 4, 4, 4]
GRID_INDICES = [0, 1, 4, 3, 1, 2, 5, 4, 3, 4, 7, 6, 4, 5, 8, 7]


def asset_st(kind):
    """(values, indices or None, interpolation) of the asset's own st for
    `kind`; None for a mesh the asset gave none. Values no dive computes."""
    if kind == 'asset_same':
        return [(0.9 - 0.01 * k, 0.6 + 0.01 * k) for k in range(16)], None, 'faceVarying'
    if kind == 'asset_none':
        return ([(0.8, 0.1), (0.7, 0.2), (0.6, 0.3), (0.5, 0.4)],
                [k % 4 for k in range(16)], 'faceVarying')
    if kind == 'asset_point':
        return [(0.3 + 0.02 * k, 0.9 - 0.02 * k) for k in range(9)], None, 'vertex'
    if kind == 'asset_cut':  # one quad: its st cannot fit the dive's grid
        return [(0.11, 0.12), (0.21, 0.22), (0.31, 0.32), (0.41, 0.42)], None, 'faceVarying'
    return None


def write_asset(path):
    """The asset: /CHAR/hero/geo with a mesh per asset_* kind, each with the
    st `asset_st` gives it."""
    from pxr import Sdf, Usd, UsdGeom, Vt
    stage = Usd.Stage.CreateNew(str(path))
    for prim_path in ('/CHAR', '/CHAR/hero', GEO):
        UsdGeom.Xform.Define(stage, prim_path)
    for kind in KINDS:
        if not kind.startswith('asset_'):
            continue
        mesh = UsdGeom.Mesh.Define(stage, f'{GEO}/{kind}')
        if kind == 'asset_cut':
            mesh.CreatePointsAttr(Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]))
            mesh.CreateFaceVertexCountsAttr([4])
            mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        else:
            mesh.CreatePointsAttr(Vt.Vec3fArray(
                [(0.5 * (n % 3), 0.0, 0.5 * (n // 3)) for n in range(9)]))
            mesh.CreateFaceVertexCountsAttr(GRID_COUNTS)
            mesh.CreateFaceVertexIndicesAttr(GRID_INDICES)
        own = asset_st(kind)
        if own is None:
            continue
        values, indices, interpolation = own
        primvar = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            'st', Sdf.ValueTypeNames.TexCoord2fArray, interpolation)
        primvar.Set(Vt.Vec2fArray(values))
        if indices is not None:
            primvar.SetIndices(Vt.IntArray(indices))
    stage.GetRootLayer().Save()


# --- the scene ---------------------------------------------------------------


def build(label, layout, asset):
    """A th::animate over the asset whose dive outputs DIVE_GEO laid out as
    `layout`, nothing else, and a plain SOP Import of what it imports
    (points_sops/placed, unpacked twice as th::animate unpacks it)."""
    stage = hou.node('/stage')
    under = stage.createNode('sublayer', f'asset_{label}')
    under.parm('filepath1').set(str(asset).replace('\\', '/'))
    animate = stage.createNode('th::animate::2.0', f'animate_{label}')
    animate.setInput(0, under)
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    names = {name: globals()[name] for name in (
        'KINDS', 'GROWS', 'VERTEX_UV', 'POINT_UV', 'LOOSE', 'CHANGE', 'GEO')}
    source.parm('python').set(
        ''.join(f'{name} = {value!r}\n' for name, value in names.items())
        + f'LAYOUT = {layout!r}\n' + DIVE_GEO)
    output.setInput(0, source)
    obj = hou.node('/obj').createNode('geo', f'ref_{label}')
    merge = obj.createNode('object_merge', 'placed')
    merge.parm('objpath1').set(animate.path() + '/points_sops/placed')
    last = merge
    for name in ('unpack1', 'unpack2'):
        unpack = obj.createNode('unpack', name)
        unpack.setInput(0, last)
        last = unpack
    ref = stage.createNode('sopimport', f'ref_{label}')
    ref.parm('soppath').set(last.path())
    ref.parm('enable_attribs').set(1)
    ref.parm('attribs').set(ATTRIBS)
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


# --- reading -----------------------------------------------------------------


def primvar(prim, name, frame):
    """(type name, interpolation, values with their indices applied) of
    `prim`'s primvar `name` at `frame`; None when it has no value there, or
    cannot be read (indexed, it says, with no indices: what a blocked time
    sample on its `:indices` makes of it)."""
    from pxr import Tf, UsdGeom
    found = UsdGeom.PrimvarsAPI(prim).GetPrimvar(name) if prim else None
    if not found or not found.HasAuthoredValue():
        return None
    try:
        values = found.ComputeFlattened(frame)
    except Tf.ErrorException:
        return None
    if values is None:
        return None
    return (str(found.GetTypeName()), found.GetInterpolation(),
            np.asarray(values, np.float32))


def indexed(prim, name) -> bool:
    from pxr import UsdGeom
    return UsdGeom.PrimvarsAPI(prim).GetPrimvar(name).IsIndexed()


def same(got, want) -> bool:
    return (got is not None and want is not None and got[:2] == want[:2]
            and got[2].shape == want[2].shape and np.allclose(got[2], want[2], atol=1e-6))


def dive_st(prim, kind, frame):
    """The st the dive computed for `kind` at `frame`, per face-vertex of
    `prim` (per point of a curve), worked out from the prim's own points
    and faces: DIVE_GEO's rule, never read from a UV."""
    from pxr import UsdGeom
    points = np.asarray(UsdGeom.PointBased(prim).GetPointsAttr().Get(frame), np.float64)
    index = KINDS.index(kind)
    local = points - np.array([2.0 * index + 0.05 * frame, 0.02 * frame, 0.0])
    if kind == 'new_curve':
        return (local[:, [0, 2]] + 0.5).astype(np.float32)
    mesh = UsdGeom.Mesh(prim)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(frame))
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(frame))
    out = local[indices][:, [0, 2]] + 0.5
    if kind in VERTEX_UV:
        start = 0
        for count in counts:
            centre = local[indices[start:start + count]].mean(axis=0)
            out[start:start + count] += 0.1 * centre[[0, 2]]
            start += count
    return out.astype(np.float32)


def says(layer, kind, name) -> bool:
    """Whether `layer` has an opinion on `kind`'s primvar `name`."""
    from pxr import Sdf
    return layer.GetAttributeAtPath(Sdf.Path(f'{GEO}/{kind}').AppendProperty(name)) is not None


def check_frame(label, where, stage, ref_stage, frame, layout, own_layers):
    """Every check of one frame of `stage` (th::animate's, live or saved)
    against `ref_stage` (the plain SOP Import at that frame). `own_layers`
    are the layers that are not the asset's file."""
    tag = f'{label} {where} frame {frame}'
    for kind in KINDS:
        prim = stage.GetPrimAtPath(f'{GEO}/{kind}')
        want_prim = ref_stage.GetPrimAtPath(f'{GEO}/{kind}')
        if not prim or not want_prim:
            check(f'{tag}: {kind} is on both stages', False, (bool(prim), bool(want_prim)))
            continue
        for stamp in STAMPS:
            check(f'{tag}: {kind} carries no {stamp}', not prim.HasAttribute(stamp))
        got = primvar(prim, 'st', frame)
        if kind in FROM_DIVE:
            want = primvar(want_prim, 'st', frame)
            check(f'{tag}: {kind} has st', got is not None)
            if got is None:
                continue
            check(f'{tag}: {kind} st is a texCoord2f[]', got[0] == 'texCoord2f[]', got[0])
            check(f"{tag}: {kind} st is not indexed, as a SOP Import's is not",
                  not indexed(prim, 'st') and not indexed(want_prim, 'st'))
            check(f"{tag}: {kind} st is a SOP Import's, interpolation and values",
                  same(got, want), (got[1], want and want[1], got[2][:3].tolist(),
                                    want is not None and want[2][:3].tolist()))
            computed = dive_st(prim, kind, frame)
            check(f'{tag}: {kind} st is what the dive computed',
                  got[2].shape == computed.shape and np.allclose(got[2], computed, atol=1e-5),
                  (got[2][:3].tolist(), computed[:3].tolist()))
            grown = 4 if kind in GROWS and frame >= CHANGE else 3
            size = grown + 1 if kind == 'new_curve' else 4 * 2 * (grown - 1)
            check(f'{tag}: {kind} st has the frame\'s size', len(got[2]) == size,
                  (len(got[2]), size))
        elif kind in FROM_ASSET:
            values, indices, interpolation = asset_st(kind)
            flat = np.asarray(values if indices is None else [values[i] for i in indices],
                              np.float32)
            check(f"{tag}: {kind} reads the asset's own st",
                  got is not None and got[1] == interpolation and got[2].shape == flat.shape
                  and np.allclose(got[2], flat, atol=1e-6),
                  got and (got[1], got[2][:3].tolist()))
            check(f'{tag}: th::animate says nothing of {kind}\'s st',
                  not any(says(layer, kind, name) for layer in own_layers
                          for name in ('primvars:st', 'primvars:st:indices')))
        elif layout == 'packed':  # new_none; merged by hand it has the merge's zeros
            check(f'{tag}: {kind}, without UVs, gets no st',
                  not prim.HasAttribute('primvars:st'), got and got[2][:3].tolist())
    multi = stage.GetPrimAtPath(f'{GEO}/new_multi')
    want_multi = ref_stage.GetPrimAtPath(f'{GEO}/new_multi')
    for name in ('uv2', 'displayColor'):
        got, want = primvar(multi, name, frame), primvar(want_multi, name, frame)
        check(f'{tag}: new_multi {name} is not indexed', not indexed(multi, name))
        check(f"{tag}: new_multi {name} is a SOP Import's, interpolation and values",
              same(got, want), (got and got[:2], want and want[:2]))
    uv2, st = primvar(multi, 'uv2', frame), primvar(multi, 'st', frame)
    check(f'{tag}: new_multi uv2 is its own, not its st',
          uv2 is not None and st is not None and len(uv2[2]) == len(st[2])
          and np.allclose(uv2[2][:, :2], 1.0 - st[2], atol=1e-5))


def verify(label, layout, work, asset):
    from pxr import Sdf, Usd
    animate, ref = build(label, layout, asset)
    asset_real = Sdf.Layer.FindOrOpen(str(asset)).realPath

    def own(stage):
        return [layer for layer in stage.GetUsedLayers() if layer.realPath != asset_real]

    for frame in LIVE_FRAMES:
        hou.setFrame(frame)
        stage = animate.stage()
        check(f'{label} live frame {frame}: th::animate cooks', not animate.errors(),
              animate.errors())
        check_frame(label, 'live', stage, ref.stage(), frame, layout, own(stage))
    out = work / f'{label}.usd'
    rop = save(animate, out)
    check(f'{label}: the ROP saves without errors', not rop.errors() and out.exists(),
          rop.errors())
    if out.exists():
        saved = Usd.Stage.Open(str(out))
        layers = own(saved)
        check(f'{label}: the saved range sits over the asset\'s file',
              len(layers) < len(saved.GetUsedLayers()))
        for kind in FROM_DIVE:
            path = Sdf.Path(f'{GEO}/{kind}').AppendProperty('primvars:st')
            samples = sorted({int(t) for layer in layers
                              for t in layer.ListTimeSamplesForPath(path)})
            check(f'{label} saved: {kind} st is sampled on every frame',
                  samples == list(range(FIRST, LAST + 1)), samples)
        for frame in range(FIRST, LAST + 1):
            hou.setFrame(frame)
            check_frame(label, 'saved', saved, ref.stage(), frame, layout, layers)
    print(f'{label}: checked', flush=True)


def main():
    install()
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        asset = work / 'asset.usd'
        write_asset(asset)
        for layout in ('packed', 'flat', 'mixed'):
            verify(layout, layout, work, asset)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f'{len(CHECKED) - len(FAILED)}/{len(CHECKED)} checks passed', flush=True)
    print(f"{'FAILED: ' + ', '.join(FAILED[:10]) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
