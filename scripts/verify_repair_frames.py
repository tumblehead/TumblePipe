"""Headless Qt harness for repairing frames (#92): Farm Submit's Into and the
Farm Manager's Render again.

Builds the real Render section of ``asset_browser.submit_jobs_dialog`` on a
bare pane (as ``verify_submit_frame_list.py`` does) and pins:

  1. Into sits in the Render section on New version, with Latest version
     beside it, its note saying it renders a new version, and the form sends
     ``into_version`` '' .
  2. Latest version with no Frame list says the Frame list is needed; with a
     list the note names the frames and the newest version, and the form
     sends ``latest``.
  3. Opening the menu lists the versions the ticked rows' channels have,
     newest first, after New and Latest, and keeps the pick.
  4. With Into set, a multi-range Frame list leaves Copy to edit on (the
     version decides); back on New version it goes off again.
  5. The Assets tab hides Into and its note and sends ''.
  6. The Farm Manager's task list selects several tasks.

Run (no Houdini, no project):
    QT_QPA_PLATFORM=offscreen PYTHONUTF8=1 TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --no-project --python 3.12 --with pyside6 python scripts/verify_repair_frames.py
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))


def _tumbletrove_python() -> Path | None:
    configured = os.environ.get('TUMBLETROVE_PYTHON')
    if configured:
        return Path(configured)
    fetched = sorted((REPO_ROOT / '.tools' / 'hpm-fetch').glob('tumbletrove-tumbletrove-*/python'))
    return fetched[-1] if fetched else None


_tt = _tumbletrove_python()
if _tt is None or not _tt.is_dir():
    sys.exit("No tumbletrove python found: set TUMBLETROVE_PYTHON (see the docstring).")
sys.path.insert(0, str(_tt))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.asset_browser import submit_jobs_dialog as sjd  # noqa: E402
from tumblepipe.farm.specs import repair  # noqa: E402

FAILED = []


def check(name: str, ok: bool, info: str = '') -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info and not ok else ''))
    if not ok:
        FAILED.append(name)


class _Timer:
    def start(self) -> None:
        pass


def build_pane(context: str):
    """A _ContextPane with only what _build_render_section touches."""
    pane = sjd._ContextPane.__new__(sjd._ContextPane)
    QWidget.__init__(pane)
    pane._fields = {}
    pane._seeding = False
    pane._context = context
    pane._renderable_departments = ['animation', 'render']
    pane._refresh_timer = _Timer()
    changes = []
    pane._changed = lambda: changes.append(1)
    pane._on_cut_changed = lambda: None
    pane._seed_values = lambda: {}
    pane._reverts = []
    pane._comp_copy_edit = None
    pane._build_render_section()
    return pane, changes


def _row_visible(widget) -> bool:
    return widget.parentWidget().layout().isRowVisible(widget)


def main() -> int:
    pane, changes = build_pane('shots')
    into, note = pane._rnd_into, pane._rnd_into_note

    # 1. New version by default
    check("Into starts on New version", into.currentData() == repair.TARGET_NEW)
    check("Latest version is offered", into.findData(repair.TARGET_LATEST) == 1)
    check("the note says a new version", 'new version' in note.text(), note.text())
    check("the form sends into_version ''", pane._into_target() == '')

    # 2. Latest version
    into.setCurrentIndex(into.findData(repair.TARGET_LATEST))
    check("picking it counts as a change", bool(changes))
    check("without a Frame list the note asks for one",
          repair.NEEDS_FRAME_LIST in note.text(), note.text())
    pane._rnd_frame_list.setText('1047 1102-1104')
    check("with a list the note names the frames and the newest version",
          '4 frames' in note.text() and 'newest version' in note.text(), note.text())
    check("the form sends latest", pane._into_target() == 'latest')

    # 3. The menu lists the versions on disk, newest first, keeping the pick
    rows = [('entity:/shots/010/sh010', {'render': True, 'render_department': 'render',
                                         'channels': ['default']}, [], [])]
    pane._resolved_rows = lambda: rows
    import tumblepipe.pipe.paths.render as render_paths
    saved = render_paths.list_render_versions
    render_paths.list_render_versions = lambda uri, dept, channel, *a: {
        'v0001': {}, 'v0003': {}, 'v0002': {},
    }
    try:
        pane._refresh_into_versions()
    finally:
        render_paths.list_render_versions = saved
    labels = [into.itemText(i) for i in range(into.count())]
    check("the menu lists the versions newest first",
          labels == ['New version', 'Latest version', 'v0003', 'v0002', 'v0001'], str(labels))
    check("the pick is kept", into.currentData() == 'latest')
    into.setCurrentIndex(into.findData('v0002'))
    check("a version picked is sent", pane._into_target() == 'v0002')

    # 4. Copy to edit stays on with Into set
    copy_edit = pane._rnd_copy_edit
    check("a multi-range list with Into set leaves Copy to edit on", copy_edit.isEnabled())
    into.setCurrentIndex(0)
    check("back on New version a multi-range list turns it off", not copy_edit.isEnabled())

    # 5. Assets
    assets, _changes = build_pane('assets')
    check("the Assets tab hides Into", not assets._rnd_into.isVisibleTo(assets))
    check("... and its note", not _row_visible(assets._rnd_into_note))
    check("the Assets tab sends ''", assets._into_target() == '')

    # 6. The Farm Manager's task list selects several
    import ast
    source = (REPO_ROOT / 'python' / 'tumblepipe' / 'farm' / 'manager' / 'window.py').read_text(encoding='utf-8')
    ast.parse(source)
    check("the Farm Manager's task list selects several tasks",
          'tree.setSelectionMode(QAbstractItemView.ExtendedSelection)\n        header = tree.header()' in source)

    print(f"\n{'all passed' if not FAILED else f'{len(FAILED)} failed'}")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
