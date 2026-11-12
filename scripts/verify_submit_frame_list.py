"""Headless Qt harness for the Farm Submit dialog's Frame List (#84).

Builds the real Render section of ``asset_browser.submit_jobs_dialog`` on a
bare pane (only what the section builder touches) and pins:

  1. The Frame list field sits in the Render section, empty, with the note
     under it saying that multi-range renders are not published.
  2. Typing a list updates the note: one range says it publishes as First
     → Last, two or more give the frame count and say they are not
     published, a bad word names the problem.
  3. The field is what the form sends as ``frame_list`` on the Shots tab,
     and the Assets tab sends none and hides the field and its note (a
     turntable renders the studio's frames).
  4. While the list has several ranges, Copy to edit (which a multi-range
     render ignores) is disabled with a tooltip saying why and is not
     sent; one range, a cleared list or a bad one gives it back as it was,
     tick and tooltip.

Run (no Houdini, no project):
    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --python 3.12 --with pyside6 python scripts/verify_submit_frame_list.py

``TUMBLETROVE_PYTHON`` defaults to the newest ``.tools/hpm-fetch/
tumbletrove-tumbletrove-*/python`` (``.ci/fetch_hpm_packages.py`` lays it
out). Set ``FRAME_LIST_SCREENSHOT=<png>`` to also save the section.
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
from tumblepipe.farm.specs import frame_ranges  # noqa: E402

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
    box = pane._build_render_section()
    return pane, box, changes


def _row_visible(pane, widget) -> bool:
    form = widget.parentWidget().layout()
    return form.isRowVisible(widget)


def main() -> int:
    pane, box, changes = build_pane('shots')
    field, note = pane._rnd_frame_list, pane._rnd_frame_list_note

    # 1. The field and its note
    check("the Frame list field starts empty", field.text() == '')
    check("the note says multi-range renders are not published",
          frame_ranges.NOT_PUBLISHED in note.text(), note.text())

    # 2. The note follows the text
    field.setText('1001-1030')
    check("one range publishes as First → Last",
          'publishes' in note.text() and frame_ranges.NOT_PUBLISHED not in note.text()
          and note.text().startswith('30 frames'), note.text())
    field.setText('1001-1030 1040-1055 1100-1200')
    check("three ranges: the frame count, and not published",
          note.text().startswith('147 frames in 3 ranges')
          and frame_ranges.NOT_PUBLISHED in note.text(), note.text())
    field.setText('1001-1030 1031-1040')
    check("touching ranges merge into one",
          note.text().startswith('40 frames:') and 'publishes' in note.text(), note.text())
    field.setText('1001-1030 abc')
    check("a bad word is named", "'abc'" in note.text(), note.text())
    check("each edit reaches the warnings", len(changes) >= 4, str(len(changes)))

    # 3. What the form sends
    field.setText('1001-1030 1040-1055')
    sent = None
    try:
        sent = pane._form_values().get('frame_list')
    except Exception as error:  # noqa: BLE001 — a bare pane lacks the other sections
        check("the form reads", False, repr(error))
    check("the Shots tab sends the field as frame_list",
          sent == '1001-1030 1040-1055', repr(sent))

    # 4. What a multi-range list ignores is shown off, and comes back
    copy = pane._rnd_copy_edit
    field.setText('')
    copy.setToolTip('the own tooltip')
    pane._frame_list_targets['copy_to_edit'] = (copy, 'the own tooltip')
    copy.setChecked(True)
    field.setText('1001-1030 1040-1055')
    values = pane._form_values()
    check("a multi-range list disables Copy to edit, saying why",
          not copy.isEnabled() and frame_ranges.NOT_PUBLISHED in copy.toolTip(), copy.toolTip())
    check("... keeps its tick but sends none",
          copy.isChecked() and values['copy_to_edit'] is False, repr(values['copy_to_edit']))
    for text, what in (('1001-1030', 'one range'), ('', 'a cleared list'), ('1001-1030 x', 'a bad list')):
        field.setText('1001-1030 1040-1055')
        field.setText(text)
        values = pane._form_values()
        check(f"{what} gives Copy to edit back as it was",
              copy.isEnabled() and copy.isChecked() and copy.toolTip() == 'the own tooltip'
              and values['copy_to_edit'] is True,
              f"enabled={copy.isEnabled()} tip={copy.toolTip()!r} sent={values['copy_to_edit']!r}")
    field.setText('1001-1030 1040-1055')

    shot = os.environ.get('FRAME_LIST_SCREENSHOT')
    if shot:
        box.resize(420, box.sizeHint().height())
        box.grab().save(shot)

    asset_pane, _box, _changes = build_pane('assets')
    asset_pane._rnd_frame_list.setText('1001-1010 1020')
    check("the Assets tab hides the field and its note",
          not _row_visible(asset_pane, asset_pane._rnd_frame_list)
          and not _row_visible(asset_pane, asset_pane._rnd_frame_list_note))
    sent = asset_pane._form_values().get('frame_list')
    check("the Assets tab sends no frame list", sent == '', repr(sent))

    print()
    print('FAILED: ' + ', '.join(FAILED) if FAILED else 'all ok')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
