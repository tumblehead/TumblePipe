"""th::animate: import the rigs' geometry once, then copy only what moves.

What `anim_dive` outputs (its Output SOP with index 0, whatever it is
called, else its display node: `dive_output`) is imported in full, with
one SOP Import, at the shot's first frame (the HDA's `points_sops/REST`,
LOP `static_import`). Every frame after that, `apply` copies the points,
normals and extents out of the dive's geometry onto those prims, the way
SideFX's APEX bake copies a GU_Detail's P/N into USD arrays
(HoudiniUsdBridge XUSD_ApexBakeSceneUtils). Converting the whole
geometry to USD every frame, and unpacking it first, is what made the
node slow; the result is the same, value for value.

What is read is `points_sops/placed`: the dive's output (`points_sops/scene`,
invoked first by the HDA when the dive outputs the APEX scene itself:
`animate_invoke`) less the shapes with nowhere to go (see the end).

The Scene Invoke outputs packed folders, so its shapes reach here
without the unpack: `read_points` reads each packed shape's points where
they are. A dive whose Scene Invoke still outputs unpacked geometry
straight into the dive's output is switched to packed folders when the
scene loads (`optimize_dive`), with a comment on the Scene Invoke and a
message, so the change is never silent; once per Scene Invoke, and never
when anything else reads its geometry. A dive with SOPs after its Scene
Invoke (TumbleRig's Camera Lattice, say) is left as it is: those SOPs keep
their unpacked geometry, and this reads what they output.

Which point lands where is not worked out here: the static import carries
each point's number (`__th_pt`, stamped in `points_sops`) as a primvar, so
the SOP Import's own point order is read back from the stage, then the
primvar is removed. Prims with no points (cameras, lights, packed prims)
are imported every frame, by `remainder_import`.

If a frame's point count is not the first frame's, the rigs changed
topology (a rig that cuts its mesh as it poses: shot 010's lilGuy from
frame 1151), and `full_import` imports that frame whole. `apply` copies what
it made into its own layer at that frame (`author_full_frame`), not a layer
of its own: a USD ROP saving a frame range composes every frame's layers
into one file, and the full import's layer, stronger than `apply`'s, hid
`apply`'s samples (or, chosen by a switch from that frame on, was dropped
whole); the export held the first pose from the change on. Every frame
also samples the static import's other varying attributes (topology,
displayColor: `sample_defaults`), as a SOP Import with animated
topology does, so frames before a change keep their own topology once
later frames sample theirs. On a changed frame a prim the first frame had
takes only the attributes it had then, and those of them the full import
lacks are blocked there, never left at the first frame's (sized for its
points). A prim that first appears on such a frame is visible before it too
(it holds its first sample).

Normals follow the same way, whichever kind the rig puts out. Point N
reaches the stage per point (`vertex` interpolation) and is copied with the
points. Vertex N (a Normal SOP's default, and what some rigs' graphs put
out) reaches it per face-vertex (`faceVarying`): `rest_gate` stamps each
vertex's number (`__th_vtx`) when the first frame has vertex N, the static
import carries it like `__th_pt`, and `apply` copies each frame's vertex N in
that order. Before, only point N was copied, and vertex normals stayed as
they were at the first frame while the points moved: shading turned with
the world, not the character, once the meshes stopped being subdivided
(v1.106.0; a subdivision surface ignores authored normals). Normals of any
other kind (per primitive, per detail) cannot follow; the node warns.

Visibility follows too. A rig that shows and hides a shape (TumbleRig's TH
Visibility) writes `usdvisibility` on its primitives every frame, which a
SOP Import turns into the prim's USD `visibility`. The static import did
that once, so up to 1.112.0 the first frame's visibility held for the shot:
a shape hidden at the first frame never appeared, one hidden later never
hid (1.112.1 sampled each frame's where the rig wrote it). `apply` reads each frame's (`read_visibility`) and samples it on every
prim, every frame (`author_visibility`): where the dive says nothing, what
the prim has without it (`visibility_bases`). Every frame, because a prim
sampled only where it is hidden is read as hidden on every frame of a saved
range: a shape missing on a frame whose topology changed is made invisible
there, which hid it for the shot when no other frame had a sample. A prim
that first appears on a changed frame is sampled on every changed frame the
same way (sampled only where the dive said something of it, it read the
nearest such frame's on the changed frames that said nothing).

Widths follow as well. The imports read `width` and `pscale` (left out of
their Attributes pattern up to 1.109, every curve got the missing width,
0.01), and `apply` copies each frame's (`read_widths`) onto the curves and
points: per point, per curve or one, as the static import authored them
(`width_offsets`). Each shape's widths are read where the shape is, as a SOP
Import of it alone reads them. Merging shapes that read different
attributes (pscale beside width, curves with none beside hair with some)
gives the others the attribute's default, 0 as a rule: there
`normalize_widths` writes each shape's own as point width onto the merged
geometry, in the static import's (`rest_gate`) and in the frame
`full_import` imports (`current_widths`), so every frame agrees.

UVs come with the static import, for the geometry that has no others. The
imports read `uv*` (left out of their Attributes pattern up to 1.112: a
mesh made in the dive has no asset prim under it to give it UVs, got none,
and its materials broke). The rigs carry their meshes' UVs too, and those
the animation layer must not say again: it is stronger than the asset's,
and a rig's copy (or the zeros a Merge gives a mesh without `uv` beside
one with) would hide the asset's own. So `apply` takes a primvar of the
static import back out where the incoming stage already has one that fits
the prim (`asset_primvars`), and where only the merge of the dive's shapes
put it there (`filler_primvars`). What stays is read once, like `Cd`: UVs
animated in the dive hold the first frame's, but for a frame whose topology
changed, which brings its own. A primvar's `:indices`, blocked by the SOP
Import ("not indexed"), is never sampled as the other defaults are
(`_unindexes_nothing`): the block as a time sample reads as indexed with no
indices, which `displayColor` did on every frame up to 1.112.

Only shapes with somewhere to go reach the imports (`points_sops/placed`,
`placed_shapes`). A Scene Invoke whose Shapes pattern is wider than
`Base` (Houdini's own default is `*`) also outputs a rig's helper shapes,
a TumbleRig rig's joint and spline viz, face board and skeleton display.
They carry no `path`, so a SOP Import would put them under a root-level
`/Geometry`, outside every character, and the animation layer would
publish them. In a character that has geometry with paths, its path-less
shapes made of points (meshes, curves) are left out, with a warning on the
node and one status-bar message. A character with no path anywhere (a rig
not built for the pipeline) is kept as it is and named in the warning.
"""

import time

import hou
import numpy as np

STAMP = '__th_pt'
COUNT = '__th_count'
STAMP_PRIMVAR = f'primvars:{STAMP}'
# Each vertex's number, stamped only when the first frame has vertex N.
VERTEX_STAMP = '__th_vtx'
VERTEX_COUNT = '__th_vcount'
VERTEX_STAMP_PRIMVAR = f'primvars:{VERTEX_STAMP}'
POINTS_SOPS = 'points_sops'
REST = 'REST'
CURRENT = 'CURRENT'
SCENE = 'scene'
DIVE = 'anim_dive'

# SOP primitive types a SOP Import turns into a UsdGeomPointBased prim;
# the per-frame copy covers these, `remainder_import` the rest.
POINT_BASED_TYPES = (
    'Poly', 'PolySoup', 'Mesh', 'NURBCurve', 'BezierCurve', 'Part',
)

_POINT_BASED_ENUMS = (
    'Polygon', 'PolySoup', 'Mesh', 'NURBSCurve', 'BezierCurve', 'ParticleSystem',
)


def non_point_prim_count(geo) -> int:
    """How many of `geo`'s prims a SOP Import does not make a point-based
    prim from (cameras, packed prims, volumes, ...), counted in C++."""
    point_based = sum(
        geo.countPrimType(getattr(hou.primType, name))
        for name in _POINT_BASED_ENUMS if hasattr(hou.primType, name)
    )
    return geo.intrinsicValue('primitivecount') - point_based


_mapping_cache = {}

#: `points_sops/placed`: what the imports read instead of `scene`.
PLACED = 'placed'
# What was last announced per node, so a cook every frame announces once.
_announced: dict = {}


class TopologyChanged(Exception):
    pass


# --- reading the dive's points ------------------------------------------


def _packed_leaves(geo):
    """The shapes inside `geo`'s packed folders, in the order two Unpack
    SOPs lay out their points; None when `geo` is not two levels of packed
    prims with identity transforms over unpacked geometry (`read_points`
    then reads an unpacked copy instead)."""
    leaves = []
    folders = geo.prims()
    if len(folders) == 0: return None
    for folder in folders:
        if folder.type() != hou.primType.PackedGeometry: return None
        if folder.fullTransform() != hou.hmath.identityTransform(): return None
        inner = folder.getEmbeddedGeometry()
        if inner is None: return None
        for shape in inner.prims():
            if shape.type() != hou.primType.PackedGeometry: return None
            if shape.fullTransform() != hou.hmath.identityTransform(): return None
            shape_geo = shape.getEmbeddedGeometry()
            if shape_geo is None: return None
            if shape_geo.countPrimType(hou.primType.PackedGeometry): return None
            leaves.append(shape_geo)
    return leaves


def _has_packed(geo) -> bool:
    return geo.countPrimType(hou.primType.PackedGeometry) > 0


