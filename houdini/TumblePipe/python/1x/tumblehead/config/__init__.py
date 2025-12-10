from dataclasses import dataclass

from tumblehead.util.uri import Uri

# Core config classes (moved from config.py)
@dataclass(frozen=True)
class Entity:
    uri: Uri
    properties: dict

# Import Schema types for interface
from tumblehead.config.schema import Schema, FieldDefinition

class ConfigConvention:
    def add_entity(self, uri: Uri, properties: dict, schema_uri: Uri):
        raise NotImplementedError()

    def remove_entity(self, uri: Uri):
        raise NotImplementedError()

    def get_properties(self, uri: Uri) -> dict | None:
        """Get properties with hierarchical resolution.

        Merges properties from root to leaf, with child values overriding parent.
        E.g., for entity:/shots/seq010/shot020:
          root_props < shots_props < seq010_props < shot020_props

        Nested dicts (like 'render', 'farm') are merged recursively.
        """
        raise NotImplementedError()

    def set_properties(self, uri: Uri, properties: dict):
        raise NotImplementedError()

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
        raise NotImplementedError()

    def get_schema(self, schema_uri: Uri) -> Schema | None:
        raise NotImplementedError()

    def list_schemas(self, parent_uri: Uri | None = None) -> list[Schema]:
        raise NotImplementedError()

    def get_entity_schema(self, entity_uri: Uri) -> Schema | None:
        raise NotImplementedError()

    def get_child_schemas(self, schema_uri: Uri) -> list[Schema]:
        raise NotImplementedError()

# Export submodule classes for convenience
from tumblehead.config.timeline import (
    BlockRange,
    FrameRange,
    get_frame_range,
    get_fps
)

from tumblehead.config.department import (
    Department,
    add_department,
    remove_department,
    set_independent,
    set_publishable,
    list_departments
)

from tumblehead.config.groups import (
    Group,
    is_group_uri,
    add_group,
    remove_group,
    add_member,
    remove_member,
    add_department as add_group_department,
    remove_department as remove_group_department,
    get_group,
    list_groups,
    find_group
)

from tumblehead.config.procedurals import (
    list_procedural_names
)

from tumblehead.config.variants import (
    DEFAULT_VARIANT,
    refresh_cache as refresh_variants_cache,
    list_variants,
    add_variant,
    remove_variant,
    has_variant,
    get_entity_type
)

from tumblehead.config.discord import (
    get_token as get_discord_token,
    get_user_discord_id,
    get_channel_id as get_discord_channel_id,
    get_channel_for_department as get_discord_channel_for_department,
    list_users as list_discord_users,
    list_channels as list_discord_channels
)

from tumblehead.config.schema import (
    validate_properties,
    apply_defaults,
    schema_from_properties
)

from tumblehead.config.farm import (
    Pool,
    PriorityPreset,
    list_pools,
    get_default_pool,
    add_pool,
    remove_pool,
    set_default_pool,
    list_priority_presets,
    get_default_priority,
    add_priority_preset,
    remove_priority_preset,
    set_default_priority_preset,
    get_entity_farm_settings
)

from tumblehead.config.renderer import (
    RangeSetting,
    RendererDefaults,
    get_renderer_defaults,
    get_tile_count_range,
    get_batch_size_range,
    get_timeout_range,
    get_denoise_default,
    set_renderer_setting,
    get_entity_render_settings
)

__all__ = [
    # Core classes
    'Entity',
    'ConfigConvention',
    # Schema
    'Schema',
    'FieldDefinition',
    'validate_properties',
    'apply_defaults',
    'schema_from_properties',
    # Timeline
    'BlockRange',
    'FrameRange',
    'get_frame_range',
    'get_fps',
    # Department
    'Department',
    'add_department',
    'remove_department',
    'set_independent',
    'set_publishable',
    'list_departments',
    # Groups
    'Group',
    'is_group_uri',
    'add_group',
    'remove_group',
    'add_member',
    'remove_member',
    'add_group_department',
    'remove_group_department',
    'get_group',
    'list_groups',
    'find_group',
    # Procedurals
    'list_procedural_names',
    # Variants
    'DEFAULT_VARIANT',
    'refresh_variants_cache',
    'list_variants',
    'add_variant',
    'remove_variant',
    'has_variant',
    'get_entity_type',
    # Discord
    'get_discord_token',
    'get_user_discord_id',
    'get_discord_channel_id',
    'get_discord_channel_for_department',
    'list_discord_users',
    'list_discord_channels',
    # Farm
    'Pool',
    'PriorityPreset',
    'list_pools',
    'get_default_pool',
    'add_pool',
    'remove_pool',
    'set_default_pool',
    'list_priority_presets',
    'get_default_priority',
    'add_priority_preset',
    'remove_priority_preset',
    'set_default_priority_preset',
    'get_entity_farm_settings',
    # Renderer
    'RangeSetting',
    'RendererDefaults',
    'get_renderer_defaults',
    'get_tile_count_range',
    'get_batch_size_range',
    'get_timeout_range',
    'get_denoise_default',
    'set_renderer_setting',
    'get_entity_render_settings',
]
