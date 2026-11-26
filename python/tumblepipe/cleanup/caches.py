"""Old ``th::cache`` versions.

``th::cache`` writes ``<department>/cache/<name>/vNNNN`` (SOP,
``.bgeo.sc``) or ``…/lops_cache/<name>/vNNNN`` (LOP, USD) in a workfile
folder, or under the proxy root, and nothing removes the old ones. A cache
is the one derived thing other files point straight into: a cache node
loads the exact version it is set to, and a published export references a
cache's files by absolute path instead of copying them. Like every derived
thing the cleanup removes, a cache is made again by caching again, so
whether its ``context.json`` record is there, or names a workfile still in
the project, does not matter. A version goes when all of these hold:

- at least ``keep_caches`` newer versions of the same cache exist (so the
  highest, whose number plus one the next cache takes, always stays: a node
  set to it would load the new files);
- no **latest workfile** names it: a node whose Name and Version are its
  (in any department: a cache node can load another department's), or any
  path into it;
- no **export** on disk references it: every USD layer under the export
  root is read for the files it references. Where USD cannot be loaded
  (``pxr``), nothing is deleted;
- nothing was written to it in the last two days, and it holds no
  ``.keep`` file.
"""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from tumblepipe import naming
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import WORKFILE_PURPOSES
from tumblepipe.cleanup import hip
from tumblepipe.cleanup.core import (
    KEEP_FILE, RECENT_DAYS, Category, CategoryPlan, Entry, Settings, delete_entries, recently_written,
    under_context, within,
)

CACHE_DIRS = ('cache', 'lops_cache')
PURPOSES = (*WORKFILE_PURPOSES[:3], 'proxy')       # assets, shots, groups, proxy
_LAYERS = ('.usd', '.usda', '.usdc')
_INTO = re.compile(r'(?:^|/)((?:cache|lops_cache)/[^/"\'\s]+/v\d{4,})(?![0-9])')


class Reason:
    PRUNE = 'older than the newest {keep} versions'
    KEEP_NEWEST = 'one of the newest {keep} versions'
    KEEP_WORKFILE = 'a latest workfile names it'
    KEEP_EXPORT = 'an export references it'
    KEEP_NO_USD = 'the exports could not be read here (no USD): nothing is deleted'
    KEEP_RECENT = 'written to in the last {days:g} days'
    KEEP_MARKED = f'marked to keep ({KEEP_FILE})'


@dataclass
class Unit:
    """One cache version folder: ``rel`` under the root of ``purpose``."""
    purpose: str
    rel: str
    code: int
    size: int = 0
    marked: bool = False

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.rel.split('/'))

    @property
    def key(self) -> tuple[str, str]:
        return self.purpose, self.rel

    @property
    def series(self) -> tuple[str, str]:
        return self.purpose, self.rel.rsplit('/', 1)[0]

    @property
    def cache_name(self) -> str:
        return self.parts[-2]

    @property
    def tail(self) -> str:
        """``cache/<name>/vNNNN``: how a path into it ends."""
        return '/'.join(self.parts[-3:])


def cache_unit(parts) -> tuple[str, ...] | None:
    """The cache version folder a file's path parts are in
    (``…/<cache|lops_cache>/<name>/vNNNN/…``), or None."""
    for at, part in enumerate(parts[:-3]):
        if part in CACHE_DIRS and naming.is_valid_version_name(parts[at + 2]):
            return tuple(parts[:at + 3])
    return None


def list_units(inventory) -> dict[tuple[str, str], Unit]:
    units: dict[tuple[str, str], Unit] = {}
    for item in inventory.items:
        if item.root not in PURPOSES:
            continue
        parts = item.rel.split('/')
        unit_parts = cache_unit(parts)
        if unit_parts is None or '_deleted' in parts:
            continue
        key = (item.root, '/'.join(unit_parts))
        unit = units.get(key)
        if unit is None:
            unit = units[key] = Unit(item.root, key[1], naming.get_version_code(unit_parts[-1]))
        unit.size += item.size
        if parts[len(unit_parts):] == [KEEP_FILE]:
            unit.marked = True
    return units


