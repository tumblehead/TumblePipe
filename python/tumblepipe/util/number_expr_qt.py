"""Maths in Qt number fields: type ``100/2`` and the field takes 50.

:func:`install` gives a ``QSpinBox``, a ``QDoubleSpinBox`` or a ``QLineEdit``
with a number validator the evaluator of :mod:`tumblepipe.util.number_expr`
(``+ - * /``, brackets, ``+=10`` and its like for "from what is there").
:func:`tumblepipe.util.value_ladder_qt.install` calls it, so every number
field TumblePipe builds has it: a field gets the value ladder and maths
together, from that one call.

How a field takes it:

- while typing, text made only of the evaluator's characters is let in (Qt's
  own number validators would refuse the ``/``);
- on Enter, Tab or a click away, the text is worked out and the field shows
  and holds the result, as if that number had been typed: a whole-number
  field rounds it (a half away from zero), a spin box keeps it inside its
  limits, and a line edit's own validator still has the last word (a result
  outside its range is not accepted, as a typed number outside it is not);
- text that is no expression (``3/``, ``1/0``) is left to the field, which
  does what it always did with text it cannot read: a spin box goes back to
  its value, a line edit does not accept it.

A plain number never goes through here, so nothing changes for one.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator, QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget

from tumblepipe.util import number_expr

_ATTR = '_th_number_expr'
#: Every character the evaluator's grammar has.
_CHARACTERS = frozenset('0123456789.+-*/()= \t')


def looks_like_maths(text: str) -> bool:
    """Whether ``text`` could become an expression: only the grammar's
    characters, and not too many."""
    return 0 < len(text) <= number_expr.MAX_LENGTH and all(c in _CHARACTERS for c in text)


def number_text(value: float, validator: Optional[QValidator]) -> Optional[str]:
    """``value`` as a line edit with ``validator`` shows it: a whole number
    for a ``QIntValidator``, at most its decimals for a ``QDoubleValidator``,
    else Python's own shortest text. None for a value no field shows."""
    if isinstance(validator, QIntValidator):
        whole = number_expr.whole(value)
        return None if whole is None else str(whole)
    if isinstance(validator, QDoubleValidator):
        text = f'{value:.{max(min(validator.decimals(), 15), 0)}f}'
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        text = '0' if text in ('', '-0') else text
        # The validator reads numbers in its locale: a decimal comma where
        # that is the custom (the sum itself is typed with a point).
        return text.replace('.', validator.locale().decimalPoint())
    return str(float(value))


def _state(result) -> QValidator.State:
    """A validator's verdict, whether it answered a state or Qt's
    ``(state, text, position)``."""
    return result[0] if isinstance(result, tuple) else result


class _Validator(QValidator):
    """A field's own validator, letting expressions in while they are typed
    and working them out when Qt asks it to fix the text up."""

    def __init__(self, inner: Optional[QValidator], maths: '_Maths') -> None:
        super().__init__(maths)
        # Held here too: Qt lets go of a validator it no longer has set.
        self.inner = inner
        self._maths = maths

    def validate(self, text: str, pos: int):  # noqa: N802 (Qt)
        try:
            result = self.inner.validate(text, pos) if self.inner is not None else QValidator.Acceptable
        except RuntimeError:
            # The field is being torn down and its own validator went first.
            return QValidator.Intermediate
        if _state(result) == QValidator.Acceptable:
            return result
        if looks_like_maths(self._maths.bare(text)):
            return QValidator.Intermediate
        return result

    def fixup(self, text: str) -> str:  # noqa: N802 (Qt)
        worked = self._maths.worked_out(text)
        if worked is not None:
            return worked
        if self.inner is not None:
            try:
                fixed = self.inner.fixup(text)
            except RuntimeError:
                return text
            return fixed if isinstance(fixed, str) else text
        return text