def _float3(geo, name):
    attrib = geo.findPointAttrib(name)
    count = geo.intrinsicValue('pointcount')
    if attrib is None or attrib.size() != 3:
        return None if name != 'P' else np.zeros((count, 3), np.float32)
    return np.frombuffer(
        geo.pointFloatAttribValuesAsString(name), dtype=np.float32
    ).reshape(count, 3)


def _vertex_float3(geo, name):
    """`geo`'s vertex attribute `name` as (vertices, 3) float32, in the
    order HOM lists vertices (each primitive's, primitive by primitive);
    None when there is no such 3-float attribute."""
    attrib = geo.findVertexAttrib(name)
    if attrib is None or attrib.size() != 3:
        return None
    return np.frombuffer(
        geo.vertexFloatAttribValuesAsString(name), dtype=np.float32
    ).reshape(-1, 3)


def _computed_normals(geo, per_vertex: bool):
    """Normals for `geo`, which has none of the kind asked for: per vertex,
    its own point N copied to each vertex when it has point N, else what a
    Normal SOP with its defaults computes; per point, the Normal SOP's.

    A shot can mix rigs with point N, vertex N and none at all. The static
    import merges them, so every mesh gets the kind one of them has (vertex
    N wins), and `apply` then asks every rig for it. Zeros in its place
    (what this replaces) render as a flat cut-out in Karma."""
    # Houdini keeps one N per name: a shape with point N has no vertex N to
    # clear first, and the verb leaves `geo` as it was.
    if per_vertex and geo.findPointAttrib('N') is not None:
        promote = hou.sopNodeTypeCategory().nodeVerb('attribpromote')
        promote.setParms({'inname': 'N', 'inclass': 2, 'outclass': 3, 'deletein': 0})
        out = hou.Geometry()
        promote.execute(out, [geo])
        promoted = _vertex_float3(out, 'N')
        if promoted is not None:
            return promoted
    verb = hou.sopNodeTypeCategory().nodeVerb('normal')
    verb.setParms({'type': 1 if per_vertex else 0})
    out = hou.Geometry()
    verb.execute(out, [geo])
    return _vertex_float3(out, 'N') if per_vertex else _float3(out, 'N')


def _filled(values, geo, per_vertex: bool):
    """`values` (N per vertex or per point, or None) with every zero-length
    normal replaced by a computed one (`_computed_normals`), all of them
    when there are none. Geometry merged from shapes with and without N
    has zeros where it had none (a Merge or Unpack fills a missing
    attribute with zeros)."""
    if values is None:
        return _computed_normals(geo, per_vertex)
    zero = np.einsum('ij,ij->i', values, values) < 1e-20
    if not zero.any():
        return values
    values = values.copy()
    values[zero] = _computed_normals(geo, per_vertex)[zero]
    return values


def _concat_or_none(leaves, read, per_vertex: bool):
    """Each leaf's `read(leaf)` one after another, a leaf without it (or
    with zero normals in it) given computed ones (`_filled`); None when no
    leaf has it."""
    parts = [read(leaf) for leaf in leaves]
    if all(part is None for part in parts):
        return None
    return np.concatenate([
        _filled(part, leaf, per_vertex) for part, leaf in zip(parts, leaves)
    ]) if parts else None


# --- widths ------------------------------------------------------------------
#
# Where a SOP Import takes a curve's or a point cloud's USD `widths` from, first
# found first: `width` on the points or the vertices (Houdini keeps one class
# per name), on the primitives, on the detail, and only then point `pscale`
# (doubled: pscale is a radius). Without any of them it authors the Set
# Missing Widths value (0.01 here), the "default width" a missing
# `width`/`pscale` in the Attributes pattern gave every hair.
WIDTH_SOURCES = (
    ('point', 'width'), ('vertex', 'width'), ('prim', 'width'),
    ('detail', 'width'), ('point', 'pscale'),
)
#: The HDA's Set Missing Widths, when its static import cannot be asked.
MISSING_WIDTH = 0.01

_FIND = {
    'point': 'findPointAttrib', 'vertex': 'findVertexAttrib',
    'prim': 'findPrimAttrib', 'detail': 'findGlobalAttrib',
}
_CLASS = {'detail': 0, 'prim': 1, 'point': 2, 'vertex': 3}


def _scalar_attrib(geo, kind, name):
    """`geo`'s one-float attribute `name` of class `kind`, else None."""
    attrib = getattr(geo, _FIND[kind])(name)
    if attrib is None or attrib.size() != 1:
        return None
    return attrib if _enum_name(attrib.dataType()) == 'Float' else None


def width_source(geos):
    """(class, name, default) of the attribute a SOP Import of `geos`
    merged into one (as two Unpack SOPs merge a dive's shapes) reads widths
    from, `default` what the merge gives a shape without it (the attribute's
    default where it first appears); None when none of them has one."""
    for kind, name in WIDTH_SOURCES:
        for geo in geos:
            attrib = _scalar_attrib(geo, kind, name)
            if attrib is not None:
                default = attrib.defaultValue()
                if isinstance(default, (tuple, list)):
                    default = default[0] if default else 0.0
                return kind, name, float(default)
    return None


def _promoted(geo, kind, name, to='point'):
    """`geo`'s `kind` attribute `name` as one value per point (`to`
    'point', the way an Attribute Promote SOP averages it) or per vertex
    (`to` 'vertex': each vertex its point's or primitive's value), in HOM's
    order."""
    promote = hou.sopNodeTypeCategory().nodeVerb('attribpromote')
    promote.setParms({'inname': name, 'inclass': _CLASS[kind],
                      'outclass': _CLASS[to], 'deletein': 1})
    out = hou.Geometry()
    promote.execute(out, [geo])
    values = (out.vertexFloatAttribValuesAsString(name) if to == 'vertex'
              else out.pointFloatAttribValuesAsString(name))
    return np.frombuffer(values, dtype=np.float32)


def widths_of(geo, source, per_vertex=False) -> np.ndarray:
    """One width per point of `geo` (per vertex with `per_vertex`, in HOM's
    vertex order), as a SOP Import authors them from `source`
    (`width_source`): the attribute's own values, `default` where `geo`
    lacks it (a shape merged with others that have it), pscale doubled;
    the values of a coarser class on each of its points (vertices). Vertex
    widths per point are averaged: per vertex, a curve's joint (one point,
    two vertices) keeps each vertex's own, as a SOP Import does when it
    gives each curve its own copy of the point."""
    kind, name, default = source
    to = 'vertex' if per_vertex else 'point'
    count = geo.intrinsicValue(f'{to}count')
    scale = 2.0 if name == 'pscale' else 1.0
    attrib = _scalar_attrib(geo, kind, name)
    if attrib is None:
        values = np.full(count, default, np.float32)
    elif kind == to:
        values = np.frombuffer(getattr(geo, f'{to}FloatAttribValuesAsString')(name),
                               dtype=np.float32)
    elif kind == 'detail':
        values = np.full(count, geo.attribValue(name), np.float32)
    else:
        values = _promoted(geo, kind, name, to)
    return (values * scale).astype(np.float32) if scale != 1.0 else values.astype(np.float32)


def vertex_widths(geos) -> bool:
    """Whether a shape of `geos` reads its widths from the vertices: they
    are then read per vertex, a joint of two curves holding two widths."""
    return any((width_source([geo]) or ('',))[0] == 'vertex' for geo in geos)


def read_widths(geos, missing=MISSING_WIDTH, per_vertex=False):
    """One width per point of `geos` laid out one after another (a dive's
    shapes as two Unpack SOPs merge them), or per vertex with `per_vertex`,
    each shape's as a SOP Import of that shape alone authors them
    (`width_source` of it), `missing` for a shape without any; None when no
    shape has a width (the static import then authored the missing width,
    the same every frame).

    Where every shape reads the same attribute this is what a SOP Import of
    the merge authors too. Where they differ, the merge would not keep
    them (`width_conflict`): `normalize_widths` puts these on the merged
    geometry first."""
    sources = [width_source([geo]) for geo in geos]
    if all(source is None for source in sources):
        return None
    count = 'vertexcount' if per_vertex else 'pointcount'
    parts = [
        np.full(geo.intrinsicValue(count), missing, np.float32) if source is None
        else widths_of(geo, source, per_vertex)
        for geo, source in zip(geos, sources)
    ]
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


_SURFACE_ENUMS = ('Polygon', 'PolySoup', 'Mesh')


def _takes_widths(geo) -> bool:
    """Whether a SOP Import gives some of `geo`'s prims widths: anything
    but closed polygons (curves, open polygons, particles)."""
    surfaces = sum(
        geo.countPrimType(getattr(hou.primType, name))
        for name in _SURFACE_ENUMS if hasattr(hou.primType, name)
    )
    if surfaces < geo.intrinsicValue('primitivecount'):
        return True
    return surfaces > 0 and len(geo.globPrims('@intrinsic:closed=0')) > 0


def width_conflict(geos) -> bool:
    """Whether merging `geos` (two Unpack SOPs, a dive's shapes) loses
    widths: the shapes that take widths (`_takes_widths`) or have one do not
    all read the same attribute. The merge keeps one attribute and fills
    the shapes without it with its default (0, as a rule): a rig with pscale
    beside one with width, curves with no width beside hair with one, got
    no width at all."""
    sources = set()
    for geo in geos:
        source = width_source([geo])
        if source is None and not _takes_widths(geo):
            continue
        sources.add(None if source is None else source[:2])
    return len(sources) > 1


