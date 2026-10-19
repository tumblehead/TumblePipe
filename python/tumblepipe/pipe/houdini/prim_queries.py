"""Queries and edits on a USD stage: prims, cameras, render vars, LPE tags, placement ops."""

from pxr import Gf, Usd, UsdGeom, UsdLux

from tumblepipe.util.uri import Uri

def apply_placement_op_order(prim) -> bool:
    """Author the xformOpOrder that applies composed placement op values.

    The order derivation lives in pipe.usd.instances.composed_placement_op_order
    (shared with the static batch-submit flatten); see its docstring for
    the why. Called on each re-established instance prim (import_asset's
    metadata script, import_shot's duplicates subnet) after it is
    (re)created. Returns True when an order was authored; False when no
    placement composed (the caller decides the fallback).
    """
    from tumblepipe.pipe.usd.instances import composed_placement_op_order

    order = composed_placement_op_order(prim)
    if not order:
        return False
    UsdGeom.Xformable(prim).GetXformOpOrderAttr().Set(order)
    return True


def author_identity_placement_ops(prim) -> bool:
    """Author the identity XformCommonAPI op set on a prototype prim.

    Mirrors what th::import_asset's internal Transform LOP (xformdescription
    'import', usexformcommonapi on) authors on a DIRECTLY imported asset
    root: translate, translate:pivot, rotateXYZ, scale and the inverted
    pivot, all identity. Sub-asset roots re-established from a staged
    context.json never pass through that LOP, so without this the prototype
    the instance prims reference carries no transform at all — importing a
    set gave a bare base while manually importing the same asset gave the
    full op set.

    Only call this when no placement composed (see
    apply_placement_op_order): the values authored here are identity, and
    the import node's layer is stronger than the set layer's placement
    overs, so authoring unconditionally would zero out an instances==1
    sub-asset's placement. Returns False when the prim's existing op stack
    is not XformCommonAPI-compatible (e.g. a baked xformOp:transform), in
    which case the caller leaves it alone.
    """
    common_api = UsdGeom.XformCommonAPI(prim)
    if not common_api:
        return False
    return common_api.SetXformVectors(
        Gf.Vec3d(0.0, 0.0, 0.0),  # translation
        Gf.Vec3f(0.0, 0.0, 0.0),  # rotation
        Gf.Vec3f(1.0, 1.0, 1.0),  # scale
        Gf.Vec3f(0.0, 0.0, 0.0),  # pivot
        UsdGeom.XformCommonAPI.RotationOrderXYZ,
        Usd.TimeCode.Default()
    )


def iter_prim_children(prim):
    """Children including instance proxies and undefined (over) prims.

    GetChildren() skips both, and each hides tracked assets from every
    scrape built on this traversal: instanceable copies (e.g. Duplicate
    LOP output) hold their assets in instance proxies, and an asset
    imported via a department-layer import composes as a typeless *over*
    once the layerbreak discards its def — the customData survives on
    the over, and the export scrape must still see it so staging can
    re-reference the asset's real definition downstream.
    """
    predicate = Usd.TraverseInstanceProxies(
        Usd.PrimIsActive & Usd.PrimIsLoaded & ~Usd.PrimIsAbstract
    )
    return prim.GetFilteredChildren(predicate)


def iter_scene(prim, predicate):
    if not prim.IsActive(): return
    for subprim in iter_prim_children(prim):
        if predicate(subprim):
            yield subprim
            continue
        for subsubprim in iter_scene(subprim, predicate):
            yield subsubprim


def is_camera(prim):
    return prim.GetTypeName() == 'Camera'


def is_render_var(prim):
    return prim.GetTypeName() == 'RenderVar'


def list_cameras(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_camera)))


# Where projects keep their cameras. Both spellings are live: the project
# template keeps them at the root, projects made from the template
# before 2026-10-07 nest them under /scene. Order is preference, not precedence —
# the first scope that holds a camera wins.
CAMERA_SCOPE_PATHS = ('/cameras', '/scene/cameras')


def list_stage_cameras(root):
    """Every camera a node can offer, wherever the project keeps them.

    Asking for one hardcoded scope is what left the playblast nodes with an
    empty camera menu (and "No camera path found" on export) on every
    /scene-rooted project. The known scopes are tried first because they make
    the menu cheap on a heavy shot stage; anything else falls back to walking
    the stage, which is slower but never wrong.
    """
    for scope_path in CAMERA_SCOPE_PATHS:
        scope = root.GetPrimAtPath(scope_path)
        if scope is None: continue
        if not scope.IsValid(): continue
        cameras = list_cameras(scope)
        if cameras: return cameras
    return list_cameras(root)