# ── what names a version ───────────────────────────────────────────────────


def hip_names(text: str) -> tuple[set[tuple[str, str]], set[str]]:
    """What a workfile's text names (``archive.hipfile``): ``(cache name,
    version)`` for every node
    holding a ``name`` parm and an exact ``version`` parm (``th::cache``),
    and every ``cache/<name>/vNNNN`` a path in it runs into."""
    pairs: set[tuple[str, str]] = set()
    for block in hip.node_blocks(text):
        names = {value for name, value in block if name == 'name' and value}
        versions = {
            value for name, value in block
            if name == 'version' and naming.is_valid_version_name(value)
        }
        pairs.update((name, version) for name in names for version in versions)
    return pairs, path_tails(text)


def path_tails(text: str) -> set[str]:
    """Every ``<cache|lops_cache>/<name>/vNNNN`` a path in ``text`` runs
    into, with what leads up to it: ``…/fx/cache/smoke/v0003``."""
    flat = re.sub(r'/+', '/', text.replace('\\', '/'))
    found = set()
    for match in _INTO.finditer(flat):
        start = max(flat.rfind(stop, 0, match.start(1)) for stop in ('"', "'", ' ', '\t', '\n', '@'))
        found.add(flat[start + 1:match.end(1)])
    return found


def _named_by_paths(units: dict, paths: set[str]) -> set[tuple[str, str]]:
    """The units the paths lead into: a path ending in a unit's own path
    under its root (whatever drive or share the root was spelled as)."""
    by_tail: dict[str, list[Unit]] = defaultdict(list)
    for unit in units.values():
        by_tail[unit.tail].append(unit)
    named = set()
    for path in paths:
        tail = '/'.join(path.split('/')[-3:])
        for unit in by_tail.get(tail, ()):
            if path == unit.rel or path.endswith('/' + unit.rel):
                named.add(unit.key)
    return named


def named_by_workfiles(
    workfiles: hip.Workfiles, units: dict, notes: list[str], errors: list[str],
) -> set[tuple[str, str]]:
    by_pair: dict[tuple[str, str], list[Unit]] = defaultdict(list)
    for unit in units.values():
        by_pair[(unit.cache_name, unit.parts[-1])].append(unit)
    named: set[tuple[str, str]] = set()
    read = unreadable = 0
    for workfile in workfiles.reads():
        if workfile.unreadable:
            unreadable += 1
            continue
        if workfile.error is not None:
            errors.append(workfile.error)
            continue
        read += 1
        pairs, paths = hip_names(workfile.text)
        for pair in pairs:
            named.update(unit.key for unit in by_pair.get(pair, ()))
        named |= _named_by_paths(units, paths)
    notes.append(f'Read {read} latest workfile(s) for the cache versions they name.')
    if unreadable:
        notes.append(
            f'{unreadable} latest workfile(s) are .hiplc/.hipnc in a form that cannot be read: '
            'cache versions only they load are not kept for them.'
        )
    return named


def layer_references(path: Path) -> list[str]:
    """Every file the USD layer at ``path`` references: sublayers,
    references, payloads, asset-valued attributes and clips. Raises
    ImportError where USD (``pxr``) is not there."""
    from pxr import UsdUtils
    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(str(path))
    return [str(p) for p in (*sublayers, *references, *payloads)]


def referenced_by_exports(roots, units: dict, notes: list[str], errors: list[str]) -> set[tuple[str, str]] | None:
    """The units an export layer on disk references; None where USD cannot
    be loaded."""
    try:
        import pxr  # noqa: F401
    except ImportError:
        return None
    export = roots.get('export')
    if not export.path.is_dir():
        return set()
    paths: set[str] = set()
    read = 0
    for dirpath, dirs, files in os.walk(long_path(export.path)):
        dirs[:] = [d for d in dirs if d not in ('other', '_deleted')]
        for name in files:
            if not name.lower().endswith(_LAYERS):
                continue
            layer_path = Path(dirpath) / name
            read += 1
            try:
                references = layer_references(layer_path)
            except Exception as error:  # USD raises its own error types for a layer it cannot parse
                errors.append(f'{layer_path}: {type(error).__name__}: {str(error)[:200]}')
                # A layer that cannot be read may reference anything.
                return None
            for reference in references:
                paths |= path_tails(reference)
    notes.append(f'Read {read} export layer(s) for the cache files they reference.')
    return _named_by_paths(units, paths)