def normalize_widths(merged, geos, missing=MISSING_WIDTH) -> bool:
    """Put each shape's own widths (`read_widths`) on `merged`, `geos`
    merged by two Unpack SOPs (one point and vertex each, in that order),
    as `width`, which a SOP Import reads first, when the merge lost them
    (`width_conflict`): on the vertices when a shape reads vertex widths
    (`vertex_widths`; a joint keeps both its widths), else on the points.
    The static import (`rest_gate`) and the full import of a frame whose
    topology changed (`current_widths`) both read it, so they author what
    `apply` copies every other frame. True when written."""
    if not geos or not width_conflict(geos):
        return False
    per_vertex = vertex_widths(geos)
    values = read_widths(geos, missing, per_vertex)
    to = 'vertex' if per_vertex else 'point'
    if values is None or len(values) != merged.intrinsicValue(f'{to}count'):
        return False
    for kind in ('point', 'vertex'):  # one class per name: drop the other
        attrib = getattr(merged, _FIND[kind])('width')
        if attrib is not None and (kind != to or attrib.size() != 1
                                   or _enum_name(attrib.dataType()) != 'Float'):
            attrib.destroy()
    if getattr(merged, _FIND[to])('width') is None:
        merged.addAttrib(getattr(hou.attribType, 'Vertex' if per_vertex else 'Point'),
                         'width', 0.0)
    getattr(merged, f'set{to.capitalize()}FloatAttribValuesFromString')(
        'width', np.ascontiguousarray(values, dtype=np.float32).tobytes())
    return True


def missing_width(import_node) -> float:
    """The Set Missing Widths of `import_node` (the static import)."""
    parm = import_node.parm('setmissingwidths') if import_node is not None else None
    return float(parm.eval()) if parm is not None else MISSING_WIDTH


def read_points(geo, unpacked_node=None, vertex_normals=False, widths=False,
                missing=MISSING_WIDTH) -> dict:
    """{'P': (n, 3) float32, 'N': (n, 3) or None, 'vN': (vertices, 3) or
    None, 'W': float32 or None} for `geo` as two Unpack SOPs would lay it
    out. 'vN' (vertex N) is read only when `vertex_normals` asks for it,
    'W' (`read_widths`, `missing` for a shape without) only when `widths`
    does: 'point' one per point, 'vertex' one per vertex (`widths_mode`).
    Packed shapes are read where they are; anything else is read from
    `unpacked_node`'s cook (points_sops/CURRENT)."""
    per_vertex = widths == 'vertex'

    def flat(source):
        normals = _float3(source, 'N')
        vertex = _vertex_float3(source, 'N') if vertex_normals else None
        return {
            'P': _float3(source, 'P'),
            'N': None if normals is None else _filled(normals, source, False),
            'vN': None if vertex is None else _filled(vertex, source, True),
            'W': read_widths([source], missing, per_vertex) if widths else None,
        }
    if not _has_packed(geo):
        return flat(geo)
    leaves = _packed_leaves(geo)
    if leaves is None:
        if unpacked_node is None:
            raise hou.NodeError('th::animate: no unpacked copy of the dive to read.')
        return flat(unpacked_node.geometry())
    points = [_float3(leaf, 'P') for leaf in leaves]
    normals = _concat_or_none(leaves, lambda leaf: _float3(leaf, 'N'), False)
    vertex = None
    if vertex_normals:
        vertex = _concat_or_none(leaves, lambda leaf: _vertex_float3(leaf, 'N'), True)
    return {
        'P': np.concatenate(points) if points else np.zeros((0, 3), np.float32),
        'N': normals,
        'vN': vertex,
        'W': read_widths(leaves, missing, per_vertex) if widths else None,
    }


# --- visibility --------------------------------------------------------------
#
# A SOP Import authors a prim's USD `visibility` from `usdvisibility`: on the
# primitives (the prim's first primitive decides), the points or the detail;
# `invisible` hides, any other value shows, an empty one authors nothing.
# TumbleRig's TH Visibility writes it on the primitives it switches, every
# frame.
VISIBILITY = 'usdvisibility'


def said_visibility(value):
    """What a SOP Import authors for a `usdvisibility` of `value`: None for
    an empty one, 'invisible', else 'inherited'."""
    if not value:
        return None
    return 'invisible' if value == 'invisible' else 'inherited'


def _is_string(attrib) -> bool:
    return attrib is not None and _enum_name(attrib.dataType()) == 'String'


def _visibility_reader(geo):
    """A function from a point's number in `geo` to the `usdvisibility` of
    the shape it is in (of the first primitive using the point, of the point
    itself, or the detail's); None when `geo` has no such attribute."""
    if _is_string(geo.findPrimAttrib(VISIBILITY)):
        def of_prim(number):
            prims = geo.point(number).prims()
            return prims[0].attribValue(VISIBILITY) if prims else ''
        return of_prim
    if _is_string(geo.findPointAttrib(VISIBILITY)):
        return lambda number: geo.point(number).attribValue(VISIBILITY)
    if _is_string(geo.findGlobalAttrib(VISIBILITY)):
        value = geo.attribValue(VISIBILITY)
        return lambda number: value
    return None


def read_visibility(geo, mapping, unpacked_node=None):
    """What `geo` (the dive's frame, laid out as `read_points` reads it)
    says of each mapped prim's visibility: 'invisible', 'inherited', or None
    where it says nothing (`said_visibility` of the `usdvisibility` where
    the prim's first point is). None when no shape has `usdvisibility`: the
    frame says nothing at all, and nothing more is read. Only for a frame
    with the first frame's topology: the points are found by number."""
    if not _has_packed(geo):
        sources = [geo]
    else:
        sources = _packed_leaves(geo)
        if sources is None:
            if unpacked_node is None:
                raise hou.NodeError('th::animate: no unpacked copy of the dive to read.')
            sources = [unpacked_node.geometry()]
    readers = [_visibility_reader(source) for source in sources]
    if not any(reader is not None for reader in readers):
        return None
    starts = np.cumsum([0] + [source.intrinsicValue('pointcount') for source in sources])
    index, bounds = mapping['index'], mapping['bounds']
    said = []
    for i in range(len(mapping['paths'])):
        value = None
        if bounds[i + 1] > bounds[i]:
            number = int(index[int(bounds[i])])
            if 0 <= number < starts[-1]:
                source = int(np.searchsorted(starts, number, side='right')) - 1
                if readers[source] is not None:
                    value = said_visibility(readers[source](number - int(starts[source])))
        said.append(value)
    return said


def visibility_bases(stage, layer, paths, frame) -> list:
    """The visibility of each prim at `paths` on a frame where the dive
    says nothing of it. 'inherited' when the static import authored its
    visibility (the first frame said something; `layer`, apply's, has it as
    a default): a SOP Import of a frame that says nothing authors none.
    Else what `stage`, the stage coming into th::animate (None without one),
    has for it at `frame`: th::animate's layer is the stronger, and what is
    under it keeps its say (a mesh the asset hides stays hidden)."""
    bases = []
    for path in paths:
        base = 'inherited'
        own = layer.GetAttributeAtPath(path.AppendProperty('visibility'))
        if stage is not None and (own is None or not own.HasInfo('default')):
            prim = stage.GetPrimAtPath(path)
            attr = prim.GetAttribute('visibility') if prim else None
            if attr and attr.HasAuthoredValue() and attr.Get(frame) == 'invisible':
                base = 'invisible'
        bases.append(base)
    return bases


def author_visibility(layer, paths, said, bases, frame) -> None:
    """A visibility sample at `frame` on every prim at `paths` that has none
    there yet: what the dive says of it this frame (`said`:
    `read_visibility`; None when it says nothing of any), else `bases`
    (`visibility_bases`).

    On every prim, every frame, whatever the first frame had: a prim hidden
    on a few frames and sampled only on those is read as hidden on all of
    them, once a USD ROP has put the range in one file. Up to 1.112 the
    visibility was the static import's, the first frame's, on every frame
    (hidden at the first frame: hidden for the shot), and a shape missing on
    a frame whose topology changed was made invisible at that frame alone,
    so for the shot."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for i, path in enumerate(paths):
            attr_path = path.AppendProperty('visibility')
            if layer.QueryTimeSample(attr_path, frame) is not None:
                continue
            if layer.GetAttributeAtPath(attr_path) is None:
                spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
                Sdf.AttributeSpec(spec, 'visibility', Sdf.ValueTypeNames.Token,
                                  Sdf.VariabilityVarying)
            value = said[i] if said is not None else None
            layer.SetTimeSample(attr_path, frame, value or bases[i])


# --- primvars ----------------------------------------------------------------
#
# The static import reads `uv*` (a SOP Import turns `uv` into `primvars:st`,
# per face-vertex or per point as the attribute is; `uv2` and on keep their
# names). The rigs carry their meshes' UVs too, and those are not for the
# animation layer to say: see `asset_primvars`.

#: The primvars every static import authors, whatever is under the prim: they
#: stay as they were before UVs were read.
OWN_PRIMVARS = frozenset({'displayColor', 'displayOpacity', STAMP, VERTEX_STAMP})
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
    topology). An empty set per prim without `stage`."""
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
                if primvar_fits(primvar.GetInterpolation(), len(values), shape):
                    have.add(name)
        found.append(have)
    return found


def _has_attrib(geo, name) -> bool:
    return any(getattr(geo, find)(name) is not None for find in _FIND.values())


