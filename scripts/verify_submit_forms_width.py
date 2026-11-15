"""Headless Qt harness: the Farm Submit settings panel fits every section.

The panel's width used to be fixed once, while the dialog was built, from
fields the window's stylesheet had not polished yet: Playblast measured
493px there and drew 553px, so its right edge hid behind a horizontal
scroll bar (GUI check 2026-11-15). ``grid.forms_width`` is the arithmetic
(``tests/test_farm_submit_forms_width.py``); this pins the wiring, on the
real dialog, shown, with its stylesheet:

  1. Each section shown alone, on each tab that has it, fits without a
     horizontal scroll bar, beside the vertical one.
  2. The panel never narrows as sections come and go: one width for the
     dialog's life, whatever was ticked.
  3. Every section open and showing at once still fits (the widest case).

Run (no Houdini; the preflight ``qt`` stage sets TH_PROJECT_PATH at a copy
of scripts/project_template)::

    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --no-project --python 3.12 --with pyside6 python scripts/verify_submit_forms_width.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Whatever the window remembers goes to a temp prefs dir, never the artist's.
os.environ["HOUDINI_USER_PREF_DIR"] = tempfile.mkdtemp(prefix="forms_width_prefs_")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from tumblepipe.asset_browser import submit_jobs_dialog as mod  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def settle() -> None:
    for _ in range(3):
        app.processEvents()


def fits(pane, section) -> tuple[bool, str]:
    scroll = pane._forms_scroll
    bar = scroll.verticalScrollBar().sizeHint().width()
    need = section.minimumSizeHint().width() + bar
    clipped = scroll.horizontalScrollBar().maximum()
    return clipped == 0 and scroll.width() >= need, f"panel {scroll.width()}px, needs {need}px, clipped {clipped}px"


def main() -> int:
    win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=())
    win.show()
    settle()
    for context in ("shots", "assets"):
        # Tabs are built when first shown; this builds and shows each.
        win._show_context(context)
        settle()
        pane = win._pane(context)
        scroll = pane._forms_scroll
        widths = [scroll.width()]
        pane._no_settings.hide()
        for kind, section in pane._sections.items():
            section.set_expanded(True)
            for other in pane._sections.values():
                other.setVisible(other is section)
            settle()
            ok, detail = fits(pane, section)
            check(f"1. {context}: {kind} alone fits without a horizontal scroll bar", ok, detail)
            widths.append(scroll.width())
        check(f"2. {context}: the panel never narrows as sections come and go",
              widths == sorted(widths), f"widths {widths}")
        for section in pane._sections.values():
            section.setVisible(True)
        settle()
        clipped = scroll.horizontalScrollBar().maximum()
        check(f"3. {context}: every section showing at once still fits", clipped == 0,
              f"panel {scroll.width()}px, clipped {clipped}px")
    win.reject()
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    sys.exit(main())
