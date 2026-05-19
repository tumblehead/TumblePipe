"""RenderSettings and RenderProduct validation."""

from .base import ValidationResult


_SUGG_ADD_RENDERSETTINGS = (
    "Add a Render Settings LOP that creates /Render/rendersettings. The shot "
    "render template normally provides this; check the shot's render layer."
)
_SUGG_FIX_RS_CAMERA = (
    "Set Camera Path on the Render Settings LOP to a valid /cameras/... prim."
)
_SUGG_ADD_PRODUCTS = (
    "Add at least one Render Product LOP under /Render/Products and reference "
    "it from Render Settings' Products input."
)
_SUGG_FIX_PRODUCT_CAMERA = (
    "Set Camera Path on the Render Product LOP. Each product needs a camera."
)


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
            "RenderSettings prim not found at /Render/rendersettings",
            suggestion=_SUGG_ADD_RENDERSETTINGS,
        )
        return result

    # Check prim type
    prim_type = settings_prim.GetTypeName()
    if prim_type != 'RenderSettings':
        result.add_error(
            f"Prim at /Render/rendersettings has type '{prim_type}', expected 'RenderSettings'",
            '/Render/rendersettings',
            suggestion=(
                "Re-author /Render/rendersettings via a Render Settings LOP "
                "(or set its type to UsdRenderSettings on a Configure Primitive)."
            ),
        )

    # Check camera relationship
    camera_rel = settings_prim.GetRelationship('camera')
    if not camera_rel.IsValid():
        result.add_error(
            "RenderSettings missing 'camera' relationship",
            '/Render/rendersettings',
            suggestion=_SUGG_FIX_RS_CAMERA,
        )
    else:
        camera_targets = camera_rel.GetTargets()
        if not camera_targets:
            result.add_error(
                "RenderSettings 'camera' relationship has no target",
                '/Render/rendersettings',
                suggestion=_SUGG_FIX_RS_CAMERA,
            )
        else:
            # Verify camera target exists
            camera_path = str(camera_targets[0])
            camera_prim = stage.GetPrimAtPath(camera_path)
            if not camera_prim.IsValid():
                result.add_error(
                    f"RenderSettings camera target does not exist: {camera_path}",
                    '/Render/rendersettings',
                    suggestion=(
                        "The Camera Path on Render Settings doesn't resolve. "
                        "Update it or add the missing camera to the stage."
                    ),
                )

    # Check products relationship (warning only)
    products_rel = settings_prim.GetRelationship('products')
    if not products_rel.IsValid():
        result.add_warning(
            "RenderSettings missing 'products' relationship",
            '/Render/rendersettings',
            suggestion=_SUGG_ADD_PRODUCTS,
        )
    else:
        products_targets = products_rel.GetTargets()
        if not products_targets:
            result.add_warning(
                "RenderSettings 'products' relationship has no targets",
                '/Render/rendersettings',
                suggestion=_SUGG_ADD_PRODUCTS,
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
            "/Render/Products prim not found",
            suggestion=_SUGG_ADD_PRODUCTS,
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
            '/Render/Products',
            suggestion=_SUGG_ADD_PRODUCTS,
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
                product_path,
                suggestion=_SUGG_FIX_PRODUCT_CAMERA,
            )
            continue

        camera_targets = camera_rel.GetTargets()
        if not camera_targets:
            result.add_error(
                "RenderProduct 'camera' relationship has no target",
                product_path,
                suggestion=_SUGG_FIX_PRODUCT_CAMERA,
            )
            continue

        # Verify camera target exists
        camera_path = str(camera_targets[0])
        camera_prim = stage.GetPrimAtPath(camera_path)
        if not camera_prim.IsValid():
            result.add_error(
                f"RenderProduct camera target does not exist: {camera_path}",
                product_path,
                suggestion=(
                    "The Render Product's Camera Path doesn't resolve. Update "
                    "it on the Render Product LOP or add the missing camera."
                ),
            )

        # Check productName attribute (warning only)
        product_name_attr = product.GetAttribute('productName')
        if not product_name_attr.IsValid():
            result.add_warning(
                "RenderProduct missing 'productName' attribute",
                product_path,
                suggestion=(
                    "Set the Product Name (output filename) on the Render "
                    "Product LOP."
                ),
            )
        else:
            product_name = product_name_attr.Get()
            if not product_name:
                result.add_warning(
                    "RenderProduct 'productName' attribute is empty",
                    product_path,
                    suggestion=(
                        "Set a non-empty Product Name on the Render Product LOP, "
                        "e.g. '/path/to/<aov>.exr'."
                    ),
                )

    return result