def filler_primvars(leaves, mapping) -> list:
    """Per mapped prim, the primvars the static import authored on it only
    because another shape has the attribute: `leaves` are the dive's packed
    shapes at the first frame (`_packed_leaves`), and merging them (the two
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


def point_count(geo) -> int:
    """How many points `read_points` would return for `geo`."""
    if not _has_packed(geo):
        return geo.intrinsicValue('pointcount')
    leaves = _packed_leaves(geo)
    if leaves is None:
        return -1
    return sum(leaf.intrinsicValue('pointcount') for leaf in leaves)


# --- the point map ---------------------------------------------------------


def _stamp_numbers(prim, name):
    """The int64 numbers a stamp primvar `name` holds on `prim`, its
    `:indices` applied; None when it has none."""
    stamp = prim.GetAttribute(name)
    numbers = stamp.Get() if stamp else None
    if numbers is None:
        return None
    numbers = np.asarray(numbers, dtype=np.int64)
    indices = prim.GetAttribute(f'{name}:indices')
    if indices and indices.Get() is not None:
        numbers = numbers[np.asarray(indices.Get(), dtype=np.int64)]
    return numbers


def normals_kind(interpolation, has_vertex_stamp: bool) -> str:
    """What `apply` copies onto authored normals of `interpolation`:
    'point' (per point: the points' N), 'vertex' (per face-vertex, and the
    prim carries the vertex stamp: the vertices' N) or 'frozen' (per
    primitive or detail, or per face-vertex without the stamp: nothing to
    copy them from, so they keep the first frame's)."""
    if interpolation in ('vertex', 'varying'):
        return 'point'
    if interpolation == 'faceVarying' and has_vertex_stamp:
        return 'vertex'
    return 'frozen'


def point_map(stage) -> dict:
    """What the static import made, for `apply`: every point-based prim
    carrying the stamp, its points' numbers in the dive's geometry, its
    vertices' numbers when it carries the vertex stamp, and which normals
    it authors.

    {'paths': [Sdf.Path], 'index': int64 array (all prims' point numbers,
    concatenated), 'bounds': start of each prim's run (len(paths) + 1),
    'vindex' / 'vbounds': the same for vertex numbers (a prim without the
    vertex stamp has an empty run), 'normals': [None (none authored) or
    `normals_kind`], 'frozen': [Sdf.Path] (the prims whose normals are
    'frozen'), 'widths': [VtArray or None], 'width_offsets': [int array or
    None: `width_offsets`], 'kinds': [str], 'primvars': [[str]:
    `primvar_names`], 'topology': [`prim_topology`, None for a prim with no
    such primvar]}"""
    from pxr import UsdGeom
    paths, runs, vruns, normals, widths, kinds = [], [], [], [], [], []
    offsets, primvars, topology = [], [], []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.PointBased): continue
        numbers = _stamp_numbers(prim, STAMP_PRIMVAR)
        if numbers is None: continue
        vertex_numbers = _stamp_numbers(prim, VERTEX_STAMP_PRIMVAR)
        point_based = UsdGeom.PointBased(prim)
        paths.append(prim.GetPath())
        primvars.append(primvar_names(prim))
        topology.append(prim_topology(prim) if primvars[-1] else None)
        runs.append(numbers)
        vruns.append(vertex_numbers if vertex_numbers is not None else np.zeros(0, np.int64))
        if point_based.GetNormalsAttr().HasAuthoredValue():
            normals.append(normals_kind(
                point_based.GetNormalsInterpolation(), vertex_numbers is not None,
            ))
        else:
            normals.append(None)
        if prim.IsA(UsdGeom.Mesh):
            kinds.append('mesh')
            widths.append(None)
            offsets.append(None)
        else:
            kinds.append('curves' if prim.IsA(UsdGeom.Curves) else 'points')
            width_attr = prim.GetAttribute('widths')
            value = width_attr.Get() if width_attr else None
            widths.append(value)
            counts = None
            if prim.IsA(UsdGeom.Curves):
                counts = UsdGeom.Curves(prim).GetCurveVertexCountsAttr().Get()
            interpolation = (width_attr.GetMetadata('interpolation') or 'vertex'
                             if width_attr else None)
            offsets.append(None if value is None else width_offsets(
                interpolation, len(value), len(numbers),
                None if counts is None else list(counts),
            ))
    index = np.concatenate(runs) if runs else np.zeros(0, np.int64)
    vindex = np.concatenate(vruns) if vruns else np.zeros(0, np.int64)
    return {
        'paths': paths,
        'index': index,
        'bounds': np.cumsum([0] + [len(run) for run in runs]),
        'vindex': vindex,
        'vbounds': np.cumsum([0] + [len(run) for run in vruns]),
        'normals': normals,
        'frozen': [path for path, kind in zip(paths, normals) if kind == 'frozen'],
        'widths': widths,
        'width_offsets': offsets,
        'kinds': kinds,
        'primvars': primvars,
        'topology': topology,
    }


def width_offsets(interpolation, size, points, curve_counts=None):
    """Which of a prim's points `apply` reads each authored width from, its
    widths `size` long with `interpolation` over `points` points (curves
    with `curve_counts` points each): every point for per point widths
    (`vertex`, or `varying` the same length), each curve's first point for
    per curve ones (`uniform`; a curve's points share its primitive width),
    the first point for one width (`constant`). None when the size does not
    fit the interpolation: those widths keep the first frame's."""
    if interpolation in ('vertex', 'varying') and size == points:
        return np.arange(points, dtype=np.int64)
    if interpolation == 'uniform' and curve_counts is not None:
        if size == len(curve_counts) and sum(curve_counts) == points:
            return np.cumsum([0] + list(curve_counts[:-1]), dtype=np.int64)[:size]
        return None
    if interpolation in ('constant', 'uniform') and size == 1 and points > 0:
        return np.zeros(1, np.int64)
    return None


def needs_vertex_normals(mapping) -> bool:
    """Whether any mapped prim's normals are copied per vertex."""
    return 'vertex' in mapping['normals']


def needs_widths(mapping) -> bool:
    """Whether any mapped prim (curves, points) has widths that follow."""
    return any(offsets is not None for offsets in mapping.get('width_offsets', ()))


def _vertex_run(mapping, i) -> bool:
    """Whether mapped prim `i` carries a vertex number for each of its
    points (a curve's points are its vertices: a SOP Import gives each
    curve its own copy of a point two curves share)."""
    vbounds, bounds = mapping['vbounds'], mapping['bounds']
    vcount = int(vbounds[i + 1] - vbounds[i])
    return vcount > 0 and vcount == int(bounds[i + 1] - bounds[i])


def widths_mode(mapping):
    """How `apply` reads widths for `mapping`: 'vertex' (one per vertex)
    when the static import carried vertex numbers on a prim with widths
    that follow (`stamp_vertices` stamps them where widths are on the
    vertices), 'point' when only point numbers, False with no widths to
    follow."""
    offsets = mapping.get('width_offsets', ())
    with_widths = [i for i, o in enumerate(offsets) if o is not None]
    if not with_widths:
        return False
    return 'vertex' if any(_vertex_run(mapping, i) for i in with_widths) else 'point'


def _extent(kind, points, widths):
    from pxr import Gf, UsdGeom, Vt
    if widths is not None and len(widths) != len(points):
        # one width, or per curve: the widest bounds every point (Points'
        # ComputeExtent wants one per point)
        widths = (Vt.FloatArray([float(max(widths))] * len(points))
                  if len(widths) else None)
    if kind == 'mesh' or widths is None or len(points) == 0:
        if len(points) == 0:
            return Vt.Vec3fArray()
        low, high = points.min(axis=0), points.max(axis=0)
        return Vt.Vec3fArray([
            Gf.Vec3f(*(float(v) for v in low)),
            Gf.Vec3f(*(float(v) for v in high)),
        ])
    vt_points = Vt.Vec3fArray.FromNumpy(points)
    compute = UsdGeom.Curves.ComputeExtent if kind == 'curves' else UsdGeom.Points.ComputeExtent
    return compute(vt_points, widths)


def author_frame(layer, mapping, points, normals, frame, expected_count,
                 vertex_normals=None, expected_vertices=None, widths=None,
                 widths_per_vertex=False) -> None:
    """Write every mapped prim's points, extent and (where the static
    import authored them) normals and widths at `frame` into `layer`:
    normals per point from `normals` (point N), per face-vertex from
    `vertex_normals` (vertex N), widths from `widths` (one per dive point,
    or per dive vertex with `widths_per_vertex`: `read_widths`) read at the
    prim's `width_offsets`, as `point_map` says for each prim; the extent
    takes this frame's widths. Per vertex, a prim without a vertex number
    per point keeps its widths. TopologyChanged when `points` is not
    `expected_count` long (the first frame's count), or `vertex_normals`
    (or per vertex widths) not `expected_vertices`."""
    from pxr import Sdf, Vt

    def attribute(spec, name, type_name):
        attr = layer.GetAttributeAtPath(spec.path.AppendProperty(name))
        if attr: return attr
        return Sdf.AttributeSpec(spec, name, type_name)

    def normals_sample(spec, values):
        layer.SetTimeSample(
            attribute(spec, 'normals', Sdf.ValueTypeNames.Normal3fArray).path,
            frame, Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(values)),
        )

    index = mapping['index']
    if expected_count is not None and len(points) != expected_count:
        raise TopologyChanged()
    per_vertex = vertex_normals is not None and needs_vertex_normals(mapping)
    if per_vertex and expected_vertices is not None and len(vertex_normals) != expected_vertices:
        raise TopologyChanged()
    posed = points[index]
    posed_normals = normals[index] if normals is not None else None
    posed_vertex = vertex_normals[mapping['vindex']] if per_vertex else None
    if widths is not None:
        want = expected_vertices if widths_per_vertex else len(points)
        if want is not None and len(widths) != want:
            raise TopologyChanged()
    posed_widths = None
    if widths is not None:
        posed_widths = widths[mapping['vindex'] if widths_per_vertex else index]
    width_bounds = mapping['vbounds'] if widths_per_vertex else mapping['bounds']
    width_offsets = mapping.get('width_offsets') or [None] * len(mapping['paths'])
    bounds, vbounds = mapping['bounds'], mapping['vbounds']
    with Sdf.ChangeBlock():
        for i, path in enumerate(mapping['paths']):
            start, end = int(bounds[i]), int(bounds[i + 1])
            spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
            run = posed[start:end]
            layer.SetTimeSample(
                attribute(spec, 'points', Sdf.ValueTypeNames.Point3fArray).path,
                frame, Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(run)),
            )
            extent_widths = mapping['widths'][i]
            offsets = width_offsets[i]
            if widths_per_vertex and not _vertex_run(mapping, i):
                offsets = None
            if offsets is not None and posed_widths is not None:
                run_widths = np.ascontiguousarray(
                    posed_widths[int(width_bounds[i]):int(width_bounds[i + 1])])
                layer.SetTimeSample(
                    attribute(spec, 'widths', Sdf.ValueTypeNames.FloatArray).path,
                    frame, Vt.FloatArray.FromNumpy(np.ascontiguousarray(run_widths[offsets])),
                )
                extent_widths = Vt.FloatArray.FromNumpy(run_widths)
            layer.SetTimeSample(
                attribute(spec, 'extent', Sdf.ValueTypeNames.Float3Array).path,
                frame, _extent(mapping['kinds'][i], run, extent_widths),
            )
            kind = mapping['normals'][i]
            if kind == 'point' and posed_normals is not None:
                normals_sample(spec, posed_normals[start:end])
            elif kind == 'vertex' and posed_vertex is not None:
                normals_sample(spec, posed_vertex[int(vbounds[i]):int(vbounds[i + 1])])


