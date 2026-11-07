"""Prune old export versions of shots and assets, keeping the newest few.

    python -m tumblepipe.prune exports [project] [--keep 3] [--context shots] [--apply]

Every export writes a new ``vNNNN`` folder (``export/<entity>/<channel>/
<department>/vNNNN``, ``export/<shot>/_root/vNNNN``) and nothing ever
removes the old ones. Workfiles load the latest, so most old versions are
dead weight. A version folder is pruned only when all of these hold:

- it is **finished** (its ``context.json`` is there; an unfinished one may
  still be being written, and is never touched or counted);
- at least ``--keep`` newer finished versions of the same export exist;
- no **current staged build** names it: the newest version of every
  ``_staged`` folder (a shot's, an asset's, a Root's) pins exact versions
  of the layers it composes, and of the staged assets it brings in, whose
  own pins are followed in turn, as are the ``.usda`` layers among the
  versions kept;
- no **latest workfile** pins it. ``th::import_layer`` always loads the
  exact version it was set to, and ``import_asset`` and the other import
  nodes can be set to one, so the newest ``.hip`` in every department
  folder is read for the versions its nodes name. Only plain ``.hip`` can
  be read: ``.hiplc`` / ``.hipnc`` are encoded, and the listing says how
  many there were and that their pins are not protected.

Staged builds themselves (``_staged``) are pointer scenes, a few KB each,
and are never pruned; nor is anything under ``export/other`` (farm job
folders: ``python -m tumblepipe.prune``). Only shots' and assets' exports
are: published edits (``export/edits``) and the media library
(``export/media``) are never pruned, so the media versions a published edit
plays always stay.

Pruning deletes, so it lists by default and deletes only with ``--apply``,
recording each folder removed in ``export/other/logs/prune-<date>.log``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe import naming
from tumblepipe.archive.inventory import Kind, scan
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import ProjectRoots, project_roots

CONTEXTS = ('shots', 'assets')
DEFAULT_KEEP = 3
LOGS_DIR = ('other', 'logs')
_SKIP_TOP = ('other',)
_STAGED = '_staged'
_ROOT = '_root'

#: Folders inside a workfile folder that hold no workfiles.
_NOT_WORKFILE_DIRS = frozenset({
    'cache', 'lops_cache', 'backup', 'render', 'galleries', '_context',
    '__pycache__',
})
_WORKFILE = re.compile(r'_(v\d{4})\.(hip|hiplc|hipnc)$', re.IGNORECASE)
_PARM = re.compile(r'^(\w+)\t\[[^\]]*\]\t\(\t(.*)\t\)\s*$')
_ENTITY = re.compile(r'entity:/((?:assets|shots)/[A-Za-z0-9_./-]+?)(?=[?"\s@]|$)')
_PINNED_URI = re.compile(r'entity:/[^\s"@<>]+')
_VERSION = re.compile(r'v\d{4,}')
_REF = re.compile(rb'@([^@\r\n]+)@')


class Reason:
    PRUNE = 'older than the newest {keep} finished versions'
    KEEP_NEWEST = 'one of the newest {keep} finished versions'
    KEEP_UNFINISHED = 'unfinished (no context.json): may still be being written'
    KEEP_STAGED = 'a current staged build uses it'
    KEEP_WORKFILE = 'a latest workfile pins it'


@dataclass(frozen=True)
class Version:
    """One export version folder: ``rel`` is its path under the export
    root, ``series`` the folder holding its siblings."""
    rel: str
    series: str
    code: int
    finished: bool
    size: int

    @property
    def name(self) -> str:
        return self.rel.rsplit('/', 1)[-1]

    @property
    def context(self) -> str:
        return self.rel.split('/', 1)[0]


@dataclass(frozen=True)
class Verdict:
    version: Version
    prune: bool
    reason: str


@dataclass
class Plan:
    export_path: Path
    keep: int
    verdicts: list[Verdict] = field(default_factory=list)
    #: latest workfiles read for pins, and those that could not be
    hips_read: int = 0
    hips_unreadable: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def prunable(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.prune]


# ── what the export root holds ─────────────────────────────────────────────


def _unit(parts: tuple[str, ...] | list[str]) -> tuple[str, ...] | None:
    """The version folder a path under the export root is in: its parts up
    to the first version name, or None."""
    for index, part in enumerate(parts):
        if naming.is_valid_version_name(part):
            return tuple(parts[:index + 1])
    return None


def list_versions(roots: ProjectRoots) -> tuple[list[Version], list[Version], list[str]]:
    """``(exports, staged, errors)``: every version folder under the export
    root, its own exports and its staged builds apart. ``other/`` is left
    out."""
    inventory = scan(roots, purposes=('export',))
    sizes: dict[str, int] = defaultdict(int)
    info: dict[str, tuple[str, str, int, bool]] = {}
    for item in inventory.items:
        if item.kind not in (Kind.EXPORT, Kind.STAGED) or item.unit is None:
            continue
        if item.unit.split('/', 1)[0] in _SKIP_TOP:
            continue
        sizes[item.unit] += item.size
        info[item.unit] = (item.kind, item.series, item.version, item.complete)
    exports, staged = [], []
    for unit, (kind, series, code, complete) in sorted(info.items()):
        if kind == Kind.STAGED:
            # Staged builds write context.json last too; one without it is
            # being written (or failed) and is not a current build.
            finished = long_path(roots.get('export').path.joinpath(*unit.split('/'), 'context.json')).is_file()
            staged.append(Version(unit, series, code, finished, sizes[unit]))
        else:
            exports.append(Version(unit, series, code, complete, sizes[unit]))
    return exports, staged, list(inventory.errors)


def _by_series(versions: list[Version]) -> dict[str, list[Version]]:
    series: dict[str, list[Version]] = defaultdict(list)
    for version in versions:
        series[version.series].append(version)
    for group in series.values():
        group.sort(key=lambda v: v.code)
    return series


def current_staged(staged: list[Version]) -> list[Version]:
    """The newest finished version of every staged series (the newest at
    all when none finished: what the resolver takes as latest)."""
    out = []
    for group in _by_series(staged).values():
        done = [v for v in group if v.finished]
        out.append((done or group)[-1])
    return out


# ── what names a version ───────────────────────────────────────────────────


def ref_units(ref: str, file_dir: tuple[str, ...]) -> set[tuple[str, ...]]:
    """The version folders an ``@…@`` ref in a ``.usda`` (or a URI in a
    workfile) names, as parts under the export root. ``file_dir`` is the
    folder (under the export root) of the file holding a relative ref. A
    ref naming no version (latest) names none: the newest is kept anyway."""
    from tumblepipe.pipe.usd.sublayer_refs import parse_entity_sublayer_uri
    if ref.startswith('entity:/'):
        parsed = parse_entity_sublayer_uri(ref)
        if parsed is None or not parsed.version:
            return set()
        segments = tuple(s for s in parsed.base[len('entity:/'):].split('/') if s)
        if parsed.department is None:
            return {(*segments, _STAGED, parsed.channel, parsed.version)}
        if parsed.department == 'root':
            return {(*segments, _ROOT, parsed.version)}
        return {(*segments, parsed.channel, parsed.department, parsed.version)}
    if ':' in ref.split('/', 1)[0] or ref.startswith(('/', '\\')):
        return set()        # another purpose, or an absolute path
    parts = list(file_dir)
    for step in re.split(r'[\\/]', ref):
        if step in ('', '.'):
            continue
        if step == '..':
            if not parts:
                return set()
            parts.pop()
        else:
            parts.append(step)
    unit = _unit(parts)
    return {unit} if unit is not None else set()


def _usda_refs(path: Path) -> list[str]:
    try:
        data = long_path(path).read_bytes()
    except OSError:
        return []
    return [m.group(1).decode('utf-8', 'replace') for m in _REF.finditer(data)]


def staged_closure(
    export_path: Path, start: set[tuple[str, ...]], known: set[tuple[str, ...]],
) -> set[tuple[str, ...]]:
    """Every version folder reached from ``start`` through the ``.usda``
    files in it, followed transitively (a shot's staged build names an
    asset's, which names that asset's layers). Only folders in ``known``
    are followed and returned."""
    seen: set[tuple[str, ...]] = set()
    todo = [unit for unit in start if unit in known]
    while todo:
        unit = todo.pop()
        if unit in seen:
            continue
        seen.add(unit)
        folder = export_path.joinpath(*unit)
        for dirpath, _dirs, files in os.walk(long_path(folder)):
            rel_dir = unit + tuple(Path(dirpath).relative_to(long_path(folder)).parts)
            for name in files:
                if not name.lower().endswith('.usda'):
                    continue
                for ref in _usda_refs(Path(dirpath) / name):
                    for target in ref_units(ref, rel_dir):
                        if target in known and target not in seen:
                            todo.append(target)
    return seen


def latest_workfiles(roots: ProjectRoots) -> list[tuple[str, tuple[str, ...], Path]]:
    """``(root purpose, folder parts, file)`` for the newest workfile in
    every workfile folder of the assets, shots and groups roots."""
    found = []
    for purpose in ('assets', 'shots', 'groups'):
        root = roots.get(purpose)
        if root is None or not root.path.is_dir():
            continue
        others = {long_path(r.path) for r in roots.roots if r.path != root.path}
        for dirpath, dirs, files in os.walk(long_path(root.path)):
            here = Path(dirpath)
            dirs[:] = [
                d for d in dirs
                if d not in _NOT_WORKFILE_DIRS and not d.startswith('.')
                and here / d not in others
            ]
            newest = None
            for name in files:
                match = _WORKFILE.search(name)
                if match is None:
                    continue
                code = naming.get_version_code(match.group(1).lower())
                if newest is None or code > newest[0]:
                    newest = (code, name)
            if newest is not None:
                parts = here.relative_to(long_path(root.path)).parts
                found.append((purpose, tuple(parts), here / newest[1]))
    return found


def _workfile_entity(purpose: str, parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """The entity a workfile folder (``<…entity>/<department>``) is for,
    as export parts; None for a Multi's (it covers several)."""
    if purpose not in CONTEXTS or len(parts) < 2:
        return None
    return (purpose, *parts[:-1])


