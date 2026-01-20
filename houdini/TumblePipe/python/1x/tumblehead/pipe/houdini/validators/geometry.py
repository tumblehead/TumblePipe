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
                    mesh_path
                )

        # Check for normals
        normals_attr = mesh_prim.GetAttribute('primvars:normals')
        if not normals_attr.IsValid():
            # Also check standard normals attribute
            std_normals_attr = mesh_prim.GetAttribute('normals')
            if not std_normals_attr.IsValid():
                result.add_warning(
                    "Mesh missing normals (primvars:normals or normals)",
                    mesh_path
                )

    if mesh_count == 0:
        # No meshes found - not an error, just nothing to validate
        pass

    return result


def validate_material_bindings(root) -> ValidationResult:
    """Validate that all geometry has material bindings.

    For lookdev exports, all geometry should have materials assigned.
    This validator checks ALL Mesh prims in the stage.
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    for mesh_prim in _iter_mesh_prims(root):
        mesh_path = str(mesh_prim.GetPath())

        # Check for material binding relationship
        binding_rel = mesh_prim.GetRelationship('material:binding')
        if not binding_rel.IsValid() or not binding_rel.GetTargets():
            result.add_warning(
                "Mesh has no material binding",
                mesh_path
            )

    return result
