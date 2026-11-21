"""The workfile's entity as Houdini context options.

A pipeline workfile knows whose it is (its ``_context`` sidecar), but
nothing in the scene could read that: an artist who wanted the asset's name
in a parm (``/CHAR/`@asset`/mtl``) typed it into the Context Options Editor
by hand, and a copied scene kept the old value. These are set from the
workfile's context whenever the pipeline saves a workfile (before the hip is
written, so it carries them, farm jobs included) and whenever a scene is
opened in the GUI.

Houdini saves context options in the hip and clears them on loading a hip
without them, so nothing leaks from one workfile into the next. Names stay
clear of Houdini's own options (``fps``, ``fstart``, ``ropname``, ...).
"""

import hou

from tumblepipe.pipe.paths import Context

#: Every option this module sets. They belong to the pipeline: one that does
#: not apply to the workfile (``asset`` in a shot's) is removed, or a scene
#: saved as another entity's workfile with New from Current would keep it.
MANAGED = (
    'entity', 'entity_name', 'entity_path', 'department', 'version',
    'asset', 'category', 'sequence', 'shot', 'multi',
)


def options_for(context: Context) -> dict[str, str]:
    """The context options for a workfile of ``context``.

    Every workfile gets ``entity`` (its URI), ``entity_name``, ``department``
    and, once it has one, ``version``. An asset's adds ``asset``,
    ``category`` and ``entity_path`` (its prim path, ``/CHAR/Mom``); a
    shot's adds ``sequence``, ``shot`` and ``entity_path``; a Multi's adds
    ``multi``.
    """
    uri = context.entity_uri
    segments = list(uri.segments)
    options = {
        'entity': str(uri),
        'entity_name': segments[-1] if segments else '',
        'department': context.department_name,
    }
    if context.version_name:
        options['version'] = context.version_name
    if uri.purpose == 'groups':
        options['multi'] = segments[-1] if segments else ''
        return options
    if len(segments) >= 2:
        options['entity_path'] = '/' + '/'.join(segments[1:])
    if segments[:1] == ['assets'] and len(segments) >= 3:
        options['category'] = segments[1]
        options['asset'] = segments[-1]
    elif segments[:1] == ['shots'] and len(segments) >= 3:
        options['sequence'] = segments[1]
        options['shot'] = segments[-1]
    return options


def apply(context: Context | None) -> None:
    """Set the options for ``context`` on the open scene, and remove the
    managed ones that do not apply to it.

    Only an option whose value differs is written, so opening a workfile the
    pipeline saved changes nothing. None (not a pipeline workfile) touches
    nothing.
    """
    if context is None:
        return
    wanted = options_for(context)
    current = set(hou.contextOptionNames())
    for name, value in wanted.items():
        if name in current and hou.contextOption(name) == value:
            continue
        hou.setContextOption(name, value)
    for name in MANAGED:
        if name in current and name not in wanted:
            hou.removeContextOption(name)


def context_from_options() -> Context | None:
    """The workfile the open scene's context options name; None without them.

    The options travel inside the hip, so they still say whose workfile a
    scene is when its path no longer does: Houdini's crash file
    (``crash.<name>_<user>_<pid>.hiplc`` in its temp folder) is not
    versioned and has no ``context.json`` beside it. Opening such a file
    leaves the options alone (:func:`apply` of None touches nothing), so they
    name the workfile that crashed. None when ``entity`` or ``department`` is
    missing or ``entity`` is not a URI.
    """
    from tumblepipe.util.uri import Uri

    names = set(hou.contextOptionNames())
    if 'entity' not in names or 'department' not in names:
        return None
    entity = hou.contextOption('entity')
    department = hou.contextOption('department')
    if not isinstance(entity, str) or not isinstance(department, str):
        return None
    if not entity or not department:
        return None
    try:
        entity_uri = Uri.parse(entity)
    except ValueError:
        return None
    version = hou.contextOption('version') if 'version' in names else None
    return Context(
        entity_uri=entity_uri,
        department_name=department,
        version_name=version if isinstance(version, str) and version else None,
    )
