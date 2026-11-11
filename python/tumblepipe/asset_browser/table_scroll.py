"""How the Entity Editor's tables scroll (#30) and freeze columns (#40).

The Multis tab's department columns run wider than the window, and neither
the wheel nor Shift+wheel scrolled them sideways; a click on a cell scrolled
the table horizontally, moving the clicked cell away from the pointer. These
are the two decisions, Qt-free; ``entity_editor._Table`` does the wiring.
"""

from __future__ import annotations

#: One wheel notch, in Qt's angle-delta units (eighths of a degree).
NOTCH = 120


def horizontal_wheel_steps(
    shift: bool, vertical_room: int, dy: int, dx: int, step: int,
) -> int:
    """How far a wheel turn moves the horizontal bar (0: leave it to Qt).

    ``vertical_room`` is the vertical bar's maximum (0 when everything fits
    top to bottom), ``dy``/``dx`` the event's angle delta, ``step`` the
    horizontal bar's single step. Shift turns the wheel sideways, as in
    most apps; so does a table that has nothing to scroll vertically. A
    platform that already sends Shift+wheel as ``dx`` is scrolled by
    ``dx``. Wheel down/away moves right, as the vertical bar moves down.
    """
    if not (shift or vertical_room <= 0):
        return 0
    delta = dy or dx
    if not delta:
        return 0
    return -round(delta * 3 * step / NOTCH)


def keep_view_on_click(
    cell_left: int, cell_right: int, view_width: int, clicked: bool,
) -> bool:
    """True when the table must not scroll to a cell it was asked to show.

    ``clicked``: the cell is the one last clicked (Qt scrolls to it on a
    timer after the press). A click lands on a cell the artist can see;
    scrolling it into full view only moves it away from the pointer.
    Keyboard moves and a cell wholly out of view still scroll.
    """
    return clicked and cell_right > 0 and cell_left < view_width


#: How many leading columns the Shots and Assets tabs keep in view: the
#: group (sequence or category) and the name (#40).
FROZEN_COLUMNS = 2


def hidden_under_frozen(cell_left: int, frozen_width: int) -> int:
    """How many pixels of a scrolling cell sit under the frozen columns.

    The frozen columns are drawn over the table's left ``frozen_width``
    pixels, so a cell Qt scrolled to the viewport's left edge is covered.
    The table scrolls back by this much (0: the cell is clear of them).
    """
    return max(0, frozen_width - cell_left)


def scrolls_sideways(column: int, frozen_columns: int) -> bool:
    """Whether showing a cell of ``column`` may move the horizontal bar: a
    frozen column is always in view, so going to one only scrolls rows."""
    return column >= frozen_columns
