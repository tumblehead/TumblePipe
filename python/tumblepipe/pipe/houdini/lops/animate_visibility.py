"""The visibility th::animate copies each frame: what the dive says per
shape, the shapes of one prim that disagree (split into parts), and
authoring it over the incoming stage. Split from animate_points."""

import hou
import numpy as np

from tumblepipe.pipe.houdini.lops.animate_geometry import (
    enum_name, has_packed, packed_leaves,
)


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
    return attrib is not None and enum_name(attrib.dataType()) == 'String'


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
    sources = _visibility_sources(geo, unpacked_node)
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


# --- shapes of one prim that disagree ------------------------------------------
#
# What a primitive's `usdvisibility` says, as a number: nothing, shown, hidden.
NOTHING, SHOWN, HIDDEN = 0, 1, 2


def _code(value) -> int:
    return NOTHING if not value else HIDDEN if value == 'invisible' else SHOWN


def _shape_codes(geo):
    """What `usdvisibility` says of each primitive of `geo` (NOTHING, SHOWN
    or HIDDEN, int8): its own on the primitives, else its first point's,
    else the detail's (the classes in the order `_visibility_reader` reads
    them); None when `geo` has no such string attribute."""
    count = geo.intrinsicValue('primitivecount')
    if _is_string(geo.findPrimAttrib(VISIBILITY)):
        values = geo.primStringAttribValues(VISIBILITY)
        return np.fromiter((_code(value) for value in values), np.int8, len(values))
    if _is_string(geo.findPointAttrib(VISIBILITY)):
        by_point = geo.pointStringAttribValues(VISIBILITY)
        codes = np.zeros(count, np.int8)
        for number, prim in enumerate(geo.prims()):
            points = prim.points()
            if points:
                codes[number] = _code(by_point[points[0].number()])
        return codes
    if _is_string(geo.findGlobalAttrib(VISIBILITY)):
        return np.full(count, _code(geo.attribValue(VISIBILITY)), np.int8)
    return None


def _visibility_sources(geo, unpacked_node=None) -> list:
    """The geometries `geo` (the dive's frame) is read from, one after
    another as two Unpack SOPs lay them out: itself when it is not packed,
    its packed shapes, else `unpacked_node`'s cook (points_sops/CURRENT)."""
    if not has_packed(geo):
        return [geo]
    sources = packed_leaves(geo)
    if sources is None:
        if unpacked_node is None:
            raise hou.NodeError('th::animate: no unpacked copy of the dive to read.')
        sources = [unpacked_node.geometry()]
    return sources


def shape_visibility(geo, unpacked_node=None, expected_count=None):
    """What `geo` (the dive's frame, laid out as `read_points` reads it)
    says of each of its primitives' visibility: NOTHING, SHOWN or HIDDEN
    (int8), in the order `stamp_prims` numbers them. None when no shape has
    `usdvisibility`, and when the frame has not `expected_count` primitives
    (the first frame's, where the numbers are from: they would name other
    primitives)."""
    sources = _visibility_sources(geo, unpacked_node)
    counts = [source.intrinsicValue('primitivecount') for source in sources]
    if expected_count is not None and sum(counts) != expected_count:
        return None
    codes = [_shape_codes(source) for source in sources]
    if all(part is None for part in codes):
        return None
    return np.concatenate([
        np.zeros(count, np.int8) if part is None else part
        for part, count in zip(codes, counts)
    ])


def can_split(mapping) -> bool:
    """Whether any mapped prim carries its primitives' numbers
    (`point_map`'s 'shapes'): only then is `shape_visibility` worth
    reading."""
    return any(run is not None for run in mapping.get('shapes') or ())


def split_visibility(codes, mapping, said):
    """The mapped prims whose shapes disagree at this frame: some of a
    prim's faces (curves) hidden, the others not. `codes` is
    `shape_visibility`, `said` what `read_visibility` gave (one shape
    deciding for the prim).

    Returns (said, parts, unsplit). `parts` has, per mapped prim, None or
    one bool per face (curve), True where it is hidden; None when no prim
    is split. `said` is the prim's own visibility: unchanged where the
    shapes agree, and for a split prim 'inherited' when a part that shows
    says so, else None (it says nothing: `visibility_bases`), never
    'invisible', which would hide the part that shows. `unsplit` names the
    prims that disagree and cannot be split (`point_map`'s 'splits'): they
    keep `said`, one shape deciding."""
    said = list(said)
    shapes = mapping.get('shapes') or ()
    if codes is None or not (codes == HIDDEN).any():
        return said, None, []
    parts, unsplit = [None] * len(mapping['paths']), []
    for i, run in enumerate(shapes):
        if run is None or len(run) < 2:
            continue
        if int(run.min()) < 0 or int(run.max()) >= len(codes):
            continue
        own = codes[run]
        hidden = own == HIDDEN
        if not hidden.any() or hidden.all():
            continue
        if not mapping['splits'][i]:
            unsplit.append(mapping['paths'][i])
            continue
        parts[i] = hidden
        said[i] = 'inherited' if (own == SHOWN).any() else None
    if all(part is None for part in parts):
        parts = None
    return said, parts, unsplit


