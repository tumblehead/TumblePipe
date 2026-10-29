"""th::animate: the transforms its animated layer authors.

The rigs come out of the anim_dive as world-space geometry, and the
HDA's ``zero_transforms`` Python LOP makes the prims they land on agree:
the transforms sopimport writes for packed prims are dropped, and nothing
above the posed geometry may move it again.

It used to zero every prim in the layer. A character's own prim is in it,
as the parent of the posed meshes, so that wiped the layout placement off
the copy: with the rig started at the placement (import_rig's ``place``),
the posed meshes stood where layout put them, but whatever under the copy
the rig does not output (Ryan's render-purpose groom) fell back to the
origin. Now the prims above the geometry keep their transforms from the
input stage, and each geometry prim cancels what they add up to, so the
posed geometry still lands in world space. The cancel sits on the
geometry itself: a prim above it can end up a Scope (an asset's ``geo``),
which ignores a transform.
"""

from pxr import Gf, Usd, UsdGeom


def _clear_ops(xformable):
    """Drop the prim's ops, time samples included."""
    for op in xformable.GetOrderedXformOps():
        op.GetAttr().Clear()
    xformable.ClearXformOpOrder()


def _set_identity(xformable):
    xformable.AddTranslateOp().Set(value=(0, 0, 0))
    xformable.AddRotateXYZOp().Set(value=(0, 0, 0))
    xformable.AddScaleOp().Set(value=(1, 1, 1))


def _copy_ops(prim, source):
    """Author ``source``'s xformOpOrder and ops (defaults and samples) on
    ``prim``, whose ops are cleared."""
    order_attr = UsdGeom.Xformable(source).GetXformOpOrderAttr()
    order = order_attr.Get() or []
    for token in order:
        name = str(token).removeprefix('!invert!')
        if name == UsdGeom.XformOpTypes.resetXformStack:
            continue
        source_attr = source.GetAttribute(name)
        if not source_attr.IsValid():
            continue
        attr = prim.CreateAttribute(name, source_attr.GetTypeName(), custom=False)
        default = source_attr.Get(Usd.TimeCode.Default())
        if default is not None:
            attr.Set(default)
        for time in source_attr.GetTimeSamples():
            attr.Set(source_attr.Get(time), time)
    UsdGeom.Xformable(prim).GetXformOpOrderAttr().Set(order)


def zero_transforms(stage, input_stage=None, time=Usd.TimeCode.Default()):
    """Rewrite the transforms of the animated layer ``stage`` so its posed
    geometry lands in world space over ``input_stage`` (th::animate's
    IN_stage; None when there is none).

    Cameras keep the transform they arrive with. Every other prim the
    input stage has keeps its input transform (a character's layout
    placement stays on its copy, for what the rig does not output), and a
    prim it lacks gets the identity. A geometry prim then cancels its
    parents: at identity when they add up to it, else with the inverse of
    their world transform at ``time``.
    """
    time = Usd.TimeCode(time)
    identity = Gf.Matrix4d(1)
    worlds = {}
    for prim in stage.Traverse():
        # A camera's transform is its placement, not a rig offset.
        if prim.IsA(UsdGeom.Camera):
            continue
        path = prim.GetPath()
        parent_world = worlds.get(path.GetParentPath(), identity)
        xformable = UsdGeom.Xformable(prim)
        _clear_ops(xformable)

        # Posed geometry: world space, whatever is above it
        if prim.IsA(UsdGeom.Boundable):
            if parent_world == identity:
                _set_identity(xformable)
            else:
                xformable.AddTransformOp().Set(parent_world.GetInverse())
            worlds[path] = identity
            continue

        source = (
            Usd.Prim() if input_stage is None
            else input_stage.GetPrimAtPath(path)
        )
        source_xformable = UsdGeom.Xformable(source) if source.IsValid() else None
        if not source_xformable or not source_xformable.GetOrderedXformOps():
            _set_identity(xformable)
            worlds[path] = parent_world
            continue
        _copy_ops(prim, source)
        worlds[path] = source_xformable.GetLocalTransformation(time) * parent_world
