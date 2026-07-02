"""Shot stage structure validation for USD stages."""

from .base import ValidationResult


# Allowed root prim names (exact matches)
ALLOWED_ROOT_PRIMS = {'collections', 'lights', 'cameras', 'Render', 'scene'}

# Categories and asset roots compose as either type: import nodes author
# them as Scopes, but asset exports type them Xform (set_kinds makes
# assembly/component prims transformable), and whichever layer wins
# composition decides what the shot stage sees.
ASSET_CONTAINER_TYPES = {'Scope', 'Xform'}


def _is_asset_category_prim(prim) -> bool:
    """Check if a prim looks like an asset category (e.g., /CHAR with /CHAR/mom child).

    Asset categories are Scope or Xform prims that contain child Scope or
    Xform prims representing individual assets.
    """
    if prim.GetTypeName() not in ASSET_CONTAINER_TYPES:
        return False
    for child in prim.GetChildren():
        if child.GetTypeName() in ASSET_CONTAINER_TYPES:
            return True
    return False


def validate_shot_root_prims(root) -> ValidationResult:
    """Validate that shot stages only contain allowed root prims.

    For shot departments, validates that only these root prims exist:
    - Asset category prims (Scope prims with Scope children, e.g., /CHAR, /PROP)
    - /collections - Collection groupings
    - /lights - Light sources
    - /cameras - Camera prims
    - /Render - Render settings hierarchy
    - /scene - Scene layer overrides
    Returns:
        ValidationResult with errors for any disallowed root prims
    """
    result = ValidationResult()
    stage = root.GetStage()

    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    for prim in stage.GetPseudoRoot().GetChildren():
        prim_name = prim.GetName()

        # Check if it's an allowed fixed name
        if prim_name in ALLOWED_ROOT_PRIMS:
            continue

        # Check if it looks like an asset category
        if _is_asset_category_prim(prim):
            continue

        # Not allowed
        result.add_error(
            f"Disallowed root prim '{prim_name}'. "
            f"Only asset categories, collections, lights, cameras, Render, and scene are allowed.",
            f"/{prim_name}",
            suggestion=(
                "Reparent this content under one of the allowed roots "
                "(collections, lights, cameras, Render, scene), or under an "
                "asset category Scope (e.g. /CHAR). Edit the upstream LOP "
                "graph so the prim isn't authored at the stage root."
            ),
        )

    return result
