"""RenderSettings and RenderProduct validation."""

from .base import ValidationResult


def validate_render_settings(root) -> ValidationResult:
    """Validate that RenderSettings prim exists and is properly configured.

    Checks:
    - /Render/rendersettings prim exists
    - Prim is of type RenderSettings
    - Camera relationship exists and targets a valid prim
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Check RenderSettings prim exists
    settings_prim = stage.GetPrimAtPath('/Render/rendersettings')
    if not settings_prim.IsValid():
        result.add_error(
            "RenderSettings prim not found at /Render/rendersettings"
        )
        return result

    # Check prim type
    prim_type = settings_prim.GetTypeName()
    if prim_type != 'RenderSettings':
        result.add_error(
            f"Prim at /Render/rendersettings has type '{prim_type}', expected 'RenderSettings'",
            '/Render/rendersettings'
        )

    # Check camera relationship
    camera_rel = settings_prim.GetRelationship('camera')
    if not camera_rel.IsValid():
        result.add_error(
            "RenderSettings missing 'camera' relationship",
            '/Render/rendersettings'
        )
    else:
        camera_targets = camera_rel.GetTargets()
        if not camera_targets:
            result.add_error(
                "RenderSettings 'camera' relationship has no target",
                '/Render/rendersettings'
            )
        else:
            # Verify camera target exists
            camera_path = str(camera_targets[0])
            camera_prim = stage.GetPrimAtPath(camera_path)
            if not camera_prim.IsValid():
                result.add_error(
                    f"RenderSettings camera target does not exist: {camera_path}",
                    '/Render/rendersettings'
                )

    # Check products relationship (warning only)
    products_rel = settings_prim.GetRelationship('products')
    if not products_rel.IsValid():
        result.add_warning(
            "RenderSettings missing 'products' relationship",
            '/Render/rendersettings'
        )
    else:
        products_targets = products_rel.GetTargets()
        if not products_targets:
            result.add_warning(
                "RenderSettings 'products' relationship has no targets",
                '/Render/rendersettings'
            )

    return result


def validate_render_products(root) -> ValidationResult:
    """Validate that RenderProduct prims exist and are properly configured.

    Checks:
    - /Render/Products hierarchy exists
    - At least one RenderProduct prim exists
    - Each RenderProduct has a camera relationship
    - Each RenderProduct camera target exists
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Check /Render/Products exists
    products_prim = stage.GetPrimAtPath('/Render/Products')
    if not products_prim.IsValid():
        result.add_error(
            "/Render/Products prim not found"
        )
        return result

    # Find all RenderProduct prims
    render_products = []
    for child in products_prim.GetChildren():
        if child.GetTypeName() == 'RenderProduct':
            render_products.append(child)

    if not render_products:
        result.add_error(
            "No RenderProduct prims found under /Render/Products",
            '/Render/Products'
        )
        return result

    # Validate each RenderProduct
    for product in render_products:
        product_path = str(product.GetPath())

        # Check camera relationship
        camera_rel = product.GetRelationship('camera')
        if not camera_rel.IsValid():
            result.add_error(
                "RenderProduct missing 'camera' relationship",
                product_path
            )
            continue

        camera_targets = camera_rel.GetTargets()
        if not camera_targets:
            result.add_error(
                "RenderProduct 'camera' relationship has no target",
                product_path
            )
            continue

        # Verify camera target exists
        camera_path = str(camera_targets[0])
        camera_prim = stage.GetPrimAtPath(camera_path)
        if not camera_prim.IsValid():
            result.add_error(
                f"RenderProduct camera target does not exist: {camera_path}",
                product_path
            )

        # Check productName attribute (warning only)
        product_name_attr = product.GetAttribute('productName')
        if not product_name_attr.IsValid():
            result.add_warning(
                "RenderProduct missing 'productName' attribute",
                product_path
            )
        else:
            product_name = product_name_attr.Get()
            if not product_name:
                result.add_warning(
                    "RenderProduct 'productName' attribute is empty",
                    product_path
                )

    return result
