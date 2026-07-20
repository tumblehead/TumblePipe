import logging
from dataclasses import dataclass

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)

DEPARTMENTS_URI = Uri.parse_unsafe('departments:/')

# Names the pipeline already uses for pseudo-departments that are never
# registered in the pool: 'root' is the scene-build root layer (and the Rust
# resolver's ROOT_DEPARTMENT), 'staged' the build task's synthetic department,
# 'none' import_shot's "exclude nothing" sentinel. A pool entry under one of
# these names would collide with that plumbing, so creation rejects them.
RESERVED_NAMES = frozenset({'root', 'staged', 'none'})


@dataclass(frozen=True)
class Department:
    name: str
    independent: bool
    publishable: bool
    renderable: bool
    generated: bool = False  # True for Python-generated departments (not Houdini-exportable)
    enabled: bool = True  # False means retired/hidden from normal use
    short: str | None = None  # Optional abbreviated label (e.g. "mdl" for "model"); UIs may show this when there isn't room for the full name

def validate_name(name: str):
    """Raise ValueError unless ``name`` is usable as a department name.

    A department name is a path segment (it is joined onto workspace, export
    and staged URIs), so it must survive that unchanged.
    """
    if not name or name.strip() != name:
        raise ValueError('Department name must be non-empty and unpadded')
    if '/' in name or '\\' in name:
        raise ValueError('Department name may not contain a path separator')
    if name in RESERVED_NAMES:
        raise ValueError(
            f"'{name}' is reserved for a pipeline pseudo-department "
            f"({', '.join(sorted(RESERVED_NAMES))})"
        )


def list_department_names(context: str, include_disabled: bool = True) -> list[str]:
    """Department names for a context, in pool order.

    Pool order is the pipeline order — it decides USD sublayer strength and
    every "downstream" computation — so callers that need to place, index or
    reorder departments read it from here rather than sorting names.
    """
    return [
        d.name for d in
        list_departments(context, include_disabled=include_disabled)
    ]


def department_names_up_to(
    department_names: list[str],
    department_name: str,
    ) -> list[str]:
    """The pool-order prefix ending at (and including) ``department_name``.

    The inclusive counterpart of the ``names[index + 1:]`` "downstream"
    slice: this is "everything this department composes on top of, plus
    itself". Used by the publish task graph and by the render stage, which
    must agree on what "render up to lighting" means.

    Raises ValueError when the department is not in the list. Callers pass
    a list that is already filtered (renderable-only, or an entity's own
    assignment), so a miss means the caller and the pool disagree — and
    silently composing the whole pool instead is exactly the
    renders-too-much bug this slice exists to prevent.
    """
    if department_name not in department_names:
        raise ValueError(
            f"Department '{department_name}' is not in "
            f"[{', '.join(department_names)}]"
        )
    return department_names[:department_names.index(department_name) + 1]


def add_department(
    context: str,
    name: str,
    independent: bool = False,
    publishable: bool = True,
    renderable: bool = False,
    generated: bool = False,
    enabled: bool = True,
    short: str | None = None,
    index: int | None = None,
    ):
    """Add a department to the pool.

    ``index`` places it at that position in the pool order; the default
    (None) appends. Position is load-bearing — see ``reorder_departments``.
    """
    validate_name(name)
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is not None: raise ValueError('Department already exists')
    api.config.set_properties(department_uri, dict(
        independent = independent,
        publishable = publishable,
        renderable = renderable,
        generated = generated,
        enabled = enabled,
        short = short,
    ))
    if index is None: return
    names = list_department_names(context)
    names.remove(name)
    names.insert(max(0, min(index, len(names))), name)
    reorder_departments(context, names)


def reorder_departments(context: str, names: list[str]):
    """Set the pool order for a context.

    This is not cosmetic. Pool order is the pipeline order:

    * the staged build sublayers departments in ``reversed()`` pool order, so
      **later in the pool = stronger USD layer**;
    * "downstream" is ``names[index + 1:]`` — it drives the Downstream Exports
      menu, import_shot's layer/asset exclusion, the publish task graph, and
      the propagate/update farm jobs;
    * AOV precedence ranks by pool index.

    So reordering an established pool restages composition for every existing
    entity. ``names`` must be a permutation of the context's departments,
    disabled ones included.
    """
    api.config.reorder_children(DEPARTMENTS_URI / context, list(names))

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