def remove_stamp(layer, mapping) -> None:
    """Take the stamp primvars back out, so the output has the prims and
    attributes a plain SOP Import makes."""
    from pxr import Sdf
    names = (STAMP_PRIMVAR, f'{STAMP_PRIMVAR}:indices',
             VERTEX_STAMP_PRIMVAR, f'{VERTEX_STAMP_PRIMVAR}:indices')
    with Sdf.ChangeBlock():
        for path in mapping['paths']:
            spec = layer.GetPrimAtPath(path)
            if spec is None: continue
            for name in names:
                prop = spec.properties.get(name)
                if prop is not None:
                    spec.RemoveProperty(prop)


def frozen_message(paths) -> str:
    """The warning for prims whose normals cannot follow the animation."""
    shown = ', '.join(str(path) for path in paths[:5])
    more = f' and {len(paths) - 5} more' if len(paths) > 5 else ''
    return (
        f'th::animate: the normals of {shown}{more} are not per point or per '
        'vertex (per primitive, say), so they stay as they were at the first '
        'frame while the points move. Put N on the points or vertices (a '
        'Normal SOP), or remove it.'
    )


# --- the HDA's Python nodes ------------------------------------------------


def _sops(lop_node):
    return lop_node.node(f'../{POINTS_SOPS}')


def _output_type():
    return hou.sopNodeTypeCategory().nodeTypes().get('output')


def dive_output(dive_node):
    """The SOP whose geometry is the dive's output, as Houdini picks it
    for a reference to the network (the HDA's `points_sops/dive` Object
    Merges `../../anim_dive`): the Output SOP with Output Index 0, whatever
    it is called, else the display node.

    Found by node type, not by listing the dive's children: a Python LOP
    (or an expression) that lists a network's children recooks whenever a
    node is created in it, so placing an unconnected node in the dive
    re-ran the whole import.
    """
    output_type = _output_type()
    for node in (output_type.instances() if output_type is not None else ()):
        if node.parent() != dive_node: continue
        parm = node.parm('outputidx')
        if parm is not None and parm.eval() == 0:
            return node
    display = getattr(dive_node, 'displayNode', None)
    return display() if display is not None else None


def _dive_output(lop_node):
    dive = lop_node.node(f'../{DIVE}')
    return None if dive is None else dive_output(dive)


def _placed(lop_node):
    """`points_sops/placed`: `points_sops/scene` (the dive's output,
    invoked by th::animate when the dive outputs the APEX scene itself:
    `animate_invoke`) with only the shapes that have somewhere to go
    (`placed_shapes`); what every import reads."""
    sops = _sops(lop_node)
    return None if sops is None else (sops.node(PLACED) or sops.node(SCENE))


def apply(lop_node) -> None:
    """The `apply` Python LOP: this frame's points onto the static import."""
    if _dive_output(lop_node) is None:
        return
    output = _placed(lop_node)
    if output is None:
        return
    static_node = lop_node.inputs()[0]
    key = (lop_node.sessionId(), static_node.cookCount())
    mapping = _mapping_cache.get(key)
    if mapping is None:
        for stale in [k for k in _mapping_cache if k[0] == key[0]]:
            del _mapping_cache[stale]
        mapping = _mapping_cache[key] = point_map(static_node.stage())
    sops = _sops(lop_node)
    # The first frame's count before this frame's geometry: the dive's SOPs
    # keep one frame's cook, so reading this frame first would cook the rigs
    # again for the static import.
    expected = rest_point_count(sops)
    left_out = _left_out_primvars(lop_node, sops, mapping)  # reads the first frame too
    geo = output.geometry()
    if geo is None:
        raise hou.NodeError('\n'.join(output.errors()) or f'anim_dive: {output.name()} has no geometry.')
    layer = lop_node.editableLayer()
    remove_stamp(layer, mapping)
    if len(mapping['paths']) == 0:
        return
    remove_primvars(layer, mapping['paths'], left_out)
    if mapping['frozen']:
        add_warning = getattr(lop_node, 'addWarning', None)
        if add_warning is not None:
            add_warning(frozen_message(mapping['frozen']))
    per_vertex = needs_vertex_normals(mapping)
    mode = widths_mode(mapping)
    read = read_points(geo, sops.node(CURRENT), vertex_normals=per_vertex,
                       widths=mode, missing=missing_width(static_node))
    frame = hou.frame()
    paths = list(mapping['paths'])
    said = None
    try:
        author_frame(layer, mapping, read['P'], read['N'], frame, expected,
                     read['vN'],
                     rest_vertex_count(sops) if per_vertex or mode == 'vertex' else None,
                     widths=read['W'], widths_per_vertex=mode == 'vertex')
        said = read_visibility(geo, mapping, sops.node(CURRENT))
    except TopologyChanged:
        # the full import brings this frame's visibility with it
        paths += author_full_frame(layer, _full_layer(lop_node), paths, frame)
    # The prims a changed frame made too (`said` is None there, so it never
    # indexes them): one the dive hid on some changed frames and said
    # nothing of on others was sampled on the first alone, and hidden on both.
    author_visibility(
        layer, paths, said,
        visibility_bases(_incoming_stage(lop_node), layer, paths, frame), frame)
    sample_defaults(layer, paths, frame)


def _incoming_stage(lop_node):
    """The stage coming into th::animate (`IN_stage`), which its own layer
    goes over; None without one."""
    incoming = lop_node.node('../IN_stage')
    try:
        return incoming.stage() if incoming is not None else None
    except hou.Error:
        return None


def _left_out_primvars(lop_node, sops, mapping) -> list:
    """Per mapped prim, the primvars of the static import that `apply`
    takes back out: the asset's own (`asset_primvars`) and a merge's filler
    (`filler_primvars`).

    Worked out once per static import and incoming stage, not every frame:
    the filler with the mapping, the asset's again only when the stage held
    at the shot's start (`IN_stage_start`, which does not change with the
    frame as `IN_stage` may; that one where there is no such node) has
    cooked since."""
    if not any(mapping.get('primvars', ())):
        return [set() for _ in mapping['paths']]
    filler = mapping.get('filler')
    if filler is None:
        at_start = sops.node(AT_START) if sops is not None else None
        first = at_start.geometry() if at_start is not None else None
        leaves = _packed_leaves(first) if first is not None and _has_packed(first) else None
        filler = mapping['filler'] = filler_primvars(leaves, mapping)
    incoming = lop_node.node('../IN_stage_start') or lop_node.node('../IN_stage')
    cook_count = getattr(incoming, 'cookCount', None)
    try:
        stage = incoming.stage() if incoming is not None else None
    except hou.Error:
        stage = None
    cooked = cook_count() if cook_count is not None else None
    cached = mapping.get('asset')
    if cached is None or cooked is None or cached[0] != cooked:
        cached = mapping['asset'] = (cooked, asset_primvars(stage, mapping))
    return [a | f for a, f in zip(cached[1], filler)]


def _full_layer(lop_node):
    """`full_import`'s layer at this frame: the dive's geometry imported
    whole, with its own topology."""
    full = lop_node.node('../full_import')
    try:
        full.cook()
    except hou.OperationFailed:
        pass
    layer = full.activeLayer() if not full.errors() else None
    if layer is None:
        raise hou.NodeError(
            'th::animate: the rigs changed topology since the first frame and '
            'importing this frame in full failed:\n' + '\n'.join(full.errors())
        )
    return layer


def _sample_at(layer, path, frame):
    """`layer`'s value for the attribute at `path` at `frame`: its time
    sample nearest `frame`, else its default; None when it has neither."""
    times = layer.ListTimeSamplesForPath(path)
    if times:
        nearest = min(times, key=lambda time: abs(time - frame))
        return layer.QueryTimeSample(path, nearest)
    attr = layer.GetAttributeAtPath(path)
    return attr.default if attr is not None and attr.HasInfo('default') else None


