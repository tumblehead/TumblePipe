"""Versioned, on-the-fly project ``_config`` migrations.

A project's ``_config`` layout (the db schema files *and* the convention
modules that drive them) evolves over time. Historically those modules were
copied into each project at creation and then frozen, so a fix to the engine
never reached existing projects — the recurring "only new projects get the
fix" trap.

This module makes the layout *versioned* and migratable in place. Each
migration is a small, idempotent transform of a project's ``_config``
directory, keyed to the version it brings the project *to*. The current
version lives in ``_config/version.json``; a project with no such file
predates this system and is treated as version 0. ``migrate_project`` walks
the pending migrations in order, applies each, and stamps the new version.

The module is deliberately stdlib-only and Houdini-free so it can run from a
launcher, a CLI, or a headless test — anywhere a project needs bringing
forward before it is opened. It lives at the package top level, not under
``tumblepipe.config``, and depends on nothing in that package on purpose:
migration must run *before* a project is configured (no ``TH_*``, no client),
so it operates on the raw ``_config`` files directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

VERSION_FILE = 'version.json'


class MigrationError(Exception):
    """A migration could not be applied safely (e.g. a customized file)."""


@dataclass(frozen=True)
class Migration:
    """One step forward in the ``_config`` layout.

    ``apply`` mutates the project's ``_config`` directory in place and must be
    idempotent — re-running a migration that already happened is a no-op, so a
    partially-migrated project can always be re-run to completion.
    """
    version: int                       # the version this brings the project TO
    description: str
    apply: Callable[[Path], None]      # (config_dir) -> None


@dataclass(frozen=True)
class MigrationResult:
    config_dir: Path
    from_version: int
    to_version: int
    applied: list[int]
    dry_run: bool


def _config_dir(project_path: Path) -> Path:
    """Accept either a project root or the ``_config`` directory itself."""
    project_path = Path(project_path)
    if project_path.name == '_config':
        return project_path
    return project_path / '_config'


def current_version(project_path: Path) -> int:
    """The project's recorded config layout version (0 if unstamped)."""
    version_path = _config_dir(project_path) / VERSION_FILE
    if not version_path.exists():
        return 0
    data = json.loads(version_path.read_text(encoding='utf-8'))
    return int(data['version'])


def latest_version() -> int:
    """The newest version any registered migration targets."""
    return max((m.version for m in MIGRATIONS), default=0)


def _write_version(config_dir: Path, version: int) -> None:
    path = config_dir / VERSION_FILE
    path.write_text(json.dumps({'version': version}, indent=4) + '\n', encoding='utf-8')


def pending(project_path: Path) -> list[Migration]:
    """Registered migrations newer than the project's current version, in order."""
    version = current_version(project_path)
    return [m for m in MIGRATIONS if m.version > version]


def migrate_project(project_path: Path, dry_run: bool = False) -> MigrationResult:
    """Apply every pending migration to ``project_path`` in version order.

    Stamps ``version.json`` after each applied step, so an interrupted run
    resumes from where it stopped. With ``dry_run`` nothing is written; the
    result still reports what *would* run.
    """
    config_dir = _config_dir(project_path)
    if not config_dir.is_dir():
        raise MigrationError(f'No _config directory at {config_dir}')

    start = current_version(config_dir)
    todo = [m for m in MIGRATIONS if m.version > start]

    applied: list[int] = []
    for migration in todo:
        logger.info('migrate %s -> v%d: %s', config_dir, migration.version, migration.description)
        if not dry_run:
            migration.apply(config_dir)
            _write_version(config_dir, migration.version)
        applied.append(migration.version)

    to_version = start if dry_run else (todo[-1].version if todo else start)
    return MigrationResult(config_dir, start, to_version, applied, dry_run)


def ensure_migrated(project_path: Path) -> None:
    """Bring a project forward if it is behind — the on-the-fly entry point.

    Safe to call before a project is opened. It is intentionally *not* wired
    into ``default_client`` by default: auto-rewriting a project's files on
    open is a deliberate choice (a read-only farm worker should not mutate the
    project), so a launcher or tool opts into it explicitly.
    """
    if pending(project_path):
        migrate_project(project_path)


# --------------------------------------------------------------------------- #
# v1 — config DB engine moved into the package
# --------------------------------------------------------------------------- #
# The engine that used to be copied, in full, into every project's
# config_convention.py now lives in tumblepipe.config.store. The per-project
# file becomes a thin shim, so future engine fixes ship with the package.

