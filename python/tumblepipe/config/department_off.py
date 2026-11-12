"""Which departments apply to an entity, given the ones turned off on it (#64).

Pure: no config store, no ``hou``, no imports. A shot (or asset) may turn
departments of its context's pool off, a reversible per-entity switch stored
as ``disabled_departments`` on the entity node. ``config.department`` reads
and writes it through the store; everything that decides from it (the
staged build, import_shot, Farm Submit, batch_submit, the publish's task
collection, the Entity Editor) goes through the functions here, so the rule
is written once.

A missing key reads as "none off", which is how every entity from before
the feature behaves, so no layout migration is needed.
"""

from __future__ import annotations

DISABLED_DEPARTMENTS = 'disabled_departments'


def read_disabled_departments(properties) -> list[str]:
    """The departments ``properties`` (an entity's) turn off, as stored.

    Anything that is not a list of names reads as none off: a hand edit
    that broke the value must not take every department of the shot out of
    its build. Duplicates are dropped, first spelling kept.
    """
    if not isinstance(properties, dict):
        return []
    raw = properties.get(DISABLED_DEPARTMENTS)
    if not isinstance(raw, (list, tuple)):
        return []
    return list(dict.fromkeys(
        name for name in raw if isinstance(name, str) and name
    ))


def applied_departments(departments, disabled) -> list[str]:
    """``departments`` (pool order) without the ones ``disabled`` turns off.

    The one rule every consumer follows: the staged build's layer stack,
    import_shot's in-session exclusion, Farm Submit's grid and
    batch_submit, and the publish's task collection. Order is kept, since
    pool order is the pipeline order; a disabled name the list lacks is
    ignored.
    """
    off = set(disabled)
    return [name for name in departments if name not in off]


def toggled_disabled_departments(pool, disabled, name: str, off: bool) -> list[str]:
    """The ``disabled`` list with ``name`` turned off (``off``) or back on.

    Pool names come in pool order; names the pool no longer has are kept
    after them, untouched, so a department dropped from the pool and added
    back finds the shot as it was left.
    """
    wanted = set(disabled)
    if off:
        wanted.add(name)
    else:
        wanted.discard(name)
    pool = list(pool)
    ordered = [each for each in pool if each in wanted]
    ordered += [
        each for each in dict.fromkeys(disabled)
        if each in wanted and each not in pool
    ]
    if off and name not in ordered:
        ordered.append(name)
    return ordered