_ROW = re.compile(r'(\d+)(?!.*\d)')
# Parms that only show a value: ``*_label`` (a node on latest shows what it
# resolved to there) and import_shot's Layer Stack rows (``layer_3_version``,
# Label parms rebuilt on every import).
_LABEL = re.compile(r'_label\d*$|^layer_\d+_version$')


def _row(name: str) -> int | None:
    """The row of a multi-row parm (``entity2``, ``layer_3_version``): its
    last number; None for a plain parm."""
    match = _ROW.search(name)
    return int(match.group(1)) if match else None


Pin = tuple[tuple[str, ...], str, 'str | None']


def hip_pins(text: str, own: tuple[str, ...] | None) -> tuple[set[Pin], set[tuple[str, ...]]]:
    """What a plain ``.hip`` pins: ``(entity parts, version name,
    department or None)``, and exact version folders (from URIs carrying
    ``version=``).

    A node's parms sit in one ``{ … }`` block, one ``name [ … ] ( value )``
    line each. An exact version a node holds (``v0003``) pins that version
    of the entity the node names: on a multi-row parm (``version2``,
    ``layer_3_version``) the entity of the same row if it has one, else the
    node's own entity parm; narrowed to the row's department when the row
    names one. ``from_context`` on an entity parm stands for the
    workfile's own entity (``own``). More is kept than strictly needed
    (the version of that entity in every channel), never less.
    Parms that only show a value (``*_label``, import_shot's
    ``layer_N_version``) are left out.
    """
    pins: set[Pin] = set()
    exact: set[tuple[str, ...]] = set()
    entities: dict[int | None, set[tuple[str, ...]]] = {}
    versions: dict[int | None, set[str]] = {}
    departments: dict[int | None, str] = {}

    def flush():
        everyone = set().union(*entities.values()) if entities else set()
        for row, names in versions.items():
            # The row's own entity, else the node's, else any it names.
            targets = entities.get(row) or entities.get(None) or everyone
            department = departments.get(row)
            for entity in targets:
                pins.update((entity, v, department) for v in names)

    for line in text.splitlines():
        if line.startswith('}') or line.startswith('{'):
            flush()
            entities, versions, departments = {}, {}, {}
            continue
        match = _PARM.match(line)
        if match is None:
            continue
        name, value = match.group(1), match.group(2).strip().strip('"')
        if _LABEL.search(name):
            continue
        row = _row(name)
        for uri in _PINNED_URI.findall(value):
            if 'version=' in uri:
                exact.update(ref_units(uri, ()))
        for entity in _ENTITY.findall(value):
            entities.setdefault(row, set()).add(tuple(entity.strip('/').split('/')))
        lowered = name.lower()
        if 'entity' in lowered and value.startswith('from_context') and own is not None:
            entities.setdefault(row, set()).add(own)
        if ('department' in lowered or lowered.startswith('dept')) and re.fullmatch(r'[A-Za-z0-9_]+', value) \
                and value != 'from_context':
            departments[row] = value
        if _VERSION.fullmatch(value) and naming.is_valid_version_name(value):
            versions.setdefault(row, set()).add(value)
    flush()
    return pins, exact


