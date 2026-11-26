"""The USD `widths` th::animate copies each frame for curves and point
clouds: where a SOP Import takes them from, reading them, and the
conflicts and missing widths it settles. Split from animate_points."""

import hou
import numpy as np

from tumblepipe.pipe.houdini.lops.animate_geometry import enum_name


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

FIND_ATTRIB = {
    'point': 'findPointAttrib', 'vertex': 'findVertexAttrib',
    'prim': 'findPrimAttrib', 'detail': 'findGlobalAttrib',
}
_CLASS = {'detail': 0, 'prim': 1, 'point': 2, 'vertex': 3}


def scalar_attrib(geo, kind, name):
    """`geo`'s one-float attribute `name` of class `kind`, else None."""
    attrib = getattr(geo, FIND_ATTRIB[kind])(name)
    if attrib is None or attrib.size() != 1:
        return None
    return attrib if enum_name(attrib.dataType()) == 'Float' else None


def width_source(geos):
    """(class, name, default) of the attribute a SOP Import of `geos`
    merged into one (as two Unpack SOPs merge a dive's shapes) reads widths
    from, `default` what the merge gives a shape without it (the attribute's
    default where it first appears); None when none of them has one."""
    for kind, name in WIDTH_SOURCES:
        for geo in geos:
            attrib = scalar_attrib(geo, kind, name)
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
    attrib = scalar_attrib(geo, kind, name)
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
        attrib = getattr(merged, FIND_ATTRIB[kind])('width')
        if attrib is not None and (kind != to or attrib.size() != 1
                                   or enum_name(attrib.dataType()) != 'Float'):
            attrib.destroy()
    if getattr(merged, FIND_ATTRIB[to])('width') is None:
        merged.addAttrib(getattr(hou.attribType, 'Vertex' if per_vertex else 'Point'),
                         'width', 0.0)
    getattr(merged, f'set{to.capitalize()}FloatAttribValuesFromString')(
        'width', np.ascontiguousarray(values, dtype=np.float32).tobytes())
    return True


def missing_width(import_node) -> float:
    """The Set Missing Widths of `import_node` (the static import)."""
    parm = import_node.parm('setmissingwidths') if import_node is not None else None
    return float(parm.eval()) if parm is not None else MISSING_WIDTH
