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

    Falls back to a depth heuristic if the schema API is unavailable (older
    per-project config_convention.py): both terminal entity types live at
    depth 3 (assets/<category>/<asset>, shots/<sequence>/<shot>).
    """
    try:
        schema_uri = config.get_entity_schema_uri(uri)
        if schema_uri is None:
            return False
        return len(config.get_child_schemas(schema_uri)) == 0
    except Exception:
        return len(uri.segments) == 3
