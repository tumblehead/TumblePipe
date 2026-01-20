"""Asset structure validation for USD stages."""

from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.util import uri_to_prim_path
from .base import ValidationResult


def _validate_asset_base(root, context: dict | None, child_prim_name: str) -> ValidationResult:
    """Common validation for asset structure.

    Validates:
    - Asset prim exists at path derived from entity URI
    - Asset prim is of type Scope
    - Required child prim exists (geo or mat)
    - Child prim is of type Scope

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri'
        child_prim_name: Name of required child prim ('geo' or 'mat')

    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()

    # Get entity URI from context
    entity_uri_str = context.get('entity_uri') if context else None
    if not entity_uri_str:
        result.add_warning(
            f"No entity URI provided - cannot validate {child_prim_name} structure"
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
            asset_prim_path
        )
        return result

    # Check asset prim type is Scope
    prim_type = asset_prim.GetTypeName()
    if prim_type != 'Scope':
        result.add_error(
            f"Asset prim must be of type 'Scope', found '{prim_type}'",
            asset_prim_path
        )

    # Check child prim exists
    child_prim_path = f"{asset_prim_path}/{child_prim_name}"
    child_prim = stage.GetPrimAtPath(child_prim_path)
    if not child_prim.IsValid():
        result.add_error(
            f"Missing required '{child_prim_name}' child prim",
            child_prim_path
        )
        return result

    # Check child prim type is Scope
    child_type = child_prim.GetTypeName()
    if child_type != 'Scope':
        result.add_error(
            f"'{child_prim_name}' prim must be of type 'Scope', found '{child_type}'",
            child_prim_path
        )

    return result


def validate_model_structure(root, context: dict | None = None) -> ValidationResult:
    """Validate model department asset structure.

    For model exports, validates:
    - Asset prim exists at path derived from entity URI (e.g., /CHAR/mom)
    - Asset prim is of type Scope
    - 'geo' child prim exists (e.g., /CHAR/mom/geo)
    - 'geo' prim is of type Scope

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri' for the asset

    Returns:
        ValidationResult with any structure errors
    """
    return _validate_asset_base(root, context, 'geo')


def validate_lookdev_structure(root, context: dict | None = None) -> ValidationResult:
    """Validate lookdev department asset structure.

    For lookdev exports, validates:
    - Asset prim exists at path derived from entity URI (e.g., /CHAR/mom)
    - Asset prim is of type Scope
    - 'mat' child prim exists (e.g., /CHAR/mom/mat)
    - 'mat' prim is of type Scope

    Args:
        root: USD stage pseudo root prim
        context: Optional dict containing 'entity_uri' for the asset

    Returns:
        ValidationResult with any structure errors
    """
    return _validate_asset_base(root, context, 'mat')