def _unindexes_nothing(target, value) -> bool:
    """Whether `value` is a block for a primvar's `:indices` (`target`). A
    SOP Import says "not indexed" that way of every primvar it does not
    index, with a blocked default. The same block as a time sample reads as
    indexed with no indices: `UsdGeomPrimvar.IsIndexed` is true,
    `ComputeFlattened` fails, and a reader gets no primvar. So it is never
    sampled: up to 1.112 `displayColor` got it on every frame, and so would
    the UVs of geometry made in the dive."""
    from pxr import Sdf
    return target.name.endswith(':indices') and isinstance(value, Sdf.ValueBlock)


#: What `apply` writes on every frame whatever the first frame had
#: (`author_frame`, `author_visibility`).
ALWAYS_AUTHORED = frozenset({'points', 'extent', 'visibility'})


def author_full_frame(layer, source, mapped_paths, frame) -> list:
    """A frame whose topology is not the first frame's: every point-based
    prim of `source` (`full_import`'s layer) written into `layer` at
    `frame`, each varying attribute as a time sample (a uniform one, which
    cannot change over time, only where `layer` has none yet); prims missing
    from the layer are made. Mapped prims `source` does not have are made
    invisible at `frame`. All in `layer`, never a layer over it: see the
    module docstring. Returns the prims made.

    Every frame of the range must read its own values. So a prim the first
    frame had takes only the attributes it had then (one sampled on a few
    frames only would be read on all of them), and those of them `source`
    lacks are blocked at `frame` (normals sized for the first frame's
    points do not fit this frame's)."""
    from pxr import Sdf
    found, made = [], []

    def visit(path):
        if path.IsPrimPath():
            spec = source.GetPrimAtPath(path)
            if spec is not None and 'points' in spec.properties:
                found.append(spec)

    source.Traverse(Sdf.Path.absoluteRootPath, visit)
    with Sdf.ChangeBlock():
        for src in found:
            path = src.path
            dest = layer.GetPrimAtPath(path)
            first_frame = None  # a prim the first frame had: only its attributes
            if dest is None:
                parent = Sdf.CreatePrimInLayer(layer, path.GetParentPath())
                dest = Sdf.PrimSpec(parent, path.name, Sdf.SpecifierDef, src.typeName)
                made.append(path)
            else:  # and what `author_frame` writes every other frame
                first_frame = set(dest.attributes.keys()) | ALWAYS_AUTHORED
            for attr in src.attributes:
                if first_frame is not None and attr.name not in first_frame:
                    # sampled on this frame only, every other frame would read it
                    continue
                value = _sample_at(source, attr.path, frame)
                target = layer.GetAttributeAtPath(attr.path)
                if target is None:
                    target = Sdf.AttributeSpec(dest, attr.name, attr.typeName, attr.variability)
                    for key in attr.ListInfoKeys():
                        if key not in ('default', 'timeSamples', 'typeName', 'variability'):
                            target.SetInfo(key, attr.GetInfo(key))
                elif attr.HasInfo('interpolation'):
                    target.SetInfo('interpolation', attr.GetInfo('interpolation'))
                if value is None:
                    continue
                if attr.variability == Sdf.VariabilityUniform:
                    if not target.HasInfo('default'):
                        target.default = value
                    continue
                if _unindexes_nothing(target, value):
                    continue
                layer.SetTimeSample(target.path, frame, value)
            # What the first frame had and this frame does not (normals,
            # widths, a primvar's indices): sized for the first frame's
            # points, so blocked here rather than sampled (`sample_defaults`).
            authored = set(src.attributes.keys())
            for attr in dest.attributes:
                if attr.name in authored or attr.variability != Sdf.VariabilityVarying:
                    continue
                if attr.name == 'visibility':  # `author_visibility` samples it
                    continue
                layer.SetTimeSample(attr.path, frame, Sdf.ValueBlock())
        imported = {src.path for src in found}
        for path in mapped_paths:
            if path in imported: continue
            spec = layer.GetPrimAtPath(path)
            if spec is None: continue
            visibility = layer.GetAttributeAtPath(path.AppendProperty('visibility')) or Sdf.AttributeSpec(
                spec, 'visibility', Sdf.ValueTypeNames.Token, Sdf.VariabilityVarying,
            )
            layer.SetTimeSample(visibility.path, frame, 'invisible')
    return made


def sample_defaults(layer, paths, frame) -> None:
    """Every varying attribute of the prims at `paths` in `layer` that has
    a default and no time sample (the static import's topology,
    displayColor, frozen normals, ...) sampled at `frame` with
    its default (visibility has a sample by now: `author_visibility`). A
    frame whose topology changed samples them
    (`author_full_frame`); once one does, every frame must, or USD reads the
    frames before it with that frame's topology (time samples hide a
    default)."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for path in paths:
            spec = layer.GetPrimAtPath(path)
            if spec is None: continue
            for attr in spec.attributes:
                if attr.variability != Sdf.VariabilityVarying: continue
                if not attr.HasInfo('default'): continue
                if layer.GetNumTimeSamplesForPath(attr.path): continue
                if _unindexes_nothing(attr, attr.default): continue
                layer.SetTimeSample(attr.path, frame, attr.default)


def rest_point_count(sops) -> int | None:
    """The dive's point count at the first frame, as `points_sops` stamped
    it (before dropping the prims without points)."""
    rest = sops.node(REST)
    rest_geo = None if rest is None else rest.geometry()
    if rest_geo is None or rest_geo.findGlobalAttrib(COUNT) is None:
        return None
    return rest_geo.attribValue(COUNT)


def rest_vertex_count(sops) -> int | None:
    """The dive's vertex count at the first frame, as `stamp_vertices`
    stamped it; None when it stamped none (no vertex N)."""
    rest = sops.node(REST)
    rest_geo = None if rest is None else rest.geometry()
    if rest_geo is None or rest_geo.findGlobalAttrib(VERTEX_COUNT) is None:
        return None
    return rest_geo.attribValue(VERTEX_COUNT)


def stamp_vertices(geo) -> bool:
    """Stamp each vertex's number (`__th_vtx`, in the order `read_points`
    reads vertex N) and the vertex count on the static import's geometry
    when it has vertex N or vertex `width`, as `points_sops/stamp` stamps
    the points: the static import carries the numbers to the stage
    (faceVarying on a mesh, one per point on a curve, whose points are its
    vertices), so `apply` copies each frame's vertex N and vertex widths in
    the SOP Import's own order: a joint two curves share keeps each curve's
    width. Geometry with neither is left as it is. True when stamped."""
    if geo.findVertexAttrib('N') is None and _scalar_attrib(geo, 'vertex', 'width') is None:
        return False
    count = geo.intrinsicValue('vertexcount')
    if geo.findVertexAttrib(VERTEX_STAMP) is None:
        geo.addAttrib(hou.attribType.Vertex, VERTEX_STAMP, 0)
    geo.setVertexIntAttribValuesFromString(
        VERTEX_STAMP, np.arange(count, dtype=np.int32).tobytes(),
    )
    if geo.findGlobalAttrib(VERTEX_COUNT) is None:
        geo.addAttrib(hou.attribType.Global, VERTEX_COUNT, 0)
    geo.setGlobalAttribValue(VERTEX_COUNT, count)
    return True


def topology_changed(lop_node) -> int:
    """0: `apply` imports a frame whose topology changed itself
    (`author_full_frame`), so `topology_switch` stays on it. Was the
    switch's expression, sending such frames to `full_import`'s own layer,
    which a USD ROP saving a range dropped; kept for th::animate nodes whose
    contents were unlocked and still call it."""
    return 0


_MOVING = ('P', 'N')
_rest_cache = {}


def _all_attribs(geo):
    for attribs in (geo.pointAttribs(), geo.primAttribs(), geo.vertexAttribs(), geo.globalAttribs()):
        yield from attribs


def imported_patterns(geo, attribs_pattern: str) -> list[str]:
    """The attribute names (fnmatch patterns) a SOP Import with Attributes
    `attribs_pattern` reads from `geo`: those, the path attributes, the
    usdconfig* attributes and the attributes they name (constant primvars,
    subset groups, ...)."""
    patterns = attribs_pattern.replace(',', ' ').split() + ['path', 'name', 'usd*']
    for attrib in _all_attribs(geo):
        if not attrib.name().startswith('usdconfig'): continue
        if attrib.dataType() != hou.attribData.String: continue
        if attrib.type() == hou.attribType.Global:
            values = [geo.attribValue(attrib.name())]
        elif attrib.type() == hou.attribType.Prim:
            values = set(geo.primStringAttribValues(attrib.name()))
        else:
            continue
        for value in values:
            if isinstance(value, str):
                patterns.extend(value.replace(',', ' ').split())
    return patterns


# Geometry with at most this many prims is compared by its attributes'
# values, not their data ids: th::animate rebuilds such small leaves itself
# every cook (a skeleton, `animate_invoke.skeleton_paths`), which gives them
# new data ids though nothing changed.
SMALL_PRIMS = 2000

def _enum_name(value) -> str:
    name = getattr(value, 'name', None)
    return name() if callable(name) else str(value)


def attrib_value_key(geo, attrib):
    """A hashable stand-in for `attrib`'s values on `geo`."""
    kind = _enum_name(attrib.type())
    if kind == 'Global':
        return repr(geo.attribValue(attrib.name()))
    prefix = {'Point': 'point', 'Prim': 'prim', 'Vertex': 'vertex'}[kind]
    data = _enum_name(attrib.dataType())
    if data == 'Float':
        return hash(getattr(geo, f'{prefix}FloatAttribValuesAsString')(attrib.name()))
    if data == 'Int':
        return hash(getattr(geo, f'{prefix}IntAttribValuesAsString')(attrib.name()))
    if data == 'String':
        return hash(tuple(getattr(geo, f'{prefix}StringAttribValues')(attrib.name())))
    return str(attrib.dataId())


