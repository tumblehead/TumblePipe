"""RenderSettings and RenderProduct validation.

Nothing here assumes a prim path. Where the settings and products live is
project-owned — 7 of 13 live projects keep them under `/scene/Render`, the
rest at `/Render` — so a validator that looked at `/Render/rendersettings`
reported "not found" on a stage that has one, and skipped the render-camera
check entirely on the projects most likely to need it. See
`pipe.usd.find_render_settings_prim_path` and
docs/composition.md#where-the-render-settings-live.
"""

from tumblepipe.pipe.usd import (
    RenderSettingsError,
    find_render_settings_prim_path,
)

from .base import ValidationResult


_SUGG_ADD_RENDERSETTINGS = (
    "Add a Render Settings LOP that creates the shot's RenderSettings prim. "
    "The shot render template normally provides this; check the shot's "
    "render layer."
)
_SUGG_FIX_RS_CAMERA = (
    "Set Camera Path on the Render Settings LOP to a valid camera prim."
)
_SUGG_ADD_PRODUCTS = (
    "Add at least one Render Product LOP and reference it from Render "
    "Settings' Products input."
)


def _render_products(stage) -> list:
    """Every RenderProduct prim on *stage*, wherever the project puts them."""
    from pxr import Usd, UsdRender

    return [
        prim
        for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
        if prim.IsA(UsdRender.Product)
    ]
_SUGG_FIX_PRODUCT_CAMERA = (
    "Set Camera Path on the Render Settings LOP so every product inherits it, "
    "or on this Render Product LOP to override it for this product alone."
)


def validate_render_settings(root) -> ValidationResult:
    """Validate that RenderSettings prim exists and is properly configured.

    Checks:
    - the stage carries exactly one RenderSettings prim
    - Camera relationship exists and targets a valid prim
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Where the settings prim is, asked of the stage. Zero and many are both
    # errors the artist has to resolve: husk picks by the same question.
    try:
        settings_path = find_render_settings_prim_path(stage)
    except RenderSettingsError as error:
        result.add_error(str(error), suggestion=_SUGG_ADD_RENDERSETTINGS)
        return result
    settings_prim = stage.GetPrimAtPath(settings_path)

    # Check camera relationship
    camera_rel = settings_prim.GetRelationship('camera')
    if not camera_rel.IsValid():
        result.add_error(
            "RenderSettings missing 'camera' relationship",
            settings_path,
            suggestion=_SUGG_FIX_RS_CAMERA,
        )
    else:
        camera_targets = camera_rel.GetTargets()
        if not camera_targets:
            result.add_error(
                "RenderSettings 'camera' relationship has no target",
                settings_path,
                suggestion=_SUGG_FIX_RS_CAMERA,
            )
        else:
            # Verify camera target exists
            camera_path = str(camera_targets[0])
            camera_prim = stage.GetPrimAtPath(camera_path)
            if not camera_prim.IsValid():
                result.add_error(
                    f"RenderSettings camera target does not exist: {camera_path}",
                    settings_path,
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
            settings_path,
            suggestion=_SUGG_ADD_PRODUCTS,
        )
    else:
        products_targets = products_rel.GetTargets()
        if not products_targets:
            result.add_warning(
                "RenderSettings 'products' relationship has no targets",
                settings_path,
                suggestion=_SUGG_ADD_PRODUCTS,
            )

    return result


def validate_render_products(root) -> ValidationResult:
    """Validate that RenderProduct prims exist and are properly configured.

    Checks:
    - At least one RenderProduct prim exists, wherever it lives
    - Each RenderProduct renders through a camera that exists — its own if it
      overrides one, otherwise the RenderSettings'
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Found by type, not under an assumed /Render/Products scope: the project
    # owns that hierarchy too, and the template nests products one level
    # deeper (<settings root>/Render/Products/renderproduct).
    render_products = _render_products(stage)
    if not render_products:
        result.add_error(
            "No RenderProduct prims found on the stage",
            suggestion=_SUGG_ADD_PRODUCTS,
        )
        return result

    # A product's `camera` is an *override* of the settings' camera, not a
    # requirement — UsdRenderProduct inherits it when absent, which is how
    # every live project template is authored. Demanding one here failed
    # every stage the pipeline itself produces, so it is only an error when
    # the settings prim does not supply one either.
    try:
        settings_prim = stage.GetPrimAtPath(
            find_render_settings_prim_path(stage)
        )
        settings_camera = bool(
            settings_prim.GetRelationship('camera').GetTargets()
        )
    except RenderSettingsError:
        settings_camera = False

    # Validate each RenderProduct
    for product in render_products:
        product_path = str(product.GetPath())

        # Check camera relationship
        camera_rel = product.GetRelationship('camera')
        camera_targets = camera_rel.GetTargets() if camera_rel.IsValid() else []
        if not camera_targets:
            # No override. Fine when there is a settings camera to inherit;
            # the productName check below still runs either way.
            if not settings_camera:
                result.add_error(
                    "RenderProduct renders through no camera, and the "
                    "RenderSettings names none to inherit",
                    product_path,
                    suggestion=_SUGG_FIX_PRODUCT_CAMERA,
                )
        else:
            # Verify the overriding camera target exists
            camera_path = str(camera_targets[0])
            camera_prim = stage.GetPrimAtPath(camera_path)
            if not camera_prim.IsValid():
                result.add_error(
                    f"RenderProduct camera target does not exist: {camera_path}",
                    product_path,
                    suggestion=(
                        "The Render Product's Camera Path doesn't resolve. "
                        "Update it on the Render Product LOP or add the "
                        "missing camera."
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