class _Maths(QObject):
    """The evaluator on one field."""

    def __init__(self, field: QWidget) -> None:
        super().__init__(field)
        self.field = field
        self._spin = field if isinstance(field, QAbstractSpinBox) else None
        self._edit: QLineEdit = field.lineEdit() if self._spin is not None else field
        # What the field held before the typing now under way: where ``+=``
        # starts from, and what a spin box goes back to when a sum has no
        # answer (Qt sets a spin box's value key by key, so by the time
        # ``3/`` is entered over 33 the box holds 3).
        self._held: Optional[float] = self._plain_value(self._edit.text())
        self._typing = False
        self.validator = _Validator(self._edit.validator(), self)
        self._edit.setValidator(self.validator)
        field.installEventFilter(self)
        if self._spin is None:
            self._edit.editingFinished.connect(self._remember)

    # -- the text ----------------------------------------------------------

    def bare(self, text: str) -> str:
        """``text`` without a spin box's prefix and suffix."""
        spin = self._spin
        if isinstance(spin, (QSpinBox, QDoubleSpinBox)):
            prefix, suffix = spin.prefix(), spin.suffix()
            if prefix and text.startswith(prefix):
                text = text[len(prefix):]
            if suffix and text.endswith(suffix):
                text = text[:len(text) - len(suffix)]
        return text

    def _plain_value(self, text: str) -> Optional[float]:
        text = self.bare(text)
        return number_expr.try_evaluate(text) if number_expr.is_plain(text) else None

    def _remember(self) -> None:
        held = self._plain_value(self._edit.text())
        if held is not None:
            self._held = held

    def current(self) -> Optional[float]:
        """The value ``+=`` starts from."""
        spin = self._spin
        if isinstance(spin, (QSpinBox, QDoubleSpinBox)):
            return float(self._held if self._typing and self._held is not None else spin.value())
        return self._held

    def pending(self, text: str) -> Optional[float]:
        """The number an expression in ``text`` works out to; None for a
        plain number (the field's own business) and for no expression."""
        text = self.bare(text)
        if number_expr.is_plain(text) or not looks_like_maths(text):
            return None
        return number_expr.try_evaluate(text, self.current())

    def worked_out(self, text: str) -> Optional[str]:
        """A line edit's ``text`` with its expression worked out, as the
        field shows a number; None when there is none to work out."""
        value = self.pending(text)
        return None if value is None else number_text(value, self.validator.inner)

    def settle(self) -> bool:
        """Work out a spin box's expression and set the value, before Qt
        reads the text itself. True when it did."""
        spin = self._spin
        if not isinstance(spin, (QSpinBox, QDoubleSpinBox)) or spin.isReadOnly():
            return False
        text = self.bare(self._edit.text())
        value = self.pending(text)
        typing, self._typing = self._typing, False
        if value is None:
            if typing and self._held is not None and not number_expr.is_plain(text) and looks_like_maths(text):
                # A sum with no answer: back to what was there before it.
                spin.setValue(int(self._held) if isinstance(spin, QSpinBox) else self._held)
            return False
        if isinstance(spin, QSpinBox):
            whole = number_expr.whole(value)
            if whole is None:
                return False
            spin.setValue(int(min(max(whole, spin.minimum()), spin.maximum())))
        else:
            spin.setValue(min(max(value, spin.minimum()), spin.maximum()))
        return True

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt)
        kind = event.type()
        try:
            if kind == QEvent.FocusIn and self._spin is None:
                self._remember()
            elif kind == QEvent.FocusOut:
                self.settle()
            elif kind == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Backtab):
                    self.settle()
                elif not self._typing and isinstance(self._spin, (QSpinBox, QDoubleSpinBox)):
                    self._held, self._typing = float(self._spin.value()), True
        except RuntimeError:
            # The field's Qt side is gone (a window closing): nothing to do.
            pass
        return False


def install(field: QWidget) -> QWidget:
    """Give ``field`` (a spin box, or a line edit with its number validator
    already set) maths; returns ``field``. Installing twice is harmless."""
    if getattr(field, _ATTR, None) is not None:
        return field
    if not isinstance(field, (QAbstractSpinBox, QLineEdit)):
        return field
    if isinstance(field, QLineEdit) and field.validator() is None:
        # No validator: any text is the field's, and Qt never asks for a
        # fix-up, so there is nothing to work out.
        return field
    setattr(field, _ATTR, _Maths(field))
    return field


def maths_of(field: QWidget) -> Optional[_Maths]:
    """The evaluator on ``field``, if it has one (for tests)."""
    return getattr(field, _ATTR, None)


def number_validator(field: QWidget) -> Optional[QValidator]:
    """``field``'s own validator: the one it was given, not the one that
    lets maths in around it."""
    validator = field.validator() if isinstance(field, QLineEdit) else None
    return validator.inner if isinstance(validator, _Validator) else validator