def hole_bases(stage, mapping, frame) -> list:
    """Per mapped prim, the faces `stage` (the stage coming into
    th::animate; None without one) leaves out of it at `frame`: its
    authored `holeIndices` that name a face of the mesh as th::animate has
    it, as an int array; None for a prim that is not a mesh `author_parts`
    writes holes on, or has none below. th::animate's layer is the
    stronger, so its `holeIndices` keep the asset's own holes."""
    shapes = mapping.get('shapes') or [None] * len(mapping['paths'])
    bases = []
    for i, path in enumerate(mapping['paths']):
        base = None
        if stage is not None and shapes[i] is not None and mapping['kinds'][i] == 'mesh':
            prim = stage.GetPrimAtPath(path)
            attr = prim.GetAttribute('holeIndices') if prim else None
            value = attr.Get(frame) if attr and attr.HasAuthoredValue() else None
            if value is not None and len(value):
                holes = np.asarray(value, dtype=np.int64)
                holes = holes[(holes >= 0) & (holes < len(shapes[i]))]
                base = holes if len(holes) else None
        bases.append(base)
    return bases


def author_parts(layer, mapping, parts, bases, frame) -> None:
    """The hidden part of every split prim (`parts`: `split_visibility`;
    None when no prim is split) taken out of it at `frame`, in `layer`.

    A mesh: its hidden faces as `holeIndices`, with the faces the incoming
    stage already leaves out (`bases`: `hole_bases`; None for none). Every
    mesh that carries its primitives' numbers gets the sample, on every
    frame, split or not: sampled on the split frames only, a saved range
    would read their holes on every frame between them (as with visibility:
    `author_visibility`). Curves: the widths this frame has (`author_frame`'s
    sample, else the static import's) with 0 on the hidden curves, per
    point or per curve as they are authored; the other frames keep theirs
    (`author_frame`, `sample_defaults`)."""
    from pxr import Sdf, Vt
    shapes = mapping.get('shapes')
    if not shapes:
        return
    with Sdf.ChangeBlock():
        for i, path in enumerate(mapping['paths']):
            if shapes[i] is None or not mapping['splits'][i]:
                continue
            hidden = parts[i] if parts is not None else None
            spec = layer.GetPrimAtPath(path)
            if mapping['kinds'][i] == 'mesh':
                holes = np.zeros(0, np.int64)
                if bases is not None and bases[i] is not None:
                    holes = bases[i]
                if hidden is not None:
                    holes = np.union1d(holes, np.nonzero(hidden)[0])
                attr_path = path.AppendProperty('holeIndices')
                if layer.GetAttributeAtPath(attr_path) is None:
                    spec = spec or Sdf.CreatePrimInLayer(layer, path)
                    Sdf.AttributeSpec(spec, 'holeIndices', Sdf.ValueTypeNames.IntArray,
                                      Sdf.VariabilityVarying)
                layer.SetTimeSample(
                    attr_path, frame,
                    Vt.IntArray.FromNumpy(np.ascontiguousarray(holes, dtype=np.int32)))
                continue
            if hidden is None or spec is None:
                continue
            attr_path = path.AppendProperty('widths')
            attr = layer.GetAttributeAtPath(attr_path)
            if attr is None:
                continue
            value = layer.QueryTimeSample(attr_path, frame)
            if value is None or isinstance(value, Sdf.ValueBlock):
                value = attr.default if attr.HasInfo('default') else None
            if value is None or isinstance(value, Sdf.ValueBlock):
                continue
            widths = np.array(value, dtype=np.float32)
            counts = mapping['curve_counts'][i]
            if len(widths) == len(hidden):
                widths[hidden] = 0.0
            elif counts is not None and len(widths) == int(counts.sum()):
                widths[np.repeat(hidden, counts)] = 0.0
            else:
                continue
            layer.SetTimeSample(attr_path, frame, Vt.FloatArray.FromNumpy(widths))


def unsplit_message(paths) -> str:
    """The warning for prims whose shapes disagree on visibility and
    cannot be split (`split_visibility`)."""
    shown = ', '.join(str(path) for path in paths[:5])
    more = f' and {len(paths) - 5} more' if len(paths) > 5 else ''
    return (
        f'th::animate: the shapes of {shown}{more} are not all hidden or all '
        'shown on this frame, and only a mesh, or curves with a width per '
        'curve or per point, can be hidden in part: one shape decides for '
        'all of it. Give the parts paths of their own, or the curves a width '
        'attribute on their points or primitives.'
    )
