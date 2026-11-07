"""What the Farm Submit window remembers between one opening and the next.

Artists asked for it (2026-11): every opening started from each entity's
own values again, so a renderer or sample count set for the last submission
had to be set again. The window now keeps, per project and tab:

* the settings the artist **pinned** (set for the whole submission), except
  the frame range, which belongs to the shots it was typed for;
* the Render section's batch choices (Preview / Full range, Standalone,
  Copy to edit);
* which columns the artist collapsed;

and, for the window as a whole, which settings sections are collapsed,
whether empty columns collapse by themselves, whether old jobs are cleaned
up on submit, and its size.

Pure: the dialog reads and writes the prefs file
(``asset_browser.prefs.PipelinePrefs.farm_submit``) and hands the plain
dicts here. Everything that comes back from disk is checked, since an older
or hand-edited file must not put a value into a field that cannot take it.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

# Pinned settings never carried to the next opening: the frame range is the
# shots' own, and a remembered 1001-1100 would quietly cut a longer shot.
NOT_REMEMBERED = frozenset({'first_frame', 'last_frame'})

# The Render section's batch fields (plain check boxes, never per entity).
BATCH_KEYS = ('render_preview', 'render_full', 'standalone', 'copy_to_edit')

# Window-wide choices and their defaults.
UI_DEFAULTS = {
    'collapsed_sections': [],     # step kinds whose settings section is collapsed
    'collapse_empty': True,       # columns with nothing to do collapse by themselves
    'cleanup': True,              # clean up an entity's old finished jobs on submit
    'size': None,                 # [width, height]
}

_SCALARS = (bool, int, str)


def scope(project: str, context: str) -> str:
    """The key one project's tab is remembered under."""
    return f"{project}|{context}"


def _scalar(value: Any) -> bool:
    # bool is an int; both are fine. Floats, lists, None and sentinels are not.
    return isinstance(value, _SCALARS)


def artist_pins(pinned: Mapping[str, Any], context_pins: Mapping[str, Any]) -> dict:
    """The pins the artist made, of ``pinned``: a pin the open workfile made
    (``context_pins``, its department as the cut) is the workfile's, so it is
    dropped unless the artist changed its value."""
    missing = object()
    return {
        key: value for key, value in pinned.items()
        if context_pins.get(key, missing) != value
    }


def capture(pinned: Mapping[str, Any], batch: Mapping[str, Any], collapsed: list[str]) -> dict:
    """The record to store for one tab: its pinned settings, batch choices
    and collapsed columns, keeping only plain values worth carrying over."""
    return {
        'pinned': {
            key: value for key, value in pinned.items()
            if key not in NOT_REMEMBERED and _scalar(value)
        },
        'batch': {
            key: bool(batch[key]) for key in BATCH_KEYS
            if isinstance(batch.get(key), bool)
        },
        'collapsed': sorted({key for key in collapsed if isinstance(key, str)}),
    }


def restore_pinned(
    record: Any,
    accepts: Callable[[str, Any], bool],
) -> dict[str, Any]:
    """The pinned settings of a stored tab record that the form can take.

    ``accepts(key, value)`` is the form's say: the field exists and the
    value is one it offers (a department still in the pool, a number in
    range). Anything else is dropped, never coerced.
    """
    pinned = record.get('pinned') if isinstance(record, dict) else None
    if not isinstance(pinned, dict):
        return {}
    return {
        key: value for key, value in pinned.items()
        if isinstance(key, str) and key not in NOT_REMEMBERED
        and _scalar(value) and accepts(key, value)
    }


def restore_batch(record: Any) -> dict[str, bool]:
    batch = record.get('batch') if isinstance(record, dict) else None
    if not isinstance(batch, dict):
        return {}
    restored = {key: batch[key] for key in BATCH_KEYS if isinstance(batch.get(key), bool)}
    # Neither Preview nor Full range is not a render the form can submit.
    if restored.get('render_preview') is False and restored.get('render_full') is False:
        restored.pop('render_preview')
        restored.pop('render_full')
    return restored


def restore_collapsed(record: Any, column_keys: list[str]) -> set[str]:
    """Stored collapsed columns that this tab still has."""
    collapsed = record.get('collapsed') if isinstance(record, dict) else None
    if not isinstance(collapsed, list):
        return set()
    return {key for key in collapsed if key in column_keys}


def ui_settings(stored: Any) -> dict:
    """The window-wide choices, each defaulted when missing or malformed."""
    stored = stored if isinstance(stored, dict) else {}
    ui = dict(UI_DEFAULTS)
    ui['collapsed_sections'] = []  # never the default's own list
    sections = stored.get('collapsed_sections')
    if isinstance(sections, list):
        ui['collapsed_sections'] = [s for s in sections if isinstance(s, str)]
    for key in ('collapse_empty', 'cleanup'):
        if isinstance(stored.get(key), bool):
            ui[key] = stored[key]
    size = stored.get('size')
    if (
        isinstance(size, list) and len(size) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and 200 <= v <= 20000 for v in size)
    ):
        ui['size'] = list(size)
    return ui
