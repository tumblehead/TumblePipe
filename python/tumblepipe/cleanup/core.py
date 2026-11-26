"""What every cleanup category shares: entries, plans, deleting and logging.

A category reads the project and gives every thing it looked at an
:class:`Entry`: a file or folder, its size, whether it goes and why. Its
``apply`` deletes the entries marked prune, checking each once more just
before it goes. The categories are listed in :func:`categories`; the project
is read once (``archive.inventory.scan``) for all of those picked.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import ProjectRoots
from tumblepipe.util.text import human_bytes

LOGS_DIR = ('other', 'logs')


@dataclass(frozen=True)
class Settings:
    """What the categories keep. Each default is the careful one."""
    #: only shots, only assets, or a path under them (``shots/sq010``):
    #: narrows exports and renders
    context: str | None = None
    #: finished versions kept of every export
    keep_exports: int = 3
    #: published versions kept of every render layer
    keep_renders: int = 2
    #: a check render goes once it is this old
    check_render_days: float = 14.0
    #: finished versions kept of every th::cache
    keep_caches: int = 2
    #: backups kept of each department's latest workfile version
    keep_backups: int = 5
    #: no backup newer than this goes
    backup_days: float = 7.0
    #: a ``_deleted/<stamp>`` folder goes once it is this old
    deleted_days: float = 30.0
    #: a farm job folder no Deadline job names is kept until this old
    job_days: float = 2.0
    #: and a local farm's, which only its own machine can vouch for, this old
    local_job_days: float = 14.0

    def problem(self) -> str | None:
        if self.keep_exports < 1:
            return 'keep at least one export version'
        if self.keep_renders < 1:
            return 'keep at least one render version'
        if self.keep_caches < 1:
            return 'keep at least one cache version'
        if self.keep_backups < 0:
            return 'the number of backups to keep cannot be negative'
        for name in ('check_render_days', 'backup_days', 'deleted_days', 'job_days', 'local_job_days'):
            if getattr(self, name) < 0:
                return f'{name.replace("_", " ")} cannot be negative'
        if self.context and self.context.strip('/').split('/', 1)[0] not in ('shots', 'assets'):
            return 'the context must be shots, assets or a path under one of them'
        return None


@dataclass(frozen=True)
class Entry:
    """One file or folder a category looked at."""
    path: Path
    #: how it is listed and logged: its path under its storage root
    label: str
    #: what the listing totals by (an entity, a department folder)
    group: str
    size: int
    prune: bool
    reason: str


@dataclass
class CategoryPlan:
    key: str
    title: str
    #: what one entry is called: 'version', 'folder', 'file'
    noun: str = 'item'
    entries: list[Entry] = field(default_factory=list)
    #: lines the listing shows under the category (what it could not read)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: whatever the category's own ``apply`` needs
    payload: object = None

    @property
    def prunable(self) -> list[Entry]:
        return [entry for entry in self.entries if entry.prune]

    @property
    def prunable_size(self) -> int:
        return sum(entry.size for entry in self.entries if entry.prune)


@dataclass
class Plan:
    roots: ProjectRoots
    settings: Settings
    categories: list[CategoryPlan] = field(default_factory=list)

    def get(self, key: str) -> CategoryPlan | None:
        for category in self.categories:
            if category.key == key:
                return category
        return None

    @property
    def prunable(self) -> list[Entry]:
        return [entry for category in self.categories for entry in category.prunable]

    @property
    def prunable_size(self) -> int:
        return sum(category.prunable_size for category in self.categories)


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    #: one line for the window and ``--help``
    about: str
    #: the storage roots it needs read (``archive.inventory.scan``)
    purposes: tuple[str, ...]
    #: ``(roots, inventory, settings, *, workfiles) -> plan``: ``workfiles``
    #: (``hip.Workfiles``) the latest workfiles, read once for every
    #: category that asks
    plan: Callable[..., CategoryPlan]
    #: ``(category plan, log path) -> (removed, bytes, errors)``
    apply: Callable[[CategoryPlan, Path], tuple[int, int, list[str]]]


def categories() -> list[Category]:
    """The categories, in the order they are listed."""
    from tumblepipe.cleanup import backups, caches, deleted, exports, jobs, renders
    return [exports.CATEGORY, renders.CATEGORY, caches.CATEGORY, backups.CATEGORY, jobs.CATEGORY, deleted.CATEGORY]


CATEGORY_KEYS = ('exports', 'renders', 'caches', 'backups', 'jobs', 'deleted')


def age_days(stamp: float, now: float) -> float:
    return (now - stamp) / 86400


def export_path(roots: ProjectRoots) -> Path:
    return roots.get('export').path


def log_path_for(roots: ProjectRoots) -> Path:
    return export_path(roots).joinpath(*LOGS_DIR, f'cleanup-{dt.date.today().isoformat()}.log')


def make_plan(
    roots: ProjectRoots, settings: Settings | None = None, only: tuple[str, ...] | list[str] | None = None,
    *, progress: Callable[[str], None] | None = None,
) -> Plan:
    """A plan for the categories in ``only`` (all of them when None). Reads
    the project; changes nothing."""
    from tumblepipe.archive.inventory import scan
    from tumblepipe.cleanup import hip
    settings = settings or Settings()
    problem = settings.problem()
    if problem:
        raise ValueError(problem)
    picked = [c for c in categories() if only is None or c.key in only]
    unknown = sorted(set(only or ()) - set(CATEGORY_KEYS))
    if unknown:
        raise ValueError(f'no such category: {", ".join(unknown)} (there are {", ".join(CATEGORY_KEYS)})')
    purposes = tuple(dict.fromkeys(p for c in picked for p in c.purposes))
    inventory = None
    if purposes:
        if progress:
            progress('Reading the project …')
        inventory = scan(roots, purposes=purposes)
    plan = Plan(roots, settings)
    workfiles = hip.Workfiles(roots)
    for category in picked:
        if progress:
            progress(f'{category.title} …')
        plan.categories.append(category.plan(roots, inventory, settings, workfiles=workfiles))
    return plan


def apply(plan: Plan, log_path: Path | None = None) -> dict[str, tuple[int, int, list[str]]]:
    """Delete what ``plan`` marks prune; ``{category: (removed, bytes,
    errors)}``. A category that fails outright is reported as its error and
    the others still run."""
    log_path = log_path or log_path_for(plan.roots)
    by_key = {c.key: c for c in categories()}
    results = {}
    for category_plan in plan.categories:
        if not category_plan.prunable:
            results[category_plan.key] = (0, 0, [])
            continue
        try:
            results[category_plan.key] = by_key[category_plan.key].apply(category_plan, log_path)
        except Exception as error:  # reported per category, the rest still runs
            results[category_plan.key] = (0, 0, [f'{category_plan.title}: {type(error).__name__}: {error}'])
    return results


def delete_entries(
    category_plan: CategoryPlan, log_path: Path, still_ok: Callable[[Entry], str | None],
) -> tuple[int, int, list[str]]:
    """Delete the entries marked prune, each only if ``still_ok`` finds no
    problem with it now; log each one removed. Returns (removed, bytes,
    errors)."""
    removed = removed_bytes = 0
    errors = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f'# cleanup {category_plan.key} {dt.datetime.now().isoformat(timespec="seconds")} '
                  f'by {os.environ.get("TH_USER") or "unknown"}\n')
        for entry in category_plan.prunable:
            problem = still_ok(entry)
            if problem:
                errors.append(f'{entry.path}: {problem}, left alone')
                continue
            target = long_path(entry.path)
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except OSError as error:
                errors.append(f'{entry.path}: {error}')
                continue
            removed += 1
            removed_bytes += entry.size
            log.write(f'{entry.label}\t{entry.size}\t{entry.reason}\n')
    return removed, removed_bytes, errors


#: A version folder holding a file of this name is never deleted.
KEEP_FILE = '.keep'
#: A version written to this recently may still be being written.
RECENT_DAYS = 2.0


def recently_written(path: Path, now: float, days: float) -> bool:
    """Whether the version folder ``path`` or a folder in it changed in the
    last ``days`` (a folder's time moves when a file is added to it)."""
    limit = now - days * 86400
    try:
        if long_path(path).stat().st_mtime > limit:
            return True
        for entry in os.scandir(long_path(path)):
            if entry.stat(follow_symlinks=False).st_mtime > limit:
                return True
    except OSError:
        return True         # cannot tell: keep
    return False


def under_context(label: str, context: str | None) -> bool:
    """Whether ``label`` (``shots/sq010/010/fx/…``) is under ``context``
    (``shots``, ``shots/sq010``); everything is under no context."""
    prefix = context.strip('/') if context else ''
    return not prefix or label == prefix or label.startswith(prefix + '/')


def within(path: Path, parent: Path) -> bool:
    """Whether ``path`` (resolved) sits under ``parent`` (resolved)."""
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def summary(plan: Plan, *, detail: int = 40) -> list[str]:
    """The listing, as lines: per category what goes and what stays by
    reason, then the biggest groups to go."""
    lines = []
    for category in plan.categories:
        prunable = category.prunable
        lines.append(
            f'{category.title}: {len(category.entries)} {category.noun}(s), '
            f'{len(prunable)} to delete ({human_bytes(category.prunable_size)})'
        )
        by_reason: dict[tuple[bool, str], list[Entry]] = defaultdict(list)
        for entry in category.entries:
            by_reason[(entry.prune, entry.reason)].append(entry)
        for (prune, reason), group in sorted(by_reason.items(), key=lambda kv: (not kv[0][0], kv[0][1])):
            size = sum(e.size for e in group)
            lines.append(f'  {"delete" if prune else "keep":<6}  {len(group):>6}  {human_bytes(size):>10}  {reason}')
        lines.extend(f'  {note}' for note in category.notes)
        per_group: dict[str, list[Entry]] = defaultdict(list)
        for entry in prunable:
            per_group[entry.group].append(entry)
        ordered = sorted(per_group.items(), key=lambda kv: -sum(e.size for e in kv[1]))
        if ordered:
            lines.append('  To delete, biggest first:')
            for group, entries in ordered[:detail]:
                lines.append(f'    {human_bytes(sum(e.size for e in entries)):>10}  {len(entries):>5}  {group}')
            if len(ordered) > detail:
                rest = ordered[detail:]
                lines.append(f'    {human_bytes(sum(e.size for g in rest for e in g[1])):>10}  '
                             f'{sum(len(g[1]) for g in rest):>5}  ({len(rest)} more)')
        for error in category.errors[:20]:
            lines.append(f'  ! {error}')
        lines.append('')
    lines.append(f'In all: {len(plan.prunable)} to delete, {human_bytes(plan.prunable_size)}.')
    return lines
