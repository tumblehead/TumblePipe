"""What goes into an archive, per profile, with a reason for everything else.

Three profiles (``designs/project-archive.md`` §3):

- ``proof``  — for the client: every workfile version, and the latest
  finished version of every export;
- ``latest`` — small cold storage: the latest workfile per department and
  the same exports;
- ``full``   — complete cold storage: every workfile version and every
  finished export version.

Never archived: Houdini's ``backup/`` hips, farm job copies, noise, and
anything outside the pipeline (client material, the edit, delivery) unless
``_config/archive.json`` includes it. Caches and renders are derived and
only go in when asked (``with_caches`` / ``with_renders``), and then only
their latest complete version.

**Only the latest export.** The pipeline's import nodes move to the latest
version of each export when a workfile opens, so a workfile never needs an
older one: ``proof`` and ``latest`` keep the newest *finished* version of
each export series (an abandoned, unfinished newer one is skipped, its
predecessor kept). Older versions a staged scene once pinned are left out.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from tumblepipe.archive.inventory import Inventory, Item, Kind


class Profile:
    PROOF = 'proof'
    LATEST = 'latest'
    FULL = 'full'
    ALL = (PROOF, LATEST, FULL)


class Reason:
    INCLUDED = 'included'
    NOT_LATEST_WORKFILE = 'older workfile version (latest profile)'
    SUPERSEDED = 'older export version (workfiles load the latest)'
    INCOMPLETE = 'unfinished version (no context.json)'
    BACKUP = 'Houdini backup file'
    NOISE = 'temporary or backup file'
    FARM_JOB = 'farm job copy of a workfile'
    DERIVED = 'derived: re-made from the workfiles'
    CACHE = 'cache: re-made by the workfile that wrote it'
    CACHE_UNKNOWN = 'cache with no record of what wrote it'
    CACHE_OLD = 'older cache version'
    RENDER = 'render (opt in with renders)'
    RENDER_OLD = 'older render version'
    NOT_PIPELINE = 'not pipeline data (client material, edit, delivery)'
    ENVIRONMENT = 'installed software (Python environment, node_modules)'


@dataclass(frozen=True)
class Options:
    profile: str = Profile.PROOF
    with_caches: bool = False
    with_renders: bool = False

    def __post_init__(self):
        if self.profile not in Profile.ALL:
            raise ValueError(f'unknown profile {self.profile!r}; one of {", ".join(Profile.ALL)}')


@dataclass(frozen=True)
class Decision:
    item: Item
    include: bool
    reason: str


@dataclass
class Plan:
    inventory: Inventory
    options: Options
    decisions: list[Decision] = field(default_factory=list)
    #: things a person should look at before sending the archive anywhere
    warnings: list[str] = field(default_factory=list)
    #: absolute paths the archived scenes name (archive.references)
    references: object = None

    def included(self) -> list[Item]:
        return [d.item for d in self.decisions if d.include]

    def excluded(self) -> list[Decision]:
        return [d for d in self.decisions if not d.include]

    def summary(self) -> dict[str, dict[str, int]]:
        """Per kind: files and bytes included and excluded."""
        table: dict[str, dict[str, int]] = defaultdict(
            lambda: dict(files_in=0, bytes_in=0, files_out=0, bytes_out=0)
        )
        for decision in self.decisions:
            row = table[decision.item.kind]
            side = 'in' if decision.include else 'out'
            row[f'files_{side}'] += 1
            row[f'bytes_{side}'] += decision.item.size
        return dict(table)

    def excluded_by_reason(self) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = defaultdict(lambda: dict(files=0, bytes=0))
        for decision in self.excluded():
            table[decision.reason]['files'] += 1
            table[decision.reason]['bytes'] += decision.item.size
        return dict(table)


# ── series bookkeeping ─────────────────────────────────────────────────────


def _versions_by_series(items, kinds) -> dict[str, dict[int, bool]]:
    """series -> {version: complete} over the units of ``kinds``."""
    series: dict[str, dict[int, bool]] = defaultdict(dict)
    for item in items:
        if item.kind in kinds and item.unit is not None:
            key = f'{item.root}:{item.series}'
            series[key][item.version] = item.complete
    return series


def _latest(versions: dict[int, bool], *, complete_only: bool = True) -> int | None:
    codes = [v for v, done in versions.items() if done or not complete_only]
    return max(codes) if codes else None


# ── decisions ──────────────────────────────────────────────────────────────

_ALWAYS = {
    Kind.CONFIG, Kind.WORKFILE_META, Kind.WORKFILE_FILE, Kind.PIPELINE_DIR,
    Kind.PROJECT_FILE, Kind.EXTRA, Kind.EXPORT_FILE, Kind.EXPORT_LOG,
}
_NEVER = {
    Kind.BACKUP: Reason.BACKUP,
    Kind.NOISE: Reason.NOISE,
    Kind.ENVIRONMENT: Reason.ENVIRONMENT,
    Kind.FARM_JOB: Reason.FARM_JOB,
    Kind.LOCAL_RENDER: Reason.DERIVED,
    Kind.DERIVED_TEXTURE: Reason.DERIVED,
    Kind.DERIVED_ROOT: Reason.DERIVED,
    Kind.EDIT: Reason.NOT_PIPELINE,
    Kind.UNMANAGED: Reason.NOT_PIPELINE,
}


def _human_bytes(count: int) -> str:
    size = float(count)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
        size /= 1024
    return f'{count} B'


def make_plan(
    inventory: Inventory, options: Options, *, scan_references: bool = True,
) -> Plan:
    """Decide every file of ``inventory`` under ``options``.

    With ``scan_references`` the archived scenes are also read for absolute
    paths (``archive.references``), and what they find joins the warnings.
    """
    plan = Plan(inventory, options)
    profile = options.profile
    items = inventory.items

    workfiles = _versions_by_series(items, (Kind.WORKFILE,))
    exports = _versions_by_series(items, (Kind.EXPORT, Kind.STAGED))
    caches = _versions_by_series(items, (Kind.CACHE,))
    renders = _versions_by_series(items, (Kind.RENDER,))

    def latest_of(table, item, complete_only=True):
        return _latest(table[f'{item.root}:{item.series}'], complete_only=complete_only)

    def decide(item: Item) -> tuple[bool, str]:
        kind = item.kind
        if kind in _ALWAYS:
            return True, Reason.INCLUDED
        if kind in _NEVER:
            return False, _NEVER[kind]
        if kind == Kind.WORKFILE:
            if profile == Profile.LATEST and item.version != latest_of(workfiles, item, False):
                return False, Reason.NOT_LATEST_WORKFILE
            return True, Reason.INCLUDED
        if kind in (Kind.EXPORT, Kind.STAGED):
            if item.unit is None:
                return True, Reason.INCLUDED
            if not item.complete:
                return False, Reason.INCOMPLETE
            if profile == Profile.FULL:
                return True, Reason.INCLUDED
            if item.version == latest_of(exports, item):
                return True, Reason.INCLUDED
            return False, Reason.SUPERSEDED
        if kind == Kind.CACHE:
            if item.unit is None or not item.complete:
                return False, Reason.CACHE_UNKNOWN
            if not options.with_caches:
                return False, Reason.CACHE
            if item.version != latest_of(caches, item):
                return False, Reason.CACHE_OLD
            return True, Reason.INCLUDED
        if kind == Kind.RENDER:
            if not options.with_renders:
                return False, Reason.RENDER
            if item.unit is not None and item.version != latest_of(renders, item):
                return False, Reason.RENDER_OLD
            return True, Reason.INCLUDED
        return False, Reason.NOT_PIPELINE

    for item in items:
        include, reason = decide(item)
        plan.decisions.append(Decision(item, include, reason))

    _add_warnings(plan)
    if scan_references:
        from tumblepipe.archive import references
        plan.references = references.scan_references(plan)
        plan.warnings.extend(
            references.warnings_for(plan.references, inventory.roots.project_path)
        )
    return plan


def _add_warnings(plan: Plan) -> None:
    unknown = [d.item for d in plan.decisions if d.reason == Reason.CACHE_UNKNOWN]
    if unknown:
        units = {i.unit or i.rel for i in unknown}
        plan.warnings.append(
            f'{len(units)} cache version(s) ({_human_bytes(sum(i.size for i in unknown))}) '
            'have no record of the workfile that wrote them (cached before '
            'TumblePipe recorded it, or never finished). They are left out; '
            'keep any that cannot be cached again.'
        )
    unmanaged = defaultdict(int)
    for decision in plan.decisions:
        item = decision.item
        if item.kind == Kind.UNMANAGED:
            unmanaged[item.rel.split('/', 1)[0]] += item.size
    if unmanaged:
        listing = ', '.join(
            f'{name} ({_human_bytes(size)})' for name, size in sorted(unmanaged.items())
        )
        plan.warnings.append(
            f'Left out as not pipeline data: {listing}. Add a folder to '
            '"include" in _config/archive.json to archive it.'
        )
    if plan.inventory.errors:
        plan.warnings.append(
            f'{len(plan.inventory.errors)} path(s) could not be read and are '
            'not in the plan.'
        )
