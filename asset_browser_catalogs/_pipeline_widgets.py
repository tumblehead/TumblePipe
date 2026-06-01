"""Reusable Qt widgets for the Pipeline detail panel.

:class:`DeptNameLabel` and :class:`DeptMetaLabel` used to be defined
inline inside ``_build_departments_section``, closing over loop-local
font, scaling, and icon-HTML variables. Hoisting them to module level
makes the section builder readable and lets the widgets be reused or
tested in isolation. Environment (font metrics, pre-rendered icons,
scaling pixel budgets) is now injected through each class's
constructor instead of captured by closure.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel


class DeptNameLabel(QLabel):
    """Three-stage shrinking name label:

    1. The full name when the column has room.
    2. The optional ``short`` (abbreviated) label when full doesn't fit.
    3. ``…``-elided short (or full, if no short) when even the short
       doesn't fit.

    :meth:`sizeHint` is based on the full name so the layout grants
    room when available; :meth:`minimumSizeHint` matches the short
    label width (or :attr:`floor_width` when no short is set) so the
    column yields cleanly on narrow detail panels.
    """

    def __init__(
        self,
        floor_width: int,
        short_padding: int,
        sizehint_padding: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._full = ""
        self._short = ""
        self._floor_width = floor_width
        self._short_padding = short_padding
        self._sizehint_padding = sizehint_padding

    def setFullText(self, text: str, short: str = "") -> None:
        self._full = text or ""
        self._short = short or ""
        self.setToolTip(self._full)
        self.updateGeometry()
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(
            fm.horizontalAdvance(self._full) + self._sizehint_padding,
            fm.height(),
        )

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        base = (
            fm.horizontalAdvance(self._short)
            if self._short else self._floor_width
        )
        return QSize(base + self._short_padding, fm.height())

    def _refresh(self) -> None:
        fm = QFontMetrics(self.font())
        avail = max(0, self.width())
        if fm.horizontalAdvance(self._full) <= avail:
            target = self._full
        elif self._short and fm.horizontalAdvance(self._short) <= avail:
            target = self._short
        else:
            candidate = self._short or self._full
            target = fm.elidedText(candidate, Qt.ElideRight, avail)
        if target != QLabel.text(self):
            QLabel.setText(self, target)


class DeptMetaLabel(QLabel):
    """Compact rich-text label with inline icons:

        user · 🕐 Nw ago · 📦 Md ago

    Drops pieces in priority order as the column shrinks:

    1. user + edited + exported (full)
    2. edited + exported (user dropped)
    3. edited (exported dropped)
    4. ``""`` (nothing fits)

    Candidates are stored with ``{U}`` / ``{E}`` / ``{X}`` placeholder
    tokens; measurement substitutes them with an approximate icon-
    pixel cost (:attr:`icon_width`), while rendering swaps them for
    the precomputed icon HTML.
    """

    def __init__(
        self,
        user_html: str,
        edited_html: str,
        exported_html: str,
        icon_width: int,
        sizehint_padding: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._user = ""
        self._when = ""
        self._exported = ""
        self._user_html = user_html
        self._edited_html = edited_html
        self._exported_html = exported_html
        self._icon_width = icon_width
        self._sizehint_padding = sizehint_padding
        self.setTextFormat(Qt.RichText)

    def set_parts(
        self, user: str, when: str, exported: str = "",
    ) -> None:
        self._user = user or ""
        self._when = when or ""
        self._exported = exported or ""
        self.updateGeometry()
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _candidates(self) -> list[str]:
        user = f"{{U}} {self._user}" if self._user else ""
        edited = f"{{E}} {self._when}" if self._when else ""
        exported = f"{{X}} {self._exported}" if self._exported else ""
        items = [s for s in (edited, exported) if s]
        full = " · ".join(items)
        out: list[str] = []
        if user and full:
            out.append(f"{user} · {full}")
        if full:
            out.append(full)
        if edited:
            out.append(edited)
        elif exported:
            out.append(exported)
        return out

    def _measure(self, tokenized: str) -> int:
        fm = QFontMetrics(self.font())
        n = (
            tokenized.count("{U}")
            + tokenized.count("{E}")
            + tokenized.count("{X}")
        )
        plain = (
            tokenized
            .replace("{U}", "")
            .replace("{E}", "")
            .replace("{X}", "")
        )
        return fm.horizontalAdvance(plain) + n * self._icon_width

    def _to_html(self, tokenized: str) -> str:
        return (
            tokenized
            .replace("{U}", self._user_html)
            .replace("{E}", self._edited_html)
            .replace("{X}", self._exported_html)
        )

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        cands = self._candidates()
        widest = cands[0] if cands else ""
        return QSize(
            self._measure(widest) + self._sizehint_padding,
            fm.height(),
        )

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(0, fm.height())

    def _refresh(self) -> None:
        avail = max(0, self.width())
        target = ""
        for cand in self._candidates():
            if self._measure(cand) <= avail:
                target = self._to_html(cand)
                break
        if target != QLabel.text(self):
            QLabel.setText(self, target)
