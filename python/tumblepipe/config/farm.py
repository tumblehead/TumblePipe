"""Farm configuration for render pools and priority presets.

Pools and priority presets are stored as properties on the entity root
(``entity:/``) under the ``farm`` sub-object. The same shape is what
``get_entity_farm_settings`` cascades to per-entity overrides, so readers
and writers share one canonical store.
"""

from dataclasses import dataclass

from tumblepipe.api import default_client
from tumblepipe.util.uri import Uri

api = default_client()

ENTITY_ROOT_URI = Uri.parse_unsafe('entity:/')


@dataclass(frozen=True)
class Pool:
    name: str


@dataclass(frozen=True)
class PriorityPreset:
    name: str
    value: int


def _get_root_farm() -> dict:
    props = api.config.get_properties(ENTITY_ROOT_URI) or {}
    farm = props.get('farm')
    return dict(farm) if isinstance(farm, dict) else {}


def _write_root_farm(farm: dict) -> None:
    props = api.config.get_properties(ENTITY_ROOT_URI) or {}
    props = dict(props)
    props['farm'] = farm
    api.config.set_properties(ENTITY_ROOT_URI, props)


def list_pools() -> list[Pool]:
    """List all available render pools."""
    pools = _get_root_farm().get('pools', [])
    return [Pool(name=p) for p in pools]


def get_default_pool() -> str:
    """Get the default pool name."""
    return _get_root_farm().get('default_pool', '') or 'general'


def add_pool(name: str) -> None:
    """Add a new render pool."""
    farm = _get_root_farm()
    pools = list(farm.get('pools', []))
    if name in pools:
        raise ValueError(f'Pool already exists: {name}')
    pools.append(name)
    farm['pools'] = pools
    _write_root_farm(farm)


def remove_pool(name: str) -> None:
    """Remove a render pool."""
    farm = _get_root_farm()
    pools = list(farm.get('pools', []))
    if name not in pools:
        raise ValueError(f'Pool does not exist: {name}')
    pools.remove(name)
    farm['pools'] = pools
    if farm.get('default_pool') == name:
        farm['default_pool'] = ''
    _write_root_farm(farm)


def set_default_pool(name: str) -> None:
    """Set the default pool."""
    farm = _get_root_farm()
    pools = farm.get('pools', [])
    if name not in pools:
        raise ValueError(f'Pool does not exist: {name}')
    farm['default_pool'] = name
    _write_root_farm(farm)


def list_priority_presets() -> list[PriorityPreset]:
    """List all priority presets."""
    presets = _get_root_farm().get('priorities', {})
    if not isinstance(presets, dict):
        return []
    return [PriorityPreset(name=n, value=v) for n, v in presets.items()]


def get_default_priority() -> int:
    """Get the default priority value, falling back to the schema default."""
    farm = _get_root_farm()
    presets = farm.get('priorities', {}) if isinstance(farm.get('priorities'), dict) else {}
    default_name = farm.get('default_priority')
    if default_name and default_name in presets:
        return presets[default_name]
    # Fall back to the entity root's flat farm.priority schema default.
    return farm.get('priority', 50)


def add_priority_preset(name: str, value: int) -> None:
    """Add a new priority preset."""
    if not 0 <= value <= 100:
        raise ValueError('Priority value must be between 0 and 100')
    farm = _get_root_farm()
    presets = dict(farm.get('priorities', {}) or {})
    if name in presets:
        raise ValueError(f'Priority preset already exists: {name}')
    presets[name] = value
    farm['priorities'] = presets
    _write_root_farm(farm)


def remove_priority_preset(name: str) -> None:
    """Remove a priority preset."""
    farm = _get_root_farm()
    presets = dict(farm.get('priorities', {}) or {})
    if name not in presets:
        raise ValueError(f'Priority preset does not exist: {name}')
    del presets[name]
    farm['priorities'] = presets
    if farm.get('default_priority') == name:
        farm['default_priority'] = ''
    _write_root_farm(farm)


def set_default_priority_preset(name: str) -> None:
    """Set the default priority preset."""
    farm = _get_root_farm()
    presets = farm.get('priorities', {}) or {}
    if name not in presets:
        raise ValueError(f'Priority preset does not exist: {name}')
    farm['default_priority'] = name
    _write_root_farm(farm)


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
