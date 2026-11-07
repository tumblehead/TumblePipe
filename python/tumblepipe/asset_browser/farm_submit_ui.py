"""The Farm Submit window's building blocks: tabs, sections, its overlay, review and progress.

``submit_jobs_dialog`` is the grid and its settings form; this module holds
the pieces around them that artists asked to read better (2026-11):

* :class:`FolderTabBar` — Assets / Shots / Edits drawn as folder tabs whose
  outline runs on as a baseline across the window, so the tab that owns the
  content below is plain.
* :class:`Section` — a settings section: a headline that collapses it and
  sums it up, on a lighter frame than the window, with its step kind's
  colour down its left edge.
* :class:`Overlay` — a card over the whole window, behind a scrim: the
  review before submitting and the submission's progress live here rather
  than in windows of their own.
* :class:`ReviewSheet` — what Submit is about to send, one row per entity
  with its steps and what to check first.
* :class:`SubmissionView` — a submission running in its own process
  (``farm.submit.plan``), followed through the progress file it appends to.

Each step kind has a colour (:data:`KIND_COLOURS`) shared by its grid
columns, its ticked cells, its settings section and its chips here, so a
section can be traced to its columns at a glance. The kind colours stay
clear of the cell states' green (current), amber (stale) and red (error).
"""

from __future__ import annotations

import logging
import subprocess

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QPushButton, QSizePolicy, QStyledItemDelegate, QTabBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BG_MID, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.farm.submit import grid
from tumblepipe.farm.submit import plan as submit_plan
from tumblepipe.farm.submit import progress as submit_progress
from tumblepipe.farm.submit.review import ReviewRow, can_submit, review_lines, review_order

log = logging.getLogger(__name__)

# ── Colours ───────────────────────────────────────────────

# One colour per step kind: the strong one draws lines, chips' text and
# section edges; the fill is a ticked cell's background, dark enough for a
# white glyph on it.
KIND_COLOURS = {
    grid.PUBLISH: "#6ea2ef",
    grid.PLAYBLAST: "#ad92ee",
    grid.RENDER: "#ec88bd",
}
KIND_FILLS = {
    grid.PUBLISH: "#2f5b99",
    grid.PLAYBLAST: "#56449a",
    grid.RENDER: "#8f3f6c",
}
# The Edits tab's one step renders a movie: it wears Render's colour.
EDIT_KIND = grid.RENDER

STALE_COLOUR = "#f0a030"
CURRENT_COLOUR = "#2bae86"
ERROR_COLOUR = "#e05a4f"
RUNNING_COLOUR = "#7fb2f0"
CANCELLED_COLOUR = "#8a8a8a"

# The settings sections' frame: a step lighter than the window, so they
# stand off it; their fields sit a step darker again, inset.
SECTION_BG = BG_MID
SECTION_BORDER = "#363636"
CARD_BORDER = "#3c3c3c"
SCRIM = QColor(0, 0, 0, 150)


# ── Tabs ──────────────────────────────────────────────────

