"""Headless Qt harness: the Farm Submit grid highlights the open workfile's row.

Remy, 2026-11-18: "Would be nice if the active entity row was highlighted
in the submit window. When you submit a render and accidentally disable the
task, it's sometimes hard to find the entity again".

Which rows are the workfile's and which is scrolled to is arithmetic
(``grid.active_rows`` / ``grid.scroll_target``,
``tests/test_farm_submit_active_row.py``); this pins the drawing and the
wiring, on the real dialog, shown, over a project of 45 shots (more than
the grid shows at once), by reading the pixels the grid paints:

  1. The workfile's row, deep in the list, is in view once the dialog is
     shown; no other row is marked.
  2. Its first cell starts with the theme's accent (``ACCENT``) as a bar,
     and the rest of the row is the theme's wash (``ACCENT_DIM``) over what
     the row was; a row above it has neither.
  3. Unticking everything (**Clear**) leaves the highlight; ticking the
     row's Render leaves the bar and the wash in its other cells.
  4. Filtering the row out and back keeps it highlighted.
  5. Hovering the row's name says it is the open workfile's; another
     row's name says nothing.
  6. A Multi's workfile (two members): both rows are highlighted, the
     first is in view.
  7. No workfile: no row is highlighted, and the grid opens on the row it
     was opened for. Opened for another shot than the workfile's (a card's
     Submit Jobs…): the grid opens on that shot, and the workfile's row is
     the highlighted one.
  8. The other tab (Assets) lists none of the workfile's shots: nothing is
     highlighted there.

Run (no Houdini; the preflight ``qt`` stage sets TH_PROJECT_PATH at a copy
of scripts/project_template)::

    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --no-project --python 3.12 --with pyside6 python scripts/verify_submit_active_row.py

Set ``ACTIVE_ROW_SCREENSHOT=<png>`` to also save the dialog as drawn.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Whatever the window remembers goes to a temp prefs dir, never the artist's.
os.environ["HOUDINI_USER_PREF_DIR"] = tempfile.mkdtemp(prefix="active_row_prefs_")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from tumbletrove.asset_browser.core import theme  # noqa: E402

from tumblepipe.asset_browser import submit_jobs_dialog as mod  # noqa: E402
from tumblepipe.farm.submit import grid  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""), flush=True)


def settle() -> None:
    for _ in range(6):
        app.processEvents()


def make_shots() -> list[str]:
    from tumblepipe.api import default_client
    config = default_client().config
    uris = []
    for seq in ("010", "020", "030"):
        config.add_entity(Uri.parse(f"entity:/shots/{seq}"), {})
        for shot in range(1, 16):
            uri = Uri.parse(f"entity:/shots/{seq}/sh{shot * 10:03d}")
            config.add_entity(uri, {"frame_start": 1001, "frame_end": 1010})
            uris.append(str(uri))
    return uris


def row_rect(pane, uri: str, column: int = 0):
    return pane._tree.visualRect(pane._tree.indexFromItem(pane._items[uri], column))


def in_view(pane, uri: str) -> bool:
    rect = row_rect(pane, uri)
    return rect.isValid() and pane._tree.viewport().rect().contains(rect.center())


def pixels(pane):
    return pane._tree.viewport().grab().toImage()


def colour_at(image, point: QPoint) -> tuple[int, int, int]:
    ratio = image.devicePixelRatio()
    colour = QColor(image.pixel(int(point.x() * ratio), int(point.y() * ratio)))
    return colour.red(), colour.green(), colour.blue()


def washed(base: tuple[int, int, int]) -> tuple[int, int, int]:
    """``base`` under the theme's wash, by the usual source-over rule."""
    r, g, b, a = grid.token_rgba(theme.ACCENT_DIM)
    mix = lambda top, under: round(top * a / 255 + under * (255 - a) / 255)  # noqa: E731
    return mix(r, base[0]), mix(g, base[1]), mix(b, base[2])


def near(a, b, tolerance: int = 2) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def bar_point(pane, uri: str) -> QPoint:
    rect = row_rect(pane, uri)
    return QPoint(rect.left() + 1, rect.center().y())


def quiet_point(pane, uri: str) -> QPoint:
    """A spot at the far end of the row's name cell, past the name: nothing
    is drawn there but the row's fill."""
    rect = row_rect(pane, uri)
    return QPoint(rect.right() - 8, rect.top() + 4)


def highlighted(pane, image, uri: str) -> bool:
    accent = grid.token_rgba(theme.ACCENT)[:3]
    return near(colour_at(image, bar_point(pane, uri)), accent)


def open_dialog(opened: list[str], active: list[str]):
    win = mod.SubmitJobsDialog(
        [Uri.parse(u) for u in opened], [u.rsplit("/", 1)[-1] for u in opened], "shots",
        tick_kinds=(grid.RENDER,) if opened else (), active=active,
    )
    win.show()
    settle()
    return win, win._pane("shots")


