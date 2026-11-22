"""Houdini's numbered save backups (``<department>/backup/*_bakN.hip``).

Houdini copies the file it is about to overwrite into ``backup/`` on every
save, and never removes one. TumblePipe saves a new workfile version each
time, so a backup is only a safety net for the version being worked on: a
backup of ``…_v0012`` is dead weight once ``…_v0013`` is saved. A backup
goes when it is older than ``backup_days`` and either

- it is of a workfile version that has a newer saved version in its
  department folder; or
- it is of the latest version, and not one of the newest ``keep_backups``
  of that department.

Anything in a ``backup`` folder not named like a backup of a workfile
version (``<stem>_vNNNN_bakN.hip``) is left alone.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path

from tumblepipe.archive.inventory import Kind
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import WORKFILE_PURPOSES
from tumblepipe.cleanup.core import (
    Category, CategoryPlan, Entry, Settings, age_days, delete_entries, under_context, within,
)

BACKUP_DIR = 'backup'
_BACKUP = re.compile(r'^.+_v(?P<version>\d{4,})_bak(?P<n>\d+)\.(?:hip|hiplc|hipnc)$', re.IGNORECASE)


class Reason:
    PRUNE_SUPERSEDED = 'a backup of a workfile version that has a newer saved version'
    PRUNE_EXTRA = "beyond the newest {keep} backups of its department's latest workfile version"
    KEEP_NEWEST = "one of the newest {keep} backups of its department's latest workfile version"
    KEEP_RECENT = 'newer than {days:g} days'
    KEEP_OTHER = 'not named like a backup of a workfile version'


def backup_of(name: str) -> tuple[int, int] | None:
    """``(workfile version, backup number)`` a backup file's name says."""
    match = _BACKUP.match(name)
    return (int(match.group('version')), int(match.group('n'))) if match else None


def plan(roots, inventory, settings: Settings, *, workfiles, now: float | None = None) -> CategoryPlan:
    out = CategoryPlan('backups', 'Houdini backups', 'file')
    out.payload = {root.purpose: root.path for root in roots.roots}
    if inventory is None:
        return out
    now = time.time() if now is None else now
    latest: dict[tuple[str, str], int] = {}
    for item in inventory.items:
        if item.kind == Kind.WORKFILE and item.root in WORKFILE_PURPOSES:
            key = (item.root, item.series)
            latest[key] = max(latest.get(key, -1), item.version)

    # (root, department folder) -> [(backup number, item)] of its latest version
    current: dict[tuple[str, str], list] = defaultdict(list)
    verdicts: dict[tuple[str, str], tuple[bool, str]] = {}
    items = [i for i in inventory.items if i.kind == Kind.BACKUP and i.root in WORKFILE_PURPOSES]
    for item in items:
        parts = item.rel.split('/')
        named = backup_of(parts[-1])
        if named is None or parts[-2] != BACKUP_DIR:
            verdicts[(item.root, item.rel)] = (False, Reason.KEEP_OTHER)
            continue
        department = '/'.join(parts[:-2])
        version, number = named
        if version < latest.get((item.root, department), -1):
            verdicts[(item.root, item.rel)] = (True, Reason.PRUNE_SUPERSEDED)
        else:
            current[(item.root, department)].append((version, number, item))
    for group in current.values():
        group.sort(key=lambda entry: entry[:2])
        # Never a negative start: `group[-2:]` of three backups with five to
        # keep would keep only two of them.
        kept = group[max(len(group) - settings.keep_backups, 0):] if settings.keep_backups else []
        kept_rels = {item.rel for _v, _n, item in kept}
        for _version, _number, item in group:
            if item.rel in kept_rels:
                verdicts[(item.root, item.rel)] = (False, Reason.KEEP_NEWEST.format(keep=settings.keep_backups))
            else:
                verdicts[(item.root, item.rel)] = (True, Reason.PRUNE_EXTRA.format(keep=settings.keep_backups))

    for item in sorted(items, key=lambda i: (i.root, i.rel)):
        if not under_context(f'{item.root}/{item.rel}', settings.context):
            continue
        prune, reason = verdicts[(item.root, item.rel)]
        path = item.path(roots)
        if prune:
            try:
                stamp = long_path(path).stat().st_mtime
            except OSError as error:
                out.errors.append(f'{path}: {error}')
                continue
            if age_days(stamp, now) < settings.backup_days:
                prune, reason = False, Reason.KEEP_RECENT.format(days=settings.backup_days)
        parts = item.rel.split('/')
        out.entries.append(Entry(
            path, f'{item.root}/{item.rel}', f'{item.root}/{"/".join(parts[:-2])}', item.size, prune, reason,
        ))
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    root_paths = category_plan.payload or {}

    def still_ok(entry: Entry) -> str | None:
        purpose, _sep, rel = entry.label.partition('/')
        root = root_paths.get(purpose)
        if purpose not in WORKFILE_PURPOSES or root is None or entry.path != root.joinpath(*rel.split('/')):
            return 'not in a workfile folder'
        if entry.path.parent.name != BACKUP_DIR or backup_of(entry.path.name) is None:
            return 'not a workfile backup'
        if not within(entry.path.parent, root):
            return 'outside its storage root'
        path = long_path(entry.path)
        if not path.is_file() or path.is_symlink():
            return 'not a file'
        return None

    return delete_entries(category_plan, log_path, still_ok)


CATEGORY = Category(
    'backups', 'Houdini backups',
    "Houdini's save backups of workfile versions that have since been saved again",
    ('assets', 'shots', 'groups'), plan, apply,
)