# ── the plan ───────────────────────────────────────────────────────────────


def _in_context(version: Version, context: str | None) -> bool:
    if version.context not in CONTEXTS:
        return False
    if not context:
        return True
    prefix = context.strip('/')
    return version.rel == prefix or version.rel.startswith(prefix + '/')


def make_plan(
    roots: ProjectRoots, keep: int = DEFAULT_KEEP, context: str | None = None,
    *, read_workfiles: bool = True,
) -> Plan:
    """A verdict for every export version of the shots and assets (those
    under ``context``, a context or a path like ``shots/sq010``)."""
    if keep < 1:
        raise ValueError('keep at least one version')
    export_path = roots.get('export').path
    exports, staged, errors = list_versions(roots)
    plan = Plan(export_path, keep, errors=errors)
    known = {tuple(v.rel.split('/')) for v in (*exports, *staged)}

    start = {tuple(v.rel.split('/')) for v in current_staged(staged)}
    pinned_by_workfile: set[tuple[str, ...]] = set()
    if read_workfiles:
        units_by_entity_version: dict[tuple[tuple[str, ...], str], list[tuple[str, ...]]] = defaultdict(list)
        for unit in known:
            for depth in range(1, len(unit)):
                units_by_entity_version[(unit[:depth], unit[-1])].append(unit)
        for purpose, parts, path in latest_workfiles(roots):
            if not path.name.lower().endswith('.hip'):
                plan.hips_unreadable.append(f'{purpose}/{"/".join(parts)}/{path.name}')
                continue
            try:
                text = long_path(path).read_bytes().decode('utf-8', 'replace')
            except OSError as error:
                plan.errors.append(f'{path}: {error}')
                continue
            plan.hips_read += 1
            own = _workfile_entity(purpose, parts)
            pins, exact = hip_pins(text, own)
            for entity, version, department in pins:
                for unit in units_by_entity_version.get((entity, version), ()):
                    # A department narrows it to that department's layers
                    # (``root``: the shot's root layer), but never away
                    # from a staged build: import_shot's version is its
                    # staged build's, set beside the department it works in.
                    within = unit[len(entity):-1]
                    if department is None or department in within or _STAGED in within or (
                        department == 'root' and _ROOT in within
                    ):
                        pinned_by_workfile.add(unit)
            pinned_by_workfile.update(u for u in exact if u in known)
    staged_used = staged_closure(export_path, start, known)
    workfile_used = staged_closure(export_path, pinned_by_workfile, known)

    for series in _by_series([v for v in exports if _in_context(v, context)]).values():
        finished = [v for v in series if v.finished]
        newest = {v.rel for v in finished[-keep:]}
        for version in series:
            unit = tuple(version.rel.split('/'))
            if not version.finished:
                verdict = Verdict(version, False, Reason.KEEP_UNFINISHED)
            elif version.rel in newest:
                verdict = Verdict(version, False, Reason.KEEP_NEWEST.format(keep=keep))
            elif unit in staged_used:
                verdict = Verdict(version, False, Reason.KEEP_STAGED)
            elif unit in workfile_used:
                verdict = Verdict(version, False, Reason.KEEP_WORKFILE)
            else:
                verdict = Verdict(version, True, Reason.PRUNE.format(keep=keep))
            plan.verdicts.append(verdict)
    return plan


