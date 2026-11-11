"""RenderVar validation - ensures prim names match aov:name attributes."""

from tumblepipe.pipe.usd.render_vars import FIX, twin_problems, var_planes, var_problems
from tumblepipe.pipe.houdini import prim_queries
from .base import ValidationResult


def _get_render_var_paths(stage) -> set[str]:
    """Get all RenderVar prim paths from the stage."""
    root = stage.GetPseudoRoot()
    return set(prim_queries.list_render_vars(root))


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

    for prim_path in prim_queries.list_render_vars(root):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            continue

        # A var that names its plane is checked by the farm's rule (its
        # plane is its name; a cryptomatte is named crypto*, Karma's
        # Cryptomatte LOP naming the plane in aov:husk:name).
        if var_planes(prim):
            for problem in var_problems(prim):
                result.add_error(problem, prim_path, suggestion=FIX)
            continue

        # Get the aov:name attribute
        aov_attr = prim.GetAttribute('driver:parameters:aov:name')
        if not aov_attr.IsValid():
            result.add_warning(
                "Missing 'driver:parameters:aov:name' attribute",
                prim_path,
                suggestion=(
                    "Set the AOV name on the Render Var LOP so the prim has a "
                    "driver:parameters:aov:name attribute matching the prim name."
                ),
            )
            continue

        result.add_warning(
            "Empty 'driver:parameters:aov:name' attribute",
            prim_path,
            suggestion=(
                "Set a non-empty AOV name on the Render Var LOP (e.g. "
                "'beauty', 'N', 'albedo')."
            ),
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
                product_path,
                suggestion=(
                    "Add an 'orderedVars' relationship to the Render Product "
                    "listing each Render Var to render, in order."
                ),
            )
            continue

        # Get the targets (paths referenced by orderedVars)
        ordered_targets = ordered_vars_rel.GetTargets()
        ordered_paths = {str(target) for target in ordered_targets}

        if not ordered_paths:
            result.add_warning(
                "'orderedVars' relationship is empty",
                product_path,
                suggestion=(
                    "Populate orderedVars on the Render Product with the "
                    "Render Vars to render."
                ),
            )
            continue

        # Check for paths in orderedVars that don't exist as RenderVars
        missing_from_stage = ordered_paths - actual_render_vars
        for missing_path in sorted(missing_from_stage):
            result.add_error(
                f"orderedVars references non-existent RenderVar: {missing_path}",
                product_path,
                suggestion=(
                    "Remove the stale path from orderedVars, or re-add the "
                    "Render Var prim it points at."
                ),
            )

        # Check for RenderVars that aren't in orderedVars
        missing_from_ordered = actual_render_vars - ordered_paths
        for missing_path in sorted(missing_from_ordered):
            result.add_error(
                f"RenderVar not in orderedVars: {missing_path}",
                product_path,
                suggestion=(
                    "Add this Render Var to the Render Product's orderedVars "
                    "list (or delete the Render Var if you don't want it "
                    "rendered)."
                ),
            )

    return result


def validate_render_var_twins(root) -> ValidationResult:
    """Two RenderVars that make one AOV (the same name, any case) are an
    error: husk writes two planes of that name and keeps one, and the farm
    refuses the render (pipe.usd.render_vars). Karma's own Albedo toggle
    beside the project's ``albedo`` is the usual pair."""
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result
    for problem in twin_problems(sorted(_get_render_var_paths(stage))):
        result.add_error(problem, suggestion=FIX)
    return result