def main() -> int:
    shots = make_shots()
    target = "entity:/shots/030/sh120"          # deep: below the first screen
    plain = "entity:/shots/030/sh100"           # in view beside it, not the workfile's

    win, pane = open_dialog([target], [target])
    check("1. the workfile's row is the only one marked", pane._active == {target}, str(pane._active))
    check("1. the grid lists more rows than it shows at once",
          not in_view(pane, shots[0]) or not in_view(pane, shots[-1]))
    check("1. the workfile's row is in view once shown", in_view(pane, target),
          str(row_rect(pane, target)))

    image = pixels(pane)
    base_plain = colour_at(image, quiet_point(pane, plain))
    check("2. the row starts with the theme's accent as a bar", highlighted(pane, image, target),
          str(colour_at(image, bar_point(pane, target))))
    check("2. a row beside it has no bar", not highlighted(pane, image, plain))
    # The two rows are an even number apart, so they carry the same stripe.
    same_stripe = (target in pane._zebra) == (plain in pane._zebra)
    got = colour_at(image, quiet_point(pane, target))
    check("2. the row is the theme's wash over what the row was",
          same_stripe and near(got, washed(base_plain)) and not near(got, base_plain),
          f"{got} vs washed {washed(base_plain)} of {base_plain}")

    if os.environ.get("ACTIVE_ROW_SCREENSHOT"):
        win.grab().save(os.environ["ACTIVE_ROW_SCREENSHOT"])

    pane._clear_ticks()
    settle()
    image = pixels(pane)
    check("3. nothing ticked: the row is still highlighted",
          not pane._ticks and highlighted(pane, image, target)
          and near(colour_at(image, quiet_point(pane, target)), washed(base_plain)))
    pane._toggle([(target, grid.RENDER)])
    settle()
    image = pixels(pane)
    check("3. its Render ticked: the bar and the wash stay",
          (target, grid.RENDER) in pane._ticks and highlighted(pane, image, target)
          and near(colour_at(image, quiet_point(pane, target)), washed(base_plain)))

    pane._filter.setText("sh010")
    settle()
    hidden = target in pane._hidden
    pane._filter.setText("")
    settle()
    pane._scroll_on_open()
    settle()
    image = pixels(pane)
    check("4. filtered out and back, it is highlighted and found again",
          hidden and in_view(pane, target) and highlighted(pane, image, target))

    name_cell = row_rect(pane, target).center()
    check("5. hovering its name says it is the open workfile's",
          pane._tooltip_at(name_cell) == grid.ACTIVE_ROW_TIP, repr(pane._tooltip_at(name_cell)))
    check("5. another row's name says nothing", pane._tooltip_at(row_rect(pane, plain).center()) == "")
    win.reject()
    settle()

    members = ["entity:/shots/020/sh140", "entity:/shots/030/sh130"]
    win, pane = open_dialog([], members)
    image = pixels(pane)
    check("6. a Multi's workfile: both members are marked, the first in view",
          pane._active == set(members) and in_view(pane, members[0])
          and highlighted(pane, image, members[0]), str(pane._active))
    pane._tree.scrollToItem(pane._items[members[1]])
    settle()
    check("6. the second member is highlighted too", highlighted(pane, pixels(pane), members[1]))
    win.reject()
    settle()

    win, pane = open_dialog([target], [])
    image = pixels(pane)
    check("7. no workfile: nothing is highlighted", not pane._active and not highlighted(pane, image, target))
    check("7. the grid opens on the row it was opened for", in_view(pane, target))

    win.reject()
    settle()

    # A card's Submit Jobs… for another shot than the open workfile's.
    card = "entity:/shots/010/sh020"
    win, pane = open_dialog([card], [target])
    check("7. opened for another shot: the grid opens on that shot",
          in_view(pane, card) and not in_view(pane, target))
    pane._tree.scrollToItem(pane._items[target])
    settle()
    image = pixels(pane)
    check("7. and the workfile's row, not the card's, is the highlighted one",
          pane._active == {target} and highlighted(pane, image, target))
    pane._tree.scrollToItem(pane._items[card])
    settle()
    check("7. the card's row has no bar", not highlighted(pane, pixels(pane), card))

    win._show_context("assets")
    settle()
    assets = win._pane("assets")
    check("8. the Assets tab highlights nothing for a shot workfile", assets._active == frozenset())
    win.reject()
    settle()

    win, pane = open_dialog([], [target])
    win._show_context("assets")
    settle()
    check("8. nor when the workfile's shot is given to it", win._pane("assets")._active == frozenset())
    win.reject()
    settle()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    sys.exit(main())
