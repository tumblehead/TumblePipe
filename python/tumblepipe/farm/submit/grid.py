"""The Farm Submit grid's policy: cells, tick states, stale checks and what gets submitted.

Pure — no Qt, no ``hou``, no config or filesystem reads — so all of it is
testable headlessly (``tests/test_farm_grid.py``). The dialog
(``submit_jobs_dialog``) owns the widgets and feeds this plain data; the
status scan (``submit_status``) supplies the per-cell states.

The grid is *steps*, not settings: rows are entities, columns are pipeline
steps (one publish column per department, then Playblast and Render), and a
ticked cell means "do this step for this entity". Settings stay in the
per-entity tri-state form (``submit_settings``).

Publish cells are individual departments. Ticking anim and light on a row
publishes exactly those two, chained in pool order; **Select stale** is the
one-click way to get what the old "publish up to X" form did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

# ── cell states ───────────────────────────────────────────

NONE = '-'      # nothing to do: no workfile / no frame range
NEVER = 'o'     # never exported / never made
UNCLAIMED = 'u' # only a Multi's workfile, and it has never exported this entity
STALE = 's'     # older than what it is made from
CURRENT = 'k'   # up to date
PENDING = '?'   # status not read yet

# UNCLAIMED is tickable (a Multi's first farm publish starts there) but never
# needs work: Select stale and the upstream warnings leave it alone.
TICKABLE = frozenset({NEVER, UNCLAIMED, STALE, CURRENT, PENDING})
NEEDS_WORK = frozenset({NEVER, STALE})

PUBLISH = 'publish'
PLAYBLAST = 'playblast'
RENDER = 'render'
KINDS = (PUBLISH, PLAYBLAST, RENDER)


@dataclass(frozen=True)
class Column:
    """One step column. ``department`` is set for publish columns only."""
    key: str
    label: str
    kind: str
    department: str | None = None


def publish_key(department: str) -> str:
    return f'{PUBLISH}:{department}'


def columns_for(context: str, publish_departments: Sequence[str]) -> list[Column]:
    """The step columns for ``context``, publish departments in pool order.

    Playblast is a shots-only preview, as it is on the farm side.
    """
    columns = [
        Column(publish_key(name), name, PUBLISH, name)
        for name in publish_departments
    ]
    if context == 'shots':
        columns.append(Column(PLAYBLAST, 'Playblast', PLAYBLAST))
    columns.append(Column(RENDER, 'Render', RENDER))
    return columns


def publish_state(
    hip_mtime: float | None,
    export_mtime: float | None,
    shared: bool = False,
) -> str:
    """A department's publish cell, from its newest workfile and export.

    The same rule as ``_publish.is_out_of_date``: stale when the workfile
    was saved after the latest export.

    ``shared`` means the workfile is a Multi's, not the entity's own. A
    Multi covers every member for its departments, but its workfile need
    not export them all (HideAndReek's Backgrounds environment exports one
    shot of twenty), so a member it has never exported is UNCLAIMED rather
    than NEVER: nothing says that workfile publishes it. Once it has, the
    member's cell is stale or current like any other.
    """
    if hip_mtime is None:
        return NONE
    if export_mtime is None:
        return UNCLAIMED if shared else NEVER
    return STALE if hip_mtime > export_mtime else CURRENT


def preview_state(
    preview_mtime: float | None,
    upstream_export_mtimes: Iterable[float | None],
    *,
    available: bool = True,
) -> str:
    """A playblast or render cell.

    ``upstream_export_mtimes`` are the latest exports of the departments the
    preview composes (the pool up to its cut). Stale when any of them was
    published after the preview was made. ``available=False`` (no frame
    range) makes the cell untickable.
    """
    if not available:
        return NONE
    if preview_mtime is None:
        return NEVER
    newest = max((m for m in upstream_export_mtimes if m is not None), default=None)
    if newest is not None and newest > preview_mtime:
        return STALE
    return CURRENT


# ── ticks ─────────────────────────────────────────────────

CellKey = tuple  # (entity uri string, column key)


def tri_state(keys: Iterable[CellKey], ticks: set) -> str:
    """``'on'`` when every key is ticked, ``'mixed'`` for some, ``'off'`` for none.

    An empty set of keys is ``'off'``: a header over nothing tickable must
    not claim to be checked.
    """
    keys = list(keys)
    count = sum(1 for key in keys if key in ticks)
    if not keys or count == 0:
        return 'off'
    return 'on' if count == len(keys) else 'mixed'


def toggle(keys: Iterable[CellKey], ticks: set) -> set:
    """Tick every key, or clear them all when every one is already ticked.

    The checkbox rule: clicking an empty or partly checked box fills it,
    clicking a full one empties it. Returns a new set.
    """
    keys = list(keys)
    result = set(ticks)
    if keys and all(key in ticks for key in keys):
        result.difference_update(keys)
    else:
        result.update(keys)
    return result


def stale_keys(states: Mapping[CellKey, str]) -> list[CellKey]:
    """Every cell that needs work: stale, or never done."""
    return [key for key, state in states.items() if state in NEEDS_WORK]


def drop_untickable(ticks: set, states: Mapping[CellKey, str]) -> set:
    """Ticks minus any cell whose status arrived as nothing-to-do."""
    return {key for key in ticks if states.get(key, PENDING) in TICKABLE}


# ── per-row submission ────────────────────────────────────

def row_kinds(uri: str, columns: Sequence[Column], ticks: set) -> list[str]:
    """The step kinds ticked on one row, in ``KINDS`` order."""
    ticked = {c.kind for c in columns if (uri, c.key) in ticks}
    return [kind for kind in KINDS if kind in ticked]


def ticked_kinds(columns: Sequence[Column], ticks: set) -> list[str]:
    """The step kinds with at least one ticked cell, in ``KINDS`` order.

    The settings panel shows only these kinds' sections: a Render section
    beside a grid that submits no render is settings for nothing.
    """
    kind_of = {c.key: c.kind for c in columns}
    ticked = {kind_of.get(key) for _uri, key in ticks}
    return [kind for kind in KINDS if kind in ticked]


def opened_ticks(
    uris: Iterable[str],
    columns: Sequence[Column],
    kinds: Sequence[str],
    department: str | None = None,
) -> set:
    """The cells ticked when the grid opens for ``uris``.

    Every ``kinds`` column of each row, except that a ``department`` (the
    open workfile's) narrows the publish columns to that department's: the
    grid opened from an animation workfile means "publish my animation",
    not every department of the shot.
    """
    return {
        (uri, c.key)
        for uri in uris
        for c in columns
        if c.kind in kinds
        and (c.kind != PUBLISH or department is None or c.department == department)
    }


def row_publish_departments(uri: str, columns: Sequence[Column], ticks: set) -> list[str]:
    """The ticked publish departments of one row, in pool (column) order."""
    return [
        c.department for c in columns
        if c.kind == PUBLISH and (uri, c.key) in ticks
    ]


def default_preview_department(renderable: Sequence[str]) -> str | None:
    """The Playblast / Render **Up to** for an entity that configures none.

    The *last* renderable department, so an unconfigured preview composes
    the whole shot. The first used to be the default, which on a typical
    pool is layout: a playblast from the Farm Submit button — which has no
    workfile department to pin — then silently left out animation.
    """
    return renderable[-1] if renderable else None


def preview_cut(departments: Sequence[str], cut: str | None) -> list[str]:
    """The pool departments up to and including ``cut`` (all when unknown)."""
    departments = list(departments)
    if cut in departments:
        return departments[:departments.index(cut) + 1]
    return departments


def unpublished_upstream(
    uri: str,
    columns: Sequence[Column],
    ticks: set,
    states: Mapping[CellKey, str],
    *,
    cuts: Mapping[str, str | None],
) -> list[str]:
    """Departments a ticked preview on this row would show stale.

    For each ticked preview (``cuts`` maps its kind to its department cut),
    the publish columns inside the cut that need work and are not ticked.
    The warning, not a block: previewing what is published is sometimes the
    point.
    """
    previews = [
        c for c in columns
        if c.kind in (PLAYBLAST, RENDER) and (uri, c.key) in ticks
    ]
    if not previews:
        return []
    publish_columns = [c for c in columns if c.kind == PUBLISH]
    names = [c.department for c in publish_columns]
    missing: list[str] = []
    for preview in previews:
        inside = set(preview_cut(names, cuts.get(preview.kind)))
        for column in publish_columns:
            key = (uri, column.key)
            if (
                column.department in inside
                and states.get(key) in NEEDS_WORK
                and key not in ticks
                and column.department not in missing
            ):
                missing.append(column.department)
    return missing


def finish_row_settings(settings: dict, publish_departments: Sequence[str]) -> dict:
    """The resolver's settings for one row, turned into the grid's publish.

    The resolver still emits the old form's ``pub_department`` ("up to");
    the grid replaces it with the explicit ``pub_departments`` list, which
    ``batch_submit`` publishes exactly.
    """
    out = dict(settings)
    out.pop('pub_department', None)
    if out.get('publish'):
        out['pub_departments'] = list(publish_departments)
    return out


def breakdown(
    uris: Sequence[str],
    columns: Sequence[Column],
    ticks: set,
) -> tuple[dict[str, int], dict[str, int], int]:
    """What a submission holds: ``(per kind, per publish department, rows)``.

    Kinds with nothing ticked are left out; publish departments come in
    column (pool) order. The confirmation lists these, one line per kind.
    """
    kinds: dict[str, int] = {}
    departments: dict[str, int] = {}
    rows = 0
    for uri in uris:
        touched = False
        for column in columns:
            if (uri, column.key) not in ticks:
                continue
            touched = True
            kinds[column.kind] = kinds.get(column.kind, 0) + 1
            if column.kind == PUBLISH:
                departments[column.department] = departments.get(column.department, 0) + 1
        rows += touched
    ordered_kinds = {kind: kinds[kind] for kind in KINDS if kind in kinds}
    ordered_departments = {
        c.department: departments[c.department]
        for c in columns if c.kind == PUBLISH and c.department in departments
    }
    return ordered_kinds, ordered_departments, rows


def summary(
    uris: Sequence[str],
    columns: Sequence[Column],
    ticks: set,
    noun: tuple[str, str] = ('entity', 'entities'),
) -> str:
    """``3 publishes · 2 playblasts · 1 render — 2 shots``, or ``Nothing ticked``."""
    counts = {kind: 0 for kind in KINDS}
    rows = 0
    for uri in uris:
        touched = False
        for column in columns:
            if (uri, column.key) in ticks:
                counts[column.kind] += 1
                touched = True
        rows += touched
    if not rows:
        return 'Nothing ticked'
    words = {
        PUBLISH: ('publish', 'publishes'),
        PLAYBLAST: ('playblast', 'playblasts'),
        RENDER: ('render', 'renders'),
    }
    parts = [
        f"{counts[kind]} {words[kind][counts[kind] != 1]}"
        for kind in KINDS if counts[kind]
    ]
    return f"{' · '.join(parts)} — {rows} {noun[rows != 1]}"


def empty_columns(
    uris: Sequence[str],
    columns: Sequence[Column],
    states: Mapping[CellKey, str],
) -> set[str]:
    """Keys of the columns with nothing to do on any row: every cell ``NONE``.

    A department no entity has a workfile in is one of these; the dialog
    collapses them so the departments in use fit on screen. A column with
    a cell still ``PENDING`` is not empty yet, and with no rows nothing is.
    """
    if not uris:
        return set()
    return {
        column.key for column in columns
        if all(states.get((uri, column.key)) == NONE for uri in uris)
    }


def untick_sent(ticks: set, sent: set[str]) -> set:
    """``ticks`` without the cells of the rows that went out: back from a
    submission, what is left ticked is what was not sent."""
    return {key for key in ticks if key[0] not in sent}


def stale_ticks(stale: Mapping[str, Sequence[str]]) -> set:
    """The publish cells "Tick stale publishes" adds: each row's stale,
    unticked departments its previews compose, and nothing else."""
    return {
        (uri, publish_key(department))
        for uri, departments in stale.items() for department in departments
    }


def row_steps(uri: str, columns: Sequence[Column], ticks: set) -> list[tuple[str, str]]:
    """``(kind, label)`` of each ticked step on ``uri``, in column order:
    a publish by its department, a preview by its name."""
    return [
        (column.kind, column.label)
        for column in columns if (uri, column.key) in ticks
    ]


def kind_summary(
    kind: str,
    uris: Sequence[str],
    columns: Sequence[Column],
    ticks: set,
    noun: tuple[str, str] = ('entity', 'entities'),
) -> str:
    """A settings section's headline summary: who its settings apply to.

    ``2 shots`` for a preview; ``3 publishes, 2 shots`` for publish, where a
    row can tick several departments. Empty when nothing of ``kind`` is
    ticked.
    """
    keys = [column.key for column in columns if column.kind == kind]
    cells = sum(1 for uri in uris for key in keys if (uri, key) in ticks)
    rows = sum(1 for uri in uris if any((uri, key) in ticks for key in keys))
    if not rows:
        return ''
    who = f"{rows} {noun[rows != 1]}"
    if kind == PUBLISH and cells != rows:
        return f"{cells} publishes, {who}"
    return who


# ── context tabs ──────────────────────────────────────────

# The dialog's tabs, in the order they are shown. One grid per context: the
# department columns (and Playblast) differ between them, so they never share
# a grid, and Submit sends only the tab in front. Edits have no departments:
# their tab lists the edits with one step, Render Edit (see the end of this
# module).
CONTEXTS = ('assets', 'shots', 'edits')
EDITS = 'edits'


def context_noun(context: str) -> tuple[str, str]:
    """``('shot', 'shots')``, ``('asset', 'assets')`` or ``('edit', 'edits')``
    for counts and headers.

    Only the :data:`CONTEXTS` have a tab; any other is a ValueError rather
    than being counted as assets."""
    if context == 'shots':
        return ('shot', 'shots')
    if context == 'assets':
        return ('asset', 'assets')
    if context == EDITS:
        return ('edit', 'edits')
    raise ValueError(f'{context!r} is not a farm submission context')


def tab_seed(
    context: str,
    opened_context: str,
    uris: Sequence,
    department: str | None,
    kinds: Sequence[str],
) -> tuple[list, str | None, tuple[str, ...]]:
    """What the ``context`` tab's grid opens with: ``(uris, department, kinds)``.

    Only the tab the dialog was opened for is seeded. The other tab starts
    clean — nothing ticked and no department pinned — because the opened
    entities and the workfile's department belong to the opened context: an
    asset workfile's ``model`` says nothing about which shots to submit or
    how far to render them.
    """
    if context == opened_context:
        return list(uris), department, tuple(kinds)
    return [], None, ()


def ticked_rows(ticks: Iterable[CellKey]) -> int:
    """How many entities have at least one ticked cell."""
    return len({uri for uri, _key in ticks})


def tab_label(context: str, rows: int) -> str:
    """``Shots``, or ``Shots (3)`` while three shots have a ticked cell.

    The count is what shows that a tab you are not looking at still holds
    ticks, which Submit on the other tab leaves alone.
    """
    name = context.capitalize()
    return f"{name} ({rows})" if rows else name


# ── the Edits tab ─────────────────────────────────────────

# An edit's one step: render a finished published version into a movie on
# the farm (farm/pipelines/edit_render.py). A working version is never
# offered: it can change under the job.

def default_edit_version(published: Sequence[str]) -> str | None:
    """The version an edit's row offers first: its latest finished published
    version (``published`` oldest first), None when it has none, so its row
    cannot be ticked."""
    return published[-1] if published else None


def edit_rows(
    ticked: Mapping[str, str],
    names: Mapping[str, str],
    pool_name: str,
    priority: int,
) -> list[dict]:
    """The plan rows of the Edits tab: one per ticked edit (``ticked`` maps
    its URI to the chosen version), in URI order, as
    ``batch_submit.submit_entity_batch`` takes them."""
    return [
        {
            'entity': {'uri': uri, 'name': names[uri], 'context': EDITS},
            'settings': {
                'version': ticked[uri],
                'pool_name': pool_name,
                'priority': priority,
            },
        }
        for uri in sorted(ticked)
    ]


def edit_summary(ticked: int) -> str:
    """``2 edit renders — 2 edits``, or ``Nothing ticked``."""
    if not ticked:
        return 'Nothing ticked'
    renders = 'edit render' if ticked == 1 else 'edit renders'
    edits = 'edit' if ticked == 1 else 'edits'
    return f"{ticked} {renders} — {ticked} {edits}"
