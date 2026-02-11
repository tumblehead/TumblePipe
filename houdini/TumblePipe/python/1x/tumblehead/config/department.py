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
    generated: bool = False  # True for Python-generated departments (not Houdini-exportable)
    enabled: bool = True  # False means retired/hidden from normal use

def add_department(
    context: str,
    name: str,
    independent: bool = False,
    publishable: bool = True,
    renderable: bool = False,
    generated: bool = False,
    enabled: bool = True
    ):
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is not None: raise ValueError('Department already exists')
    api.config.set_properties(department_uri, dict(
        independent = independent,
        publishable = publishable,
        renderable = renderable,
        generated = generated,
        enabled = enabled
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

def set_generated(context: str, name: str, generated: bool):
    """Set whether a department is generated (Python-only, not Houdini-exportable)."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['generated'] = generated
    api.config.set_properties(department_uri, properties)

def is_generated(context: str, name: str) -> bool:
    """Check if a department is generated (Python-only, not Houdini-exportable)."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None:
        raise KeyError(f'Department not found: {department_uri}')
    return properties.get('generated', False)

def set_enabled(context: str, name: str, enabled: bool):
    """Set whether a department is enabled (active) or disabled (retired)."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['enabled'] = enabled
    api.config.set_properties(department_uri, properties)

def is_enabled(context: str, name: str) -> bool:
    """Check if a department is enabled."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None:
        raise KeyError(f'Department not found: {department_uri}')
    return properties.get('enabled', True)

def list_departments(context: str, include_generated: bool = True, include_disabled: bool = False) -> list[Department]:
    """
    List departments for a context (shots or assets).

    Args:
        context: 'shots' or 'assets'
        include_generated: If False, excludes Python-generated departments
                          (useful for Houdini export menus)
        include_disabled: If True, includes disabled/retired departments
    """
    departments_data = api.config.cache.get('departments', {})
    root_children = departments_data.get('children', {})
    context_data = root_children.get(context, {}).get('children', {})
    departments = [
        Department(
            name = dept_name,
            independent = dept_data['properties']['independent'],
            publishable = dept_data['properties']['publishable'],
            renderable = dept_data['properties']['renderable'],
            generated = dept_data['properties'].get('generated', False),
            enabled = dept_data['properties'].get('enabled', True)
        )
        for dept_name, dept_data in context_data.items()
    ]
    if not include_generated:
        departments = [d for d in departments if not d.generated]
    if not include_disabled:
        departments = [d for d in departments if d.enabled]
    return departments