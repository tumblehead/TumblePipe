"""Helpers for reasoning about entities in the config database."""

from tumblepipe.util.uri import Uri


def is_terminal_entity(config, uri: Uri) -> bool:
    """True if ``uri`` is an actual leaf-type entity (an asset/shot), not an
    intermediate category/sequence node.

    ``list_entities(closure=True)`` returns every node with no children, so an
    empty category (e.g. ``entity:/assets/CHAR`` before any asset is created
    under it) is returned as if it were an asset - which is why pickers showed
    the seeded CHAR/PROP/SET categories and "no entities". We key off the
    schema, the single source of truth for an entity's type: a real asset's
    schema node (``schemas:/entity/assets/category/asset``) has no children,
    whereas a category's (``.../category``) still does.

    A project whose config lacks the schema API is unmigrated — run
    scripts/migrate_config.py on it; guessing by URI depth here silently
    misclassified entities.
    """
    schema_uri = config.get_entity_schema_uri(uri)
    if schema_uri is None:
        return False
    return len(config.get_child_schemas(schema_uri)) == 0


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
