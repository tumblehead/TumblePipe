from dataclasses import dataclass

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

GROUPS_URI = Uri.parse_unsafe('entity:/groups')

@dataclass(frozen=True)
class Group:
    uri: Uri
    members: list[Uri]
    departments: list[str]

def is_group_uri(uri: Uri) -> bool:
    if uri.purpose == 'entity': return False
    if len(uri.segments) != 3: return False
    return uri.segments[0] == 'groups'

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
        if root_uri.contains(member): continue
        raise ValueError('Invalid group member list')
    api.config.add_properties(group_uri, dict(
        members = list(map(str, members)),
        departments = departments
    ))
    return group_uri

def remove_group(group_uri: Uri):
    api.config.remove_entity(group_uri)

def add_member(group_uri: Uri, member_uri: Uri):
    root_uri = Uri.parse_unsafe(f'entity:/{group_uri.segments[1]}')
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
    root_uri = Uri.parse_unsafe(f'entity:/{group_uri.segments[1]}')
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
    return [
        Group(
            uri = group_entity.uri,
            members = list(map(Uri.parse_unsafe, group_entity.properties['members'])),
            departments = group_entity.properties['departments']
        )
        for group_entity in api.config.list_entities(context_uri)
    ]

def find_group(context: str, member: Uri) -> Group | None:
    for group in list_groups(context):
        if member not in group.members: continue
        return group
    return None