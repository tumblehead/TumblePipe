"""Per-entity render settings, resolved through the config hierarchy."""

from tumblepipe.api import api
from tumblepipe.config import render_config
from tumblepipe.util.uri import Uri


def get_entity_render_settings(entity_uri: Uri) -> dict:
    """Get resolved render settings for an entity (with inheritance).

    Returns render settings merged from root to entity, with child values
    overriding parent values. This allows per-entity overrides of resolution,
    samples, ray limits, and other render parameters.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)

    Returns:
        Dict with render settings, or empty dict if none found.
    """
    props = api.config.get_properties(entity_uri)
    return props.get('render', {}) if props else {}


def get_pinned_render_settings(entity_uri: Uri, keys) -> dict:
    """The ``render`` keys of ``keys`` an entity level pins for
    ``entity_uri``, as they resolve: the project ``entity:/``, each node
    down to the entity itself, strongest last. No schema defaults: a key
    only the schema holds is not here.

    The merge is ``config.render_config.resolve``'s (the store's), so a
    level holding ``render`` as something other than an object (a broken
    hand edit) drops what the levels before it pinned."""
    levels = []
    for depth in range(len(entity_uri.segments) + 1):
        uri = Uri.parse(f"{entity_uri.purpose}:/{'/'.join(entity_uri.segments[:depth])}")
        levels.append((str(uri), api.config.get_own_properties(uri) or {}))
    names = [f'{render_config.RENDER}.{key}' for key in keys]
    found = render_config.resolve(levels, names)
    return {
        render_config.split(name)[1]: resolved.value
        for name, resolved in found.items() if resolved.source
    }
