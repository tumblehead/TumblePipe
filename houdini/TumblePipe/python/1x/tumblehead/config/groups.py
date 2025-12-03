from dataclasses import dataclass

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

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
    schema_uri = Uri.parse_unsafe('schemas:/groups/group')
    api.config.add_entity(group_uri, dict(
        members = list(map(str, members)),
        departments = departments
    ), schema_uri)
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
    properties['members'] = members
    api.config.set_properties(group_uri, properties)

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
    properties['members'] = members
    api.config.set_properties(group_uri, properties)

def add_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties['departments']
    if department in departments: raise ValueError('Already a department of group')
    departments.append(department)
    properties['departments'] = departments
    api.config.set_properties(group_uri, properties)

def remove_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties['departments']
    if department not in departments: raise ValueError('Not a department of group')
    departments.pop(departments.index(department))
    properties['departments'] = departments
    api.config.set_properties(group_uri, properties)

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
