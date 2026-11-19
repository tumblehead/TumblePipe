"""The value ladder on Qt number fields: Houdini's middle-mouse drag.

:func:`install` gives a ``QSpinBox``, ``QDoubleSpinBox`` or a ``QLineEdit``
with a number validator Houdini's gestures:

- middle-press opens the ladder (100, 10, 1, 0.1, 0.01; a whole-number
  field has no step below 1) at the pointer, on the 1 row; up or down picks
  a step, left or right changes the value one step every few pixels;
  releasing sets it, as one change (the field's signals fire once, on
  release, not on every step);
- Ctrl + middle-click puts the field back to its default: the "not set"
  value of a field that has one (its special value text), else the value
  it opened with, unless :func:`install` is given one (or
  an ``on_reset`` that does the resetting itself, as the Farm Submit
  dialog's ↺ does).

The field's limits still hold. Left-mouse editing is unchanged, and typing
gains maths: :func:`install` also gives the field
:mod:`tumblepipe.util.number_expr_qt` (``100/2`` sets 50), so every number
field has both from the one call.
The maths is :mod:`tumblepipe.util.value_ladder`, shared with the tests.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator, QPainter, QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget

from tumblepipe.util import number_expr_qt
from tumblepipe.util import value_ladder as maths

Default = Optional[Union[float, Callable[[], float]]]

_ATTR = '_th_value_ladder'
_WIDTH = 76


def _fmt(value: float, decimals: int) -> str:
    if decimals <= 0:
        return str(int(round(value)))
    text = f'{value:.{min(decimals, 6)}f}'
    return text.rstrip('0').rstrip('.') or '0'


class _Rungs(QWidget):
    """The ladder column painted beside the pointer while a drag lasts."""

    def __init__(self, field: QWidget) -> None:
        # A window of its own, owned by the field (gone with it).
        super().__init__(field, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setPalette(field.palette())
        self.setFont(field.font())
        self.labels: list[str] = []
        self.row = 0

    def show_at(self, press: QPoint, labels: list[str], start: int) -> None:
        self.labels = labels
        row = int(maths.ROW)
        self.resize(_WIDTH, row * len(labels))
        self.move(press.x() - _WIDTH // 2, press.y() - start * row - row // 2)
        self.show()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        palette = self.palette()
        row = int(maths.ROW)
        for i, label in enumerate(self.labels):
            rect = QRect(0, i * row, _WIDTH - 1, row - 1)
            picked = i == self.row
            painter.fillRect(rect, palette.color(QPalette.Highlight if picked else QPalette.Base))
            painter.setPen(palette.color(QPalette.Mid))
            painter.drawRect(rect)
            painter.setPen(palette.color(
                QPalette.HighlightedText if picked else QPalette.Text))
            painter.drawText(rect, Qt.AlignCenter, label)
        painter.end()


class _Ladder(QObject):
    """The event filter on one field (and a spin box's own line edit)."""

    def __init__(self, field: QWidget, default: Default, decimals: Optional[int],
                 on_reset: Optional[Callable[[], None]] = None) -> None:
        super().__init__(field)
        self.field = field
        self._on_reset = on_reset
        self._default = default
        self._decimals = decimals
        # The value it opened with: now, or (a delegate's editor, empty until
        # the view fills it) when it first shows.
        self._opened_with: Optional[float] = None
        self._grip: Optional[maths.Grip] = None
        self._number: Optional[maths.Number] = None
        self._start_text = ''
        self._start_value: Optional[float] = None
        self._grabber: Optional[QWidget] = None
        self._rungs: Optional[_Rungs] = None

    # -- the field as a number ------------------------------------------

    def decimals(self) -> int:
        field = self.field
        if self._decimals is not None:
            return self._decimals
        if isinstance(field, QSpinBox):
            return 0
        if isinstance(field, QDoubleSpinBox):
            return field.decimals()
        validator = number_expr_qt.number_validator(field)
        if isinstance(validator, QIntValidator):
            return 0
        if isinstance(validator, QDoubleValidator):
            return min(validator.decimals(), 6)
        return 3

    def limits(self) -> tuple[float, float]:
        field = self.field
        if isinstance(field, (QSpinBox, QDoubleSpinBox)):
            return float(field.minimum()), float(field.maximum())
        validator = number_expr_qt.number_validator(field)
        if isinstance(validator, (QIntValidator, QDoubleValidator)):
            return float(validator.bottom()), float(validator.top())
        return -1e12, 1e12

    def value(self) -> Optional[float]:
        field = self.field
        if isinstance(field, (QSpinBox, QDoubleSpinBox)):
            return float(field.value())
        try:
            return float(field.text().strip())
        except ValueError:
            return None

    def default(self) -> float:
        if callable(self._default):
            return float(self._default())
        if self._default is not None:
            return float(self._default)
        field = self.field
        if isinstance(field, QAbstractSpinBox) and field.specialValueText():
            return float(field.minimum())
        if self._opened_with is not None:
            return self._opened_with
        value = self.value()
        return 0.0 if value is None else value

    def number(self) -> maths.Number:
        low, high = self.limits()
        value = self.value()
        return maths.Number(
            value=low if value is None else value, low=low, high=high,
            decimals=self.decimals(), default=self.default(),
        )

    def _write(self, value: float, quiet: bool) -> None:
        field = self.field
        was = field.blockSignals(quiet)
        try:
            if isinstance(field, QSpinBox):
                field.setValue(int(round(value)))
            elif isinstance(field, QDoubleSpinBox):
                field.setValue(value)
            else:
                field.setText(_fmt(value, self.decimals()))
        finally:
            field.blockSignals(was)

    # -- the gesture ------------------------------------------------------

    def _usable(self) -> bool:
        field = self.field
        if not field.isEnabled():
            return False
        if isinstance(field, (QAbstractSpinBox, QLineEdit)) and field.isReadOnly():
            return False
        return True

    def press(self, receiver: QWidget, x: float, y: float, ctrl: bool) -> None:
        """A middle press at global ``(x, y)`` on ``receiver``."""
        if ctrl:
            self.reset()
            return
        self._number = self.number()
        self._grip = maths.Grip(self._number, x, y)
        self._remember_start()
        self._grabber = receiver
        receiver.grabMouse()
        if self._rungs is None:
            self._rungs = _Rungs(self.field)
        self._rungs.row = self._grip.row
        self._rungs.show_at(QPoint(int(x), int(y)), self._labels(), self._grip.row)

    def move(self, x: float, y: float) -> None:
        if self._grip is None:
            return
        value = self._grip.moved(self._number, x, y)
        self._write(value, quiet=True)
        if self._rungs is not None:
            self._rungs.row = self._grip.row
            self._rungs.labels = self._labels()
            self._rungs.update()

    def release(self) -> None:
        """The middle button up: set the value, as one change."""
        if self._grip is None:
            return
        value = self._grip.value
        self._grip = None
        if self._grabber is not None:
            self._grabber.releaseMouse()
            self._grabber = None
        if self._rungs is not None:
            self._rungs.hide()
        self._commit(value)

    def reset(self) -> None:
        """Ctrl + middle-click: back to the default."""
        if self._on_reset is not None:
            self._on_reset()
            return
        number = self.number()
        self._remember_start()
        self._commit(number.reset())

    def _commit(self, value: float) -> None:
        # Back to where it started without a sound, then to the value out
        # loud: the field's own signals fire once, with the final value.
        field = self.field
        self._restore_quietly()
        self._write(value, quiet=False)
        if self._text() != self._start_text:
            field.editingFinished.emit()

    def _text(self) -> str:
        field = self.field
        return field.text() if isinstance(field, (QLineEdit, QAbstractSpinBox)) else ''

    def _remember_start(self) -> None:
        self._start_text = self._text()
        self._start_value = self.value()

    def _restore_quietly(self) -> None:
        field = self.field
        was = field.blockSignals(True)
        try:
            if isinstance(field, QSpinBox) and self._start_value is not None:
                field.setValue(int(self._start_value))
            elif isinstance(field, QDoubleSpinBox) and self._start_value is not None:
                field.setValue(self._start_value)
            elif isinstance(field, QLineEdit):
                field.setText(self._start_text)
        finally:
            field.blockSignals(was)

    def _labels(self) -> list[str]:
        decimals = self._number.decimals
        labels = [_fmt(s, maths.FINEST) for s in maths.steps(decimals)]
        if self._grip is not None:
            labels[self._grip.row] = _fmt(self._grip.value, decimals)
        return labels

    @property
    def dragging(self) -> bool:
        return self._grip is not None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt)
        kind = event.type()
        if kind == QEvent.Show and self._opened_with is None and obj is self.field:
            self._opened_with = self.value()
            return False
        if kind == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
            if not self._usable():
                return False
            point = event.globalPosition()
            ctrl = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
            self.press(obj, point.x(), point.y(), ctrl)
            return True
        if kind == QEvent.MouseMove and self._grip is not None:
            point = event.globalPosition()
            self.move(point.x(), point.y())
            return True
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
            self.release()
            return True
        if kind == QEvent.MouseButtonDblClick and event.button() == Qt.MiddleButton:
            return True
        return False


