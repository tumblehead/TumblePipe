"""Old render versions and check renders.

Every render writes a new ``render/<entity>/<department>/<layer>/vNNNN``
under the render root, and nothing removes the old ones: they are most of a
project's bytes. A version folder goes only when all of these hold:

- at least ``keep_renders`` newer **complete** published versions of the
  same layer exist. A version is complete when every frame its
  ``context.json`` records is there in every AOV (one without a record: as
  many frames as the fullest version of its layer); a multi-range render
  never is. The shot's render is the newest complete one, so partial
  renders on top of it never push it out;
- it is not the **highest** version of its layer: the next render takes the
  highest number plus one, and a number used twice would hand everything
  that names the old version the new frames;
- no **latest workfile** names it (``th::import_render`` set to a version,
  a farm-frozen all-channels import's pins);
- no **edit** names it: a published edit plays a render's frames by path,
  a working edit can pin a take, and the sync to the edit root records the
  version it copied (re-syncing a lower number is skipped);
- no kept **composite** version read it (the ``reads`` its ``context.json``
  records), followed through;
- nothing was written to it in the last two days (it may be rendering);
- it holds no ``.keep`` file: put one in a version to keep it whatever
  else is true (a delivered render).

A **check render** (``published: false``: Farm Submit's Step) is never the
shot's render, so it goes by age alone (``check_render_days``), under the
same guards, and its review movie with it.

Only ``render/`` under the render root is read: playblasts, dailies, the
grade's mattes and the turntables are left alone.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe import naming
from tumblepipe.archive.inventory import Kind, file_version
from tumblepipe.archive.longpath import long_path
from tumblepipe.cleanup import hip
from tumblepipe.cleanup.core import (
    KEEP_FILE, RECENT_DAYS, Category, CategoryPlan, Entry, Settings, age_days, delete_entries, recently_written,
    under_context, within,
)

AREA = 'render'
MOVIES = 'playblast'


class Reason:
    PRUNE_OLD = 'older than the newest {keep} complete versions of its layer'
    PRUNE_CHECK = 'a check render older than {days:g} days'
    PRUNE_CHECK_MOVIE = "a check render's review movie, deleted with it"
    KEEP_NEWEST = 'one of the newest {keep} complete versions of its layer, or newer'
    KEEP_HIGHEST = 'the highest version of its layer: its number would be used again'
    KEEP_WORKFILE = 'a latest workfile names it'
    KEEP_EDIT = 'an edit names it'
    KEEP_READ = 'a kept composite version read it'
    KEEP_RECENT = 'written to in the last {days:g} days: it may be rendering'
    KEEP_MARKED = f'marked to keep ({KEEP_FILE})'
    KEEP_CHECK = 'a check render newer than {days:g} days'
    KEEP_CHECK_MOVIE = "a check render's review movie: its render is kept"


@dataclass
class Unit:
    """One render version folder: ``rel`` under the render root."""
    rel: str
    code: int
    size: int = 0
    published: bool = True
    has_context: bool = False
    marked: bool = False
    #: .exr files per folder inside it ('' for those directly in it)
    frames: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.rel.split('/'))

    @property
    def series(self) -> str:
        return self.rel.rsplit('/', 1)[0]

    @property
    def name(self) -> str:
        return self.rel.rsplit('/', 1)[-1]

    @property
    def frame_count(self) -> int:
        """The frames it has in every AOV: the fewest any of them holds."""
        counts = [count for count in self.frames.values() if count]
        return min(counts) if counts else 0


def in_context(rel: str, context: str | None) -> bool:
    """Whether ``rel`` (``render/shots/sq010/…``) is under ``context``
    (``shots``, ``shots/sq010``)."""
    return under_context(rel.split('/', 1)[-1], context)


def list_units(inventory) -> tuple[dict[str, Unit], list]:
    """``(render version folders by rel, check render movie items)`` from a
    scan of the render root."""
    units: dict[str, Unit] = {}
    movies = []
    for item in inventory.items:
        if item.root != 'render' or item.kind != Kind.RENDER or item.unit is None:
            continue
        parts = item.rel.split('/')
        if parts[0] == MOVIES and item.unit == item.rel and not item.published:
            movies.append(item)
            continue
        if parts[0] != AREA or not naming.is_valid_version_name(item.unit.rsplit('/', 1)[-1]):
            continue
        unit = units.get(item.unit)
        if unit is None:
            unit = units[item.unit] = Unit(item.unit, item.version)
        unit.size += item.size
        unit.published = unit.published and item.published
        rest = parts[len(item.unit.split('/')):]
        if rest == ['context.json']:
            unit.has_context = True
        elif rest == [KEEP_FILE]:
            unit.marked = True
        elif rest[-1].lower().endswith('.exr'):
            unit.frames[rest[0] if len(rest) > 1 else ''] += 1
    return units, movies


# ── what names a version ───────────────────────────────────────────────────

#: ``(entity parts, version name, department or None, layer or None)``
Pin = tuple[tuple[str, ...], str, 'str | None', 'str | None']

_VERSION_TOKEN = re.compile(r'(?<![A-Za-z0-9])v\d{4,}(?![0-9])')
_VERSION_PARM = re.compile(r'^(?:version\d*|pins)$')
_PATH = re.compile(r'(?<![A-Za-z0-9_])render/[^"\'\s<>|*?]+?/v\d{4,}(?![0-9])')


def hip_pins(text: str, own: tuple[str, ...] | None) -> set[Pin]:
    """The render versions a workfile's text names (``archive.hipfile``):
    every exact version in a
    node's ``version`` parm (``th::import_render``) or ``pins`` parm (an
    all-channels import the farm froze: ``channel:department:version``), of
    every entity that node names (``from_context``: the workfile's own,
    ``own``). Any department and layer of it: more is kept than strictly
    needed, never less."""
    pins: set[Pin] = set()
    for block in hip.node_blocks(text):
        entities: set[tuple[str, ...]] = set()
        versions: set[str] = set()
        for name, value in block:
            entities.update(hip.entities(value))
            if 'entity' in name.lower() and value.startswith('from_context') and own is not None:
                entities.add(own)
            if _VERSION_PARM.match(name):
                versions.update(_VERSION_TOKEN.findall(value))
        pins.update((entity, version, None, None) for entity in entities for version in versions)
    return pins


def edit_pins(data) -> set[Pin]:
    """The takes an edit document pins: a clip naming a shot
    (``entity:/shots/…``) whose take rule holds an exact version, narrowed
    to the rule's department and layer when it names them."""
    pins: set[Pin] = set()

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for inner in value.values():
                yield from strings(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from strings(inner)

    def walk(value):
        if isinstance(value, dict):
            shot, take = value.get('shot'), value.get('take')
            if isinstance(shot, str) and shot.startswith('entity:/') and isinstance(take, dict):
                entity = tuple(part for part in shot[len('entity:/'):].split('/') if part)
                department = take.get('department') if isinstance(take.get('department'), str) else None
                layer = take.get('layer') if isinstance(take.get('layer'), str) else None
                for text in strings(take.get('version')):
                    if naming.is_valid_version_name(text):
                        pins.add((entity, text, department, layer))
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(data)
    return pins


def path_units(text: str, known) -> set[str]:
    """The render versions the paths in ``text`` lead into: any
    ``…/render/…/vNNNN`` whose tail is a version folder in ``known``."""
    found: set[str] = set()
    flat = re.sub(r'/+', '/', text.replace('\\', '/'))
    for match in _PATH.finditer(flat):
        candidate = match.group(0)
        while True:
            if candidate in known:
                found.add(candidate)
                break
            at = candidate.find('/render/')
            if at < 0:
                break
            candidate = candidate[at + 1:]
    return found


def manifest_pins(data) -> list[tuple[str, str, str, str]]:
    """``(shot key, layer, render department, version)`` for every entry
    of the edit root's ``manifest.json`` (``farm/workers/sync.py``: ``{shot:
    {layer: {aov: [shot department, render department, version]}}}``)."""
    out = []
    if not isinstance(data, dict):
        return out
    for shot, layers in data.items():
        if not isinstance(layers, dict):
            continue
        for layer, aovs in layers.items():
            if not isinstance(aovs, dict):
                continue
            for entry in aovs.values():
                if isinstance(entry, list) and len(entry) == 3 and all(isinstance(e, str) for e in entry):
                    out.append((str(shot), str(layer), entry[1], entry[2]))
    return out


def _read_json(path: Path):
    try:
        with long_path(path).open('r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _edit_files(roots) -> list[Path]:
    """Every edit document: the working versions (the ``edits`` root) and
    the published ones (``export/edits``), with their ``.otio``."""
    files = []
    folders = []
    edits = roots.get('edits')
    if edits is not None:
        folders.append(edits.path)
    export = roots.get('export')
    if export is not None:
        folders.append(export.path / 'edits')
    for folder in folders:
        for dirpath, dirs, names in os.walk(long_path(folder)):
            dirs[:] = [d for d in dirs if d not in ('_deleted', '_context', 'media')]
            files.extend(Path(dirpath) / name for name in names if name.lower().endswith(('.json', '.otio')))
    return files


class _Index:
    def __init__(self, units: dict[str, Unit]):
        self.units = units
        self.by_version: dict[str, list[Unit]] = defaultdict(list)
        for unit in units.values():
            self.by_version[unit.name].append(unit)

    def matching(self, pin: Pin) -> list[str]:
        entity, version, department, layer = pin
        out = []
        for unit in self.by_version.get(version, ()):
            parts = unit.parts
            middle = parts[1 + len(entity):-1]
            if parts[1:1 + len(entity)] != entity:
                continue
            if department is not None and department not in middle:
                continue
            if layer is not None and layer not in middle:
                continue
            out.append(unit.rel)
        return out


def pinned(
    roots, workfiles: hip.Workfiles, units: dict[str, Unit], notes: list[str], errors: list[str],
) -> tuple[set[str], set[str]]:
    """``(named by a latest workfile, named by an edit)``: version rels."""
    index = _Index(units)
    by_workfile: set[str] = set()
    read = unreadable = 0
    for workfile in workfiles.reads():
        if workfile.unreadable:
            unreadable += 1
            continue
        if workfile.error is not None:
            errors.append(workfile.error)
            continue
        read += 1
        for pin in hip_pins(workfile.text, workfile.workfile.entity):
            by_workfile.update(index.matching(pin))
    notes.append(f'Read {read} latest workfile(s) for the render versions they name.')
    if unreadable:
        notes.append(
            f'{unreadable} latest workfile(s) are .hiplc/.hipnc in a form that cannot be read: '
            'render versions only they load are not kept for them.'
        )

    by_edit: set[str] = set()
    documents = _edit_files(roots)
    for path in documents:
        try:
            text = long_path(path).read_bytes().decode('utf-8', 'replace')
        except OSError as error:
            errors.append(f'{path}: {error}')
            continue
        by_edit |= path_units(text, units)
        try:
            data = json.loads(text)
        except ValueError:
            continue
        for pin in edit_pins(data):
            by_edit.update(index.matching(pin))
    edit_root = roots.get('edit')
    manifest = _read_json(edit_root.path / 'manifest.json') if edit_root is not None else None
    for shot, layer, department, version in manifest_pins(manifest):
        # The shot's URI (``entity:/shots/<sequence>/<shot>``), as its folders.
        segments = [part for part in re.split(r'[\\/]', shot.split(':', 1)[-1]) if part]
        matched = [
            unit.rel for unit in index.by_version.get(version, ())
            if unit.parts[-3:-1] == (department, layer)
        ]
        of_shot = [rel for rel in matched if tuple(rel.split('/')[1:-3]) == tuple(segments)]
        # A shot key spelled some other way: keep that version of the layer
        # in every shot rather than none.
        by_edit.update(of_shot or matched)
    if documents or manifest:
        notes.append(f'Read {len(documents)} edit document(s) for the render versions they name.')
    return by_workfile, by_edit


# ── the plan ───────────────────────────────────────────────────────────────


def _expected_frames(context: dict | None) -> int | None:
    """The frames a version's ``context.json`` says it holds; None when it
    has no record. 0 for a multi-range render, which is never complete."""
    if not isinstance(context, dict):
        return None
    from tumblepipe.farm.specs.frame_ranges import CONFIG_KEY
    if context.get(CONFIG_KEY):
        return 0
    try:
        first, last, step = int(context['first_frame']), int(context['last_frame']), int(context.get('step_size') or 1)
    except (KeyError, TypeError, ValueError):
        return None
    return len(range(first, last + 1, max(step, 1)))


def decide(roots, units: dict[str, Unit], settings: Settings, by_workfile: set[str], by_edit: set[str],
           *, now: float | None = None) -> dict[str, tuple[bool, str]]:
    """``{rel: (prune, reason)}`` for every version in ``units``. Always
    the whole render root: what names a version may be in another shot.
    Only versions under ``settings.context`` are looked at for having been
    written to lately (the rest are not listed)."""
    now = time.time() if now is None else now
    root = roots.get('render').path
    keep = settings.keep_renders
    contexts: dict[str, dict | None] = {}

    def context(unit: Unit):
        if unit.rel not in contexts:
            contexts[unit.rel] = _read_json(root.joinpath(*unit.parts, 'context.json')) if unit.has_context else None
        return contexts[unit.rel]

    verdicts: dict[str, tuple[bool, str]] = {}
    series: dict[str, list[Unit]] = defaultdict(list)
    for unit in units.values():
        series[unit.series].append(unit)
    for group in series.values():
        group.sort(key=lambda u: u.code)
        highest = group[-1].code
        fullest = max((u.frame_count for u in group if u.published), default=0)
        # The newest `keep` complete versions: read from the newest down.
        complete = 0
        threshold = None
        for unit in reversed(group):
            if not unit.published:
                continue
            expected = _expected_frames(context(unit))
            if expected is None:
                expected = fullest
            if expected and unit.frame_count >= expected:
                complete += 1
                if complete == keep:
                    threshold = unit.code
                    break
        for unit in group:
            if unit.marked:
                verdict = (False, Reason.KEEP_MARKED)
            elif unit.rel in by_workfile:
                verdict = (False, Reason.KEEP_WORKFILE)
            elif unit.rel in by_edit:
                verdict = (False, Reason.KEEP_EDIT)
            elif unit.published and (threshold is None or unit.code >= threshold):
                verdict = (False, Reason.KEEP_NEWEST.format(keep=keep))
            elif unit.code == highest:
                verdict = (False, Reason.KEEP_HIGHEST)
            elif not unit.published:
                stamp = _mtime(root.joinpath(*unit.parts, 'context.json'))
                if stamp is None or age_days(stamp, now) < settings.check_render_days:
                    verdict = (False, Reason.KEEP_CHECK.format(days=settings.check_render_days))
                else:
                    verdict = (True, Reason.PRUNE_CHECK.format(days=settings.check_render_days))
            else:
                verdict = (True, Reason.PRUNE_OLD.format(keep=keep))
            verdicts[unit.rel] = verdict

    # What the kept composite versions read stays, and what those read.
    todo = [rel for rel, (prune, _reason) in verdicts.items() if not prune]
    seen = set(todo)
    while todo:
        unit = units[todo.pop()]
        reads = (context(unit) or {}).get('reads')
        if not isinstance(reads, dict):
            continue
        entity = unit.parts[:-3]
        for channel, read in reads.items():
            if not isinstance(read, dict):
                continue
            target = '/'.join((*entity, str(read.get('department')), str(channel), str(read.get('version'))))
            if target in units and target not in seen:
                seen.add(target)
                todo.append(target)
                if verdicts[target][0]:
                    verdicts[target] = (False, Reason.KEEP_READ)

    for rel, (prune, _reason) in list(verdicts.items()):
        if prune and in_context(rel, settings.context) and recently_written(root.joinpath(*rel.split('/')), now, RECENT_DAYS):
            verdicts[rel] = (False, Reason.KEEP_RECENT.format(days=RECENT_DAYS))
    return verdicts


def _mtime(path: Path) -> float | None:
    try:
        return long_path(path).stat().st_mtime
    except OSError:
        return None


def _movie_units(item, units: dict[str, Unit]) -> list[str]:
    """The check render versions the review movie ``item`` is of
    (``playblast/<entity>/<dept>[/<layer>]/<stem>_vNNNN.mp4`` beside
    ``render/<entity>/<dept>/<layer>/vNNNN``)."""
    parts = item.rel.split('/')
    version_name = file_version(parts[-1])
    middle = parts[1:-1]
    out = []
    for department in (middle, middle[:-1]):
        prefix = '/'.join((AREA, *department)) + '/'
        for unit in units.values():
            if unit.name == version_name and not unit.published and unit.rel.startswith(prefix) \
                    and len(unit.parts) == len(department) + 3:
                out.append(unit.rel)
    return out


def plan(roots, inventory, settings: Settings, *, workfiles: hip.Workfiles) -> CategoryPlan:
    out = CategoryPlan('renders', 'Old render versions', 'version')
    render_root = roots.get('render')
    if render_root is None or inventory is None:
        return out
    out.payload = render_root.path
    units, movies = list_units(inventory)
    by_workfile, by_edit = pinned(roots, workfiles, units, out.notes, out.errors)
    verdicts = decide(roots, units, settings, by_workfile, by_edit)
    for rel in sorted(units):
        if not in_context(rel, settings.context):
            continue
        unit = units[rel]
        prune, reason = verdicts[rel]
        # render/<kind>/<group>/<name>/…: totalled by entity
        out.entries.append(Entry(
            render_root.path.joinpath(*unit.parts), rel, '/'.join(unit.parts[1:4]),
            unit.size, prune, reason,
        ))
    for item in sorted(movies, key=lambda i: i.rel):
        if not in_context(item.rel, settings.context):
            continue
        of = _movie_units(item, units)
        goes = bool(of) and all(verdicts[rel][0] for rel in of)
        out.entries.append(Entry(
            render_root.path.joinpath(*item.rel.split('/')), item.rel, '/'.join(item.rel.split('/')[1:4]),
            item.size, goes, Reason.PRUNE_CHECK_MOVIE if goes else Reason.KEEP_CHECK_MOVIE,
        ))
    out.errors.extend(error for error in inventory.errors if str(render_root.path) in error)
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    def still_ok(entry: Entry) -> str | None:
        parts = entry.label.split('/')
        if '_deleted' in parts or len(parts) < 4:
            return 'not a render version'
        path = long_path(entry.path)
        if parts[0] == AREA:
            if not naming.is_valid_version_name(parts[-1]) or not path.is_dir() or path.is_symlink():
                return 'not a render version folder'
            if (path / KEEP_FILE).exists():
                return f'it holds a {KEEP_FILE} file'
            if recently_written(entry.path, time.time(), RECENT_DAYS):
                return 'it was written to since it was listed'
            return None
        if parts[0] == MOVIES and path.is_file() and parts[-1].lower().endswith(('.mp4', '.mov')):
            return None
        return 'not a render version'

    root = category_plan.payload

    def guarded(entry: Entry) -> str | None:
        if root is None or entry.path != root.joinpath(*entry.label.split('/')) or not within(entry.path.parent, root):
            return 'outside the render root'
        return still_ok(entry)

    return delete_entries(category_plan, log_path, guarded)


CATEGORY = Category(
    'renders', 'Old render versions',
    'render versions older than the newest few complete ones, that no workfile, edit or kept composite names; '
    'and check renders past their age',
    ('render',), plan, apply,
)