def _geometry_signature(geo, attribs_pattern: str) -> tuple:
    from fnmatch import fnmatchcase
    counts = tuple(geo.intrinsicValue(name) for name in ('pointcount', 'primitivecount', 'vertexcount'))
    patterns = imported_patterns(geo, attribs_pattern)
    by_value = counts[1] <= SMALL_PRIMS
    ids = tuple(sorted(
        (
            attrib.type().name(), attrib.name(),
            str(attrib_value_key(geo, attrib) if by_value else attrib.dataId()),
        )
        for attrib in _all_attribs(geo)
        if attrib.name() not in _MOVING
        and any(fnmatchcase(attrib.name(), pattern) for pattern in patterns)
    ))
    groups = tuple(sorted(group.name() for group in geo.primGroups()))
    return counts, ids, groups


def rest_signature(geo, attribs_pattern: str) -> tuple:
    """What the static import depends on in the dive's geometry: its
    topology, its prim groups, and the attributes the SOP Import reads
    (`imported_patterns`) but P and N, by data id. Moving the rigs, or a
    key edit, changes P and N only there (the Scene Invoke keeps the other
    attributes' data ids); a new rig, or a SOP after the Scene Invoke
    changing an imported attribute, changes it."""
    if not _has_packed(geo):
        return ('unpacked', _geometry_signature(geo, attribs_pattern))
    leaves = _packed_leaves(geo)
    if leaves is None:
        return ('packed', _geometry_signature(geo, attribs_pattern), object())  # never equal: rebuild
    return ('leaves', tuple(_geometry_signature(leaf, attribs_pattern) for leaf in leaves))


def rest_gate(sop_node) -> None:
    """The `rest_gate` Python SOP: the static import's geometry, rebuilt
    (from `rest_build`) only when `rest_signature` of the dive's first
    frame changes. Otherwise the last build, as is: its data ids do not
    change, so the SOP Import after it has nothing to convert again, and a
    key edit does not re-import the characters."""
    geo = sop_node.geometry()
    static_import = sop_node.node('../../static_import')
    attribs_pattern = static_import.evalParm('attribs') if static_import is not None else '*'
    signature = rest_signature(geo, attribs_pattern)
    key = sop_node.sessionId()
    cached = _rest_cache.get(key)
    if cached is None or cached[0] != signature:
        build = sop_node.node('../rest_build')
        rest = build.geometry()
        if rest is None:
            raise hou.NodeError('\n'.join(build.errors()) or 'th::animate: no rest geometry.')
        rest = rest.freeze()
        leaves = _packed_leaves(geo) if _has_packed(geo) else None
        normalize_widths(rest, leaves, missing_width(static_import))
        stamp_vertices(rest)
        cached = _rest_cache[key] = (signature, rest)
    geo.clear()
    geo.merge(cached[1])


def current_widths(sop_node) -> None:
    """The `current_widths` Python SOP before CURRENT: the dive's frame
    unpacked (what `full_import` imports on a frame whose topology changed,
    and what `read_points` reads when the shapes cannot be read where they
    are) with each shape's own widths where the merge lost them
    (`normalize_widths`), as the static import has them."""
    geo = sop_node.geometry()
    placed = sop_node.node(f'../{PLACED}') or sop_node.node(f'../{SCENE}')
    packed = placed.geometry() if placed is not None else None
    if packed is None or not _has_packed(packed):
        return
    leaves = _packed_leaves(packed)
    if leaves:
        normalize_widths(geo, leaves, missing_width(sop_node.node('../../static_import')))


def remainder_shapes(sop_node) -> None:
    """The `remainder_shapes` Python SOP: the dive's geometry with only the
    packed shapes that hold something besides points (a camera, say), so
    the per-frame remainder unpacks those and not the characters. Geometry
    that is not packed passes through (a Blast after this drops its
    point-based prims)."""
    geo = sop_node.geometry()
    if not _has_packed(geo):
        # Unpacked: nothing to keep, most frames. Else the Blast after
        # this drops the point-based prims.
        if non_point_prim_count(geo) == 0:
            geo.clear()
        return
    doomed = []
    for folder in geo.prims():
        if folder.type() != hou.primType.PackedGeometry:
            continue
        inner = folder.getEmbeddedGeometry()
        if inner is None:
            continue
        inner = inner.freeze()
        inner_doomed = []
        for shape in inner.prims():
            shape_geo = shape.getEmbeddedGeometry() if shape.type() == hou.primType.PackedGeometry else None
            if shape_geo is None:
                continue
            if non_point_prim_count(shape_geo) == 0:
                inner_doomed.append(shape)
        if len(inner_doomed) == len(inner.prims()):
            doomed.append(folder)
            continue
        if inner_doomed:
            inner.deletePrims(inner_doomed)
            folder.setEmbeddedGeometry(inner)
    if doomed:
        geo.deletePrims(doomed)


# --- shapes with somewhere to go -------------------------------------------


def shapes_to_leave_out(shapes) -> list[int]:
    """Which of one character's shapes the imports leave out.

    `shapes` is `(name, has_path, points_only)` per shape: whether any of
    its prims has a `path`, and whether all of them are made of points
    (meshes, curves; not cameras or other packed prims). Left out are the
    path-less, points-only shapes of a character that has a shape with a
    path: helper geometry (viz, face boards) a SOP Import would put at
    `/Geometry`. A character with no path anywhere keeps everything.
    """
    if not any(has_path for _, has_path, _ in shapes):
        return []
    return [i for i, (_, has_path, points_only) in enumerate(shapes)
            if not has_path and points_only]


def unplaced_message(left_out: dict, pathless: list, unpicked: dict | None = None,
                     pattern: str = '') -> str:
    """The warning for `placed_shapes`: `left_out` is character -> the
    shapes left out, `pathless` the characters with no path at all,
    `unpicked` character -> the shapes th::animate's Shapes (`pattern`)
    does not pick, which a Scene Invoke in the dive output anyway."""
    parts = []
    if unpicked:
        named = '; '.join(f'{character}: {", ".join(shapes)}'
                          for character, shapes in sorted(unpicked.items()))
        parts.append(
            f"th::animate left out rig shapes its Shapes ({pattern or 'empty'}) "
            f'does not pick ({named}): the Scene Invoke in anim_dive outputs '
            "them. Set its Shapes to the same, or delete it and let "
            'th::animate invoke the scene.')
    if left_out:
        named = '; '.join(f'{character}: {", ".join(shapes)}'
                          for character, shapes in sorted(left_out.items()))
        parts.append(
            f'th::animate left out rig shapes with no path ({named}): they '
            'would land at /Geometry, outside the characters. The Scene '
            "Invoke's Shapes pattern picks them; set it to Base to output "
            'only the characters\' geometry.')
    if pathless:
        parts.append(
            'th::animate: no shape of ' + ', '.join(sorted(pathless))
            + ' has a path attribute, so its geometry lands at /Geometry, '
            'not under /CHAR. Is it a rig built for the pipeline?')
    return '\n'.join(parts)


def loose_shapes(paths, names, points_only) -> list[str]:
    """The shapes of unpacked dive output that would land at /Geometry:
    the `name` (or '' when unnamed) of every prim with no `path` (`paths`
    and `names` per prim; `names` None without the attribute), once each,
    in prim order, if `points_only(prim index)` (asked once per name) says
    the prim is made of points (cameras and packed prims go elsewhere)."""
    seen, loose = set(), []
    for index, path in enumerate(paths):
        if path:
            continue
        name = names[index] if names is not None else ''
        if name in seen:
            continue
        seen.add(name)
        if points_only(index):
            loose.append(name)
    return loose


def loose_message(shapes) -> str:
    """The warning for `loose_shapes`: '' when there are none."""
    if not shapes:
        return ''
    named = [shape for shape in shapes if shape]
    shown = ', '.join(named[:5]) + (f' and {len(named) - 5} more' if len(named) > 5 else '')
    if len(named) < len(shapes):
        shown = (shown + ' and ' if shown else '') + 'geometry with no name'
    one = len(shapes) == 1
    return (
        f'th::animate: {shown} in anim_dive {"has" if one else "have"} no path '
        f'attribute, so {"it lands" if one else "they land"} at /Geometry, '
        'outside the characters, and {} published with the animation. Give '
        '{} a path under the character, or leave {} out of the dive\'s output.'
    ).format(*(('is', 'it', 'it') if one else ('are', 'them', 'them')))


def _loose_in(geo) -> list[str]:
    """`loose_shapes` of unpacked geometry `geo`."""
    count = geo.intrinsicValue('primitivecount')
    if count == 0:
        return []
    paths = (geo.primStringAttribValues('path')
             if geo.findPrimAttrib('path') is not None else ('',) * count)
    names = (geo.primStringAttribValues('name')
             if geo.findPrimAttrib('name') is not None else None)
    kinds = tuple(getattr(hou, kind) for kind in ('Face', 'Surface')
                  if isinstance(getattr(hou, kind, None), type))
    prims = geo.prims()
    return loose_shapes(paths, names,
                        lambda index: (isinstance(prims[index], kinds) if kinds
                                       else prims[index].type() == hou.primType.Polygon))


