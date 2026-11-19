"""Headless harness for maths in Qt number fields
(``tumblepipe.util.number_expr_qt``): type ``100/2`` and the field takes 50.

Types real key events into real fields, offscreen. Checks:

  1. a QSpinBox takes ``100/2`` on Enter as 50 (valueChanged ends on the
     result), ``7/2`` as 4 (a half away from zero), ``+=10`` from its value
  2. its limits hold: a sum past the maximum sets the maximum
  3. a sum with no answer (``3/``, ``1/0``) leaves the spin box at its value
  4. focus leaving the spin box works the sum out too
  5. a QDoubleSpinBox takes ``1/4`` as 0.25, and its suffix may stay typed
  6. a QLineEdit with a QIntValidator takes ``12*3`` on Enter (editingFinished
     once, the text ``36``); a result outside the validator's range is not
     accepted; ``+=`` starts from the value it held when entered
  7. a plain number, and letters, are typed and refused exactly as before
  8. the Database Editor's integer and float cells take a sum (their
     delegate asks the validator to fix the text up on Enter)
  9. the Farm Submit dialog's spin fields: a sum pins the field at its
     result, as a typed number does
 10. every number field value_ladder_qt.install is given has maths, a plain
     text field does not, and the ladder still knows a line edit's own
     validator (its decimals and limits) under the one that lets maths in

Run (no Houdini licence; TumbleTrove's python/ on PYTHONPATH for the Farm
Submit dialog's theme):

    QT_QPA_PLATFORM=offscreen python scripts/verify_number_expr.py
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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QDoubleValidator, QIntValidator  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDoubleSpinBox, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from tumblepipe.util import number_expr_qt as neq  # noqa: E402
from tumblepipe.util import value_ladder_qt as vlq  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    _checks.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ''), flush=True)


def type_into(field, text: str, enter: bool = True) -> None:
    """Select everything in ``field`` and type ``text`` over it, key by key."""
    field.setFocus()
    QApplication.processEvents()
    field.selectAll()
    QTest.keyClicks(field, text)
    if enter:
        QTest.keyClick(field, Qt.Key_Return)
    QApplication.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    layout = QVBoxLayout(host)
    other = QLineEdit('elsewhere')
    layout.addWidget(other)

    # 1. a spin box
    spin = QSpinBox()
    spin.setRange(0, 500)
    spin.setValue(20)
    vlq.install(spin)
    layout.addWidget(spin)
    host.show()
    host.activateWindow()
    app.processEvents()
    heard: list[int] = []
    spin.valueChanged.connect(heard.append)
    type_into(spin, '100/2')
    check('1a a spin box takes 100/2 as 50, and says so last', spin.value() == 50 and heard[-1:] == [50],
          f'value {spin.value()} heard {heard} text {spin.text()!r}')
    type_into(spin, '7/2')
    check('1b 7/2 rounds a half away from zero: 4', spin.value() == 4, f'value {spin.value()}')
    type_into(spin, '+=10')
    check('1c += starts from the value', spin.value() == 14, f'value {spin.value()}')
    type_into(spin, '(1+2)*4')
    check('1d brackets first', spin.value() == 12, f'value {spin.value()}')

    # 2. limits
    type_into(spin, '400*2')
    check('2 a sum past the maximum sets the maximum', spin.value() == 500, f'value {spin.value()}')

    # 3. no answer
    spin.setValue(33)
    heard.clear()
    type_into(spin, '3/')
    left = spin.value()
    type_into(spin, '1/0')
    check('3 a sum with no answer leaves the value it had before the typing',
          left == 33 and spin.value() == 33 and heard[-1:] == [33],
          f'after "3/" {left}, after "1/0" {spin.value()}, heard {heard}')

    # 4. focus out
    type_into(spin, '6*7', enter=False)
    other.setFocus()
    app.processEvents()
    check('4 focus leaving the field works the sum out', spin.value() == 42, f'value {spin.value()} text {spin.text()!r}')

    # 5. a double spin box, with a suffix
    double = QDoubleSpinBox()
    double.setRange(0.0, 10.0)
    double.setDecimals(3)
    double.setSuffix(' s')
    double.setValue(1.5)
    vlq.install(double)
    layout.addWidget(double)
    type_into(double, '1/4')
    first = double.value()
    type_into(double, '1/8 s')
    check('5 a decimal spin box takes 1/4 as 0.25, and 1/8 with its suffix typed',
          abs(first - 0.25) < 1e-9 and abs(double.value() - 0.125) < 1e-9, f'{first} then {double.value()}')

    # 6. a validated line edit
    edit = QLineEdit('12')
    edit.setValidator(QIntValidator(0, 99, edit))
    vlq.install(edit)
    layout.addWidget(edit)
    finished: list[str] = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))
    type_into(edit, '12*3')
    check('6a a validated line edit takes 12*3: the text 36, editingFinished once',
          edit.text() == '36' and finished == ['36'], f'text {edit.text()!r} finished {finished}')
    type_into(edit, '7/2')
    check('6a2 a whole-number line edit rounds 7/2 to 4, a half away from zero', edit.text() == '4', f'text {edit.text()!r}')
    type_into(edit, '36')
    finished.clear()
    type_into(edit, '+=4')
    check('6b += starts from the value it held', edit.text() == '40' and finished == ['40'],
          f'text {edit.text()!r} finished {finished}')
    finished.clear()
    type_into(edit, '50*3')
    check('6c a result outside the validator\'s range is not accepted', finished == [] and not edit.hasAcceptableInput(),
          f'text {edit.text()!r} finished {finished}')
    decimal = QLineEdit('0.5')
    decimal.setValidator(QDoubleValidator(0.0, 100.0, 2, decimal))
    vlq.install(decimal)
    layout.addWidget(decimal)
    type_into(decimal, '10/4')
    point = decimal.validator().locale().decimalPoint()
    check('6d a QDoubleValidator field takes 10/4 as 2.5, written as its locale reads it',
          decimal.text() == f'2{point}5' and decimal.hasAcceptableInput(), f'text {decimal.text()!r}')

    # 7. plain numbers and letters, as before
    finished.clear()
    type_into(edit, '45')
    plain_ok = edit.text() == '45' and finished == ['45']
    type_into(edit, '4x5', enter=False)
    check('7 a plain number is entered as typed, and letters never get in',
          plain_ok and edit.text() == '45', f'plain {plain_ok} text {edit.text()!r}')
    type_into(spin, '77')
    check('7b a plain number in a spin box is its own business', spin.value() == 77, f'value {spin.value()}')

    # 8. the Database Editor's integer and float cells
    try:
        from PySide6.QtGui import QStandardItemModel
        from PySide6.QtWidgets import QStyleOptionViewItem
        from tumblepipe.config_editor.json_editor import items as je_items
        from tumblepipe.config_editor.json_editor import view as je_view
    except ImportError as error:
        check('8 the Database Editor imports', False, str(error))
    else:
        model = QStandardItemModel()
        # A value item tells its row's first cell, so each sits in a row of
        # its own, in the second column.
        model.appendRow([je_items.IntegerItem(0), je_items.IntegerItem(5)])
        model.appendRow([je_items.IntegerItem(0), je_items.FloatItem(0.25)])
        delegate = je_view.JsonItemDelegate()
        editors = []
        for row in (0, 1):
            index = model.index(row, 1)
            editor = delegate.createEditor(host, QStyleOptionViewItem(), index)
            delegate.setEditorData(editor, index)
            layout.addWidget(editor)
            editor.show()
            app.processEvents()
            editors.append(editor)
        whole, fraction = editors
        type_into(whole, '5*5')
        type_into(fraction, '3/4')
        check('8a an integer cell\'s editor takes 5*5', whole.text() == '25', f'text {whole.text()!r}')
        check('8b a float cell\'s editor takes 3/4', fraction.text() == '0.75', f'text {fraction.text()!r}')
        for row, editor in ((0, whole), (1, fraction)):
            delegate.setModelData(editor, model, model.index(row, 1))
        stored = [model.itemFromIndex(model.index(row, 1))._value for row in (0, 1)]
        check('8c and the cells hold the results', stored == [25, 0.75], f'{stored}')

    # 9. the Farm Submit dialog's spin fields
    try:
        from tumblepipe.asset_browser import submit_jobs_dialog as sjd
    except ImportError as error:
        check('9 the Farm Submit dialog imports', False, str(error))
    else:
        pane = sjd._ContextPane.__new__(sjd._ContextPane)
        QWidget.__init__(pane)
        pane._fields, pane._seeding, pane._reverts = {}, False, []
        # No grid in this bare pane: showEvent has no row to scroll to.
        pane._scrolled_on_open = True
        pane._renderable_departments = ['animation', 'render']
        pane._refresh_timer = type('T', (), {'start': lambda self: None})()
        pane._changed = lambda: None
        pane._on_cut_changed = lambda: None
        pane._seed_values = lambda: {'pb_samples': 64}
        pane._build_playblast_section()
        layout.addWidget(pane)
        pane.show()
        app.processEvents()
        samples = pane._fields['pb_samples']
        samples.seed(64)
        type_into(samples.widget, '64/2')
        check('9 a Farm Submit number: a sum pins it at its result',
              samples.pinned and samples.value() == 32, f'pinned {samples.pinned} value {samples.value()}')

    # 10. the one install
    plain = QLineEdit('name')
    vlq.install_all(host)
    ladder = vlq.ladder_of(decimal)
    check('10a every number field given to value_ladder_qt.install has maths',
          all(neq.maths_of(w) is not None for w in (spin, double, edit, decimal)))
    check('10b a field with no validator has none', neq.maths_of(neq.install(plain)) is None)
    check('10c the ladder still reads the field\'s own validator under it',
          ladder.decimals() == 2 and ladder.limits() == (0.0, 100.0) and vlq.is_number_field(edit)
          and isinstance(neq.number_validator(edit), QIntValidator),
          f'decimals {ladder.decimals()} limits {ladder.limits()}')
    check('10d installing twice is harmless', neq.install(spin) is spin and neq.maths_of(spin) is neq.maths_of(vlq.install(spin)))

    failed = [name for name, ok, _ in _checks if not ok]
    print(f"--- {len(_checks) - len(failed)}/{len(_checks)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    os._exit(code)
