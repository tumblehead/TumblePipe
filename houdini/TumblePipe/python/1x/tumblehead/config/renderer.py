"""Renderer configuration for default render settings."""

from dataclasses import dataclass
from typing import Any

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

RENDERER_URI = Uri.parse_unsafe('config:/renderer')
SETTINGS_URI = RENDERER_URI / 'settings'


@dataclass(frozen=True)
class RangeSetting:
    """A numeric setting with min, max, and default values."""
    default: int | float
    min: int | float
    max: int | float


@dataclass(frozen=True)
class RendererDefaults:
    """Default renderer settings."""
    tile_count: RangeSetting
    batch_size: RangeSetting
    timeout_minutes: RangeSetting
    denoise: bool


def _get_settings_data() -> dict:
    """Get the raw settings data from config cache."""
    renderer_data = api.config.cache.get('renderer', {})
    settings_data = renderer_data.get('children', {}).get('settings', {})
    return settings_data.get('properties', {})


def get_renderer_defaults() -> RendererDefaults:
    """Get all default renderer settings."""
    props = _get_settings_data()

    tile_count = props.get('tile_count', {})
    batch_size = props.get('batch_size', {})
    timeout = props.get('timeout_minutes', {})

    return RendererDefaults(
        tile_count=RangeSetting(
            default=tile_count.get('default', 4),
            min=tile_count.get('min', 1),
            max=tile_count.get('max', 16)
        ),
        batch_size=RangeSetting(
            default=batch_size.get('default', 10),
            min=batch_size.get('min', 1),
            max=batch_size.get('max', 100)
        ),
        timeout_minutes=RangeSetting(
            default=timeout.get('default', 45),
            min=timeout.get('min', 1),
            max=timeout.get('max', 480)
        ),
        denoise=props.get('denoise', {}).get('default', True)
    )


def get_tile_count_range() -> tuple[int, int, int]:
    """Get tile count (min, max, default)."""
    props = _get_settings_data()
    tile_count = props.get('tile_count', {})
    return (
        tile_count.get('min', 1),
        tile_count.get('max', 16),
        tile_count.get('default', 4)
    )


def get_batch_size_range() -> tuple[int, int, int]:
    """Get batch size (min, max, default)."""
    props = _get_settings_data()
    batch_size = props.get('batch_size', {})
    return (
        batch_size.get('min', 1),
        batch_size.get('max', 100),
        batch_size.get('default', 10)
    )


def get_timeout_range() -> tuple[int, int, int]:
    """Get timeout in minutes (min, max, default)."""
    props = _get_settings_data()
    timeout = props.get('timeout_minutes', {})
    return (
        timeout.get('min', 1),
        timeout.get('max', 480),
        timeout.get('default', 45)
    )


def get_denoise_default() -> bool:
    """Get default denoise setting."""
    props = _get_settings_data()
    return props.get('denoise', {}).get('default', True)


def set_renderer_setting(setting_name: str, values: dict[str, Any]):
    """Set a renderer setting value.

    Args:
        setting_name: Name of the setting (e.g., 'tile_count', 'batch_size')
        values: Dict with keys like 'default', 'min', 'max'
    """
    properties = api.config.get_properties(SETTINGS_URI)
    if properties is None:
        properties = {}

    if setting_name not in properties:
        properties[setting_name] = {}

    properties[setting_name].update(values)
    api.config.set_properties(SETTINGS_URI, properties)


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