def apply(plan: Plan, log_path: Path) -> tuple[int, int, list[str]]:
    """Delete the version folders ``plan`` marks prune; returns (folders,
    bytes, errors). A folder is checked again just before it goes: still a
    finished export version folder under the export root, outside
    ``_staged`` and ``other``."""
    removed = removed_bytes = 0
    errors = []
    export_path = plan.export_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f'# prune exports {dt.datetime.now().isoformat(timespec="seconds")} '
                  f'keep {plan.keep} by {os.environ.get("TH_USER") or "unknown"}\n')
        for verdict in plan.prunable:
            parts = verdict.version.rel.split('/')
            path = plan.export_path.joinpath(*parts)
            ok = (
                len(parts) >= 3 and parts[0] in CONTEXTS
                and _STAGED not in parts and parts[0] not in _SKIP_TOP
                and naming.is_valid_version_name(parts[-1])
                and _unit(parts) == tuple(parts)
                and path.resolve().parent.is_relative_to(export_path)
                and long_path(path / 'context.json').is_file()
            )
            if not ok:
                errors.append(f'{path}: not a finished export version folder, left alone')
                continue
            try:
                shutil.rmtree(long_path(path))
            except OSError as error:
                errors.append(f'{path}: {error}')
                continue
            removed += 1
            removed_bytes += verdict.version.size
            log.write(f'{verdict.version.rel}\t{verdict.version.size}\t{verdict.reason}\n')
    return removed, removed_bytes, errors


