from dataclasses import dataclass

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

GROUPS_URI = Uri.parse_unsafe('groups:/')

@dataclass(frozen=True)
class Group:
    uri: Uri
    members: list[Uri]
    departments: list[str]

    @property
    def name(self) -> str:
        return self.uri.segments[-1] if self.uri.segments else ''

    @property
    def root(self) -> Uri | None:
        if len(self.uri.segments) < 1:
            return None
        context = self.uri.segments[0]
        return Uri.parse_unsafe(f'entity:/{context}')

def is_group_uri(uri: Uri) -> bool:
    if uri.purpose != 'groups': return False
    if len(uri.segments) != 2: return False
    return True

def add_group(
    context: str,
    name: str,
    members: list[Uri],
    departments: list[str]
    ) -> Uri:
    group_uri = GROUPS_URI / context / name
    properties = api.config.get_properties(group_uri)
    if properties is not None: raise ValueError('Group already exists')
    root_uri = Uri.parse_unsafe(f'entity:/{context}')
    for member in members:
        if not root_uri.contains(member):
            raise ValueError('Invalid group member list')
    api.config.add_entity(group_uri, dict(
        members = list(map(str, members)),
        departments = departments
    ))
    return group_uri

def remove_group(group_uri: Uri):
    api.config.remove_entity(group_uri)

def add_member(group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse_unsafe(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties['members']
    member_uri_raw = str(member_uri)
    if member_uri_raw in members: raise ValueError('Already a member of group')
    members.append(member_uri_raw)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['members'] = members
    api.config.set_own_properties(group_uri, own)

def remove_member(group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse_unsafe(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties['members']
    member_uri_raw = str(member_uri)
    if member_uri_raw not in members: raise ValueError('Not a member of group')
    members.pop(members.index(member_uri_raw))
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['members'] = members
    api.config.set_own_properties(group_uri, own)

def add_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties['departments']
    if department in departments: raise ValueError('Already a department of group')
    departments.append(department)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a department layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['departments'] = departments
    api.config.set_own_properties(group_uri, own)

def remove_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties['departments']
    if department not in departments: raise ValueError('Not a department of group')
    departments.pop(departments.index(department))
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a department layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['departments'] = departments
    api.config.set_own_properties(group_uri, own)

def get_group(group_uri: Uri) -> Group | None:
    properties = api.config.get_properties(group_uri)
    if properties is None: return None
    return Group(
        uri = group_uri,
        members = list(map(Uri.parse_unsafe, properties['members'])),
        departments = properties['departments']
    )

def list_groups(context: str) -> list[Group]:
    context_uri = GROUPS_URI / context
    entities = api.config.list_entities(context_uri)
    return [
        Group(
            uri = entity.uri,
            members = list(map(Uri.parse_unsafe, entity.properties['members'])),
            departments = entity.properties['departments']
        )
        for entity in entities
    ]

def find_group(context: str, member: Uri, department: str) -> Group | None:
    for group in list_groups(context):
        if member not in group.members: continue
        if department not in group.departments: continue
        return group
    return None


def find_groups_for_entity(entity_uri: Uri) -> list[Group]:
    """
    Find all groups that contain an entity (regardless of department).

    Args:
        entity_uri: The entity URI to search for

    Returns:
        List of groups containing this entity
    """
    if len(entity_uri.segments) < 1:
        return []
    context = entity_uri.segments[0]  # 'assets' or 'shots'
    result = []
    for group in list_groups(context):
        if entity_uri in group.members:
            result.append(group)
    return result
