"""Rig publish validation: checks on the APEX character an Export Rig writes.

A rig is published as SOP geometry (the packed character stream:
``Base.rig`` and its ``*.shp`` shapes), not as a USD layer, so these checks
take the stream rather than a stage root. They are split in two:

- pure checks on plain values (``check_shape_normals``, ``check_viz_off``),
  testable without Houdini;
- ``read_character(geo)`` / ``validate_rig_character(geo)``, which read
  those values off a ``hou.Geometry`` (needs ``hou`` and ``apex``).

The checks:

- **Viz on the Main control is off** (error). ``Main_Viz`` is the rig's viz
  channel (TumbleRig's Viz slider on the Main control). Published on, every
  shot that loads the rig draws the joint viz until an animator turns it
  off, and pays for it each frame. Its value is the rig's stored parm, else
  the graph's default.
- **Big shapes carry normals** (warning). A shape of ``MIN_POINTS`` points
  or more that reaches the viewport without N makes Houdini compute normals
  on the CPU every frame it draws (5 ms of a 53 ms Animate frame on a
  205,842 point character; Apex Normals as the last node before the shape
  took it to 48 ms). A rest shape without N stays without N through the
  deformers, so the rest shape and the graph decide it without a cook. A
  rig whose graph already has a normals node anywhere is left alone.
"""

from .base import ValidationResult

MIN_POINTS = 50000
VIZ_PARM = 'Main_Viz'

NORMALS_SUGGESTION = (
    "Add Apex Normals (TumbleRig) as the last node before the shape, so the "
    "normals are computed on the GPU. A shape that brings its own normals in "
    "its mesh needs nothing."
)
VIZ_SUGGESTION = (
    "Turn the viz off on the Main control before publishing: set the rig's "
    "Main_Viz default to 0."
)


def check_shape_normals(
    result: ValidationResult,
    shapes,
    has_normals_node: bool,
    min_points: int = MIN_POINTS,
) -> ValidationResult:
    """Warn for each shape without normals of at least ``min_points``.

    Args:
        result: Result to add warnings to (returned).
        shapes: Iterable of ``(name, point_count, has_normals)``.
        has_normals_node: The rig graph already computes normals somewhere.
        min_points: Smallest shape worth a warning.
    """
    if has_normals_node:
        return result
    for name, points, has_normals in shapes:
        if not has_normals and points >= min_points:
            result.add_warning(
                f"{name} ({points:,} points) has no normals, so the viewport "
                "computes them on the CPU every frame",
                prim_path=name,
                suggestion=NORMALS_SUGGESTION,
            )
    return result


def check_viz_off(result: ValidationResult, viz_value) -> ValidationResult:
    """Fail when the Main control's viz channel is on.

    Args:
        result: Result to add the error to (returned).
        viz_value: ``Main_Viz``'s effective value, or None for a rig
            without the channel (nothing to check).
    """
    if viz_value is None:
        return result
    try:
        value = float(viz_value)
    except (TypeError, ValueError):
        return result
    if value != 0.0:
        result.add_error(
            f"{VIZ_PARM} is {value:g}: the rig would publish with its viz on",
            prim_path=VIZ_PARM,
            suggestion=VIZ_SUGGESTION,
        )
    return result


# -- reading a character stream (Houdini) ---------------------------------------

def _has_normals(geo) -> bool:
    return geo.findPointAttrib('N') is not None or geo.findVertexAttrib('N') is not None


def _point_count(geo) -> int:
    try:
        return int(geo.intrinsicValue('pointcount'))
    except Exception:
        return len(geo.iterPoints())


def _graph_has_normals_node(rig_geo, depth: int = 0) -> bool:
    """Any node of the rig graph, subnets included, whose callback names
    normals (th::cuda_normals, sop::normal ...)."""
    if rig_geo.findPointAttrib('callback') is not None:
        for callback in rig_geo.pointStringAttribValues('callback'):
            if 'normal' in callback.lower():
                return True
    if depth > 16:
        return False
    for prim in rig_geo.prims():
        embedded = getattr(prim, 'getEmbeddedGeometry', None)
        if embedded is None:
            continue
        try:
            inner = embedded()
        except Exception:
            continue
        if inner is not None and _graph_has_normals_node(inner, depth + 1):
            return True
    return False


def _viz_value(rig_geo):
    """Main_Viz as the rig evaluates it: the stored parm, else the graph's
    default; None when the rig has no such channel."""
    import apex
    graph = apex.Graph()
    graph.loadFromGeometry(rig_geo)
    stored = graph.getParmDict()
    if stored is not None and VIZ_PARM in stored.keys():
        return stored[VIZ_PARM]
    defaults = graph.getDefaultParameters()
    if defaults is not None and VIZ_PARM in defaults.keys():
        return defaults[VIZ_PARM]
    return None


def read_character(geo):
    """The values the checks need from a packed character stream.

    Returns:
        ``(shapes, has_normals_node, viz_value)``: shapes as
        ``(name, point_count, has_normals)`` for every ``*.shp`` but the
        ``*Viz.shp`` guides; ``viz_value`` from the first ``*.rig``.
    """
    shapes, rigs = [], []
    if geo is None or geo.findPrimAttrib('name') is None:
        return shapes, False, None
    for prim in geo.prims():
        embedded = getattr(prim, 'getEmbeddedGeometry', None)
        if embedded is None:
            continue
        name = prim.attribValue('name')
        try:
            inner = embedded()
        except Exception:
            continue
        if inner is None:
            continue
        if name.endswith('.rig'):
            rigs.append(inner)
        elif name.endswith('.shp') and not name.endswith('Viz.shp'):
            shapes.append((name, _point_count(inner), _has_normals(inner)))
    has_normals_node = any(_graph_has_normals_node(rig) for rig in rigs)
    viz_value = _viz_value(rigs[0]) if rigs else None
    return shapes, has_normals_node, viz_value


def validate_rig_character(geo) -> ValidationResult:
    """Run every rig check on a packed character stream (``hou.Geometry``)."""
    result = ValidationResult()
    if geo is None:
        result.add_warning("No rig geometry to validate")
        return result
    shapes, has_normals_node, viz_value = read_character(geo)
    if not shapes and viz_value is None:
        result.add_warning("No APEX character (*.rig / *.shp) found in the rig export")
        return result
    check_viz_off(result, viz_value)
    check_shape_normals(result, shapes, has_normals_node)
    return result