def install(field: QWidget, default: Default = None, decimals: Optional[int] = None,
            on_reset: Optional[Callable[[], None]] = None) -> QWidget:
    """Give ``field`` the value ladder; returns ``field``. ``default`` is
    what Ctrl + middle-click puts back (a number, or a function giving
    one); ``on_reset``, when given, is called on Ctrl + middle-click
    instead; ``decimals`` overrides how many the field shows. Installing
    twice is harmless."""
    if getattr(field, _ATTR, None) is not None:
        return field
    ladder = _Ladder(field, default, decimals, on_reset)
    ladder._opened_with = ladder.value()
    setattr(field, _ATTR, ladder)
    field.installEventFilter(ladder)
    if isinstance(field, QAbstractSpinBox):
        field.lineEdit().installEventFilter(ladder)
    # Maths typed into it (100/2): every number field, from this one call.
    number_expr_qt.install(field)
    return field


def is_number_field(widget: QWidget) -> bool:
    """A spin box, or a line edit taking only numbers."""
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return True
    return isinstance(widget, QLineEdit) and isinstance(
        number_expr_qt.number_validator(widget), (QIntValidator, QDoubleValidator))


def install_all(root: QWidget) -> int:
    """Give every number field under ``root`` the ladder; how many it found."""
    found = [w for w in root.findChildren(QWidget)
             if is_number_field(w) and not isinstance(w.parentWidget(), QAbstractSpinBox)]
    for widget in found:
        install(widget)
    return len(found)


def ladder_of(field: QWidget) -> Optional[_Ladder]:
    """The ladder on ``field``, if it has one (for tests)."""
    return getattr(field, _ATTR, None)
