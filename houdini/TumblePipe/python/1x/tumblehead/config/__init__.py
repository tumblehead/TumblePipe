from dataclasses import dataclass

from tumblehead.util.uri import Uri

# Core config classes (moved from config.py)
@dataclass(frozen=True)
class Entity:
    uri: Uri
    properties: dict

class ConfigConvention:
    def add_entity(self, uri: Uri, properties: dict):
        raise NotImplementedError()

    def remove_entity(self, uri: Uri):
        raise NotImplementedError()

    def get_properties(self, uri: Uri) -> dict | None:
        raise NotImplementedError()

    def set_properties(self, uri: Uri, properties: dict):
        raise NotImplementedError()

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
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

__all__ = [
    # Core classes
    'Entity',
    'ConfigConvention',
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
]
