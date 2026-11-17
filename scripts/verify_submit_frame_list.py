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
  5. A frame timeline (``asset_browser.frame_timeline``, Remy 2026-11-13)
     sits under the Frame list, one compact row, two-way bound: typing
     redraws it, a gesture writes the field (merged, clamped to the shot),
     unreadable text is left alone, mixed shot ranges show their union.
  6. The Render's Preview frames row (field + timeline) is hidden while
     Preview is off and shows when ticked; clicks add frames in order.
  7. The Comp's Preview frames row does the same; its timeline picks only
     frames on the comp's step.
  8. A remembered Preview tick restores its row shown (or hidden).
  9. The render's Step (Remy, 2026-11-17) sits on the Frames row and is
     sent as render_step; above 1 the note says the list renders every
     Nth frame and is not published, Copy to edit goes off (saying why),
     and the timelines take the step; back on 1 all comes back.
 10. Precision (Remy, 2026-11-17): hovering names the frame a click lands
     on, a drag names its first and last frame, Ctrl+wheel zooms about the
     pointer and Shift+wheel pans (a plain wheel scrolls the form), a click
     on the zoomed ruler picks the frame under it; the timeline's tooltip
     is short wrapped rich text drawn at a readable width, and
     ``asset_browser.tooltips.install`` wraps a window's long tooltips.

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

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.asset_browser import submit_jobs_dialog as sjd  # noqa: E402
from tumblepipe.farm.specs import frame_ranges  # noqa: E402
from tumblepipe.farm.submit import remembered  # noqa: E402
from tumblepipe.asset_browser.frame_timeline import FrameTimeline  # noqa: E402

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

    # 5. The frame list's timeline, bound both ways to the field
    timeline = pane._rnd_frame_timeline
    check("a timeline sits under the Frame list field",
          isinstance(timeline, FrameTimeline) and _row_visible(pane, timeline))
    check("the timeline is one compact row", 28 <= timeline.height() <= 36, str(timeline.height()))
    rows = [('a', {'render': True, 'first_frame': 1001, 'last_frame': 1100}, [], [])]
    pane._update_timelines(rows)
    view = timeline.view()
    check("it shows the ticked shot's frames", (view.lo, view.hi, view.caption) == (1001, 1100, ''),
          repr(view))
    field.setText('1001-1030 1040-1055')
    check("typing redraws the timeline",
          timeline.selection().ranges == ((1001, 1030), (1040, 1055)), repr(timeline.selection()))
    timeline.apply(1031, 1039, False)
    check("a drag on the timeline writes the field, merged",
          field.text() == '1001-1055' and note.text().startswith('55 frames'), field.text())
    timeline.apply(1010, 1019, True)
    check("a remove drag cuts a gap", field.text() == '1001-1009 1020-1055', field.text())
    timeline.apply(1090, 1300, False)
    check("a drag past the shot is clamped to its frames",
          field.text() == '1001-1009 1020-1055 1090-1100', field.text())
    # The mouse and keys, as events: a drag adds, a right-drag removes,
    # a click toggles, Space toggles the cursor's frame.
    field.setText('')
    timeline.resize(400, timeline.height())

    def x_of(frame):
        return int((frame - 1001 + 0.5) / 100 * (timeline.width() - 2)) + 1

    def mouse(kind, frame, button, buttons):
        pos = QPointF(x_of(frame), 10)
        event = QMouseEvent(kind, pos, timeline.mapToGlobal(pos), button, buttons, Qt.NoModifier)
        QApplication.sendEvent(timeline, event)

    def drag(a, b, button):
        mouse(QEvent.MouseButtonPress, a, button, button)
        mouse(QEvent.MouseMove, b, Qt.NoButton, button)
        mouse(QEvent.MouseButtonRelease, b, button, Qt.NoButton)

    drag(1011, 1020, Qt.LeftButton)
    check("a mouse drag writes the stretch", field.text() == '1011-1020', field.text())
    drag(1014, 1016, Qt.RightButton)
    check("a right-drag removes a stretch", field.text() == '1011-1013 1017-1020', field.text())
    QTest.mouseClick(timeline, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(1050), 10))
    check("a click adds one frame", field.text() == '1011-1013 1017-1020 1050', field.text())
    QTest.mouseClick(timeline, Qt.LeftButton, Qt.ShiftModifier, QPoint(x_of(1050), 10))
    check("a Shift-click removes it", field.text() == '1011-1013 1017-1020', field.text())
    timeline.setFocus()
    QTest.keyClick(timeline, Qt.Key_Home)
    QTest.keyClick(timeline, Qt.Key_Space)
    check("Home then Space toggles the first frame",
          field.text() == '1001 1011-1013 1017-1020', field.text())
    field.setText('1001-1030 abc')
    timeline.apply(1050, 1050, False)
    check("unreadable text is never rewritten by the timeline", field.text() == '1001-1030 abc',
          field.text())
    pane._update_timelines([
        ('a', {'render': True, 'first_frame': 1001, 'last_frame': 1100, 'pre_roll': 5}, [], []),
        ('b', {'render': True, 'first_frame': 1001, 'last_frame': 1240}, [], []),
    ])
    view = timeline.view()
    check("shots of different ranges show their union, rolls included, and say so",
          (view.lo, view.hi) == (996, 1240) and view.mixed and 'union' in view.caption
          and 'union' in timeline.toolTip(), repr(view))
    field.setText('')

    # 6. Preview frames: hidden until Preview is ticked, then with a timeline
    holder = pane._rnd_preview_list.parentWidget()
    pane._rnd_preview.setChecked(False)
    check("the render's Preview frames row is hidden while Preview is off",
          not _row_visible(pane, holder))
    pane._rnd_preview.setChecked(True)
    check("... and shows when Preview is ticked", _row_visible(pane, holder))
    ptl = pane._rnd_preview_timeline
    check("... with its timeline under the field", ptl.parentWidget() is holder)
    pane._update_timelines(rows)
    ptl.apply(1050, 1050, False)
    ptl.apply(1010, 1010, False)
    check("clicks on the preview timeline add frames in click order",
          pane._rnd_preview_list.text() == '1050 1010', pane._rnd_preview_list.text())
    ptl.apply(1050, 1050, False)
    check("a second click on a frame removes it", pane._rnd_preview_list.text() == '1010',
          pane._rnd_preview_list.text())
    pane._rnd_preview_list.setText('1077 1012')
    check("typing redraws the preview timeline", ptl.selection().frames == (1077, 1012))
    check("the preview list still reaches the form",
          pane._form_values()['preview_frame_list'] == '1077 1012')
    pane._rnd_preview.setChecked(False)
    check("unticking Preview hides the row again", not _row_visible(pane, holder))

    # 7. The Comp section's Preview frames do the same
    comp_box = pane._build_recomp_section()
    comp_holder = pane._comp_preview_list.parentWidget()
    check("the comp's Preview frames row is hidden while its Preview is off",
          not pane._comp_preview.isChecked() and not _row_visible(pane, comp_holder))
    pane._comp_preview.setChecked(True)
    check("... and shows when ticked", _row_visible(pane, comp_holder))
    pane._props = {'c': {'frame_start': 1001, 'frame_end': 1048}}
    pane._update_timelines([('c', {'comp': True, 'comp_step': 2}, [], [])])
    ctl = pane._comp_preview_timeline
    ctl.apply(1001, 1006, False)
    check("a drag on the comp timeline adds only frames on the comp's step",
          pane._comp_preview_list.text() == '1001 1003 1005', pane._comp_preview_list.text())

    # 8. A remembered Preview box shows its row as it is set back
    pane._remembered = remembered.capture({}, {'render_preview': True, 'render_full': True, 'comp_preview': False}, [])
    try:
        pane._restore_remembered()
        restored = True
    except Exception as error:  # noqa: BLE001 — the record's shape is remembered.py's
        restored = repr(error)
    check("a remembered Preview tick restores the row shown",
          restored is True and pane._rnd_preview.isChecked() == _row_visible(pane, holder)
          and pane._comp_preview.isChecked() == _row_visible(pane, comp_holder), str(restored))

    # 9. The render's Step (Remy, 2026-11-17)
    step = pane._rnd_step
    frames_row = pane._rnd_first.parentWidget()
    check("a Step field sits on the Frames row, beside First → Last",
          step.parentWidget() is frames_row and _row_visible(pane, frames_row))
    field.setText('')
    copy.setChecked(True)
    step.setValue(2)
    values = pane._form_values()
    check("the form sends the step as render_step", values.get('render_step') == 2,
          repr(values.get('render_step')))
    check("the note says every 2nd frame, not published",
          'every 2nd frame' in note.text() and frame_ranges.STEPPED_NOT_PUBLISHED in note.text(),
          note.text())
    check("a step above 1 disables Copy to edit, saying why, and sends none",
          not copy.isEnabled() and frame_ranges.STEPPED_NOT_PUBLISHED in copy.toolTip()
          and copy.isChecked() and values['copy_to_edit'] is False,
          f"enabled={copy.isEnabled()} tip={copy.toolTip()!r}")
    field.setText('1001-1030 1040-1055')
    check("a list on a step counts each range's frames on it",
          note.text().startswith('23 frames in 2 ranges'), note.text())
    pane._update_timelines(rows)
    check("the timelines take the step", timeline._step == 2 and ptl._step == 2,
          f"{timeline._step} {ptl._step}")
    pane._rnd_preview.setChecked(True)
    pane._rnd_preview_list.setText('')
    ptl.apply(1001, 1006, False)
    check("the preview timeline picks only frames on the render's step",
          pane._rnd_preview_list.text() == '1001 1003 1005', pane._rnd_preview_list.text())
    pane._rnd_preview_list.setText('')
    pane._rnd_preview.setChecked(False)
    field.setText('')
    step.setValue(1)
    values = pane._form_values()
    check("step 1 gives Copy to edit back as it was",
          copy.isEnabled() and copy.toolTip() == 'the own tooltip' and values['copy_to_edit'] is True,
          f"enabled={copy.isEnabled()} tip={copy.toolTip()!r}")
    field.setText('')

    # 10. Precision: the frame under the pointer, zoom, and wrapped tooltips
    from PySide6.QtGui import QHelpEvent, QWheelEvent  # noqa: PLC0415
    from tumblepipe.asset_browser import tooltips  # noqa: PLC0415
    pane._update_timelines(rows)
    timeline.resize(350, timeline.height())

    def x_at(frame):
        lo, hi = timeline.shown()
        return int((frame - lo + 0.5) / (hi - lo + 1) * (timeline.width() - 2)) + 1

    def move(frame, buttons=Qt.NoButton):
        pos = QPointF(x_at(frame), 10)
        QApplication.sendEvent(timeline, QMouseEvent(
            QEvent.MouseMove, pos, timeline.mapToGlobal(pos), Qt.NoButton, buttons, Qt.NoModifier,
        ))

    move(1043)
    check("hovering names the frame a click lands on", timeline.floating_label() == '1043',
          timeline.floating_label())
    pos = QPointF(x_at(1011), 10)
    QApplication.sendEvent(timeline, QMouseEvent(
        QEvent.MouseButtonPress, pos, timeline.mapToGlobal(pos), Qt.LeftButton, Qt.LeftButton,
        Qt.NoModifier,
    ))
    move(1020, Qt.LeftButton)
    check("a drag names its first and last frame before the release",
          timeline.floating_label() == '1011-1020', timeline.floating_label())
    pos = QPointF(x_at(1020), 10)
    QApplication.sendEvent(timeline, QMouseEvent(
        QEvent.MouseButtonRelease, pos, timeline.mapToGlobal(pos), Qt.LeftButton, Qt.NoButton,
        Qt.NoModifier,
    ))
    check("... and writes exactly those frames", field.text() == '1011-1020', field.text())
    QApplication.sendEvent(timeline, QEvent(QEvent.Leave))
    check("leaving clears the label", timeline.floating_label() == '', timeline.floating_label())
    field.setText('')

    def wheel(frame, notches, mods):
        pos = QPointF(x_at(frame), 10)
        event = QWheelEvent(
            pos, timeline.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120 * notches),
            Qt.NoButton, mods, Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(timeline, event)
        return event.isAccepted()

    accepted = wheel(1050, 3, Qt.NoModifier)
    check("a plain wheel is left to scroll the form", not accepted and timeline.shown() == (1001, 1100),
          repr(timeline.shown()))
    wheel(1050, 3, Qt.ControlModifier)
    lo, hi = timeline.shown()
    check("Ctrl+wheel zooms in about the pointer", hi - lo + 1 < 100 and lo <= 1050 <= hi,
          repr(timeline.shown()))
    check("... and the frame under the pointer stays under it",
          abs(timeline.frame_at(x_at(1050)) - 1050) <= 1, str(timeline.frame_at(x_at(1050))))
    before = timeline.shown()
    wheel(1050, -1, Qt.ShiftModifier)
    after = timeline.shown()
    check("Shift+wheel pans the zoomed ruler, as many frames shown",
          after != before and after[1] - after[0] == before[1] - before[0], f"{before} {after}")
    move(after[0] + 3)
    pos = QPointF(x_at(after[0] + 3), 10)
    QTest.mouseClick(timeline, Qt.LeftButton, Qt.NoModifier, pos.toPoint())
    check("a click on the zoomed ruler picks the frame under it",
          field.text() == str(after[0] + 3), field.text())
    field.setText('')
    wheel(1050, -20, Qt.ControlModifier)
    check("Ctrl+wheel out goes back to every frame", timeline.shown() == (1001, 1100),
          repr(timeline.shown()))

    tip = timeline.toolTip()
    check("the timeline's tooltip is wrapped rich text, and short",
          tip.startswith('<qt>') and len(tip) < 600, f"{len(tip)} {tip[:80]!r}")
    QApplication.sendEvent(timeline, QHelpEvent(
        QEvent.ToolTip, QPoint(x_at(1043), 10), timeline.mapToGlobal(QPoint(x_at(1043), 10)),
    ))
    shown_tip = QToolTip.text()
    label = next((w for w in QApplication.topLevelWidgets()
                  if w.inherits('QLabel') and w.isVisible() and w.objectName() == 'qtooltip_label'), None)
    check("hovering shows the frame first in the tooltip",
          'Frame 1043' in shown_tip and shown_tip.startswith('<qt>'), shown_tip[:80])
    check("... drawn at a readable width",
          label is not None and 0 < label.width() <= 640, str(label and label.width()))
    QToolTip.hideText()

    long_text = "word " * 60
    check("long plain text wraps", tooltips.wrapped(long_text).startswith('<qt>'))
    check("short text and markup are left alone",
          tooltips.wrapped('Short tip') == 'Short tip' and tooltips.wrapped('<b>x</b>') == '<b>x</b>')
    root = QWidget()
    tooltips.install(root)
    late = sjd.QCheckBox("late", root)      # added after install
    late.setToolTip(long_text.strip())
    root.resize(200, 100)
    root.show()
    QApplication.sendEvent(late, QHelpEvent(QEvent.ToolTip, QPoint(5, 5), late.mapToGlobal(QPoint(5, 5))))
    check("a window's long tooltips show wrapped, widgets added later too",
          QToolTip.text().startswith('<qt>'), QToolTip.text()[:60])
    QToolTip.hideText()
    root.close()

    shot = os.environ.get('FRAME_LIST_SCREENSHOT')
    if shot:
        step.setValue(2)
        pane._update_timelines(rows)
        move(1043)
        pane._rnd_preview.setChecked(True)
        pane._rnd_preview_list.setText('1012 1040 1077')
        field.setText('1001-1030 1040-1055')
        pane._update_timelines(rows)
        box.resize(460, box.sizeHint().height())
        box.grab().save(shot)
        comp_box.resize(460, comp_box.sizeHint().height())
        comp_box.grab().save(shot.replace('.png', '_comp.png'))

    asset_pane, _box, _changes = build_pane('assets')
    asset_pane._rnd_frame_list.setText('1001-1010 1020')
    check("the Assets tab hides the field and its note",
          not _row_visible(asset_pane, asset_pane._rnd_frame_list)
          and not _row_visible(asset_pane, asset_pane._rnd_frame_list_note))
    sent = asset_pane._form_values().get('frame_list')
    check("the Assets tab sends no frame list", sent == '', repr(sent))
    check("the Assets tab hides the Step with the frames (a turntable's own step)",
          not _row_visible(asset_pane, asset_pane._rnd_step.parentWidget())
          and asset_pane._render_step() == 1)

    print()
    print('FAILED: ' + ', '.join(FAILED) if FAILED else 'all ok')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
