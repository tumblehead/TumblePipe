"""Headless harness for the value ladder on Qt number fields
(``tumblepipe.util.value_ladder_qt``): Houdini's middle-mouse drag.

Sends real QMouseEvents to real fields, offscreen. Checks:

  1. a QSpinBox: middle-press opens the ladder on the 1 row; dragging right
     two notches adds 2; up a row (the 10s) and right a notch adds 10;
     the ladder shows no step below 1; valueChanged fires once, on release,
     with the final value
  2. limits hold: a long drag stops at the maximum
  3. Ctrl + middle-click resets to the default given at install; on a field
     with special value text, to that "not set" minimum
  4. a QDoubleSpinBox gets the 0.1 and 0.01 steps; a drag down two rows
     steps by 0.01
  5. a QLineEdit with a QIntValidator: text steps, editingFinished fires on
     release
  6. left-button presses are left to the field (no grab, value unchanged)
  7. the Entity Editor's real fields (``_spin``, a render-setting
     ``_NumberField``) carry the ladder; a drag on the render setting
     commits once
  8. ``install_all`` finds the number fields under a widget and leaves
     plain text fields alone
  9. the Farm Submit dialog's spin fields: a drag pins the field, Ctrl +
     middle-click is its ↺ (back to each entity's own value)
 10. the Database Editor's integer and float cell editors step, and reset
     to the value they opened with
 11. walking the real widgets (the Prune window, the Entity Editor's number
     and resolution fields, the Farm Submit pane, the Database Editor cells):
     every spin box and number-validated line edit has the ladder
     (tests/test_value_ladder_coverage checks the same from the source)

Run (no Houdini licence; TumbleTrove's python/ on PYTHONPATH for the Entity
Editor's theme):

    QT_QPA_PLATFORM=offscreen python scripts/verify_value_ladder.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

if os.environ.get('HFS') and hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(str(Path(os.environ['HFS']) / 'bin'))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QIntValidator, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDoubleSpinBox, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from tumblepipe.util import value_ladder as maths  # noqa: E402
from tumblepipe.util import value_ladder_qt as vlq  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    _checks.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ''), flush=True)


ORIGIN = QPointF(400.0, 300.0)


def _send(target, kind, global_pos: QPointF, button, buttons, modifiers=Qt.NoModifier) -> None:
    local = QPointF(target.mapFromGlobal(global_pos.toPoint()))
    event = QMouseEvent(kind, local, global_pos, button, buttons, modifiers)
    QApplication.sendEvent(target, event)


def _receiver(field):
    return field.lineEdit() if isinstance(field, (QSpinBox, QDoubleSpinBox)) else field


def drag(field, moves, modifiers=Qt.NoModifier, button=Qt.MiddleButton) -> None:
    """Press at ORIGIN, move by each (dx, dy), release at the last."""
    target = _receiver(field)
    _send(target, QEvent.MouseButtonPress, ORIGIN, button, button, modifiers)
    at = ORIGIN
    for dx, dy in moves:
        at = QPointF(ORIGIN.x() + dx, ORIGIN.y() + dy)
        grabber = QWidget.mouseGrabber() or target
        _send(grabber, QEvent.MouseMove, at, Qt.NoButton, button, modifiers)
    grabber = QWidget.mouseGrabber() or target
    _send(grabber, QEvent.MouseButtonRelease, at, button, Qt.NoButton, modifiers)


def _number_fields(roots) -> list:
    """Every spin box, and every line edit with a validator other than a
    regular expression one, under ``roots`` (a spin box's own line edit
    excluded: the ladder sits on the spin box)."""
    from PySide6.QtGui import QRegularExpressionValidator
    from PySide6.QtWidgets import QAbstractSpinBox
    found = []
    for root in roots:
        for widget in [root, *root.findChildren(QWidget)]:
            if isinstance(widget.parentWidget(), QAbstractSpinBox):
                continue
            if isinstance(widget, QAbstractSpinBox):
                found.append(widget)
            elif isinstance(widget, QLineEdit) and widget.validator() is not None \
                    and not isinstance(widget.validator(), QRegularExpressionValidator):
                found.append(widget)
    return found


def notches(n: float) -> float:
    return n * maths.NOTCH + maths.NOTCH / 2


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)

    # 1. a spin box
    spin = QSpinBox()
    spin.setRange(0, 500)
    spin.setValue(20)
    vlq.install(spin, default=7)
    layout.addWidget(spin)
    host.show()
    app.processEvents()
    heard: list[int] = []
    spin.valueChanged.connect(heard.append)
    ladder = vlq.ladder_of(spin)
    target = spin.lineEdit()
    _send(target, QEvent.MouseButtonPress, ORIGIN, Qt.MiddleButton, Qt.MiddleButton)
    rungs = ladder._rungs
    check('1a middle-press opens the ladder on the 1 row',
          ladder.dragging and rungs is not None and rungs.isVisible()
          and maths.steps(0)[rungs.row] == 1.0, f'rungs {rungs and rungs.labels}')
    check('1b a whole-number field has no step below 1', rungs is not None and len(rungs.labels) == 3,
          f'{rungs and rungs.labels}')
    for dx, dy in [(notches(2), 0.0), (notches(2), -maths.ROW), (notches(2) + maths.NOTCH, -maths.ROW)]:
        grabber = QWidget.mouseGrabber() or target
        _send(grabber, QEvent.MouseMove, QPointF(ORIGIN.x() + dx, ORIGIN.y() + dy), Qt.NoButton, Qt.MiddleButton)
    check('1c the field shows the dragged value while dragging, quietly', spin.value() == 32 and heard == [],
          f'value {spin.value()} heard {heard}')
    grabber = QWidget.mouseGrabber() or target
    _send(grabber, QEvent.MouseButtonRelease, ORIGIN, Qt.MiddleButton, Qt.NoButton)
    check('1d release sets it: valueChanged once, with the final value', heard == [32] and not ladder.dragging,
          f'heard {heard}')
    check('1e the ladder closes and lets go of the mouse',
          not rungs.isVisible() and QWidget.mouseGrabber() is None)

    # 2. limits
    drag(spin, [(0.0, -2 * maths.ROW), (notches(10), -2 * maths.ROW)])  # up to the 100s, ten notches
    check('2 a long drag stops at the maximum', spin.value() == 500, f'value {spin.value()}')

    # 3. reset
    heard.clear()
    drag(spin, [], modifiers=Qt.ControlModifier)
    check('3a Ctrl + middle-click resets to the default', spin.value() == 7 and heard == [7],
          f'value {spin.value()} heard {heard}')
    unset = QSpinBox()
    unset.setRange(-1, 100)
    unset.setSpecialValueText('not set')
    unset.setValue(40)
    vlq.install(unset)
    layout.addWidget(unset)
    drag(unset, [], modifiers=Qt.ControlModifier)
    check('3b with special value text, the reset is "not set"', unset.value() == -1 and unset.text() == 'not set',
          f'value {unset.value()} text {unset.text()!r}')

    # 4. a double spin box
    double = QDoubleSpinBox()
    double.setRange(0.0, 10.0)
    double.setDecimals(3)
    double.setValue(1.5)
    vlq.install(double)
    layout.addWidget(double)
    target = double.lineEdit()
    _send(target, QEvent.MouseButtonPress, ORIGIN, Qt.MiddleButton, Qt.MiddleButton)
    labels = list(vlq.ladder_of(double)._rungs.labels)
    for dx, dy in [(0.0, 2 * maths.ROW), (notches(3), 2 * maths.ROW)]:
        _send(QWidget.mouseGrabber() or target, QEvent.MouseMove,
              QPointF(ORIGIN.x() + dx, ORIGIN.y() + dy), Qt.NoButton, Qt.MiddleButton)
    _send(QWidget.mouseGrabber() or target, QEvent.MouseButtonRelease, ORIGIN, Qt.MiddleButton, Qt.NoButton)
    check('4a a decimal field gets the 0.1 and 0.01 steps', labels[-2:] == ['0.1', '0.01'], f'{labels}')
    check('4b two rows down steps by 0.01', abs(double.value() - 1.53) < 1e-9, f'value {double.value()}')

    # 5. a line edit with a validator
    edit = QLineEdit('12')
    edit.setValidator(QIntValidator(0, 99, edit))
    vlq.install(edit)
    layout.addWidget(edit)
    finished: list[str] = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))
    drag(edit, [(-notches(4), 0.0)])
    check('5 a validated line edit steps, editingFinished on release', edit.text() == '8' and finished == ['8'],
          f'text {edit.text()!r} finished {finished}')

    edit.setText('40')
    drag(edit, [], modifiers=Qt.ControlModifier)
    check('5b with no default given, Ctrl + middle-click puts back the value it opened with',
          edit.text() == '12', f'text {edit.text()!r}')

    # 6. left button
    before = spin.value()
    target = spin.lineEdit()
    _send(target, QEvent.MouseButtonPress, ORIGIN, Qt.LeftButton, Qt.LeftButton)
    grabbed = QWidget.mouseGrabber()
    _send(target, QEvent.MouseMove, QPointF(ORIGIN.x() + 80, ORIGIN.y()), Qt.NoButton, Qt.LeftButton)
    _send(target, QEvent.MouseButtonRelease, QPointF(ORIGIN.x() + 80, ORIGIN.y()), Qt.LeftButton, Qt.NoButton)
    check('6 the left button is the field\'s own: no ladder, value unchanged',
          spin.value() == before and not vlq.ladder_of(spin).dragging and grabbed is None,
          f'value {spin.value()} grabbed {grabbed}')

    # 7. the Entity Editor's own fields
    try:
        from tumblepipe.asset_browser import entity_editor_dialogs as eed
        from tumblepipe.asset_browser import entity_editor_render as eer
    except ImportError as error:  # TumbleTrove's theme missing
        check('7 the Entity Editor imports (TumbleTrove python/ on PYTHONPATH?)', False, str(error))
    else:
        frames = eed._spin(1001, -999_999)
        field = eer._NumberField('int', eer._NoWheel())
        layout.addWidget(frames)
        layout.addWidget(field)
        field.show_value(8, False)
        commits: list = []
        field.committed.connect(commits.append)
        drag(field.spin, [(notches(3), 0.0)])
        check('7a a render-setting field: a drag commits once, the final value',
              commits == [11], f'commits {commits}')
        commits.clear()
        drag(field.spin, [], modifiers=Qt.ControlModifier)
        check('7b Ctrl + middle-click on it takes the pin away (back to not set)',
              commits == [eer._UNPIN], f'commits {commits}')
        drag(frames, [(0.0, -maths.ROW), (notches(1), -maths.ROW)])
        check('7c a frame field: up a row and one notch adds 10', frames.value() == 1011, f'value {frames.value()}')
        drag(frames, [], modifiers=Qt.ControlModifier)
        check('7d and Ctrl + middle-click puts back the value it was built with', frames.value() == 1001,
              f'value {frames.value()}')

    # 9. the Farm Submit dialog's spin fields (a bare pane, as
    # verify_submit_playblast_section builds it)
    try:
        from tumblepipe.asset_browser import submit_jobs_dialog as sjd
    except ImportError as error:
        check('9 the Farm Submit dialog imports', False, str(error))
    else:
        pane = sjd._ContextPane.__new__(sjd._ContextPane)
        QWidget.__init__(pane)
        pane._fields, pane._seeding, pane._reverts = {}, False, []
        pane._renderable_departments = ['animation', 'render']
        pane._refresh_timer = type('T', (), {'start': lambda self: None})()
        pane._changed = lambda: None
        pane._on_cut_changed = lambda: None
        pane._seed_values = lambda: {'pb_samples': 64}
        pane._build_playblast_section()
        samples = pane._fields['pb_samples']
        samples.seed(64)
        drag(samples.widget, [(notches(2), 0.0)])
        check('9a a Farm Submit number: a drag pins it at the dragged value',
              samples.pinned and samples.value() == 66, f'pinned {samples.pinned} value {samples.value()}')
        drag(samples.widget, [], modifiers=Qt.ControlModifier)
        check('9b Ctrl + middle-click is its ↺: unpinned, each entity\'s own value',
              not samples.pinned and samples.value() == 64, f'pinned {samples.pinned} value {samples.value()}')

    # 10. the Database Editor's integer and float cells
    try:
        from PySide6.QtGui import QStandardItemModel
        from PySide6.QtWidgets import QStyleOptionViewItem
        from tumblepipe.config_editor.json_editor import items as je_items
        from tumblepipe.config_editor.json_editor import view as je_view
    except ImportError as error:
        check('10 the Database Editor imports', False, str(error))
    else:
        model = QStandardItemModel()
        model.appendRow([je_items.IntegerItem(5), je_items.FloatItem(0.25)])
        delegate = je_view.JsonItemDelegate()
        editors = []
        for column in (0, 1):
            index = model.index(0, column)
            editor = delegate.createEditor(host, QStyleOptionViewItem(), index)
            delegate.setEditorData(editor, index)
            layout.addWidget(editor)
            editor.show()
            app.processEvents()
            editors.append(editor)
        whole, decimal = editors
        drag(whole, [(notches(2), 0.0)])
        drag(decimal, [(0.0, 2 * maths.ROW), (notches(1), 2 * maths.ROW)])
        check('10a an integer cell\'s editor steps by whole numbers', whole.text() == '7', f'text {whole.text()!r}')
        check('10b a float cell\'s editor steps by 0.01', decimal.text() == '0.26', f'text {decimal.text()!r}')
        drag(whole, [], modifiers=Qt.ControlModifier)
        check('10c Ctrl + middle-click puts back the value the editor opened with', whole.text() == '5',
              f'text {whole.text()!r}')

    # 11. every number field the real windows build has the ladder (the
    # structural twin of tests/test_value_ladder_coverage): the Prune window,
    # the Entity Editor's render-setting and resolution fields, the Farm
    # Submit pane and Database Editor cells built above.
    roots = [host]
    try:
        from tumblepipe.cleanup import app as cleanup_app
        prune = cleanup_app.build_window()
        roots.append(prune)
    except ImportError as error:
        check('11 the Prune window builds', False, str(error))
    try:
        from tumblepipe.asset_browser import entity_editor_render as eer
        extra = QWidget()
        extra_layout = QVBoxLayout(extra)
        for field in (eer._NumberField('int', eer._NoWheel()), eer._NumberField('float', eer._NoWheel()),
                      eer._ResolutionField(lambda: [], eer._NoWheel())):
            extra_layout.addWidget(field)
        roots.append(extra)
    except (ImportError, TypeError) as error:
        check('11 the Entity Editor fields build', False, str(error))
    if 'pane' in locals():
        roots.append(pane)
    walked = _number_fields(roots)
    bare = [f'{type(w).__name__} {w.objectName() or ""}'.strip() for w in walked if vlq.ladder_of(w) is None]
    check('11 every spin box and number-validated line edit the windows build has the ladder',
          len(walked) >= 10 and not bare, f'{len(walked)} fields, without: {bare}')

    # 8. install_all
    form = QWidget()
    form_layout = QVBoxLayout(form)
    plain, number, spin2 = QLineEdit('name'), QLineEdit('3'), QSpinBox()
    number.setValidator(QIntValidator(number))
    for w in (plain, number, spin2):
        form_layout.addWidget(w)
    found = vlq.install_all(form)
    check('8 install_all takes the number fields and leaves text alone',
          found == 2 and vlq.ladder_of(plain) is None and vlq.ladder_of(number) is not None
          and vlq.ladder_of(spin2) is not None and vlq.ladder_of(spin2.lineEdit()) is None,
          f'found {found}')

    failed = [name for name, ok, _ in _checks if not ok]
    print(f"--- {len(_checks) - len(failed)}/{len(_checks)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    os._exit(code)
