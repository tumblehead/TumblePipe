"""Which of a workfile's export nodes a publish runs.

One rule for the two places a workfile is published: the publish window in
the session (``pipe.houdini.ui.task_collection``) and the farm's publish
worker (``farm.workers.publish_houdini``). They each had their own, and the
farm's took the first export node of the entity and department, bypassed or
not. A Multi's light workfile whose template export nodes had been bypassed
for a new lighting setup then published, on the farm, the template's unlit
chain: the shots rendered with no lights until the light was exported again
in the session, which skips bypassed nodes (Auguu, 2026-11-21).

Free of Houdini: a node is anything with ``native().isBypassed()``,
``get_entity_uri()`` and ``get_department_name()`` (the ``th::`` node
wrappers).
"""

from __future__ import annotations

from typing import Callable, Collection, Iterable


def publishes(
    node,
    owned: Collection,
    departments: Collection[str],
    disabled: Callable[[object], Iterable[str]],
) -> bool:
    """Whether a publish of the ``owned`` entities into ``departments`` runs
    ``node``: it is live (not bypassed), names one of the entities and one of
    the departments, and that department is not turned off on its entity
    (#64; ``disabled(entity_uri)`` lists the ones that are)."""
    if node.native().isBypassed():
        return False
    entity_uri = node.get_entity_uri()
    if entity_uri not in owned:
        return False
    department = node.get_department_name()
    if department not in departments:
        return False
    return department not in disabled(entity_uri)
