"""The value ladder's maths: Houdini's middle-mouse drag on a number field.

Middle-press a number field and a column of step sizes (100, 10, 1, 0.1,
0.01) opens at the pointer, on the 1 row. Moving up or down picks a step;
dragging left or right changes the value by that step every :data:`NOTCH`
pixels. Releasing sets the value. Ctrl + middle-click puts the field back to
its default. A whole-number field leaves out the steps below 1, and the
field's own limits still hold.

Plain functions, no Qt: :mod:`tumblepipe.util.value_ladder_qt` puts them on
Qt fields. Cinedex has the same rules in Rust (``src/cinedex/cinedex/src/
ladder.rs``); keep the two alike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: A ladder row's height, in pixels: how far up or down picks the next step.
ROW = 22.0
#: Pixels of left/right drag per step.
NOTCH = 10.0
#: The finest step the ladder offers, as decimals: 0.01.
FINEST = 2


def _round(value: float) -> float:
    """Half away from zero, as Rust's ``f64::round`` (Python's round is
    half to even)."""
    return math.copysign(math.floor(abs(value) + 0.5), value)


def steps(decimals: int) -> list[float]:
    """The ladder's steps, coarsest first, for a field showing ``decimals``
    decimals (0: a whole-number field, which gets no step below 1)."""
    finest = min(max(decimals, 0), FINEST)
    return [10.0 ** -p for p in range(-2, finest + 1)]


def start_row(ladder: list[float]) -> int:
    """The row a drag starts on: the 1 step."""
    return ladder.index(1.0) if 1.0 in ladder else 0


def row_at(start: int, dy: float, rows: int) -> int:
    """The row picked ``dy`` pixels below the press (negative: above),
    starting from ``start``, kept on the ladder's ``rows``."""
    moved = int(_round(dy / ROW))
    return min(max(start + moved, 0), max(rows - 1, 0))


def rounded(value: float, decimals: int) -> float:
    """``value`` rounded to ``decimals`` decimals."""
    scale = 10.0 ** min(max(decimals, 0), 9)
    return _round(value * scale) / scale


def nudged(base: float, dx: float, step: float, limits: tuple[float, float], decimals: int) -> float:
    """The value ``dx`` pixels of drag makes from ``base`` with ``step``: one
    step per whole :data:`NOTCH`, rounded to the field's decimals, inside its
    limits."""
    notches = math.trunc(dx / NOTCH)
    return min(max(rounded(base + notches * step, decimals), limits[0]), limits[1])


@dataclass(frozen=True)
class Number:
    """What a number field is: its value now, its limits, its decimals and
    what Ctrl + middle-click puts back."""
    value: float
    low: float
    high: float
    decimals: int
    default: float

    def reset(self) -> float:
        """The default, inside the limits."""
        return min(max(rounded(self.default, self.decimals), self.low), self.high)


class Grip:
    """A middle-mouse drag in progress, from a press at ``(x, y)``."""

    def __init__(self, number: Number, x: float, y: float) -> None:
        self.press_y = y
        self.value = min(max(rounded(number.value, number.decimals), number.low), number.high)
        # The value the current step counts from, and the x it counts from:
        # picking another step starts counting again where the value is.
        self.base = self.value
        self.anchor_x = x
        self.row = start_row(steps(number.decimals))

    def moved(self, number: Number, x: float, y: float) -> float:
        """The pointer at ``(x, y)``: the step its height picks and the value
        its drag makes."""
        ladder = steps(number.decimals)
        row = row_at(start_row(ladder), y - self.press_y, len(ladder))
        if row != self.row:
            self.row, self.base, self.anchor_x = row, self.value, x
        self.value = nudged(self.base, x - self.anchor_x, ladder[self.row],
                            (number.low, number.high), number.decimals)
        return self.value
