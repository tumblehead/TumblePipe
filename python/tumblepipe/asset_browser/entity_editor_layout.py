"""What the Entity Editor remembers of its layout between openings.

Pure: no Qt, no prefs file. The window reads and writes one dict through
its ``EntityEditorSource`` (``PipelinePrefs.entity_editor``, written with
``update_prefs``), shaped here::

    {
        "details": true,                      # the side panel shown
        "splitter": {"shots": [820, 360]},    # table and side panel widths, per tab
        "columns": {"shots": {"order": [...], "hidden": [...]}},
    }

Anything else in it, or a value of the wrong shape (a hand edit, an older
or newer editor), is ignored rather than trusted: :func:`column_layout`
always gives every column once, the locked ones (the frozen group and
name) first and shown, whatever the dict holds.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

DETAILS = 'details'
SPLITTER = 'splitter'
COLUMNS = 'columns'
ORDER = 'order'
HIDDEN = 'hidden'


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _names(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [v for v in value if isinstance(v, str)]


def details_shown(data) -> bool:
    """Whether the side panel shows; shown unless it was hidden."""
    value = _mapping(data).get(DETAILS, True)
    return value if isinstance(value, bool) else True


def splitter_sizes(data, tab: str) -> list[int] | None:
    """The remembered table and side panel widths of ``tab``: two whole
    numbers, the panel's above 0; None when there are none to trust."""
    sizes = _mapping(_mapping(data).get(SPLITTER)).get(tab)
    if not isinstance(sizes, (list, tuple)) or len(sizes) != 2:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 100_000 for v in sizes):
        return None
    if sizes[0] <= 0 or sizes[1] <= 0:
        return None
    return [int(sizes[0]), int(sizes[1])]


def column_layout(
    columns: Sequence[str], data, tab: str, locked: Iterable[str] = (),
) -> tuple[list[str], set[str]]:
    """``tab``'s columns in the order to show them, and those hidden.

    The order is every one of ``columns`` once: the ``locked`` ones first,
    in their own order, then the remembered order of the rest, then any
    column the editor gained since, each after the column it follows by
    default. Locked columns are never hidden; nor is every other column (a
    table showing nothing but its names is no use)."""
    locked = [c for c in columns if c in set(locked)]
    saved = _mapping(_mapping(_mapping(data).get(COLUMNS)).get(tab))
    known = set(columns)
    order = list(locked)
    for name in _names(saved.get(ORDER)):
        if name in known and name not in order:
            order.append(name)
    for i, name in enumerate(columns):
        if name in order:
            continue
        # After the nearest column before it by default that is placed.
        before = next((c for c in reversed(columns[:i]) if c in order), None)
        order.insert(order.index(before) + 1 if before is not None else len(locked), name)
    hidden = {n for n in _names(saved.get(HIDDEN)) if n in known and n not in locked}
    if hidden and hidden >= known - set(locked):
        hidden = set()
    return order, hidden


def remember(data, **changes) -> dict:
    """A copy of ``data`` (junk dropped to a dict) with ``changes`` made:
    ``details=bool``, ``splitter=(tab, [a, b])``,
    ``columns=(tab, order, hidden)``."""
    out = {k: v for k, v in _mapping(data).items() if isinstance(k, str)}
    if 'details' in changes:
        out[DETAILS] = bool(changes['details'])
    if 'splitter' in changes:
        tab, sizes = changes['splitter']
        found = dict(_mapping(out.get(SPLITTER)))
        found[tab] = [int(v) for v in sizes]
        out[SPLITTER] = found
    if 'columns' in changes:
        tab, order, hidden = changes['columns']
        found = dict(_mapping(out.get(COLUMNS)))
        found[tab] = {ORDER: list(order), HIDDEN: sorted(hidden)}
        out[COLUMNS] = found
    if 'reset_columns' in changes:
        found = dict(_mapping(out.get(COLUMNS)))
        found.pop(changes['reset_columns'], None)
        out[COLUMNS] = found
    return out
