from dataclasses import dataclass

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

DEPARTMENTS_URI = Uri.parse_unsafe('config:/departments')

@dataclass(frozen=True)
class Department:
    name: str
    independent: bool
    publishable: bool
    renderable: bool

def add_department(
    context: str,
    name: str,
    independent: bool = False,
    publishable: bool = True,
    renderable: bool = False
    ):
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is not None: raise ValueError('Department already exists')
    api.config.set_properties(department_uri, dict(
        independent = independent,
        publishable = publishable,
        renderable = renderable
    ))

def remove_department(context: str, name: str):
    department_uri = DEPARTMENTS_URI / context / name
    api.config.remove_entity(department_uri)

def set_independent(context: str, name: str, independent: bool):
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['independent'] = independent
    api.config.set_properties(department_uri, properties)

def set_publishable(context: str, name: str, publishable: bool):
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['publishable'] = publishable
    api.config.set_properties(department_uri, properties)

def set_renderable(context: str, name: str, renderable: bool):
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['renderable'] = renderable
    api.config.set_properties(department_uri, properties)

def is_renderable(context: str, name: str) -> bool:
    """Check if a department is renderable."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None:
        raise KeyError(f'Department not found: {department_uri}')
    return properties['renderable']

def list_departments(context: str) -> list[Department]:
    departments_data = api.config.cache.get('departments', {})
    root_children = departments_data.get('children', {})
    context_data = root_children.get(context, {}).get('children', {})
    return [
        Department(
            name = dept_name,
            independent = dept_data['properties']['independent'],
            publishable = dept_data['properties']['publishable'],
            renderable = dept_data['properties']['renderable']
        )
        for dept_name, dept_data in context_data.items()
    ]