class FolderTabBar(QTabBar):
    """Tabs as folder tabs: the current one outlined, its outline running on
    as a baseline across the whole width, open under the tab itself.

    Plain QSS tabs read as buttons (artists did not see which one owned the
    grid); a baseline that the current tab breaks through is the shape every
    tabbed window has taught. The bar spans its parent's width so the
    baseline does too.
    """

    TAB_HEIGHT = 34
    LINE = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = -1
        font = QFont(FONT_FAMILY)
        font.setPixelSize(FONT_BODY)
        self.setFont(font)

    def tabSizeHint(self, index: int) -> QSize:
        metrics = QFontMetrics(self._tab_font(True))
        return QSize(metrics.horizontalAdvance(self.tabText(index)) + 40, self.TAB_HEIGHT)

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def sizeHint(self) -> QSize:
        width = sum(self.tabSizeHint(i).width() for i in range(self.count()))
        return QSize(width, self.TAB_HEIGHT)

    def _tab_font(self, current: bool) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.DemiBold if current else QFont.Normal)
        return font

    def mouseMoveEvent(self, event) -> None:
        index = self.tabAt(event.position().toPoint())
        if index != self._hover:
            self._hover = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        current = self.currentIndex()
        base = self.height() - self.LINE / 2
        pen = QPen(QColor(ACCENT), self.LINE)
        pen.setCapStyle(Qt.FlatCap)
        for index in range(self.count()):
            rect = QRectF(self.tabRect(index)).adjusted(1, 2, -1, 0)
            if index == current:
                continue
            if index == self._hover:
                path = _tab_path(rect, base)
                painter.fillPath(path, QColor(BG_MID))
            painter.setFont(self._tab_font(False))
            painter.setPen(QColor(TEXT_PRIMARY if index == self._hover else TEXT_SECONDARY))
            painter.drawText(rect.adjusted(0, 0, 0, -self.LINE), Qt.AlignCenter, self.tabText(index))
        if current >= 0:
            rect = QRectF(self.tabRect(current)).adjusted(1, 2, -1, 0)
            path = _tab_path(rect, base)
            painter.fillPath(path, QColor(BG_DARKEST))
            painter.setPen(pen)
            painter.drawPath(path)
            painter.setFont(self._tab_font(True))
            painter.setPen(QColor(TEXT_PRIMARY))
            painter.drawText(rect.adjusted(0, 0, 0, -self.LINE), Qt.AlignCenter, self.tabText(current))
            # The baseline, broken where the current tab opens onto the page.
            painter.setPen(pen)
            painter.drawLine(QPointF(0, base), QPointF(rect.left(), base))
            painter.drawLine(QPointF(rect.right(), base), QPointF(self.width(), base))
        else:
            painter.setPen(pen)
            painter.drawLine(QPointF(0, base), QPointF(self.width(), base))
        painter.end()


def _tab_path(rect: QRectF, base: float, radius: float = 6.0) -> QPainterPath:
    """A tab's outline: up from the baseline, round over the top, back down."""
    path = QPainterPath()
    path.moveTo(rect.left(), base)
    path.lineTo(rect.left(), rect.top() + radius)
    path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
    path.lineTo(rect.right() - radius, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
    path.lineTo(rect.right(), base)
    return path


# ── Sections ──────────────────────────────────────────────

class _SectionHeader(QWidget):
    """A section's headline: chevron, title, and a one-line summary on the right."""

    clicked = Signal()

    HEIGHT = 36

    def __init__(self, title: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._colour = colour
        self._summary = ""
        self._expanded = True
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip(f"Click to {'collapse' if self._expanded else 'expand'} the {title} settings")

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.setToolTip(
            f"Click to {'collapse' if expanded else 'expand'} the {self._title} settings"
        )
        self.update()

    def set_summary(self, text: str) -> None:
        if text != self._summary:
            self._summary = text
            self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        # Chevron: a small triangle, pointing down when open.
        cx, cy = 16, rect.center().y() + 0.5
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TEXT_PRIMARY if self._hover else TEXT_SECONDARY))
        path = QPainterPath()
        if self._expanded:
            path.moveTo(cx - 4.5, cy - 2.5)
            path.lineTo(cx + 4.5, cy - 2.5)
            path.lineTo(cx, cy + 3)
        else:
            path.moveTo(cx - 2.5, cy - 4.5)
            path.lineTo(cx + 3, cy)
            path.lineTo(cx - 2.5, cy + 4.5)
        path.closeSubpath()
        painter.drawPath(path)

        title_font = QFont(FONT_FAMILY)
        title_font.setPixelSize(FONT_BODY + 1)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT_PRIMARY))
        title_width = QFontMetrics(title_font).horizontalAdvance(self._title)
        painter.drawText(QRect(30, 0, title_width + 2, rect.height()), Qt.AlignVCenter, self._title)

        if self._summary:
            summary_font = QFont(FONT_FAMILY)
            summary_font.setPixelSize(FONT_BODY - 1)
            painter.setFont(summary_font)
            painter.setPen(QColor(self._colour))
            left = 30 + title_width + 14
            room = rect.width() - left - 12
            text = QFontMetrics(summary_font).elidedText(self._summary, Qt.ElideRight, max(0, room))
            painter.drawText(QRect(left, 0, room, rect.height()), Qt.AlignVCenter | Qt.AlignRight, text)
        painter.end()


