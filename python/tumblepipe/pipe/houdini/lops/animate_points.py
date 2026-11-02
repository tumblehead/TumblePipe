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

The dive's Scene Invoke outputs packed folders, so its shapes reach here
without the unpack: `read_points` reads each packed shape's points where
they are. A dive whose Scene Invoke still outputs unpacked geometry
straight into the dive's output is switched to packed folders when the
scene loads (`optimize_dive`), with a comment on the Scene Invoke and a
message, so the change is never silent. A dive with SOPs after its Scene
Invoke (TumbleRig's Camera Lattice, say) is left as it is: those SOPs keep
their unpacked geometry, and this reads what they output.

Which point lands where is not worked out here: the static import carries
each point's number (`__th_pt`, stamped in `points_sops`) as a primvar, so
the SOP Import's own point order is read back from the stage, then the
primvar is removed. Prims with no points (cameras, lights, packed prims)
are imported every frame, by `remainder_import`. If a frame's point count
is not the first frame's, the rigs changed topology, and `full_import`
imports that frame the slow way instead.
"""

import time

import hou
import numpy as np

STAMP = '__th_pt'
COUNT = '__th_count'
STAMP_PRIMVAR = f'primvars:{STAMP}'
POINTS_SOPS = 'points_sops'
REST = 'REST'
CURRENT = 'CURRENT'
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


def read_points(geo, unpacked_node=None) -> dict:
    """{'P': (n, 3) float32, 'N': (n, 3) or None} for `geo` as two Unpack
    SOPs would lay it out. Packed shapes are read where they are; anything
    else is read from `unpacked_node`'s cook (points_sops/CURRENT)."""
    if not _has_packed(geo):
        return {'P': _float3(geo, 'P'), 'N': _float3(geo, 'N')}
    leaves = _packed_leaves(geo)
    if leaves is None:
        if unpacked_node is None:
            raise hou.NodeError('th::animate: no unpacked copy of the dive to read.')
        unpacked = unpacked_node.geometry()
        return {'P': _float3(unpacked, 'P'), 'N': _float3(unpacked, 'N')}
    points = [_float3(leaf, 'P') for leaf in leaves]
    has_normals = any(leaf.findPointAttrib('N') is not None for leaf in leaves)
    normals = None
    if has_normals:
        normals = [
            n if n is not None else np.zeros((len(p), 3), np.float32)
            for leaf, p in zip(leaves, points)
            for n in [_float3(leaf, 'N')]
        ]
    return {
        'P': np.concatenate(points) if points else np.zeros((0, 3), np.float32),
        'N': np.concatenate(normals) if normals else None,
    }


def point_count(geo) -> int:
    """How many points `read_points` would return for `geo`."""
    if not _has_packed(geo):
        return geo.intrinsicValue('pointcount')
    leaves = _packed_leaves(geo)
    if leaves is None:
        return -1
    return sum(leaf.intrinsicValue('pointcount') for leaf in leaves)


# --- the point map ---------------------------------------------------------


def point_map(stage) -> dict:
    """What the static import made, for `apply`: every point-based prim
    carrying the stamp, its points' numbers in the dive's geometry, and
    which of points/normals it authors.

    {'paths': [Sdf.Path], 'index': int64 array (all prims' point numbers,
    concatenated), 'bounds': start of each prim's run (len(paths) + 1),
    'normals': [bool], 'widths': [VtArray or None], 'kinds': [str]}"""
    from pxr import UsdGeom
    paths, runs, normals, widths, kinds = [], [], [], [], []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.PointBased): continue
        stamp = prim.GetAttribute(STAMP_PRIMVAR)
        if not stamp: continue
        numbers = stamp.Get()
        if numbers is None: continue
        indices = prim.GetAttribute(f'{STAMP_PRIMVAR}:indices')
        numbers = np.asarray(numbers, dtype=np.int64)
        if indices and indices.Get() is not None:
            numbers = numbers[np.asarray(indices.Get(), dtype=np.int64)]
        point_based = UsdGeom.PointBased(prim)
        paths.append(prim.GetPath())
        runs.append(numbers)
        normals.append(point_based.GetNormalsAttr().HasAuthoredValue())
        if prim.IsA(UsdGeom.Mesh):
            kinds.append('mesh')
            widths.append(None)
        else:
            kinds.append('curves' if prim.IsA(UsdGeom.Curves) else 'points')
            width_attr = prim.GetAttribute('widths')
            widths.append(width_attr.Get() if width_attr else None)
    index = np.concatenate(runs) if runs else np.zeros(0, np.int64)
    return {
        'paths': paths,
        'index': index,
        'bounds': np.cumsum([0] + [len(run) for run in runs]),
        'normals': normals,
        'widths': widths,
        'kinds': kinds,
    }


def _extent(kind, points, widths):
    from pxr import Gf, UsdGeom, Vt
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