def _warn(sop_node, animate_node, message) -> None:
    """`message` as a warning on `sop_node`, and on the status bar once
    per change for th::animate `animate_node`."""
    if message:
        add_warning = getattr(sop_node, 'addWarning', None)
        if add_warning is not None:
            add_warning(message)
    key = animate_node.path() if animate_node is not None else sop_node.path()
    if _announced.get(key) != message:
        _announced[key] = message
        if message:
            announce(message)


def _has_path(geo) -> bool:
    attrib = geo.findPrimAttrib('path')
    if attrib is None:
        return False
    try:
        return any(attrib.strings())
    except hou.OperationFailed:
        return False


def placed_shapes(sop_node) -> None:
    """The `placed` Python SOP after `scene`: the dive's packed folders
    without the shapes th::animate's Shapes does not pick
    (`animate_invoke.unpicked_shapes`: a Scene Invoke in the dive can output
    any shape, the node's own outputs only those) and then without the
    shapes `shapes_to_leave_out` names, a warning on this node
    naming them, and one status-bar message per change. Geometry that is
    not packed folders (SOPs after the Scene Invoke) passes through as it
    is, with a warning naming its shapes with no path (`loose_shapes`)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    geo = sop_node.geometry()
    animate_node = sop_node.parent().parent() if sop_node.parent() else None
    if not _has_packed(geo):
        _warn(sop_node, animate_node, loose_message(_loose_in(geo)))
        return
    pattern = animate_invoke.shapes_pattern(animate_node)
    left_out, pathless, unpicked = {}, [], {}
    for folder in geo.prims():
        if folder.type() != hou.primType.PackedGeometry:
            continue
        inner = folder.getEmbeddedGeometry()
        if inner is None:
            continue
        shapes, entries = [], []
        for shape in inner.prims():
            shape_geo = (shape.getEmbeddedGeometry()
                         if shape.type() == hou.primType.PackedGeometry else None)
            if shape_geo is None:
                continue
            shapes.append((_name(shape), _has_path(shape_geo),
                           non_point_prim_count(shape_geo) == 0))
            entries.append(shape.number())
        character = _name(folder) or f'folder {folder.number()}'
        # What Shapes does not pick goes first; the helper rule reads the rest.
        not_picked = animate_invoke.unpicked_shapes([s[0] for s in shapes], pattern)
        rest = [i for i in range(len(shapes)) if i not in not_picked]
        rest_shapes = [shapes[i] for i in rest]
        helpers = []
        if rest_shapes and not any(has_path for _, has_path, _ in rest_shapes):
            if any(points_only for _, _, points_only in rest_shapes):
                pathless.append(character)
        else:
            helpers = [rest[i] for i in shapes_to_leave_out(rest_shapes)]
        if not not_picked and not helpers:
            continue
        inner = inner.freeze()
        doomed = [inner.prim(entries[i]) for i in sorted(not_picked + helpers)]
        # Two folders can share a name: the warning names what both lost.
        for drop, into in ((not_picked, unpicked), (helpers, left_out)):
            if drop:
                into.setdefault(character, []).extend(
                    shapes[i][0] or f'shape {entries[i]}' for i in drop)
        inner.deletePrims(doomed)
        folder.setEmbeddedGeometry(inner)
    _warn(sop_node, animate_node, unplaced_message(left_out, pathless, unpicked, pattern or ''))


def _name(prim) -> str:
    attrib = prim.geometry().findPrimAttrib('name')
    return prim.attribValue('name') if attrib is not None else ''


# --- the dive's Scene Invoke ------------------------------------------------


def invoke_feeding_output(dive_node):
    """The Scene Invoke that is the dive's output (`dive_output`), or is
    wired straight into it; None when there is none, or other SOPs sit in
    between."""
    output = dive_output(dive_node)
    if output is None: return None
    if output.type().name() == 'output':
        output = output.input(0)
        if output is None: return None
    if output.type().name().startswith('apex::sceneinvoke'): return output
    return None


def _outputs_unpacked(invoke_node) -> bool:
    parm = invoke_node.parm('outputmode')
    return parm is not None and parm.evalAsString() == 'unpacked'


# Set on a Scene Invoke th::animate switched to Packed Folders, so it is
# switched once: an artist who sets it back to Unpacked keeps Unpacked.
SWITCHED_KEY = 'th_animate_packed'
# What the comment th::animate leaves says; a scene switched up to 1.111
# carries only the comment.
SWITCHED_NOTE = 'Packed Folders by th::animate'


def switched_before(invoke_node) -> bool:
    """True when th::animate switched this Scene Invoke to Packed Folders
    once already (its user data, or the comment it left before that)."""
    try:
        if invoke_node.userData(SWITCHED_KEY):
            return True
    except (AttributeError, hou.Error):
        pass
    return SWITCHED_NOTE in (invoke_node.comment() or '')


def _within(node, parent) -> bool:
    return node.path().startswith(parent.path() + '/')


# The nodes that read a SOP network's geometry by path: (category, type,
# the prefix of the parms naming it).
_PATH_READER_TYPES = (
    ('sopNodeTypeCategory', 'object_merge', 'objpath'),
    ('lopNodeTypeCategory', 'sopimport', 'soppath'),
)


def _path_readers(targets) -> list:
    """(reader, target) for every Object Merge SOP and SOP Import LOP in
    the session whose path parms name one of `targets`."""
    found = []
    for category, type_name, prefix in _PATH_READER_TYPES:
        node_type = hou.nodeType(getattr(hou, category)(), type_name)
        if node_type is None:
            continue
        for node in node_type.instances():
            for parm in node.parms():
                if not parm.name().startswith(prefix):
                    continue
                try:
                    named = parm.evalAsNodes()
                except hou.Error:
                    continue
                found += [(node, target) for target in targets if target in named]
    return found


def other_readers(animate_node, invoke_node) -> list[str]:
    """The nodes other than th::animate that read the Scene Invoke's
    geometry, by path: SOPs wired after it beside the dive's output (a
    deformer branch, a second Output SOP), nodes referencing it (an Object
    Merge in a shot sculpt network, or in the dive), and nodes outside
    th::animate referencing the dive or its output. th::animate's own
    nodes (`points_sops` merging the dive) do not count. Packed Folders
    would change what every one of them gets."""
    dive_node = animate_node.node(DIVE)
    if dive_node is None: return []
    output = dive_output(dive_node)
    targets = [invoke_node] + [n for n in (output, dive_node)
                               if n is not None and n != invoke_node]
    references = [(n, t) for t in targets for n in t.dependents()]
    # dependents() knows a reference only once the reader has cooked; a
    # scene that has just loaded has cooked nothing.
    references += _path_readers(targets)
    readers = [n for n in invoke_node.outputs() if n != output]
    for node, target in references:
        if target == invoke_node:
            # In the dive (an Object Merge there) it counts; th::animate's
            # own nodes do not.
            if _within(node, animate_node) and not _within(node, dive_node):
                continue
        elif _within(node, animate_node):
            continue
        readers.append(node)
    paths = []
    for node in readers:
        if node.path() not in paths:
            paths.append(node.path())
    return paths


def optimize_dive(animate_node) -> str | None:
    """Switch the Scene Invoke feeding anim_dive's output straight to
    packed folders, when it outputs unpacked geometry. The rigs then reach
    `apply` without being unpacked every frame; th::animate's output does
    not change. The Scene Invoke gets a comment saying so. Returns the
    message for the artist, or None when there was nothing to do.

    Done once per Scene Invoke (`switched_before`), and never when anything
    else reads its geometry (`other_readers`): a deformer or a shot sculpt
    working on the unpacked rigs would get packed folders. Up to 1.111 it
    ran on every load whatever read the Scene Invoke, so setting it back to
    Unpacked did not last past the next open (Remy, tumblepipe__bugs
    2026-11-17)."""
    dive_node = animate_node.node(DIVE)
    if dive_node is None: return None
    invoke_node = invoke_feeding_output(dive_node)
    if invoke_node is None or not _outputs_unpacked(invoke_node): return None
    if switched_before(invoke_node): return None
    if other_readers(animate_node, invoke_node): return None
    invoke_node.parm('outputmode').set('packedfolders')
    stamp = time.strftime('%Y-%m-%d')
    invoke_node.setUserData(SWITCHED_KEY, stamp)
    note = (
        f'Output set to {SWITCHED_NOTE} ({stamp}) for faster '
        'playback; the animation output is unchanged. th::animate unpacks '
        'it itself. SOPs added after this node get packed folders: put an '
        'Unpack (twice) before them, or set Output back to Unpacked '
        '(th::animate does not switch it again).'
    )
    comment = invoke_node.comment()
    invoke_node.setComment(note if not comment else f'{comment}\n{note}')
    invoke_node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    return (
        f'th::animate sped up {animate_node.path()}: its Scene Invoke '
        f'({invoke_node.name()}) now outputs Packed Folders. The animation '
        'is the same; save the scene to keep the change.'
    )


def announce(message: str) -> None:
    print(message)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(message, severity=hou.severityType.Message)


def on_loaded(animate_node) -> None:
    """OnLoaded: speed up a dive from before packed folders, once the
    scene has loaded (`after_load`): what else reads the Scene Invoke (an
    Object Merge in /obj, say) may load after th::animate does."""
    from tumblepipe.pipe.houdini import after_load

    def optimize():
        try:
            # A node deleted since raises hou.ObjectWasDeleted, a hou.Error.
            message = optimize_dive(animate_node)
        except hou.Error:
            return
        if message is not None:
            announce(message)

    after_load.run(optimize)