class Section(QFrame):
    """A collapsible settings section on a lighter frame, edged in its kind's colour.

    ``form()`` is the body's QFormLayout. ``toggled(expanded)`` fires on a
    click of the headline, after the body is shown or hidden.
    """

    toggled = Signal(bool)

    def __init__(self, title: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("farmSection")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame#farmSection {{ background-color: {SECTION_BG};"
            f" border: 1px solid {SECTION_BORDER}; border-left: 3px solid {colour};"
            f" border-radius: 6px; }}"
        )
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._header = _SectionHeader(title, colour)
        self._header.clicked.connect(lambda: self.set_expanded(not self.is_expanded(), emit=True))
        layout.addWidget(self._header)
        self._body = QWidget()
        self._body.setObjectName("farmSectionBody")
        self._body.setStyleSheet("QWidget#farmSectionBody { background: transparent; }")
        self._form = _form_layout(self._body)
        layout.addWidget(self._body)

    def form(self):
        return self._form

    def body(self) -> QWidget:
        return self._body

    def is_expanded(self) -> bool:
        return not self._body.isHidden()

    def set_expanded(self, expanded: bool, *, emit: bool = False) -> None:
        self._body.setVisible(expanded)
        self._header.set_expanded(expanded)
        if emit:
            self.toggled.emit(expanded)

    def set_summary(self, text: str) -> None:
        self._header.set_summary(text)


def _form_layout(parent: QWidget):
    from PySide6.QtWidgets import QFormLayout
    form = QFormLayout(parent)
    form.setContentsMargins(14, 2, 12, 12)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(7)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return form


# ── Overlay ───────────────────────────────────────────────

class Overlay(QWidget):
    """One card over the whole window behind a dim scrim: the window's own modal.

    The review and the submission's progress used to be separate windows
    that artists lost behind Houdini's; here they sit on the window that
    made them. The overlay covers its parent and follows its size; the
    card is centred and capped in width. Escape asks the card to go back
    (``escape`` signal); clicks on the scrim do nothing.
    """

    escape = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.hide()
        self._card = QFrame(self)
        self._card.setObjectName("farmCard")
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        self._card.setStyleSheet(
            f"QFrame#farmCard {{ background-color: {BG_DARK};"
            f" border: 1px solid {CARD_BORDER}; border-radius: 10px; }}"
        )
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        self._card.setGraphicsEffect(shadow)
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(24, 22, 24, 18)
        self._content: QWidget | None = None
        self._max_width = 820
        parent.installEventFilter(self)

    def content(self) -> QWidget | None:
        return self._content

    def show_content(self, widget: QWidget, max_width: int = 820) -> None:
        """Show ``widget`` on the card; any card already up is replaced."""
        if self._content is not None and self._content is not widget:
            self._drop_content()
        self._content = widget
        self._max_width = max_width
        self._card_layout.addWidget(widget)
        widget.show()
        self._fit()
        self.show()
        self.raise_()
        self.setFocus()

    def close_content(self) -> None:
        self._drop_content()
        self.hide()

    def _drop_content(self) -> None:
        # Hidden at once: deleteLater waits for the event loop, and a card
        # shown meanwhile would be drawn over the old one.
        if self._content is not None:
            self._card_layout.removeWidget(self._content)
            self._content.hide()
            self._content.deleteLater()
            self._content = None

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self._fit()
        return super().eventFilter(obj, event)

    def _fit(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        margin = 32
        width = min(self._max_width, max(360, self.width() - 2 * margin))
        hint = self._card.sizeHint()
        height = min(max(hint.height(), 200), self.height() - 2 * margin)
        self._card.setGeometry(
            (self.width() - width) // 2, max(margin, (self.height() - height) // 2),
            width, height,
        )

    def resizeEvent(self, event) -> None:
        self._fit()
        super().resizeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.escape.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Never on to the window: its default button is Submit, and the
            # card is already the answer to it.
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), SCRIM)
        painter.end()

    def mousePressEvent(self, event) -> None:
        event.accept()  # the scrim swallows clicks meant for the grid under it


# ── Step chips ────────────────────────────────────────────

# A row's steps, painted as small pills in their kind's colour.
STEPS_ROLE = Qt.UserRole + 20
CHIP_HEIGHT = 20


def chips_width(steps: Sequence[tuple[str, str]], font: QFont) -> int:
    metrics = QFontMetrics(font)
    return sum(metrics.horizontalAdvance(label) + 16 + 5 for _kind, label in steps)