def author_frame(layer, mapping, points, normals, frame, expected_count) -> None:
    """Write every mapped prim's points, extent and (where the static
    import authored them) normals at `frame` into `layer`. TopologyChanged
    when `points` is not `expected_count` long (the first frame's count)."""
    from pxr import Sdf, Vt

    def attribute(spec, name, type_name):
        attr = layer.GetAttributeAtPath(spec.path.AppendProperty(name))
        if attr: return attr
        return Sdf.AttributeSpec(spec, name, type_name)

    index = mapping['index']
    if expected_count is not None and len(points) != expected_count:
        raise TopologyChanged()
    posed = points[index]
    posed_normals = normals[index] if normals is not None else None
    bounds = mapping['bounds']
    with Sdf.ChangeBlock():
        for i, path in enumerate(mapping['paths']):
            start, end = int(bounds[i]), int(bounds[i + 1])
            spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
            run = posed[start:end]
            layer.SetTimeSample(
                attribute(spec, 'points', Sdf.ValueTypeNames.Point3fArray).path,
                frame, Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(run)),
            )
            layer.SetTimeSample(
                attribute(spec, 'extent', Sdf.ValueTypeNames.Float3Array).path,
                frame, _extent(mapping['kinds'][i], run, mapping['widths'][i]),
            )
            if posed_normals is not None and mapping['normals'][i]:
                layer.SetTimeSample(
                    attribute(spec, 'normals', Sdf.ValueTypeNames.Normal3fArray).path,
                    frame, Vt.Vec3fArray.FromNumpy(
                        np.ascontiguousarray(posed_normals[start:end])
                    ),
                )


def remove_stamp(layer, mapping) -> None:
    """Take the stamp primvar back out, so the output has the prims and
    attributes a plain SOP Import makes."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for path in mapping['paths']:
            spec = layer.GetPrimAtPath(path)
            if spec is None: continue
            for name in (STAMP_PRIMVAR, f'{STAMP_PRIMVAR}:indices'):
                prop = spec.properties.get(name)
                if prop is not None:
                    spec.RemoveProperty(prop)


# --- the HDA's Python nodes ------------------------------------------------


def _sops(lop_node):
    return lop_node.node(f'../{POINTS_SOPS}')


def _output_type():
    return hou.sopNodeTypeCategory().nodeTypes().get('output')


def dive_output(dive_node):
    """The SOP whose geometry is the dive's output, as Houdini picks it
    for a reference to the network (the HDA's `points_sops/scene` Object
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


def apply(lop_node) -> None:
    """The `apply` Python LOP: this frame's points onto the static import."""
    output = _dive_output(lop_node)
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
    expected = rest_point_count(sops)  # before this frame: see topology_changed
    geo = output.geometry()
    if geo is None:
        raise hou.NodeError('\n'.join(output.errors()) or f'anim_dive: {output.name()} has no geometry.')
    layer = lop_node.editableLayer()
    remove_stamp(layer, mapping)
    if len(mapping['paths']) == 0:
        return
    read = read_points(geo, sops.node(CURRENT))
    try:
        author_frame(layer, mapping, read['P'], read['N'], hou.frame(), expected)
    except TopologyChanged:
        raise hou.NodeError(
            'th::animate: the rigs output a different number of points than '
            'at the first frame; this frame is imported in full instead.'
        )


def rest_point_count(sops) -> int | None:
    """The dive's point count at the first frame, as `points_sops` stamped
    it (before dropping the prims without points)."""
    rest = sops.node(REST)
    rest_geo = None if rest is None else rest.geometry()
    if rest_geo is None or rest_geo.findGlobalAttrib(COUNT) is None:
        return None
    return rest_geo.attribValue(COUNT)


def topology_changed(lop_node) -> int:
    """The `topology_switch` expression: 1 when this frame's point count is
    not the first frame's (the full, per-frame import then runs)."""
    output = _dive_output(lop_node)
    sops = _sops(lop_node)
    if output is None or sops is None:
        return 0
    # The first frame before this one: the dive's SOPs keep one frame's
    # cook, so reading this frame first would cook the rigs once for the
    # count, again for the static import, and again for `apply`.
    expected = rest_point_count(sops)
    geo = output.geometry()
    if geo is None or expected is None:
        return 0
    count = point_count(geo)
    if count < 0:
        sops_current = sops.node(CURRENT)
        count = sops_current.geometry().intrinsicValue('pointcount')
    return int(count != expected)


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


def _geometry_signature(geo, attribs_pattern: str) -> tuple:
    from fnmatch import fnmatchcase
    counts = tuple(geo.intrinsicValue(name) for name in ('pointcount', 'primitivecount', 'vertexcount'))
    patterns = imported_patterns(geo, attribs_pattern)
    ids = tuple(sorted(
        (attrib.type().name(), attrib.name(), str(attrib.dataId()))
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
        cached = _rest_cache[key] = (signature, rest.freeze())
    geo.clear()
    geo.merge(cached[1])


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


def optimize_dive(animate_node) -> str | None:
    """Switch the Scene Invoke feeding anim_dive's output straight to
    packed folders, when it outputs unpacked geometry. The rigs then reach
    `apply` without being unpacked every frame; the node's output does not
    change. The Scene Invoke gets a comment saying so. Returns the message
    for the artist, or None when there was nothing to do."""
    dive_node = animate_node.node(DIVE)
    if dive_node is None: return None
    invoke_node = invoke_feeding_output(dive_node)
    if invoke_node is None or not _outputs_unpacked(invoke_node): return None
    invoke_node.parm('outputmode').set('packedfolders')
    stamp = time.strftime('%Y-%m-%d')
    note = (
        f'Output set to Packed Folders by th::animate ({stamp}) for faster '
        'playback; the animation output is unchanged. th::animate unpacks '
        'it itself. SOPs added after this node get packed folders: put an '
        'Unpack (twice) before them, or set Output back to Unpacked.'
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
    """OnLoaded: speed up a dive from before packed folders."""
    try:
        message = optimize_dive(animate_node)
    except hou.Error:
        return
    if message is not None:
        announce(message)
