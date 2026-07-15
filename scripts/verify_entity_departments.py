"""Audit a project's department pool and its per-entity assignments.

Read-only. Run it before and after migrating a live project to v3, or any
time a department is added, retired or reordered.

It reports:

* the pool per context, in pool order — which *is* the pipeline order (the
  staged build sublayers departments in reversed pool order, and everything
  below a department is downstream of it), so the order printed here is the
  order composition happens in;
* every entity that is scoped to a subset of the pool;
* assignments naming a department the pool no longer has (harmless — they
  are dropped with a warning at read time — but worth cleaning up);
* departments with a workfile on disk that the entity is not scoped to.
  That is legal and intentional (scoping never hides work), but a shot with
  eight such departments usually means someone scoped it by accident.

Usage::

    python scripts/verify_entity_departments.py                # active project
    python scripts/verify_entity_departments.py P:/paleindia   # a given one
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

CONTEXTS = ('shots', 'assets')


def _bootstrap(project_path: str | None) -> None:
    if project_path is None:
        return
    root = Path(project_path)
    if not (root / '_config').is_dir():
        raise SystemExit(f'not a project (no _config): {root}')
    os.environ['TH_PROJECT_PATH'] = str(root)
    os.environ['TH_CONFIG_PATH'] = str(root / '_config')
    os.environ['TH_EXPORT_PATH'] = str(root / 'export')
    os.environ.setdefault(
        'TH_PIPELINE_PATH', str(Path(__file__).resolve().parents[1]),
    )


def _workfile_departments(root: Path, entity_uri) -> set[str]:
    """Departments with a workfile directory on disk for this entity."""
    base = root.joinpath(*entity_uri.segments)
    if not base.is_dir():
        return set()
    return {
        child.name for child in base.iterdir()
        if child.is_dir() and any(child.glob('*.hip*'))
    }


def main() -> int:
    project_path = sys.argv[1] if len(sys.argv) > 1 else None
    _bootstrap(project_path)

    from tumblepipe.api import api
    from tumblepipe.config.department import (
        get_entity_departments,
        list_departments,
    )
    from tumblepipe.config.entities import is_terminal_entity
    from tumblepipe.util.uri import Uri

    root = Path(os.environ['TH_PROJECT_PATH'])
    print(f'project: {root}\n')

    pool: dict[str, list[str]] = {}
    for context in CONTEXTS:
        departments = list_departments(context, include_disabled=True)
        pool[context] = [d.name for d in departments]
        print(f'{context} pool (pipeline order):')
        for i, d in enumerate(departments):
            flags = ' '.join(
                name for name, on in (
                    ('independent', d.independent),
                    ('publishable', d.publishable),
                    ('renderable', d.renderable),
                    ('generated', d.generated),
                ) if on
            )
            state = '' if d.enabled else '  [DISABLED]'
            print(f'  {i}. {d.name:<14} {flags}{state}')
        print()

    scoped = 0
    stale: list[str] = []
    unassigned_work: list[str] = []

    for context in CONTEXTS:
        for uri in api.config.list_entity_uris(
            filter=Uri.parse_unsafe(f'entity:/{context}'), closure=True,
        ):
            # closure=True also returns childless *category*/*sequence* nodes;
            # only real shots/assets carry an assignment.
            if not is_terminal_entity(api.config, uri):
                continue
            assignment = get_entity_departments(uri)
            worked = _workfile_departments(root, uri)
            if assignment:
                scoped += 1
                missing = [n for n in assignment if n not in pool[context]]
                if missing:
                    stale.append(f'{uri}: {", ".join(missing)}')
                orphaned = sorted(worked - set(assignment))
                if orphaned:
                    unassigned_work.append(f'{uri}: {", ".join(orphaned)}')
                print(f'scoped: {uri} -> {", ".join(assignment)}')

    print(f'\n{scoped} scoped entit{"y" if scoped == 1 else "ies"}; '
          'the rest inherit their whole pool.')

    if stale:
        print('\nassignments naming a department that is not in the pool '
              '(dropped at read time):')
        for line in stale:
            print(f'  {line}')

    if unassigned_work:
        print('\ndepartments with a workfile that their entity is not scoped '
              'to (shown in the browser, still composed — but check these '
              'were scoped on purpose):')
        for line in unassigned_work:
            print(f'  {line}')

    if not stale and not unassigned_work:
        print('\nno stale assignments, no orphaned work.')

    # A stale assignment is a smell, not a failure: it resolves fine.
    print('\nOK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