def paint_chips(painter: QPainter, rect: QRect, steps: Sequence[tuple[str, str]], font: QFont) -> None:
    """``steps`` (``(kind, label)``) as pills left to right in ``rect``; the rest elided as +N."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    x = rect.left()
    top = rect.center().y() - CHIP_HEIGHT // 2
    for shown, (kind, label) in enumerate(steps):
        width = metrics.horizontalAdvance(label) + 16
        if x + width > rect.right() and shown:
            more = f"+{len(steps) - shown}"
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(QRect(x, top, rect.right() - x, CHIP_HEIGHT), Qt.AlignVCenter, more)
            break
        chip = QRectF(x, top, width, CHIP_HEIGHT)
        painter.setPen(QPen(QColor(KIND_COLOURS.get(kind, ACCENT)), 1))
        painter.setBrush(QColor(KIND_FILLS.get(kind, ACCENT)))
        painter.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(chip, Qt.AlignCenter, label)
        x += width + 5
    painter.restore()


class _ChipDelegate(QStyledItemDelegate):
    """Paints a tree column's ``STEPS_ROLE`` data as chips."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._font = QFont(FONT_FAMILY)
        self._font.setPixelSize(FONT_BODY - 3)
        self._font.setWeight(QFont.DemiBold)

    def sizeHint(self, option, index):
        steps = index.data(STEPS_ROLE) or []
        return QSize(chips_width(steps, self._font) + 12, 30)

    def paint(self, painter, option, index) -> None:
        steps = index.data(STEPS_ROLE)
        if not steps:
            super().paint(painter, option, index)
            return
        paint_chips(painter, option.rect.adjusted(6, 0, -6, 0), steps, self._font)


def _list_style() -> str:
    return f"""
QTreeWidget {{
    background-color: {BG_DARKEST};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    alternate-background-color: #1f1f1f;
    outline: none;
}}
QTreeWidget::item {{ padding: 2px 4px; }}
QHeaderView::section {{
    background-color: {BG_MID};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid #3a3a3a;
    padding: 5px 8px;
}}
"""


def _fit_rows(table: QTreeWidget, rows: int) -> None:
    """As tall as its rows, up to a screenful; the card scrolls it beyond."""
    tall = 40 + 31 * max(1, rows)
    table.setMinimumHeight(min(360, tall))
    table.setMaximumHeight(tall)


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-size: {FONT_BODY + 5}px; font-weight: 600; color: {TEXT_PRIMARY};")
    return label