def set_short(context: str, name: str, short: str | None):
    """Set or clear the optional abbreviated label for a department."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None: return
    properties['short'] = short
    api.config.set_properties(department_uri, properties)

def get_short(context: str, name: str) -> str | None:
    """Return the optional abbreviated label, or ``None`` if unset."""
    department_uri = DEPARTMENTS_URI / context / name
    properties = api.config.get_properties(department_uri)
    if properties is None:
        raise KeyError(f'Department not found: {department_uri}')
    return properties.get('short')

def list_departments(context: str, include_generated: bool = True, include_disabled: bool = False) -> list[Department]:
    """
    List departments for a context (shots or assets).

    Args:
        context: 'shots' or 'assets'
        include_generated: If False, excludes Python-generated departments
                          (useful for Houdini export menus)
        include_disabled: If True, includes disabled/retired departments
    """
    departments_data = api.config.root('departments') or {}
    root_children = departments_data.get('children', {})
    context_data = root_children.get(context, {}).get('children', {})
    departments = []
    for dept_name in context_data:
        # Resolve through get_properties so schema defaults fill in — the
        # raw cache node stores only sparse overrides, so default-valued
        # fields (independent/publishable/renderable) may be absent there.
        props = api.config.get_properties(DEPARTMENTS_URI / context / dept_name) or {}
        # Read every field with .get() + its schema default. get_properties
        # fills schema defaults on a migrated config, but an un-migrated or
        # sparsely-written node can be missing independent/publishable/
        # renderable — direct-key access (props['independent']) then raised
        # KeyError, which callers swallowed into a differently-shaped
        # fallback pool. The defaults here match the schema and
        # add_department() (independent=False, publishable=True,
        # renderable=False), so migrated configs are unaffected.
        departments.append(Department(
            name = dept_name,
            independent = props.get('independent', False),
            publishable = props.get('publishable', True),
            renderable = props.get('renderable', False),
            generated = props.get('generated', False),
            enabled = props.get('enabled', True),
            short = props.get('short'),
        ))
    if not include_generated:
        departments = [d for d in departments if not d.generated]
    if not include_disabled:
        departments = [d for d in departments if d.enabled]
    return departments


# --------------------------------------------------------------------------- #
# Per-entity assignment
# --------------------------------------------------------------------------- #
# A shot or asset may scope itself to a subset of its context's pool — a shot
# that only carries tracking work should not advertise eight departments it
# will never have. The assignment is a plain list property on the entity node,
# shaped exactly like ``variants``: an empty list means "inherit the whole
# enabled pool", so every entity that predates the feature keeps behaving as it
# always did, and the property inherits down entity:/shots -> sequence -> shot
# through the store's normal deep merge.
#
# The assignment is a *set*, never an order. Pool order stays the single source
# of truth for pipeline order, so resolution always filters the pool rather
# than iterating what the entity stored.


def entity_context(entity_uri: Uri) -> str:
    """The department context ('shots' / 'assets') an entity draws from."""
    if entity_uri.purpose != 'entity' or not entity_uri.segments:
        raise ValueError(f'Not an entity URI: {entity_uri}')
    context = entity_uri.segments[0]
    if context not in ('shots', 'assets'):
        raise ValueError(f'Entity has no department context: {entity_uri}')
    return context


def get_entity_departments(entity_uri: Uri) -> list[str]:
    """The entity's raw department assignment ([] means "inherit the pool").

    This is the stored intent, resolved through the entity hierarchy — use
    ``list_entity_departments`` for the departments the entity actually has.
    """
    properties = api.config.get_properties(entity_uri) or {}
    return list(properties.get('departments') or [])


def list_entity_departments(
    entity_uri: Uri,
    include_generated: bool = True,
    include_disabled: bool = False,
    ) -> list[Department]:
    """The departments assigned to an entity, in pool order.

    An entity with no assignment (the default) gets its context's whole pool.
    Names the entity carries that are no longer in the pool are dropped with a
    warning rather than raising: removing a department from the pool must not
    brick the entities that referenced it.
    """
    context = entity_context(entity_uri)
    pool = list_departments(context, include_generated, include_disabled)
    assigned = get_entity_departments(entity_uri)
    if not assigned:
        return pool
    known = {d.name for d in list_departments(context, True, True)}
    for name in assigned:
        if name not in known:
            logger.warning(
                'Entity %s is assigned department %r, which is not in the '
                '%s pool — ignoring it', entity_uri, name, context
            )
    wanted = set(assigned)
    return [d for d in pool if d.name in wanted]


def list_entity_department_names(
    entity_uri: Uri,
    include_generated: bool = True,
    include_disabled: bool = False,
    ) -> list[str]:
    return [
        d.name for d in
        list_entity_departments(entity_uri, include_generated, include_disabled)
    ]


def set_entity_departments(entity_uri: Uri, names: list[str]):
    """Scope an entity to ``names``; an empty list restores "inherit the pool".

    Stored in pool order so the JSON reads like the pipeline, and rejected if
    it names a department the pool doesn't have.
    """
    context = entity_context(entity_uri)
    properties = api.config.get_properties(entity_uri)
    if properties is None:
        raise ValueError(f'Entity not found: {entity_uri}')
    pool = list_department_names(context)
    unknown = [name for name in names if name not in pool]
    if unknown:
        raise ValueError(
            f'Not departments of the {context} pool: {", ".join(unknown)}'
        )
    properties['departments'] = [name for name in pool if name in set(names)]
    api.config.set_properties(entity_uri, properties)


def assign_department(entity_uri: Uri, name: str):
    """Add a department to an entity's assignment.

    An entity that was inheriting the whole pool is materialised first, so
    assignment is always explicit once touched.
    """
    current = list_entity_department_names(entity_uri, include_disabled=True)
    if name in current:
        return
    set_entity_departments(entity_uri, current + [name])


def unassign_department(entity_uri: Uri, name: str):
    """Drop a department from an entity's assignment.

    This is a scoping decision, not a delete: any workfile or export the
    department already has stays on disk and keeps composing into the build.
    """
    current = list_entity_department_names(entity_uri, include_disabled=True)
    if name not in current:
        return
    remaining = [n for n in current if n != name]
    if not remaining:
        # An empty assignment is how an entity says "inherit the pool", so
        # there is no way to store "this entity has no departments at all".
        raise ValueError(
            'Cannot unassign the last department — an entity scoped to '
            'nothing is indistinguishable from one inheriting the pool'
        )
    set_entity_departments(entity_uri, remaining)