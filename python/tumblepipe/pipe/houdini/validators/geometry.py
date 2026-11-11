"""Geometry validation for USD stages."""

from .base import ValidationResult


def _iter_mesh_prims(root):
    """Iterate over all Mesh prims in the stage."""
    stage = root.GetStage()
    if stage is None:
        return

    for prim in stage.Traverse():
        if prim.GetTypeName() == 'Mesh':
            yield prim


def validate_rest_geometry(root) -> ValidationResult:
    """Validate that mesh geometry has rest positions and normals.

    For model exports, deformable geometry should have:
    - primvars:rest (Pref) - Rest position for proper deformation
    - primvars:normals - Normal data for shading

    This validator checks ALL Mesh prims in the stage.
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    mesh_count = 0
    for mesh_prim in _iter_mesh_prims(root):
        mesh_count += 1
        mesh_path = str(mesh_prim.GetPath())

        # Check for rest positions (Pref)
        rest_attr = mesh_prim.GetAttribute('primvars:rest')
        if not rest_attr.IsValid():
            # Also check for alternate naming
            pref_attr = mesh_prim.GetAttribute('primvars:Pref')
            if not pref_attr.IsValid():
                result.add_warning(
                    "Mesh missing rest positions (primvars:rest or primvars:Pref)",
                    mesh_path,
                    suggestion=(
                        "Author a 'rest' primvar on the meshes: capture rest "
                        "positions with a Rest SOP in the variant network "
                        "feeding create_asset_model, before the USD import."
                    ),
                )

        # Check for normals
        normals_attr = mesh_prim.GetAttribute('primvars:normals')
        if not normals_attr.IsValid():
            # Also check standard normals attribute
            std_normals_attr = mesh_prim.GetAttribute('normals')
            if not std_normals_attr.IsValid():
                result.add_warning(
                    "Mesh missing normals (primvars:normals or normals)",
                    mesh_path,
                    suggestion=(
                        "Compute normals upstream (Normal SOP or Polyframe SOP), "
                        "or author normals3f via a Configure Primitive LOP."
                    ),
                )

    if mesh_count == 0:
        # No meshes found - not an error, just nothing to validate
        pass

    return result


BIND_SUGGESTION = (
    "Bind a material via an Assign Material LOP (or the lookdev template's "
    "material assigner), or rely on the lookdev department export to author "
    "bindings later."
)
NOTHING_BOUND_SUGGESTION = (
    "Fill the material assigner's Primitives (or an Assign Material LOP) so "
    "the asset's meshes get their materials; a lookdev that binds nothing "
    "publishes the model grey."
)


def check_bindings(
    result: ValidationResult, meshes, scope: str | None = None,
) -> ValidationResult:
    """The lookdev binding check on plain values.

    ``meshes`` is ``(path, bound)`` per Mesh prim, ``bound`` whether a
    material resolves for it (its own binding, an ancestor's or a
    collection's). Meshes outside ``scope`` (the asset prim) are ignored
    when it is given. When none of the rest is bound the lookdev binds
    nothing: one error (#37: three lookdevs with empty assigners published
    clean). Otherwise each unbound mesh warns.
    """
    if scope is not None:
        prefix = scope.rstrip('/') + '/'
        meshes = [(path, bound) for path, bound in meshes
                  if path == scope or path.startswith(prefix)]
    meshes = list(meshes)
    if not meshes:
        return result
    if not any(bound for _, bound in meshes):
        where = scope or 'the stage'
        result.add_error(
            f"No mesh under {where} has a material bound "
            f"({len(meshes)} mesh(es))",
            scope or meshes[0][0],
            suggestion=NOTHING_BOUND_SUGGESTION,
        )
        return result
    for path, bound in meshes:
        if not bound:
            result.add_warning(
                "Mesh has no material binding", path, suggestion=BIND_SUGGESTION,
            )
    return result


def _is_bound(prim) -> bool:
    from pxr import UsdShade
    try:
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    except Exception:
        return False
    return bool(material)


def validate_material_bindings(root, context: dict | None = None) -> ValidationResult:
    """Validate that the geometry has material bindings.

    A material counts when it resolves for the mesh, so a binding on a
    parent or through a collection is enough (up to 1.101.0 only a binding
    on the mesh itself counted). With an entity URI in ``context`` only the
    meshes under the asset prim are checked.
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    scope = None
    entity_uri = context.get('entity_uri') if context else None
    if entity_uri:
        from tumblepipe.util.uri import Uri
        from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
        try:
            scope = uri_to_prim_path(Uri.parse(entity_uri))
        except Exception:
            scope = None

    meshes = [
        (str(prim.GetPath()), _is_bound(prim)) for prim in _iter_mesh_prims(root)
    ]
    return check_bindings(result, meshes, scope)
