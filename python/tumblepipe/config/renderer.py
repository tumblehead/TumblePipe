"""Renderer configuration for default render settings."""

from dataclasses import dataclass
from typing import Any

from tumblepipe.api import default_client
from tumblepipe.util.uri import Uri

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


# The single source of truth for renderer defaults. Project config at
# config:/renderer/settings overrides individual fields; everything that
# wants a default reads it from here, not from inlined literals scattered
# across the getters (where they silently masked the store-mismatch bug).
DEFAULT_RENDERER = RendererDefaults(
    tile_count=RangeSetting(default=4, min=1, max=16),
    batch_size=RangeSetting(default=10, min=1, max=100),
    timeout_minutes=RangeSetting(default=45, min=1, max=480),
    denoise=True,
)


def _get_settings_data() -> dict:
    """Stored renderer overrides, or {} if none are configured.

    Reads through the same URI the writer uses (``config:/renderer/settings``,
    persisted to db/config.json). ``None`` (path absent) means "no overrides
    set" — the canonical case — so collapsing it to {} is correct here.
    """
    return api.config.get_properties(SETTINGS_URI) or {}


def _overlay(base: RangeSetting, stored: dict) -> RangeSetting:
    return RangeSetting(
        default=stored.get('default', base.default),
        min=stored.get('min', base.min),
        max=stored.get('max', base.max),
    )


def get_renderer_defaults() -> RendererDefaults:
    """Resolved renderer settings: project overrides on top of DEFAULT_RENDERER."""
    props = _get_settings_data()
    return RendererDefaults(
        tile_count=_overlay(DEFAULT_RENDERER.tile_count, props.get('tile_count', {})),
        batch_size=_overlay(DEFAULT_RENDERER.batch_size, props.get('batch_size', {})),
        timeout_minutes=_overlay(DEFAULT_RENDERER.timeout_minutes, props.get('timeout_minutes', {})),
        denoise=props.get('denoise', {}).get('default', DEFAULT_RENDERER.denoise),
    )


def get_tile_count_range() -> tuple[int, int, int]:
    """Get tile count (min, max, default)."""
    s = get_renderer_defaults().tile_count
    return (s.min, s.max, s.default)


def get_batch_size_range() -> tuple[int, int, int]:
    """Get batch size (min, max, default)."""
    s = get_renderer_defaults().batch_size
    return (s.min, s.max, s.default)


def get_timeout_range() -> tuple[int, int, int]:
    """Get timeout in minutes (min, max, default)."""
    s = get_renderer_defaults().timeout_minutes
    return (s.min, s.max, s.default)


def get_denoise_default() -> bool:
    """Get default denoise setting."""
    return get_renderer_defaults().denoise


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
