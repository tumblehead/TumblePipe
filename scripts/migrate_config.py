"""CLI: migrate a project's ``_config`` forward to the current layout.

    python scripts/migrate_config.py [project_path] [--dry-run]

``project_path`` may be a project root or its ``_config`` directory. It
defaults to ``$TH_PROJECT_PATH`` so the TumbleTrove desktop launcher's
per-project Scripts panel can run it against the active project with no
arguments — a user adopting a new pipeline version clicks Run to bring their
own project forward. Use ``--dry-run`` to report without writing anything.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from tumblepipe.migration import (  # noqa: E402
    MigrationError,
    current_version,
    latest_version,
    migrate_project,
    pending,
)


def _resolve_project(arg: Path | None) -> Path:
    """The project to migrate: the explicit arg, else ``$TH_PROJECT_PATH``."""
    if arg is not None:
        return arg
    env = os.environ.get('TH_PROJECT_PATH', '').strip()
    if not env:
        raise SystemExit(
            'no project given and TH_PROJECT_PATH is not set — pass a project '
            'path, or run this from a prepared project in the desktop launcher'
        )
    return Path(env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        'project', type=Path, nargs='?', default=None,
        help='project root or its _config directory (defaults to $TH_PROJECT_PATH)',
    )
    parser.add_argument('--dry-run', action='store_true', help='report without writing')
    args = parser.parse_args()

    project = _resolve_project(args.project)

    try:
        before = current_version(project)
        todo = pending(project)
        if not todo:
            print(f'{project}: already at v{before} (latest v{latest_version()})')
            return 0

        print(f'{project}: v{before} -> v{latest_version()}')
        for migration in todo:
            print(f'  v{migration.version}: {migration.description}')

        result = migrate_project(project, dry_run=args.dry_run)
    except MigrationError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1

    if result.dry_run:
        print('dry run — nothing written')
    else:
        print(f'migrated to v{result.to_version}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
