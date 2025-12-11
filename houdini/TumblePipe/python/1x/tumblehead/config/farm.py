"""Farm configuration for render pools and priority presets."""

from dataclasses import dataclass

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

FARM_URI = Uri.parse_unsafe('config:/farm')
POOLS_URI = FARM_URI / 'pools'
PRIORITIES_URI = FARM_URI / 'priorities'
ENTITY_ROOT_URI = Uri.parse_unsafe('entity:/')


@dataclass(frozen=True)
class Pool:
    name: str
    description: str


@dataclass(frozen=True)
class PriorityPreset:
    name: str
    value: int


def list_pools() -> list[Pool]:
    """List all available render pools from entity root config."""
    try:
        props = api.config.get_properties(ENTITY_ROOT_URI)
        if props:
            farm = props.get('farm', {})
            pools = farm.get('pools', [])
            if pools:
                return [Pool(name=p, description='') for p in pools]
    except Exception:
        pass
    # Fallback to default
    return [Pool(name='general', description='')]


def get_default_pool() -> str:
    """Get the default pool name from entity root config."""
    try:
        props = api.config.get_properties(ENTITY_ROOT_URI)
        if props:
            farm = props.get('farm', {})
            return farm.get('default_pool', 'general')
    except Exception:
        pass
    return 'general'


def add_pool(name: str, description: str = ''):
    """Add a new render pool."""
    pool_uri = POOLS_URI / name
    properties = api.config.get_properties(pool_uri)
    if properties is not None:
        raise ValueError(f'Pool already exists: {name}')
    api.config.set_properties(pool_uri, dict(
        description=description
    ))


def remove_pool(name: str):
    """Remove a render pool."""
    pool_uri = POOLS_URI / name
    api.config.remove_entity(pool_uri)


def set_default_pool(name: str):
    """Set the default pool."""
    properties = api.config.get_properties(POOLS_URI)
    if properties is None:
        properties = {}
    properties['default'] = name
    api.config.set_properties(POOLS_URI, properties)


def list_priority_presets() -> list[PriorityPreset]:
    """List all priority presets."""
    farm_data = api.config.cache.get('farm', {})
    priorities_data = farm_data.get('children', {}).get('priorities', {})
    priorities_children = priorities_data.get('children', {})
    return [
        PriorityPreset(
            name=preset_name,
            value=preset_data.get('properties', {}).get('value', 50)
        )
        for preset_name, preset_data in priorities_children.items()
    ]


def get_default_priority() -> int:
    """Get the default priority value."""
    farm_data = api.config.cache.get('farm', {})
    priorities_data = farm_data.get('children', {}).get('priorities', {})
    default_name = priorities_data.get('properties', {}).get('default', 'normal')
    # Find the preset with that name
    priorities_children = priorities_data.get('children', {})
    preset_data = priorities_children.get(default_name, {})
    return preset_data.get('properties', {}).get('value', 50)


def add_priority_preset(name: str, value: int):
    """Add a new priority preset."""
    if not 0 <= value <= 100:
        raise ValueError('Priority value must be between 0 and 100')
    preset_uri = PRIORITIES_URI / name
    properties = api.config.get_properties(preset_uri)
    if properties is not None:
        raise ValueError(f'Priority preset already exists: {name}')
    api.config.set_properties(preset_uri, dict(
        value=value
    ))


def remove_priority_preset(name: str):
    """Remove a priority preset."""
    preset_uri = PRIORITIES_URI / name
    api.config.remove_entity(preset_uri)


def set_default_priority_preset(name: str):
    """Set the default priority preset."""
    properties = api.config.get_properties(PRIORITIES_URI)
    if properties is None:
        properties = {}
    properties['default'] = name
    api.config.set_properties(PRIORITIES_URI, properties)


def get_entity_farm_settings(entity_uri: Uri) -> dict:
    """Get resolved farm settings for an entity (with inheritance).

    Returns farm settings merged from root to entity, with child values
    overriding parent values. This allows per-entity overrides of pool,
    priority, tile_count, batch_size, and timeout settings.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)

    Returns:
        Dict with farm settings, or empty dict if none found.
    """
    props = api.config.get_properties(entity_uri)
    return props.get('farm', {}) if props else {}
