"""Maths typed into a number field: ``100/2`` sets 50, ``(1001+48)*2`` sets
2098, and ``+=10`` adds ten to what the field holds.

One small evaluator, never :func:`eval`. Plain functions, no Qt:
:mod:`tumblepipe.util.number_expr_qt` puts them on Qt fields. Cinedex reads
its number fields by the same rules in Rust (``src/cinedex/pipe/src/
number_expr.rs``); ``tests/test_number_expr_parity.py`` holds the two to
the same answer for the same text, to the bit. Change the rules on both
sides.

The grammar, whole::

    field    := relative? sum
    relative := "+=" | "-=" | "*=" | "/="
    sum      := product (("+" | "-") product)*
    product  := signed (("*" | "/") signed)*
    signed   := ("+" | "-")? primary
    primary  := number | "(" sum ")"
    number   := digit+ ("." digit*)? | "." digit+

- Spaces and tabs may stand between any two tokens, nowhere else.
- Digits are ASCII; there is no exponent, no thousands separator, no name
  (``nan``, ``inf``, ``pi``) and no decimal comma.
- A sign belongs to the number or bracket right after it, once: ``2--3`` is
  5 and ``-(-2)`` is 2, but ``--2`` is refused (a slip of the finger far
  more often than a double negative).
- **``+10`` is ten, and ``-10`` is minus ten.** A leading sign is never
  "relative to what is there": a field that takes negative numbers could
  not tell ``-10`` the value from ``-10`` the step. Relative entry is spelt
  out instead, ``+=10``, ``-=10``, ``*=2``, ``/=2``, which means the field's
  value with that done to it and is refused where there is no value.
- The arithmetic is IEEE double, left to right. Dividing by zero is
  refused, as is any step whose result is not a finite number.
- At most :data:`MAX_LENGTH` characters and :data:`MAX_DEPTH` brackets deep.

A whole-number field takes the result through :func:`whole`: rounded to the
nearest whole number, a half away from zero (``7/2`` is 4, ``-7/2`` is -4).
"""

from __future__ import annotations

import math
from typing import Optional

#: The longest text read, in bytes of UTF-8, after trimming.
MAX_LENGTH = 200
#: How deep brackets may nest.
MAX_DEPTH = 32
#: The largest whole number :func:`whole` gives (and the smallest, negated):
#: every whole number up to it is exact in a double.
MAX_WHOLE = 9_007_199_254_740_992.0

_MESSAGES = {
    'empty': 'nothing typed',
    'long': 'too long to be a number',
    'deep': 'too many brackets inside each other',
    'syntax': 'not a number or a sum of numbers (+ - * / and brackets)',
    'divide': 'it divides by zero',
    'range': 'the result is too large',
    'novalue': 'there is no value here to change',
}

_DIGITS = '0123456789'
_SPACE = ' \t'


class ExprError(ValueError):
    """Why a text is no number. ``code`` is a short name, the same in the
    Rust evaluator (``Problem::code``)."""

    def __init__(self, code: str) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ExprError('range')
    return value


def _apply(op: str, left: float, right: float) -> float:
    if op == '+':
        return _finite(left + right)
    if op == '-':
        return _finite(left - right)
    if op == '*':
        return _finite(left * right)
    if right == 0.0:
        raise ExprError('divide')
    return _finite(left / right)


class _Parser:
    def __init__(self, text: str, at: int) -> None:
        self.text = text
        self.at = at

    def peek(self) -> str:
        """The next token's first character, past any space; '' at the end."""
        text = self.text
        while self.at < len(text) and text[self.at] in _SPACE:
            self.at += 1
        return text[self.at] if self.at < len(text) else ''

    def sum(self, depth: int) -> float:
        value = self.product(depth)
        while self.peek() in ('+', '-') and self.peek():
            op = self.text[self.at]
            self.at += 1
            value = _apply(op, value, self.product(depth))
        return value

    def product(self, depth: int) -> float:
        value = self.signed(depth)
        while self.peek() in ('*', '/') and self.peek():
            op = self.text[self.at]
            self.at += 1
            value = _apply(op, value, self.signed(depth))
        return value

    def signed(self, depth: int) -> float:
        sign = self.peek()
        if sign == '-':
            self.at += 1
            return -self.primary(depth)
        if sign == '+':
            self.at += 1
        return self.primary(depth)

    def primary(self, depth: int) -> float:
        first = self.peek()
        if first == '(':
            if depth >= MAX_DEPTH:
                raise ExprError('deep')
            self.at += 1
            value = self.sum(depth + 1)
            if self.peek() != ')':
                raise ExprError('syntax')
            self.at += 1
            return value
        if first and (first in _DIGITS or first == '.'):
            return self.number()
        raise ExprError('syntax')

    def _digits(self) -> int:
        start = self.at
        text = self.text
        while self.at < len(text) and text[self.at] in _DIGITS:
            self.at += 1
        return self.at - start

    def number(self) -> float:
        start = self.at
        whole = self._digits()
        fraction = 0
        if self.at < len(self.text) and self.text[self.at] == '.':
            self.at += 1
            fraction = self._digits()
        if whole + fraction == 0:
            raise ExprError('syntax')
        # ASCII digits and at most one point: always a float literal.
        return _finite(float(self.text[start:self.at]))


def evaluate(text: str, current: Optional[float] = None) -> float:
    """The number ``text`` stands for. ``current`` is what the field holds
    now, which ``+=``, ``-=``, ``*=`` and ``/=`` start from. Raises
    :class:`ExprError`."""
    text = text.strip(_SPACE)
    if not text:
        raise ExprError('empty')
    if len(text.encode('utf-8', 'surrogatepass')) > MAX_LENGTH:
        raise ExprError('long')
    relative = text[0] if len(text) >= 2 and text[0] in '+-*/' and text[1] == '=' else ''
    parser = _Parser(text, 2 if relative else 0)
    value = parser.sum(0)
    if parser.peek():
        raise ExprError('syntax')
    if relative:
        if current is None or not math.isfinite(current):
            raise ExprError('novalue')
        value = _apply(relative, float(current), value)
    # No minus zero: a field never shows "-0".
    return 0.0 if value == 0.0 else value


def whole(value: float) -> Optional[int]:
    """``value`` as a whole-number field takes it: the nearest whole number,
    a half away from zero. None past :data:`MAX_WHOLE` either way, or for
    no number."""
    if not math.isfinite(value):
        return None
    size = abs(value)
    below = math.floor(size)
    # Exact: below is within one of size, so the difference has no error
    # (floor(size + 0.5) would round 0.49999999999999994 up).
    rounded = below + 1 if size - below >= 0.5 else below
    if rounded > MAX_WHOLE:
        return None
    return -rounded if value < 0 else rounded


def is_plain(text: str) -> bool:
    """Whether ``text`` is one plain number, as a field shows a value:
    digits with at most one point and at most one leading minus, no maths."""
    text = text.strip(_SPACE)
    digits = text[1:] if text.startswith('-') else text
    head, _, tail = digits.partition('.')
    if not head and not tail:
        return False
    return all(c in _DIGITS for c in head + tail)


def try_evaluate(text: str, current: Optional[float] = None) -> Optional[float]:
    """:func:`evaluate`, None where it would raise."""
    try:
        return evaluate(text, current)
    except ExprError:
        return None
