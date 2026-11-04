"""Split a playblast into render layers by hiding prims.

A layered playblast (th::playblast's Render Layers) renders the shot once
per render layer, each time with only that layer's prims visible. Each
render is the playblast's usual wrapper with one more sublayer on top: a
layer of ``over``s that set ``visibility = "invisible"`` on the prims that
are not in the render layer (:func:`write_hide_layer`).

What to hide (:func:`prims_to_hide`) comes from which prims the layer keeps.
A kept prim stays visible with everything under it. Its ancestors must stay
visible too (an invisible parent hides its children), so they are walked
into, and every sibling along the way that holds nothing kept is hidden.
The camera is always kept: it is what the frame is rendered through.

A layer's prims are not held out of the others: a character in front of
the set still leaves the set whole behind it in the set's layer, and the
layers are stacked "over" in the player. Prims inside an instance cannot be
hidden one by one: an instance is kept or hidden whole.
"""

from pxr import Sdf, Usd, UsdGeom


def _is_under(path: Sdf.Path, roots: set[Sdf.Path]) -> bool:
    return any(path.HasPrefix(root) for root in roots)


def prims_to_hide(stage: Usd.Stage, keep: set[Sdf.Path]) -> list[Sdf.Path]:
    """The fewest prims to make invisible so only ``keep`` (and what is
    under it) renders. Sorted. ``keep`` empty hides every top-level prim."""
    keep = {Sdf.Path(str(path)) for path in keep}
    ancestors = set()
    for path in keep:
        for prefix in path.GetPrefixes()[:-1]:
            ancestors.add(prefix)
    hide = []
    iterator = iter(Usd.PrimRange(stage.GetPseudoRoot()))
    for prim in iterator:
        path = prim.GetPath()
        if path == Sdf.Path.absoluteRootPath:
            continue
        if _is_under(path, keep):
            iterator.PruneChildren()
            continue
        if path in ancestors and not prim.IsInstance():
            continue
        if path in ancestors:
            # An instance holding a kept prim: kept whole.
            iterator.PruneChildren()
            continue
        if not prim.IsA(UsdGeom.Imageable):
            # A typeless or non-imageable prim (a bare "def" parent, a
            # Scope-less group): its visibility does not reach the renderer,
            # so hide what is under it instead.
            continue
        hide.append(path)
        iterator.PruneChildren()
    return sorted(hide)


def remainder_keep(stage: Usd.Stage, claimed: set[Sdf.Path]) -> set[Sdf.Path]:
    """What a remainder layer keeps: every top-level prim, less what the
    other layers claimed. Returned as the claimed prims' complement through
    :func:`prims_to_hide` semantics: everything except the claimed subtrees."""
    claimed = {Sdf.Path(str(path)) for path in claimed}
    keep = set()
    iterator = iter(Usd.PrimRange(stage.GetPseudoRoot()))
    for prim in iterator:
        path = prim.GetPath()
        if path == Sdf.Path.absoluteRootPath:
            continue
        if _is_under(path, claimed):
            iterator.PruneChildren()
            continue
        holds_claimed = any(c.HasPrefix(path) for c in claimed)
        if holds_claimed and not prim.IsInstance():
            continue
        keep.add(path)
        iterator.PruneChildren()
    return keep


def holds_geometry(stage: Usd.Stage, keep: set[Sdf.Path]) -> bool:
    """Whether anything under ``keep`` draws: a gprim or a point instancer,
    instance proxies included. A layer keeping only a camera, lights or
    empty groups would render a wholly transparent movie."""
    keep = {Sdf.Path(str(path)) for path in keep}
    for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
        draws = prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer)
        if draws and _is_under(prim.GetPath(), keep):
            return True
    return False


def write_hide_layer(paths: list[Sdf.Path], output_path) -> None:
    """Write a layer of ``over``s making each of ``paths`` invisible."""
    layer = Sdf.Layer.CreateAnonymous('.usda')
    for path in paths:
        spec = Sdf.CreatePrimInLayer(layer, Sdf.Path(str(path)))
        attribute = Sdf.AttributeSpec(
            spec, UsdGeom.Tokens.visibility, Sdf.ValueTypeNames.Token
        )
        attribute.default = UsdGeom.Tokens.invisible
    layer.Export(str(output_path))
