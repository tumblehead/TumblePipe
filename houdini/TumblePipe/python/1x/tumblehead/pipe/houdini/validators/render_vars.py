"""RenderVar validation - ensures prim names match aov:name attributes."""

from tumblehead.pipe.houdini import util
from .base import ValidationResult


def _get_render_var_paths(stage) -> set[str]:
    """Get all RenderVar prim paths from the stage."""
    root = stage.GetPseudoRoot()
    return set(util.list_render_vars(root))


def _get_render_products(stage) -> list:
    """Get all RenderProduct prims from /Render/Products."""
    products = []
    products_prim = stage.GetPrimAtPath('/Render/Products')
    if not products_prim.IsValid():
        return products

    for child in products_prim.GetChildren():
        if child.GetTypeName() == 'RenderProduct':
            products.append(child)

    return products


def validate_render_var_names(root) -> ValidationResult:
    """Check each RenderVar prim name matches driver:parameters:aov:name property.

    This validator catches authoring mistakes where the prim path name doesn't
    match the aov:name attribute, which would cause AOV naming mismatches at
    render time.

    Note: Required AOV checks (beauty, normal, albedo) are done at render
    submission time when the full composed stage is available.
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    for prim_path in util.list_render_vars(root):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            continue

        # Extract prim name from path
        prim_name = prim_path.rsplit('/', 1)[-1]

        # Get the aov:name attribute
        aov_attr = prim.GetAttribute('driver:parameters:aov:name')
        if not aov_attr.IsValid():
            result.add_warning(f"Missing 'driver:parameters:aov:name' attribute", prim_path)
            continue

        aov_name = aov_attr.Get()
        if aov_name is None:
            result.add_warning(f"Empty 'driver:parameters:aov:name' attribute", prim_path)
            continue

        # Compare names (case-insensitive to catch common issues)
        if prim_name.lower() != str(aov_name).lower():
            result.add_error(
                f"Prim name '{prim_name}' does not match aov:name '{aov_name}'",
                prim_path
            )

    return result


def validate_ordered_vars(root) -> ValidationResult:
    """Validate that orderedVars relationship matches actual RenderVar prims.

    This validator ensures that the orderedVars relationship on RenderProduct
    prims contains exactly the RenderVar prims that exist in the stage -
    no more, no less.

    Catches errors like:
    - User adds a new RenderVar but forgets to add it to orderedVars
    - User removes a RenderVar but leaves stale reference in orderedVars
    - orderedVars contains paths that don't exist
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Get all actual RenderVar paths from the stage
    actual_render_vars = _get_render_var_paths(stage)

    if not actual_render_vars:
        # No RenderVars in stage - nothing to validate
        return result

    # Get all RenderProduct prims
    products = _get_render_products(stage)

    if not products:
        # No RenderProducts to validate - render_products validator handles this
        return result

    for product in products:
        product_path = str(product.GetPath())

        # Get the orderedVars relationship
        ordered_vars_rel = product.GetRelationship('orderedVars')
        if not ordered_vars_rel.IsValid():
            result.add_warning(
                "Missing 'orderedVars' relationship",
                product_path
            )
            continue

        # Get the targets (paths referenced by orderedVars)
        ordered_targets = ordered_vars_rel.GetTargets()
        ordered_paths = {str(target) for target in ordered_targets}

        if not ordered_paths:
            result.add_warning(
                "'orderedVars' relationship is empty",
                product_path
            )
            continue

        # Check for paths in orderedVars that don't exist as RenderVars
        missing_from_stage = ordered_paths - actual_render_vars
        for missing_path in sorted(missing_from_stage):
            result.add_error(
                f"orderedVars references non-existent RenderVar: {missing_path}",
                product_path
            )

        # Check for RenderVars that aren't in orderedVars
        missing_from_ordered = actual_render_vars - ordered_paths
        for missing_path in sorted(missing_from_ordered):
            result.add_error(
                f"RenderVar not in orderedVars: {missing_path}",
                product_path
            )

    return result
