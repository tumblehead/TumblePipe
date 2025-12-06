"""Variant configuration module.

Provides a unified variant system for both assets and shots,
replacing the previous render_layers concept for shots.

Variants allow multiple configurations of an entity (asset or shot)
to coexist. Every entity has an implicit 'default' variant.
Custom variants layer on top of default during resolution.
"""

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

DEFAULT_VARIANT = 'default'


def list_variants(entity_uri: Uri) -> list[str]:
    """Return variant names for an entity (asset or shot).

    The 'default' variant is always included as the first element,
    even if not explicitly defined in properties.

    Args:
        entity_uri: The entity URI (e.g., entity:/assets/CHAR/Hero
                    or entity:/shots/010/010)

    Returns:
        List of variant names, always starting with 'default'
    """
    properties = api.config.get_properties(entity_uri)
    if properties is None:
        return [DEFAULT_VARIANT]

    variants = properties.get('variants', [])

    # Ensure 'default' is always first
    if DEFAULT_VARIANT in variants:
        variants = [v for v in variants if v != DEFAULT_VARIANT]

    return [DEFAULT_VARIANT] + variants


def add_variant(entity_uri: Uri, variant_name: str) -> None:
    """Add a variant to an entity.

    Args:
        entity_uri: The entity URI
        variant_name: Name of the variant to add

    Raises:
        ValueError: If variant_name is 'default' (always exists implicitly)
        ValueError: If entity does not exist
        ValueError: If variant already exists
    """
    if variant_name == DEFAULT_VARIANT:
        raise ValueError(f"Cannot add '{DEFAULT_VARIANT}' variant - it exists implicitly")

    properties = api.config.get_properties(entity_uri)
    if properties is None:
        raise ValueError(f'Entity not found: {entity_uri}')

    variants = properties.get('variants', [])
    if variant_name in variants:
        raise ValueError(f'Variant already exists: {variant_name}')

    variants.append(variant_name)
    properties['variants'] = variants
    api.config.set_properties(entity_uri, properties)


def remove_variant(entity_uri: Uri, variant_name: str) -> None:
    """Remove a variant from an entity.

    Args:
        entity_uri: The entity URI
        variant_name: Name of the variant to remove

    Raises:
        ValueError: If variant_name is 'default' (cannot be removed)
        ValueError: If entity does not exist
        ValueError: If variant does not exist
    """
    if variant_name == DEFAULT_VARIANT:
        raise ValueError(f"Cannot remove '{DEFAULT_VARIANT}' variant")

    properties = api.config.get_properties(entity_uri)
    if properties is None:
        raise ValueError(f'Entity not found: {entity_uri}')

    variants = properties.get('variants', [])
    if variant_name not in variants:
        raise ValueError(f'Variant not found: {variant_name}')

    variants.remove(variant_name)
    properties['variants'] = variants
    api.config.set_properties(entity_uri, properties)


def has_variant(entity_uri: Uri, variant_name: str) -> bool:
    """Check if an entity has a specific variant.

    Args:
        entity_uri: The entity URI
        variant_name: Name of the variant to check

    Returns:
        True if the variant exists (including 'default'), False otherwise
    """
    return variant_name in list_variants(entity_uri)


def get_entity_type(entity_uri: Uri) -> str | None:
    """Get entity type from URI ('asset' or 'shot').

    Args:
        entity_uri: The entity URI

    Returns:
        'asset' for asset entities, 'shot' for shot entities, None otherwise
    """
    if entity_uri.purpose != 'entity':
        return None
    if len(entity_uri.segments) < 1:
        return None

    context = entity_uri.segments[0]
    if context == 'assets':
        return 'asset'
    if context == 'shots':
        return 'shot'
    return None
