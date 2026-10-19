"""Which departments the browser shows for a context, and for one entity.

Two answers that read alike and must not be confused:

- :meth:`DepartmentScope.pool` — the project's whole department pool for
  ``assets`` / ``shots``, in pipeline order;
- :meth:`DepartmentScope.for_entity` — the departments one entity shows:
  its assignment (``config.department.list_entity_departments``, the whole
  pool when it has none) plus any department that already has work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from .types import DEPT_API_SHORT_FALLBACK

if TYPE_CHECKING:
    from .houdini import ProjectActivator
    from .resolver import AssetResolver


class DepartmentScope:
    """Department lists for the active project, and per entity."""

    def __init__(
        self, resolver: "AssetResolver", activator: "ProjectActivator",
    ) -> None:
        self._resolver = resolver
        self._activator = activator

    def pool(self, context: str) -> list[str]:
        """The active project's department pool for ``context``, in
        pipeline order (generated departments left out)."""
        from tumblepipe.config.department import list_departments
        return [d.name for d in list_departments(context, include_generated=False)]

    def for_entity(
        self, asset_id: str, context: str, with_work: Iterable[str] = (),
    ) -> tuple[list[str], set[str]]:
        """``(departments_to_show, assigned)`` for one entity, in pool order.

        An entity may be scoped to a subset of its context's pool
        (``entity.departments``); one that isn't inherits the whole pool.
        Departments in ``with_work`` (those that already have a workfile)
        are shown even when they are not assigned — scoping states what an
        entity is *expected* to have, it never hides work that exists.
        """
        pool = self.pool(context)
        worked = list(with_work)
        assigned: list[str] = []
        proj = self._resolver.project_for(asset_id)
        entity_uri = self._resolver.uri_for(asset_id)
        if proj is not None and entity_uri is not None:
            self._activator.activate(proj)
            from tumblepipe.config.department import list_entity_departments
            assigned = [
                d.name for d in
                list_entity_departments(entity_uri, include_generated=False)
            ]
        if not assigned:
            return pool, set(pool)
        shown = set(assigned) | set(worked)
        ordered = [name for name in pool if name in shown]
        # A department dropped from the pool can still have work on disk.
        ordered += [name for name in worked if name not in pool]
        return ordered, set(assigned)

    def shorts(self, context: str) -> dict[str, str]:
        """``{department: short label}``: the department's own ``short``,
        else :data:`DEPT_API_SHORT_FALLBACK` (case-insensitively); those
        with neither are left out."""
        from tumblepipe.config.department import list_departments
        result: dict[str, str] = {}
        for d in list_departments(context, include_generated=False):
            short = d.short or DEPT_API_SHORT_FALLBACK.get(d.name.lower())
            if short:
                result[d.name] = short
        return result