_THIN_CONVENTION = '''"""Project config convention.

The config database engine now lives in the package
(``tumblepipe.config.store.JsonConfigStore``) so that fixes and features
reach every project through a normal package update instead of being frozen
into this per-project file at creation time. This module only wires it up.

If a project needs project-specific config behaviour, subclass
``JsonConfigStore`` here and return that instead.
"""

from tumblepipe.config.store import JsonConfigStore


def create() -> JsonConfigStore:
    return JsonConfigStore()
'''


def _migrate_convention_to_package(config_dir: Path) -> None:
    path = config_dir / 'config_convention.py'
    if not path.exists():
        path.write_text(_THIN_CONVENTION, encoding='utf-8')
        return
    text = path.read_text(encoding='utf-8')
    if 'JsonConfigStore' in text:
        return  # already the shim — idempotent
    if 'class ProjectConfigConvention' not in text:
        # Not the stock generic engine: refuse to clobber a customization.
        raise MigrationError(
            f'{path} is not the stock config engine — migrate it by hand'
        )
    # Drop a one-time local backup before overwriting the live file, so the
    # migration is reversible in place on the project share. Never clobber an
    # existing .bak — a prior run already preserved the true original, and the
    # current file may by then be the shim.
    backup = path.with_name(path.name + '.bak')
    if not backup.exists():
        backup.write_text(text, encoding='utf-8')
    path.write_text(_THIN_CONVENTION, encoding='utf-8')


# --------------------------------------------------------------------------- #
# v2 — refresh the department templates from the packaged scaffold
# --------------------------------------------------------------------------- #
# `_config/templates/<context>/<dept>/template.py` is copied into a project at
# creation and then frozen — the same "only new projects get the fix" trap v1
# closed for the config engine. The templates are what build a new department
# workfile's node graph, so a fix there (e.g. leaving entity-aware HDAs at
# their 'from_context' default instead of baking the entity URI in) never
# reaches a live project without this step.


def _scaffold_templates_dir() -> Path:
    """The packaged scaffold templates — the source of truth for a refresh."""
    root = Path(__file__).resolve().parents[2]
    return root / 'scripts' / 'project_template' / '_config' / 'templates'


def _refresh_templates(config_dir: Path) -> None:
    source = _scaffold_templates_dir()
    if not source.is_dir():
        raise MigrationError(
            f'packaged templates not found at {source} — cannot refresh'
        )

    target = config_dir / 'templates'
    for src in sorted(source.rglob('template.py')):
        dst = target / src.relative_to(source)
        new_text = src.read_text(encoding='utf-8')

        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(new_text, encoding='utf-8')
            logger.info('templates: added %s', dst)
            continue

        old_text = dst.read_text(encoding='utf-8')
        if old_text == new_text:
            continue  # already current — idempotent

        # Keep a one-time local backup so a project that had hand-tuned its
        # template can recover it. Never clobber an existing .bak: a prior run
        # already preserved the true original.
        backup = dst.with_name(dst.name + '.bak')
        if not backup.exists():
            backup.write_text(old_text, encoding='utf-8')
        dst.write_text(new_text, encoding='utf-8')
        logger.info('templates: refreshed %s (previous kept at %s)', dst, backup.name)


# --------------------------------------------------------------------------- #
# v3 — declare the entity `departments` property in the schema
# --------------------------------------------------------------------------- #
# An entity may scope itself to a subset of its context's department pool. The
# assignment is a list property on the entity node, and the schema has to
# declare its default for the store to resolve (and sparsely store) it. The
# default is `[]`, which means "inherit the whole pool" — so this migration is
# data-neutral: no existing shot or asset changes behaviour, it just gains the
# ability to be scoped.


def _add_entity_departments_property(config_dir: Path) -> None:
    path = config_dir / 'db' / 'schemas.json'
    if not path.exists():
        raise MigrationError(f'{path} not found — cannot add the schema default')

    data = json.loads(path.read_text(encoding='utf-8'))
    try:
        entity = data['children']['entity']
    except KeyError as exc:
        raise MigrationError(f'{path} has no entity schema node') from exc

    properties = entity.setdefault('properties', {})
    if 'departments' in properties:
        return  # already declared — idempotent

    properties['departments'] = []
    path.write_text(json.dumps(data, indent=4) + '\n', encoding='utf-8')
    logger.info('schemas: declared entity.departments on %s', path)


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description='move the config DB engine into the package (thin convention shim)',
        apply=_migrate_convention_to_package,
    ),
    Migration(
        version=2,
        description='refresh _config/templates from the packaged scaffold',
        apply=_refresh_templates,
    ),
    Migration(
        version=3,
        description='declare the entity `departments` property in the schema',
        apply=_add_entity_departments_property,
    ),
]
