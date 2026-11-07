"""The review Farm Submit shows before it submits, without Qt.

A row per entity says what it submits and what to check: errors block the
submit, stale previews and warnings ask for a look. Its order, its notes and
whether a submission can skip it are decided here
(``tests/test_farm_submit_review.py``); ``asset_browser.farm_submit_ui``
draws it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

BLOCKED, STALE, WARNED, CLEAN = 0, 1, 2, 3


@dataclass
class ReviewRow:
    """One entity on the review sheet: what it submits and what to check."""
    name: str
    steps: list[tuple[str, str]]                       # (kind, label)
    errors: list[str] = field(default_factory=list)    # block the submit
    stale: list[str] = field(default_factory=list)     # unticked stale departments a preview composes
    warnings: list[str] = field(default_factory=list)

    @property
    def severity(self) -> int:
        """BLOCKED, STALE, WARNED or CLEAN: the sheet's order."""
        if self.errors:
            return BLOCKED
        if self.stale:
            return STALE
        if self.warnings:
            return WARNED
        return CLEAN


def review_order(rows: Sequence[ReviewRow]) -> list[ReviewRow]:
    """Blocked rows first, then stale previews, then warnings, then the rest;
    grid order within each (a stable sort)."""
    return sorted(rows, key=lambda row: row.severity)


def skips_review(rows: Sequence[ReviewRow]) -> bool:
    """A single row with nothing to check goes straight out; anything more,
    or anything to check, is reviewed first."""
    return len(rows) == 1 and rows[0].severity == CLEAN


def can_submit(rows: Sequence[ReviewRow]) -> bool:
    """Whether the review's Submit is offered: there is a row, and none is
    blocked (a blocked row is fixed or unticked first, never skipped)."""
    return bool(rows) and all(row.severity != BLOCKED for row in rows)


def review_lines(row: ReviewRow) -> list[str]:
    """Everything the artist should read about ``row``, blocking reasons first."""
    lines = [f"Can't submit: {error}" for error in row.errors]
    if row.stale:
        lines.append(f"Previews {', '.join(row.stale)} as last published (stale, not ticked)")
    lines.extend(row.warnings)
    return lines
