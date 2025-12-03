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

from tumblehead.config.shots import (
    list_render_layers
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
    # Shots
    'list_render_layers',
    # Discord
    'get_discord_token',
    'get_user_discord_id',
    'get_discord_channel_id',
    'get_discord_channel_for_department',
    'list_discord_users',
    'list_discord_channels',
]
