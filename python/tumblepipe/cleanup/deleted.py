"""The ``_deleted/<stamp>/`` folders deleting an entity or an edit leaves.

Deleting an asset, a shot or an edit moves its folders to
``<storage root>/_deleted/<YYYYMMDD-HHMMSS>/`` in every storage root
(``pipe.entity_files.archive_folders``), so a mistake can be put back by
hand. Nothing ever empties them. A stamp folder goes once the delete it
records is older than ``deleted_days``; anything else in a ``_deleted``
folder is left alone.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import time
from pathlib import Path

from tumblepipe.archive.longpath import long_path
from tumblepipe.cleanup.core import Category, CategoryPlan, Entry, Settings, age_days, delete_entries

ARCHIVE_DIR = '_deleted'      # pipe.entity_files.ARCHIVE_DIR (not imported: it needs a project)
_STAMP = re.compile(r'^\d{8}-\d{6}$')


class Reason:
    PRUNE = 'deleted more than {days:g} days ago'
    KEEP_RECENT = 'deleted less than {days:g} days ago'
    KEEP_OTHER = 'not a folder a delete made'


def stamp_time(name: str) -> float | None:
    """When the delete that made the folder ``name`` happened (local time)."""
    if not _STAMP.match(name):
        return None
    try:
        return dt.datetime.strptime(name, '%Y%m%d-%H%M%S').timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(long_path(path)):
        for name in files:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def plan(roots, _inventory, settings: Settings, *, workfiles, now: float | None = None) -> CategoryPlan:
    out = CategoryPlan('deleted', 'Deleted entities', 'folder')
    now = time.time() if now is None else now
    holders = {root.purpose: root.path / ARCHIVE_DIR for root in roots.roots}
    out.payload = holders
    for purpose, holder in holders.items():
        try:
            entries = sorted(os.scandir(long_path(holder)), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            path = holder / entry.name
            label = f'{purpose}/{ARCHIVE_DIR}/{entry.name}'
            when = stamp_time(entry.name)
            if when is None or not entry.is_dir(follow_symlinks=False):
                size = _size(path) if entry.is_dir(follow_symlinks=False) else entry.stat(follow_symlinks=False).st_size
                out.entries.append(Entry(path, label, purpose, size, False, Reason.KEEP_OTHER))
                continue
            old = age_days(when, now) >= settings.deleted_days
            reason = (Reason.PRUNE if old else Reason.KEEP_RECENT).format(days=settings.deleted_days)
            out.entries.append(Entry(path, label, purpose, _size(path), old, reason))
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    holders = set((category_plan.payload or {}).values())

    def still_ok(entry: Entry) -> str | None:
        if entry.path.parent not in holders or entry.path.parent.name != ARCHIVE_DIR:
            return f'not in a {ARCHIVE_DIR} folder'
        if stamp_time(entry.path.name) is None:
            return 'not a folder a delete made'
        path = long_path(entry.path)
        if not path.is_dir() or path.is_symlink():
            return 'not a folder'
        return None

    return delete_entries(category_plan, log_path, still_ok)


CATEGORY = Category(
    'deleted', 'Deleted entities',
    'the folders of assets, shots and edits deleted a while ago (kept in _deleted so a mistake can be undone)',
    (), plan, apply,
)