def list_render_vars(prim):
    def _get_path(prim): return str(prim.GetPath())
    return list(map(_get_path, iter_scene(prim, is_render_var)))


# The LPE Tag LOP writes the tag to one of two attributes depending on what it
# tagged: lights get inputs:karma:light:lpetag, geometry lights get
# primvars:karma:object:lpetag. Both are matched by suffix because the leading
# namespace differs (inputs: vs primvars:).
LPE_TAG_LIGHT_ATTR = 'karma:light:lpetag'


LPE_TAG_GEO_ATTR = 'karma:object:lpetag'


# The LOP's own fallback for anything it did not match. Never a real tag.
UNTAGGED_LPE_TAG = 'Untagged_Lights'


def _iter_prims(prim):
    """Every descendant, including instance proxies and overs.

    Unlike iter_scene this does not stop descending at a match: a tagged
    prim can contain further tagged prims.
    """
    for subprim in iter_prim_children(prim):
        yield subprim
        yield from _iter_prims(subprim)


def _prim_lpe_tags(prim):
    if UsdGeom.Imageable(prim).ComputeVisibility() == 'invisible': return []
    tag_names = list()
    for prop in prim.GetProperties():
        prop_name = prop.GetName()
        if not prop_name.endswith((LPE_TAG_LIGHT_ATTR, LPE_TAG_GEO_ATTR)): continue
        if not prop.IsValid(): continue
        tag_name = (
            prop.Get(1001)
            if prop.GetNumTimeSamples() else
            prop.Get()
        )
        if not tag_name: continue
        if tag_name == UNTAGGED_LPE_TAG: continue
        if tag_name in tag_names: continue
        tag_names.append(tag_name)
    return tag_names


def list_lpe_tags(prim):
    """Ordered LPE tag names carried by any prim in the subtree.

    Drives the th::lpe_tags render var loop. This deliberately does NOT
    filter on prim type. LightAPI is an *applied schema*, so a mesh light
    keeps the type name 'Mesh' — the predecessor here filtered on
    GetTypeName().endswith('Light') and silently dropped every mesh-light
    tag, so the matching beauty_<tag> RenderVar was never built.

    Order is traversal order so the generated vars are stable between
    cooks; reorder_aovs sorts them for the render product afterwards.
    """
    tag_names = list()
    for subprim in _iter_prims(prim):
        for tag_name in _prim_lpe_tags(subprim):
            if tag_name in tag_names: continue
            tag_names.append(tag_name)
    return tag_names


def is_light_prim(prim):
    """Whether a prim renders as a light.

    LightAPI is an applied schema, so a mesh light reports the type name
    'Mesh'; a type-name test alone misses every one of them.
    """
    if prim.HasAPI(UsdLux.LightAPI): return True
    return prim.GetTypeName().endswith('Light')


def list_light_prims(prim):
    return [subprim for subprim in _iter_prims(prim) if is_light_prim(subprim)]


def propose_lpe_tags(prim, separator='_'):
    """Suggest (tag, [prim paths]) from how the lights are named.

    Groups every light in the stage by the leading token of its name, so
    'side_key' and 'side_rim' both propose the tag 'side'. Lights whose
    name carries no separator have no tag to infer and are skipped.

    Backs the node's Generate button, which is only ever a starting point:
    tags are a lighting decision and often have nothing to do with names.
    """
    proposals = dict()
    for light_prim in list_light_prims(prim):
        name = light_prim.GetName()
        if separator not in name: continue
        tag_name = name.split(separator, 1)[0]
        if not tag_name: continue
        proposals.setdefault(tag_name, []).append(str(light_prim.GetPath()))
    return [(tag_name, proposals[tag_name]) for tag_name in proposals]


def unmatched_lpe_tags(prim, tag_names):
    """Configured tags that no prim in the stage actually carries.

    Each one silently costs a beauty_<tag> RenderVar: the var loop reads
    tags back off the cooked stage, so a row whose Lights pattern selects
    nothing simply produces no output.
    """
    present = list_lpe_tags(prim)
    return [
        tag_name for tag_name in tag_names
        if tag_name and tag_name not in present
    ]


def uri_to_prim_path(uri: Uri) -> str:
    """
    Convert entity URI to USD prim path by omitting first segment.

    Examples:
        entity:/shots/seq010/shot0010 -> /seq010/shot0010
        entity:/assets/char/mom -> /char/mom

    Args:
        uri: Entity URI

    Returns:
        USD prim path string
    """
    segments = uri.segments[1:]
    return '/' + '/'.join(segments) if segments else '/'