# ── command line ───────────────────────────────────────────────────────────


def human(count: int) -> str:
    size = float(count)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{int(size)} B' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{count} B'


def summary(plan: Plan) -> list[str]:
    """The listing, as lines: per reason, then per entity what goes."""
    lines = [f'{len(plan.verdicts)} export version(s) under {plan.export_path}']
    by_reason: dict[tuple[bool, str], list[Verdict]] = defaultdict(list)
    for verdict in plan.verdicts:
        by_reason[(verdict.prune, verdict.reason)].append(verdict)
    for (prune, reason), group in sorted(by_reason.items(), key=lambda kv: (not kv[0][0], kv[0][1])):
        size = sum(v.version.size for v in group)
        lines.append(f'  {"prune" if prune else "keep ":<5}  {len(group):>6} version(s)  {human(size):>10}  {reason}')
    lines.append(f'Read {plan.hips_read} latest workfile(s) for pinned versions.')
    if plan.hips_unreadable:
        lines.append(
            f'{len(plan.hips_unreadable)} latest workfile(s) are .hiplc/.hipnc and cannot be read: '
            'versions they pin are NOT protected. Save them as .hip to protect them, or prune '
            'only where they are not.'
        )
    per_entity: dict[str, list[Verdict]] = defaultdict(list)
    for verdict in plan.prunable:
        per_entity['/'.join(verdict.version.series.split('/')[:3])].append(verdict)
    if per_entity:
        lines.append('')
        lines.append('To prune, by entity:')
        for entity, group in sorted(per_entity.items()):
            lines.append(f'  {entity:<40} {len(group):>4} version(s)  {human(sum(v.version.size for v in group)):>10}')
    for error in plan.errors[:20]:
        lines.append(f'  ! {error}')
    return lines


def _export_path(project: Path) -> Path:
    root = project_roots(project).get('export')
    return root.path if root is not None else project / 'export'


def log_path_for(plan: Plan) -> Path:
    return plan.export_path.joinpath(*LOGS_DIR, f'prune-{dt.date.today().isoformat()}.log')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m tumblepipe.prune exports', description=__doc__.split('\n\n')[0],
    )
    parser.add_argument('project', nargs='?', help='project folder (default: $TH_PROJECT_PATH)')
    parser.add_argument('--keep', type=int, default=DEFAULT_KEEP,
                        help=f'finished versions to keep of every export (default {DEFAULT_KEEP})')
    parser.add_argument('--context', default=None,
                        help='only shots, only assets, or a path under them (shots/sq010)')
    parser.add_argument('--apply', action='store_true',
                        help='delete the versions listed as prunable (default: only list them)')
    args = parser.parse_args(argv)
    if args.keep < 1:
        print('--keep must be at least 1', file=sys.stderr)
        return 2
    if args.context and args.context.strip('/').split('/', 1)[0] not in CONTEXTS:
        print('--context must be shots, assets or a path under one of them', file=sys.stderr)
        return 2

    project = Path(args.project or os.environ.get('TH_PROJECT_PATH') or '')
    if not project.is_dir():
        print('no project: pass its folder or set TH_PROJECT_PATH', file=sys.stderr)
        return 2
    print('Reading the exports and the latest workfiles …', flush=True)
    plan = make_plan(project_roots(project), args.keep, args.context)
    print('\n'.join(summary(plan)))
    prunable = plan.prunable
    if not prunable:
        return 0
    size = human(sum(v.version.size for v in prunable))
    if not args.apply:
        print(f'\nNothing deleted. Run again with --apply to delete the '
              f'{len(prunable)} prunable version(s) ({size}).')
        return 0
    log_path = log_path_for(plan)
    removed, removed_bytes, errors = apply(plan, log_path)
    print(f'\nDeleted {removed} version(s), {human(removed_bytes)}. Logged to {log_path}.')
    for error in errors:
        print(f'  {error}')
    return 1 if errors else 0
