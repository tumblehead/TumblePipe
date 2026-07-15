from dataclasses import dataclass

from tumblepipe.util.uri import Uri

# Core config classes (moved from config.py)
@dataclass(frozen=True)
class Entity:
    uri: Uri
    properties: dict

# Import Schema types for interface
from tumblepipe.config.schema import Schema, FieldDefinition

class ConfigConvention:
    def coherent(self):
        """Context manager batching multiple reads into one coherency check.

        Implementations that stamp backing files per read (JsonConfigStore)
        override this so a ``with config.coherent():`` block stamps each
        file once for the whole block. The default is a no-op scope.
        """
        from contextlib import nullcontext
        return nullcontext()

    def add_entity(self, uri: Uri, properties: dict):
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
        """Set properties at uri (stored sparsely against inherited defaults).

        The entity's schema is resolved from its position
        (get_entity_schema_uri), so no schema needs to be passed or stored.
        """
        raise NotImplementedError()

    def get_own_properties(self, uri: Uri) -> dict | None:
        """Properties stored directly on this entity only.

        Unlike get_properties, this applies no schema defaults and no
        inheritance from ancestors — it returns exactly what is stored on
        the entity's own node ({} if it exists but stores nothing), or None
        if the entity does not exist. Use it to tell a value set ON an
        entity apart from one merged down from a parent.
        """
        raise NotImplementedError()

    def reorder_children(self, uri: Uri, names: list[str]):
        """Reorder the children of ``uri`` to match ``names``.

        Child order is not cosmetic everywhere: the department pool's order
        is the pipeline order (it drives USD sublayer strength and every
        "downstream" computation), so it needs to be settable without
        rewriting the tree by hand. ``names`` must be a permutation of the
        node's existing children.
        """
        raise NotImplementedError()

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
        raise NotImplementedError()

    def list_entity_uris(self, filter: Uri | None = None, closure: bool = False) -> list[Uri]:
        """URIs only — no property resolution. Prefer this when the caller
        consumes ``.uri`` alone; implementations override it to skip the
        per-entity property cost that ``list_entities`` pays.
        """
        return [entity.uri for entity in self.list_entities(filter, closure)]

    def get_schema(self, schema_uri: Uri) -> Schema | None:
        raise NotImplementedError()

    def list_schemas(self, parent_uri: Uri | None = None) -> list[Schema]:
        raise NotImplementedError()

    def get_entity_schema(self, entity_uri: Uri) -> Schema | None:
        raise NotImplementedError()

    def get_entity_schema_uri(self, entity_uri: Uri) -> Uri | None:
        """The schema URI for an entity, derived purely from its position.

        This is the single source of truth for which schema an entity uses;
        there is no per-node stored value to drift out of sync. Returns None
        if the schema tree doesn't cover the entity's position.
        """
        raise NotImplementedError()

    def get_child_schemas(self, schema_uri: Uri) -> list[Schema]:
        raise NotImplementedError()

# Export submodule classes for convenience
from tumblepipe.config.timeline import (
    BlockRange,
    FrameRange,
    get_frame_range,
    get_fps,
    is_animatable
)

from tumblepipe.config.department import (
    Department,
    add_department,
    remove_department,
    reorder_departments,
    set_independent,
    set_publishable,
    list_departments,
    list_department_names
)

from tumblepipe.config.groups import (
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

from tumblepipe.config.procedurals import (
    list_procedural_names
)

from tumblepipe.config.variants import (
    DEFAULT_VARIANT,
    list_variants,
    add_variant,
    remove_variant,
    has_variant,
    get_entity_type
)

from tumblepipe.config.discord import (
    get_token as get_discord_token,
    get_user_discord_id,
    get_channel_id as get_discord_channel_id,
    get_channel_for_department as get_discord_channel_for_department,
    list_users as list_discord_users,
    list_channels as list_discord_channels
)

from tumblepipe.config.schema import (
    validate_properties,
    apply_defaults,
    schema_from_properties
)

from tumblepipe.config.farm import (
    Pool,
    PriorityPreset,
    list_pools,
    add_pool,
    remove_pool,
    list_priority_presets,
    get_default_priority,
    add_priority_preset,
    remove_priority_preset,
    set_default_priority_preset
)

from tumblepipe.config.renderer import (
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

# Global cache management functions from api module
from tumblepipe.api import (
    refresh_global_cache,
    reset_default_client
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
    'is_animatable',
    # Department
    'Department',
    'add_department',
    'remove_department',
    'reorder_departments',
    'set_independent',
    'set_publishable',
    'list_departments',
    'list_department_names',
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
    'add_pool',
    'remove_pool',
    'list_priority_presets',
    'get_default_priority',
    'add_priority_preset',
    'remove_priority_preset',
    'set_default_priority_preset',
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
    # Global cache management
    'refresh_global_cache',
    'reset_default_client',
]
