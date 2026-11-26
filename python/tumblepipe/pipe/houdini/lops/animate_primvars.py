"""The primvars th::animate keeps, fills and takes back out: the asset's
own, the ones the dive adds, and whether a primvar fits a prim's
topology. Split from animate_points."""

import numpy as np

from tumblepipe.pipe.houdini.lops.animate_geometry import (
    PRIM_STAMP, STAMP, VERTEX_STAMP,
)
from tumblepipe.pipe.houdini.lops.animate_widths import FIND_ATTRIB


# --- primvars ----------------------------------------------------------------
#
# The static import reads `uv*` (a SOP Import turns `uv` into `primvars:st`,
# per face-vertex or per point as the attribute is; `uv2` and on keep their
# names). The rigs carry their meshes' UVs too, and those are not for the
# animation layer to say: see `asset_primvars`.

#: The primvars every static import authors, whatever is under the prim: they
#: stay as they were before UVs were read.
OWN_PRIMVARS = frozenset({'displayColor', 'displayOpacity', STAMP, VERTEX_STAMP,
                          PRIM_STAMP})
#: The SOP attribute a primvar is read from, where it is not its own name.
PRIMVAR_ATTRIBS = {'st': 'uv'}
#: `points_sops/at_start`: the dive's first frame, as `placed` outputs it.
AT_START = 'at_start'


def primvar_names(prim) -> list:
    """The primvars authored on `prim` (one the static import made) that
    `apply` may leave out again: all but `OWN_PRIMVARS`. One with no value
    counts: a block hides the asset's as a value does."""
    from pxr import UsdGeom
    return [
        primvar.GetPrimvarName()
        for primvar in UsdGeom.PrimvarsAPI(prim).GetAuthoredPrimvars()
        if primvar.GetPrimvarName() not in OWN_PRIMVARS
    ]


def prim_topology(prim) -> tuple:
    """(points, faces, face-vertices) of `prim` as the static import
    authored it: what a primvar's size is held against (`primvar_fits`). A
    curves prim's faces are its curves, its face-vertices its points."""
    from pxr import UsdGeom
    points = UsdGeom.PointBased(prim).GetPointsAttr().Get()
    count = 0 if points is None else len(points)
    if prim.IsA(UsdGeom.Mesh):
        faces = UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get()
        if faces is None:
            return count, 0, 0
        return count, len(faces), int(np.asarray(faces, dtype=np.int64).sum())
    if prim.IsA(UsdGeom.Curves):
        curves = UsdGeom.Curves(prim).GetCurveVertexCountsAttr().Get()
        return count, 0 if curves is None else len(curves), count
    return count, count, count


def primvar_fits(interpolation, size, topology) -> bool:
    """Whether a primvar of `size` elements with `interpolation` fits a
    prim of `topology` (`prim_topology`): one element (`constant`), one per
    face (`uniform`), per point (`vertex`, `varying`) or per face-vertex
    (`faceVarying`)."""
    points, faces, face_vertices = topology
    want = {
        'constant': 1, 'uniform': faces, 'vertex': points, 'varying': points,
        'faceVarying': face_vertices,
    }.get(interpolation)
    return want is not None and size == want


def asset_primvars(stage, mapping, time=None) -> list:
    """Per mapped prim, the primvars the static import authored (`mapping`'s
    'primvars') that `stage`, the stage coming into th::animate, already has
    on that prim with a value that fits the prim's topology as th::animate
    authors it (`primvar_fits`). Those are the asset's to say, and `apply`
    takes the dive's copy back out (`remove_primvars`).

    The rigs carry their meshes' UVs, a copy made when the rig was built. In
    the animation layer, which is stronger than the asset's, that copy would
    hide every later UV fix of the asset, and a mesh merged with geometry
    that has UVs when it has none would hide the asset's with zeros (a Merge
    fills a missing attribute). So the asset's primvar stands wherever it
    can: the dive's is authored on a prim with none under it (geometry made
    in the dive, which got no UVs at all up to 1.112: its materials broke),
    and where the asset's does not fit (the dive changed the mesh's
    topology). A primvar that is one value and no array (`float`, not
    `float[]`) counts as one element. An empty set per prim without
    `stage`."""
    from pxr import Usd, UsdGeom
    if time is None:
        time = Usd.TimeCode.EarliestTime()
    names = mapping.get('primvars') or [[] for _ in mapping['paths']]
    topology = mapping.get('topology') or [None] * len(mapping['paths'])
    found = []
    for path, own, shape in zip(mapping['paths'], names, topology):
        have = set()
        prim = stage.GetPrimAtPath(path) if stage is not None and own else None
        if prim and shape is not None:
            api = UsdGeom.PrimvarsAPI(prim)
            for name in own:
                primvar = api.GetPrimvar(name)
                if not primvar or not primvar.HasAuthoredValue():
                    continue
                values = (primvar.GetIndices(time) if primvar.IsIndexed()
                          else primvar.Get(time))
                if values is None:  # blocked: the asset says nothing
                    continue
                # one value that is no array (a float, a vector: `len` of
                # it is an error or its components) is one element
                size = len(values) if primvar.GetTypeName().isArray else 1
                if primvar_fits(primvar.GetInterpolation(), size, shape):
                    have.add(name)
        found.append(have)
    return found


def _has_attrib(geo, name) -> bool:
    return any(getattr(geo, find)(name) is not None for find in FIND_ATTRIB.values())


def filler_primvars(leaves, mapping) -> list:
    """Per mapped prim, the primvars the static import authored on it only
    because another shape has the attribute: `leaves` are the dive's packed
    shapes at the first frame (`packed_leaves`), and merging them (the two
    Unpack SOPs before the static import) gives a shape without `uv` the
    zeros a Merge fills a missing attribute with. A SOP Import of the shape
    alone authors no such primvar, and neither does th::animate
    (`remove_primvars`). The shape is the one holding the prim's first
    point, as `read_visibility` finds it; a primvar whose attribute
    (`PRIMVAR_ATTRIBS`, else its own name) no shape has is left alone.
    Empty sets without `leaves` (a dive that is not packed folders merged
    its shapes itself)."""
    names = mapping.get('primvars') or [[] for _ in mapping['paths']]
    if not leaves:
        return [set() for _ in names]
    starts = np.cumsum([0] + [leaf.intrinsicValue('pointcount') for leaf in leaves])
    index, bounds = mapping['index'], mapping['bounds']
    known = {}

    def has(leaf, attrib) -> bool:
        key = (leaf, attrib)
        if key not in known:
            known[key] = _has_attrib(leaves[leaf], attrib)
        return known[key]

    found = []
    for i, own in enumerate(names):
        filler = set()
        if own and bounds[i + 1] > bounds[i]:
            number = int(index[int(bounds[i])])
            if 0 <= number < starts[-1]:
                leaf = int(np.searchsorted(starts, number, side='right')) - 1
                for name in own:
                    attrib = PRIMVAR_ATTRIBS.get(name, name)
                    if has(leaf, attrib):
                        continue
                    if any(has(other, attrib) for other in range(len(leaves))):
                        filler.add(name)
        found.append(filler)
    return found


def remove_primvars(layer, paths, names) -> None:
    """Take the primvars `names` (a set per prim at `paths`) back out of
    `layer`, the static import's: the attribute and its `:indices`."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for path, doomed in zip(paths, names):
            spec = layer.GetPrimAtPath(path) if doomed else None
            if spec is None: continue
            for name in doomed:
                for prop_name in (f'primvars:{name}', f'primvars:{name}:indices'):
                    prop = spec.properties.get(prop_name)
                    if prop is not None:
                        spec.RemoveProperty(prop)
