from dataclasses import dataclass
from typing import Any

from tumblepipe.util.uri import Uri


@dataclass
class SchemaMigration:
    """Represents a schema migration with affected entities"""
    schema_uri: Uri
    additions: dict[str, tuple[Any, list[Uri]]]  # field -> (default, entities)
    removals: dict[str, list[tuple[Uri, Any]]]


def detect_field_changes(old_props: dict, new_props: dict) -> tuple[set[str], set[str]]:
    """
    Compare old and new schema properties to find field changes.
    Schema properties ARE the fields directly (flattened format).

    Returns:
        (added_fields, removed_fields) - sets of field names
    """
    old_fields = set(old_props.keys())
    new_fields = set(new_props.keys())
    added = new_fields - old_fields
    removed = old_fields - new_fields
    return added, removed


def collect_entities_by_schema(config, schema_uri: Uri) -> list[tuple[Uri, dict]]:
    """
    Find all entities whose schema (derived from position) is ``schema_uri``.

    The schema URI is no longer stored per node — it's derived from each
    entity's position — so match by derivation rather than a stored string.
    Walks every node (including intermediate category/sequence entities, not
    just leaves) so a category-level schema edit still finds its entities.

    Args:
        config: The ConfigConvention (for get_entity_schema_uri + cache)
        schema_uri: The schema URI being edited (e.g.
            ``schemas:/entity/assets/category/asset``)

    Returns:
        List of (entity_uri, own_properties) tuples
    """
    if not schema_uri.segments:
        return []
    purpose = schema_uri.segments[0]
    results = []

    def walk(data: dict, uri: Uri):
        if config.get_entity_schema_uri(uri) == schema_uri:
            results.append((uri, data.get('properties', {})))
        for name, child in data.get('children', {}).items():
            walk(child, uri / name)

    purpose_data = config.root(purpose) or {}
    base_uri = Uri.parse_unsafe(f'{purpose}:/')
    for name, child in purpose_data.get('children', {}).items():
        walk(child, base_uri / name)

    return results


def build_migration(
    config,
    schema_uri: Uri,
    old_props: dict,
    new_props: dict
) -> SchemaMigration | None:
    """
    Build a migration plan for schema changes.

    Schema properties ARE the fields directly (flattened format):
        {"frame_start": 1001, "name": "untitled"}

    Args:
        config: The ConfigConvention (for entity discovery + schema derivation)
        schema_uri: The schema URI being modified
        old_props: The original schema properties (which ARE the fields)
        new_props: The new schema properties (which ARE the fields)

    Returns:
        SchemaMigration if changes detected, None otherwise
    """
    added, removed = detect_field_changes(old_props, new_props)

    if not added and not removed:
        return None

    entities = collect_entities_by_schema(config, schema_uri)

    additions = {}
    for field_name in added:
        default = new_props.get(field_name)
        entity_uris = [uri for uri, _ in entities]
        additions[field_name] = (default, entity_uris)

    removals = {}
    for field_name in removed:
        affected = []
        for uri, own_props in entities:
            # Only entities that explicitly set the field (own props) are
            # affected by its removal.
            if field_name in own_props:
                affected.append((uri, own_props[field_name]))
        if affected:
            removals[field_name] = affected

    return SchemaMigration(schema_uri, additions, removals)


def apply_migration(adapter, migration: SchemaMigration) -> None:
    """
    Execute the migration - apply additions and removals to affected entities.

    Args:
        adapter: DatabaseAdapter instance
        migration: The SchemaMigration to apply
    """
    for field_name, (default, entities) in migration.additions.items():
        for uri in entities:
            props = adapter.lookup_properties(uri)
            if field_name not in props:
                props[field_name] = default
                adapter.save_properties(uri, props)

    for field_name, affected in migration.removals.items():
        for uri, _ in affected:
            props = adapter.lookup_properties(uri)
            if field_name in props:
                del props[field_name]
                adapter.save_properties(uri, props)
