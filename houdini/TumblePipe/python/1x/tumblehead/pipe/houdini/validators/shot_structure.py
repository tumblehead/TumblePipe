"""Shot stage structure validation for USD stages."""

from .base import ValidationResult


# Allowed root prim names (exact matches)
ALLOWED_ROOT_PRIMS = {'collections', 'lights', 'cameras', 'Render', 'scene', '_METADATA'}


def _is_asset_category_prim(prim) -> bool:
    """Check if a prim looks like an asset category (e.g., /CHAR with /CHAR/mom child).

    Asset categories are Scope prims that contain child Scope prims
    representing individual assets.
    """
    # Asset categories are Scope prims
    if prim.GetTypeName() != 'Scope':
        return False
    # Asset categories have child prims that are also Scopes (individual assets)
    for child in prim.GetChildren():
        if child.GetTypeName() == 'Scope':
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
    - /_METADATA - Internal tracking

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
            f"/{prim_name}"
        )

    return result
