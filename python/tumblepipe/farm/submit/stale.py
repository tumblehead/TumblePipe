"""What "stale" means in Farm Submit, in words an artist can judge by.

Artists read the "stale" label as an alarm, and only a cell tooltip said
what it was (Remy, 2026-11-20). A stale publish is a department whose
workfile was saved after its latest export (``grid.publish_state``), so the
newest work *may* not be exported: maybe a save with nothing new in it.
Whether that matters is the artist's call, so the words here say exactly
that, calmly, wherever the label shows: the legend under the grid, the
Warnings column, the cell tooltip and the review sheet.

Pure: no Qt, no ``hou``, no pipeline imports. ``grid`` and ``review`` (which
``grid`` imports) read it; ``tests/test_farm_submit_stale.py`` pins it.
"""

from __future__ import annotations

from typing import Sequence

#: A stale publish cell's tooltip: what it means, not that something broke.
STALE_PUBLISH_TIP = (
    "stale: the workfile was saved after its latest export, so the newest "
    "work may not be exported yet"
)

#: The one-line note under the grid, shown whatever is ticked.
STALE_LEGEND = (
    "● Stale: the department's workfile was saved after its latest export, "
    "so the newest work may not be exported yet. Tick it to publish that "
    "work first, or leave it to use the latest export."
)


def _names(departments: Sequence[str]) -> str:
    return ', '.join(departments)


def stale_note(departments: Sequence[str]) -> str:
    """The Warnings column's words for the unticked stale departments a
    ticked preview composes: what the preview uses and why it may be old."""
    workfiles = 'workfile' if len(departments) == 1 else 'workfiles'
    return (
        f"{_names(departments)}: latest export used "
        f"({workfiles} saved since, not ticked)"
    )


def stale_review_line(departments: Sequence[str]) -> str:
    """The review sheet's line for the same departments."""
    whose = 'its workfile was' if len(departments) == 1 else 'their workfiles were'
    return (
        f"Previews {_names(departments)} from the latest export: {whose} "
        "saved since, so newer work may not be in it (not ticked)"
    )


def stale_banner(count: int, noun: tuple[str, str]) -> str:
    """The review sheet's banner over ``count`` rows with stale departments;
    ``noun`` is the context's (singular, plural)."""
    return (
        f"{count} {noun[count != 1]} will preview some departments from their "
        "latest export, though their workfile was saved since. If that work "
        "matters, tick them to publish it first; otherwise submit as is."
    )
