"""The vocabulary th::animate's modules share about the dive's geometry:
the point, vertex and primitive stamps the static import carries, the
shapes inside its packed folders, and Houdini enum names.

Split from animate_points (which documents the copy as a whole); kept
apart so animate_widths, animate_visibility and animate_primvars need
not import it."""

import hou

STAMP = '__th_pt'
COUNT = '__th_count'
STAMP_PRIMVAR = f'primvars:{STAMP}'
# Each vertex's number, stamped only when the first frame has vertex N.
VERTEX_STAMP = '__th_vtx'
VERTEX_COUNT = '__th_vcount'
VERTEX_STAMP_PRIMVAR = f'primvars:{VERTEX_STAMP}'
# Each primitive's number, so `apply` knows which shape a face or a curve is of.
PRIM_STAMP = '__th_prim'
PRIM_COUNT = '__th_pcount'
PRIM_STAMP_PRIMVAR = f'primvars:{PRIM_STAMP}'


def packed_leaves(geo):
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


def has_packed(geo) -> bool:
    return geo.countPrimType(hou.primType.PackedGeometry) > 0


def enum_name(value) -> str:
    name = getattr(value, 'name', None)
    return name() if callable(name) else str(value)