# ── the plan ───────────────────────────────────────────────────────────────


def plan(
    roots, inventory, settings: Settings, *, workfiles: hip.Workfiles, now: float | None = None,
) -> CategoryPlan:
    out = CategoryPlan('caches', 'Old cache versions', 'version')
    out.payload = {root.purpose: root.path for root in roots.roots}
    if inventory is None:
        return out
    now = time.time() if now is None else now
    units = list_units(inventory)
    keep = settings.keep_caches
    verdicts: dict[tuple[str, str], tuple[bool, str]] = {}
    series: dict[tuple[str, str], list[Unit]] = defaultdict(list)
    for unit in units.values():
        series[unit.series].append(unit)
    for group in series.values():
        group.sort(key=lambda u: u.code)
        newest = {u.key for u in group[-keep:]}
        for unit in group:
            if unit.marked:
                verdict = (False, Reason.KEEP_MARKED)
            elif unit.key in newest:
                verdict = (False, Reason.KEEP_NEWEST.format(keep=keep))
            else:
                verdict = (True, Reason.PRUNE.format(keep=keep))
            verdicts[unit.key] = verdict

    # Only worth reading the workfiles and the exports when something could go.
    if any(prune for prune, _reason in verdicts.values()):
        by_workfile = named_by_workfiles(workfiles, units, out.notes, out.errors)
        by_export = referenced_by_exports(roots, units, out.notes, out.errors)
        for key, (prune, _reason) in list(verdicts.items()):
            if not prune:
                continue
            unit = units[key]
            if key in by_workfile:
                verdicts[key] = (False, Reason.KEEP_WORKFILE)
            elif by_export is None:
                verdicts[key] = (False, Reason.KEEP_NO_USD)
            elif key in by_export:
                verdicts[key] = (False, Reason.KEEP_EXPORT)
            elif recently_written(out.payload[unit.purpose].joinpath(*unit.parts), now, RECENT_DAYS):
                verdicts[key] = (False, Reason.KEEP_RECENT.format(days=RECENT_DAYS))

    for key in sorted(units):
        unit = units[key]
        # The proxy root holds shots/… and assets/… itself.
        if not under_context(unit.rel if unit.purpose == 'proxy' else f'{unit.purpose}/{unit.rel}', settings.context):
            continue
        prune, reason = verdicts[key]
        out.entries.append(Entry(
            out.payload[unit.purpose].joinpath(*unit.parts), f'{unit.purpose}/{unit.rel}',
            f'{unit.purpose}/{"/".join(unit.parts[:-3])}', unit.size, prune, reason,
        ))
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    root_paths = category_plan.payload or {}

    def still_ok(entry: Entry) -> str | None:
        purpose, _sep, rel = entry.label.partition('/')
        root = root_paths.get(purpose)
        parts = tuple(rel.split('/'))
        if purpose not in PURPOSES or root is None or entry.path != root.joinpath(*parts):
            return 'not in a workfile folder or the proxy root'
        if cache_unit((*parts, 'x')) != parts or '_deleted' in parts:
            return 'not a cache version folder'
        if not within(entry.path.parent, root):
            return 'outside its storage root'
        path = long_path(entry.path)
        if not path.is_dir() or path.is_symlink():
            return 'not a folder'
        if (path / KEEP_FILE).exists():
            return f'it holds a {KEEP_FILE} file'
        if recently_written(entry.path, time.time(), RECENT_DAYS):
            return 'it was written to since it was listed'
        return None

    return delete_entries(category_plan, log_path, still_ok)


CATEGORY = Category(
    'caches', 'Old cache versions',
    'th::cache versions older than the newest few, that no latest workfile or export still loads',
    PURPOSES, plan, apply,
)