def _subtitle(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {TEXT_SECONDARY};")
    return label


def _banner(colour: str, text: str, action: QPushButton | None = None) -> QWidget:
    """A one-line notice with a coloured edge, and an optional action at its end."""
    box = QFrame()
    box.setObjectName("farmBanner")
    box.setAttribute(Qt.WA_StyledBackground, True)
    box.setStyleSheet(
        f"QFrame#farmBanner {{ background-color: {BG_MID}; border: 1px solid {SECTION_BORDER};"
        f" border-left: 3px solid {colour}; border-radius: 4px; }}"
    )
    layout = QHBoxLayout(box)
    layout.setContentsMargins(12, 8, 8, 8)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {colour}; font-weight: 600;")
    layout.addWidget(label, 1)
    if action is not None:
        layout.addWidget(action)
    return box


# ── Review ────────────────────────────────────────────────

class ReviewSheet(QWidget):
    """What Submit is about to send: a row per entity, its steps as chips,
    what to check first. Replaces the old confirmation dialog.

    Signals: ``submit_requested``, ``back_requested``,
    ``tick_stale_requested`` (tick the stale departments the previews would
    otherwise show unpublished, and go back to the grid).
    """

    submit_requested = Signal()
    back_requested = Signal()
    tick_stale_requested = Signal()

    def __init__(
        self,
        rows: Sequence[ReviewRow],
        noun: tuple[str, str],
        where: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        rows = review_order(rows)
        steps = sum(len(row.steps) for row in rows)
        blocked = [row for row in rows if row.errors]
        stale = [row for row in rows if row.stale]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(_title("Review submission"))
        count = len(rows)
        root.addWidget(_subtitle(
            f"{steps} step{'s' if steps != 1 else ''} for {count} {noun[count != 1]}, "
            f"{where}."
        ))

        if blocked:
            n = len(blocked)
            root.addWidget(_banner(
                ERROR_COLOUR,
                f"{n} {noun[n != 1]} can't be submitted. Fix {'it' if n == 1 else 'them'} "
                f"or untick {'it' if n == 1 else 'them'} first.",
            ))
        self.tick_button: QPushButton | None = None
        if stale:
            n = len(stale)
            self.tick_button = QPushButton("Tick stale publishes")
            self.tick_button.setAutoDefault(False)
            self.tick_button.setToolTip(
                "Tick the stale departments these previews compose and go back to "
                "the grid, so the previews show fresh publishes"
            )
            self.tick_button.clicked.connect(self.tick_stale_requested)
            root.addWidget(_banner(
                STALE_COLOUR,
                f"{n} {noun[n != 1]} will preview work that isn't published.",
                self.tick_button,
            ))

        self.table = QTreeWidget()
        self.table.setStyleSheet(_list_style())
        self.table.setColumnCount(3)
        self.table.setHeaderLabels([noun[0].capitalize(), "Steps", "Check first"])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setUniformRowHeights(True)
        self.table.setItemDelegateForColumn(1, _ChipDelegate(self.table))
        header = self.table.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for row in rows:
            item = QTreeWidgetItem(self.table, [row.name, "", ""])
            item.setData(1, STEPS_ROLE, row.steps)
            item.setToolTip(1, ", ".join(label for _kind, label in row.steps))
            notes, colour = _review_notes(row)
            item.setText(2, notes)
            item.setToolTip(2, "\n".join(review_lines(row)))
            item.setForeground(2, QColor(colour))
            bold = QFont(FONT_FAMILY)
            bold.setPixelSize(FONT_BODY)
            bold.setWeight(QFont.DemiBold)
            item.setFont(0, bold)
        _fit_rows(self.table, len(rows))
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.setAutoDefault(False)
        self.back_button.setToolTip("Back to the grid; nothing is sent (Esc)")
        self.back_button.clicked.connect(self.back_requested)
        buttons.addWidget(self.back_button)
        self.submit_button = QPushButton(f"Submit {steps} step{'s' if steps != 1 else ''}")
        self.submit_button.setDefault(True)
        self.submit_button.clicked.connect(self.submit_requested)
        if not can_submit(rows):
            self.submit_button.setDefault(False)
            self.submit_button.setEnabled(False)
            self.submit_button.setToolTip("Some rows can't be submitted; see the list")
            self.back_button.setDefault(True)
        buttons.addWidget(self.submit_button)
        root.addLayout(buttons)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.submit_button.isEnabled():
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


def _review_notes(row: ReviewRow) -> tuple[str, str]:
    """The row's one-line note and its colour."""
    lines = review_lines(row)
    if not lines:
        return "Ready", CURRENT_COLOUR
    colour = ERROR_COLOUR if row.errors else STALE_COLOUR if row.stale else TEXT_SECONDARY
    return "; ".join(lines), colour


class NoticeCard(QWidget):
    """Why Submit cannot go ahead yet, on the window's overlay: a headline,
    a sentence, OK (Enter or Esc). Replaces the separate message boxes."""

    dismissed = Signal()

    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(_title(title))
        body = _subtitle(text)
        body.setStyleSheet(f"color: {TEXT_PRIMARY};")
        root.addWidget(body)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.dismissed)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.dismissed.emit()
            return
        super().keyPressEvent(event)


# ── Submission progress ───────────────────────────────────

_PROGRESS_STYLE = f"""
QProgressBar {{
    background-color: {BG_DARKEST};
    border: 1px solid {BORDER};
    border-radius: 4px;
    min-height: 26px;
    max-height: 26px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
    text-align: center;
}}
"""

# A finished, clean run's Close: filled, it is the one next step.
_DONE_BUTTON_STYLE = f"""
QPushButton {{
    background-color: {CURRENT_COLOUR};
    color: #ffffff;
    font-weight: 600;
    border: 1px solid {CURRENT_COLOUR};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{ background-color: #35c095; border-color: #35c095; }}
"""


class SubmissionView(QWidget):
    """A submission running in its own process: a row per entity, errors inline.

    Reads the progress file the runner appends to (``farm.submit.plan``),
    so nothing here can block Houdini. Closing the window does not stop the
    submission; **Cancel** does, between entities (the one being submitted
    always finishes, so none is left with half its jobs on the farm).

    Signals: ``back_requested`` (to the grid, once finished),
    ``close_requested``, ``retry_requested(list[dict])`` (the failed or
    cancelled rows' configs), ``finished(bool clean)``.
    """

    back_requested = Signal()
    close_requested = Signal()
    retry_requested = Signal(list)
    finished = Signal(bool)

    def __init__(
        self,
        plan_path: Path,
        process: subprocess.Popen | None,
        configs: Sequence[dict],
        steps: dict[str, list[tuple[str, str]]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan_path = Path(plan_path)
        self._progress_path = self._plan_path.parent / submit_plan.PROGRESS_NAME
        self._process = process
        self._configs = list(configs)
        self._offset = 0
        self._state = submit_plan.fold_events([])
        self._backend = submit_plan.plan_backend(submit_plan.read_plan(self._plan_path))
        self._items: dict[str, QTreeWidgetItem] = {}
        self._cancelling = False
        self._announced = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self._heading = _title(self._heading_text(finished=False))
        root.addWidget(self._heading)
        self._note = _subtitle(
            "This runs in its own process: keep working, or close this window. "
            "Closing it does not stop the submission; Cancel does."
        )
        root.addWidget(self._note)

        self.table = QTreeWidget()
        self.table.setStyleSheet(_list_style())
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setColumnCount(3)
        self.table.setHeaderLabels(["Entity", "Steps", "Status"])
        self.table.setItemDelegateForColumn(1, _ChipDelegate(self.table))
        self.table.setWordWrap(True)
        header = self.table.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        steps = steps or {}
        for config in self._configs:
            uri = config['entity']['uri']
            item = QTreeWidgetItem(self.table, [config['entity'].get('name') or uri.split(':/', 1)[-1], "", "queued"])
            item.setToolTip(0, uri)
            if steps.get(uri):
                item.setData(1, STEPS_ROLE, steps[uri])
            item.setForeground(2, QColor(TEXT_SECONDARY))
            self._items[uri] = item
        _fit_rows(self.table, len(self._configs))
        root.addWidget(self.table, 1)

        self.bar = QProgressBar()
        self.bar.setStyleSheet(_PROGRESS_STYLE)
        self.bar.setTextVisible(True)
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setRange(0, max(1, len(self._configs)))
        self._bar_colour = None
        root.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        log_btn = QPushButton("Open log folder")
        log_btn.setAutoDefault(False)
        log_btn.clicked.connect(self._open_folder)
        buttons.addWidget(log_btn)
        manager_btn = QPushButton("Farm Manager")
        manager_btn.setAutoDefault(False)
        manager_btn.setToolTip("Follow the submitted jobs: their tasks, progress and logs")
        manager_btn.clicked.connect(self._open_manager)
        buttons.addWidget(manager_btn)
        buttons.addStretch(1)
        self.retry_button = QPushButton("Retry failed")
        self.retry_button.setAutoDefault(False)
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self._retry_unfinished)
        buttons.addWidget(self.retry_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setToolTip(
            "Stop once the entity being submitted finishes; the rest are not sent"
        )
        self.cancel_button.clicked.connect(self._cancel_submission)
        buttons.addWidget(self.cancel_button)
        self.back_button = QPushButton("Back to grid")
        self.back_button.setAutoDefault(False)
        self.back_button.setToolTip("Submit more from the grid")
        self.back_button.setVisible(False)
        self.back_button.clicked.connect(self.back_requested)
        buttons.addWidget(self.back_button)
        self.close_button = QPushButton("Close window")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close_requested)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_finished(self) -> bool:
        return bool(self._state['finished'])

    def _heading_text(self, *, finished: bool) -> str:
        if self._backend == "farm":
            return "Submitted to the render farm" if finished else "Submitting to the render farm"
        return "Queued on this machine" if finished else "Queuing on this machine"

    # ── progress ──────────────────────────────────────────

    def poll(self) -> None:
        events, self._offset = submit_plan.read_events(self._progress_path, self._offset)
        if events:
            submit_plan.fold_events(events, self._state)
            for event in events:
                kind = event.get('event')
                if kind in ('row', 'cleanup'):
                    self._show_row(event.get('uri'))
        if not self._state['finished'] and self._runner_died():
            for event in submit_progress.close_out(self._state, list(self._items)):
                self._show_row(event['uri'])
        self._update_summary()
        if self._state['finished']:
            self._timer.stop()
            if not self._announced:
                self._announced = True
                done, failed, cancelled = self._counts()
                self.finished.emit(not (failed or cancelled))

    def stop(self) -> None:
        self._timer.stop()

    def _runner_died(self) -> bool:
        return self._process is not None and self._process.poll() is not None

    def _show_row(self, uri: str | None) -> None:
        item = self._items.get(uri)
        if item is None:
            return
        row = self._state['rows'].get(uri)
        cleanup = self._state.get('cleanup', {}).get(uri)
        status = row.get('status') if isinstance(row, dict) else None
        item.setText(2, submit_progress.row_text(row, cleanup, self._backend))
        colour = {
            submit_progress.RUNNING: RUNNING_COLOUR,
            submit_progress.DONE: CURRENT_COLOUR,
            submit_progress.FAILED: ERROR_COLOUR,
            submit_progress.CANCELLED: CANCELLED_COLOUR,
        }.get(status, TEXT_SECONDARY)
        if status == submit_progress.DONE and isinstance(cleanup, dict) and cleanup.get('error'):
            colour = STALE_COLOUR
        item.setForeground(2, QColor(colour))
        item.setToolTip(2, item.text(2))

    def _counts(self) -> tuple[int, int, int]:
        return submit_progress.counts(self._state)

    def _update_summary(self) -> None:
        done, failed, cancelled = self._counts()
        total = len(self._configs)
        self.bar.setValue(done + failed + cancelled)
        finished = self._state['finished']
        if finished:
            text = f"{done} of {total} sent"
        elif self._cancelling:
            text = f"Cancelling… {done + failed} of {total}"
        else:
            text = f"Submitting… {done + failed} of {total}"
        if failed:
            text += f", {failed} failed"
        if cancelled:
            text += f", {cancelled} cancelled"
        # QProgressBar's format expands %p/%v/%m; a literal % must be doubled.
        self.bar.setFormat(text.replace('%', '%%'))
        if failed:
            colour = ERROR_COLOUR
        elif cancelled and finished:
            colour = CANCELLED_COLOUR
        elif finished:
            colour = CURRENT_COLOUR
        else:
            colour = ACCENT
        if colour != self._bar_colour:
            self._bar_colour = colour
            self.bar.setStyleSheet(
                _PROGRESS_STYLE
                + f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 3px; }}"
            )
        self.retry_button.setVisible(bool(failed or cancelled) and finished)
        self.retry_button.setText("Submit the rest" if cancelled else "Retry failed")
        if finished:
            self._heading.setText(self._heading_text(finished=True))
            self._note.setText(
                "Follow the jobs in the Farm Manager. Back to grid submits more."
            )
            self.cancel_button.hide()
            self.back_button.show()
            self.close_button.setText("Close")
            self.close_button.setStyleSheet(
                _DONE_BUTTON_STYLE if not (failed or cancelled) else ""
            )

    # ── actions ───────────────────────────────────────────

    def _open_folder(self) -> None:
        from tumblepipe.util import desktop
        desktop.open_in_file_browser(self._plan_path.parent)

    def _open_manager(self) -> None:
        try:
            from tumblepipe.farm.manager.window import show_manager
            show_manager(parent=self.window().parentWidget(), source=self._backend)
        except Exception as error:
            from tumblepipe.pipe.houdini.ui.report import report_failure
            report_failure("Opening the Farm Manager", error)

    def _cancel_submission(self) -> None:
        try:
            submit_plan.request_cancel(self._plan_path)
        except Exception as error:
            from tumblepipe.pipe.houdini.ui.report import report_failure
            report_failure("Cancelling the farm submission", error)
            return
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling…")
        self._note.setText(
            "Stopping once the entity being submitted finishes; the rest are not sent."
        )
        self._update_summary()

    def sent_uris(self) -> set[str]:
        """The entities whose jobs went out: Back to grid unticks them."""
        return submit_progress.sent_uris(self._state)

    def unfinished_configs(self) -> list[dict]:
        return submit_progress.unfinished_configs(self._state, self._configs)

    def _retry_unfinished(self) -> None:
        configs = self.unfinished_configs()
        if configs:
            self.retry_requested.emit(configs)
