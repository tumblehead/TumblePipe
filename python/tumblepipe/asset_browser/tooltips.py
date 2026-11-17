"""Tooltips that wrap to a readable width instead of one line across the screen.

Qt draws a plain-text tooltip on one line per line of text, and only wraps
it once that line is wider than the whole screen: Farm Submit's frame
timeline showed a 1600-pixel strip (Remy, 2026-11-17), and most of the
dialog's longer tooltips did the same. A rich-text tooltip wraps instead,
at about 80 characters (``QTipLabel`` sets word wrap on rich text), so
:func:`wrapped` turns long plain text into that.

:func:`install` fixes a whole window at once, without touching each
``setToolTip`` call: an event filter on every widget under it (and every
widget added later) shows a long plain tooltip wrapped. It covers a
widget's own tooltip, a tab bar's tab tooltips and an item view's item
tooltips, which is where Qt keeps them.
"""

from __future__ import annotations

import html
from functools import cache

# Qt is imported where it is used, so the text rule (:func:`wrapped`) loads
# without it: the property tests run with no PySide6.

#: Longest line (in characters) left as plain text: about where the rich
#: text wraps anyway.
WRAP_AT = 80


def is_rich(text: str) -> bool:
    """Whether ``text`` is already markup (Qt's own rough test, made strict:
    a leading tag)."""
    return text.lstrip().startswith('<')


def wrapped(text: str | None) -> str:
    """``text`` as a tooltip that wraps: plain text with a line longer than
    :data:`WRAP_AT` becomes escaped rich text (paragraphs and line breaks
    kept); short plain text and markup come back as they are."""
    if not text or is_rich(text):
        return text or ''
    if all(len(line) <= WRAP_AT for line in text.splitlines()):
        return text
    paragraphs = [
        html.escape(paragraph).replace('\n', '<br>')
        for paragraph in text.split('\n\n')
    ]
    return '<qt>' + ''.join(f'<p>{p}</p>' for p in paragraphs) + '</qt>'


@cache
def _wrapper_class():
    """The event filter's class, made on first use (it needs Qt)."""
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QAbstractItemView, QTabBar, QToolTip, QWidget

    def item_tip(view, pos) -> str:
        index = view.indexAt(pos)
        if not index.isValid():
            return ''
        value = index.data(Qt.ToolTipRole)
        return value if isinstance(value, str) else ''

    def text_at(widget, pos) -> str:
        if isinstance(widget, QTabBar):
            index = widget.tabAt(pos)
            if index >= 0:
                return widget.tabToolTip(index)
        parent = widget.parentWidget()
        if isinstance(parent, QAbstractItemView) and widget is parent.viewport():
            return item_tip(parent, pos)
        return widget.toolTip()

    class Wrapper(QObject):
        """Shows long plain tooltips wrapped, and follows the tree it watches."""

        def watch(self, widget) -> None:
            for each in (widget, *widget.findChildren(QWidget)):
                # Installing twice moves the filter, never doubles it.
                each.installEventFilter(self)

        def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
            kind = event.type()
            if kind == QEvent.ChildAdded:
                child = event.child()
                if isinstance(child, QWidget):
                    self.watch(child)
                return False
            if kind != QEvent.ToolTip or not isinstance(obj, QWidget):
                return False
            text = text_at(obj, event.pos())
            tip = wrapped(text)
            if not text or tip == text:
                return False        # Qt shows it as it would have
            QToolTip.showText(event.globalPos(), tip, obj)
            return True

    return Wrapper


def install(root):
    """Wrap every long plain tooltip under ``root`` (a QWidget), widgets
    added later included. Returns the filter, a QObject parented to
    ``root`` that lives as long as it."""
    wrapper = _wrapper_class()(root)
    wrapper.watch(root)
    return wrapper
