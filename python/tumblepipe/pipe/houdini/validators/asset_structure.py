"""Asset structure validation for USD stages."""

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from .base import ValidationResult


def _validate_asset_base(root, context: dict | None, child_prim_name: str) -> ValidationResult:
    """Common validation for asset structure.

    Convention (matches create_asset_model HDA):
    - Asset prim is of type Xform (transformable so it can be placed in shots)
    - Required child prim ('geo', 'blshp' or 'mtl') is of type Scope (grouping)
    - Mesh content lives inside the Scope as Mesh prims

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri'
        child_prim_name: Name of required child prim ('geo', 'blshp' or 'mtl')

    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()

    # Get entity URI from context
    entity_uri_str = context.get('entity_uri') if context else None
    if not entity_uri_str:
        result.add_warning(
            f"No entity URI provided - cannot validate {child_prim_name} structure",
            suggestion=(
                "Set the export_layer's 'Entity' parameter to a specific URI "
                "(or 'from_context' inside a saved workfile) so the validator "
                "knows which prim path to expect."
            ),
        )
        return result

    # Parse entity URI
    entity_uri = Uri.parse_unsafe(entity_uri_str)

    # Convert to prim path
    asset_prim_path = uri_to_prim_path(entity_uri)

    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Check asset prim exists
    asset_prim = stage.GetPrimAtPath(asset_prim_path)
    if not asset_prim.IsValid():
        result.add_error(
            f"Asset prim not found at path: {asset_prim_path}",
            asset_prim_path,
            suggestion=(
                "Author the asset prim at this path. The create_asset_model HDA "
                "sets up the expected /CATEGORY/Asset hierarchy automatically — "
                "check the export_layer's Entity URI matches the stage."
            ),
        )
        return result

    # Check asset prim type is Xform (project convention — asset roots are
    # transformable so they can be placed/instanced in shots).
    prim_type = asset_prim.GetTypeName()
    if prim_type != 'Xform':
        result.add_error(
            f"Asset prim must be of type 'Xform', found '{prim_type}'",
            asset_prim_path,
            suggestion=(
                "Set the prim's type to Xform via a Configure Primitive LOP "
                "with type=UsdGeomXform, or re-author through the "
                "create_asset_model HDA which produces the correct hierarchy."
            ),
        )

    # Check child prim exists
    child_prim_path = f"{asset_prim_path}/{child_prim_name}"
    child_prim = stage.GetPrimAtPath(child_prim_path)
    if not child_prim.IsValid():
        result.add_error(
            f"Missing required '{child_prim_name}' child prim",
            child_prim_path,
            suggestion=(
                f"Create a '{child_prim_name}' Scope under the asset. The "
                "create_asset_model HDA produces this child automatically; if "
                "you authored the stage manually, add a Configure Primitive "
                f"LOP that creates {child_prim_name} with type=UsdGeomScope."
            ),
        )
        return result

    # Check child prim type is Scope
    child_type = child_prim.GetTypeName()
    if child_type != 'Scope':
        result.add_error(
            f"'{child_prim_name}' prim must be of type 'Scope', found '{child_type}'",
            child_prim_path,
            suggestion=(
                f"Set the type of '{child_prim_name}' to UsdGeomScope via a "
                "Configure Primitive LOP. Use the set_kinds LOP for the full "
                "asset hierarchy in one shot."
            ),
        )

    return result


def validate_model_structure(root, context: dict | None = None) -> ValidationResult:
    """Validate model department asset structure.

    For model exports, validates:
    - Asset prim exists at path derived from entity URI (e.g., /CHAR/mom)
    - Asset prim is of type Xform
    - 'geo' child prim exists (e.g., /CHAR/mom/geo)
    - 'geo' prim is of type Scope

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri' for the asset

    Returns:
        ValidationResult with any structure errors
    """
    return _validate_asset_base(root, context, 'geo')


def validate_blendshape_structure(root, context: dict | None = None) -> ValidationResult:
    """Validate blendshape department asset structure.

    For blendshape exports, validates:
    - Asset prim exists at path derived from entity URI (e.g., /CHAR/Frog)
    - Asset prim is of type Xform
    - 'blshp' child prim exists (e.g., /CHAR/Frog/blshp)
    - 'blshp' prim is of type Scope

    Blendshapes export under <asset>/blshp/ (the create_blendshapes SOP sets
    pathprefix to '{prim_path}/blshp/'), so the required child prim is 'blshp',
    not 'geo' as for the model department.

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri' for the asset

    Returns:
        ValidationResult with any structure errors
    """
    return _validate_asset_base(root, context, 'blshp')


def validate_lookdev_structure(root, context: dict | None = None) -> ValidationResult:
    """Validate lookdev department asset structure.

    For lookdev exports, validates:
    - Asset prim exists at path derived from entity URI (e.g., /CHAR/mom)
    - Asset prim is of type Xform
    - 'mtl' child prim exists (e.g., /CHAR/mom/mtl)
    - 'mtl' prim is of type Scope

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri' for the asset

    Returns:
        ValidationResult with any structure errors
    """
    return _validate_asset_base(root, context, 'mtl')
