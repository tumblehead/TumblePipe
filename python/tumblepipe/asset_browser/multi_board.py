"""The Entity Editor's Multis board.

The Multis tab shows a kind's Multis as a table or, with **Board**, as a
board: a column of the rows no Multi has (by sequence or category), then a
column per Multi with its members as cards in member order, then a New
Multi column to drop cards on. Dragging a card moves it to another Multi
(out of the one it came from); with Ctrl it is added and stays where it was;
dropped on the first column it leaves its Multi; dropped within its Multi it
reorders. A sequence's header drags the sequence whole (its shots now and
any made later); a Multi that lists one shows it as one stacked card, whose
shots cannot be dragged out alone.

The board draws :meth:`config.entity_sheet.Sheet.board_columns` and every
drop is one sheet edit (``move_to_multi``, ``add_multi_from``), so it waits
for Apply and undoes with the rest of the editor. While a card hovers over a
column, the column says what the drop does, or why it is refused
(``drop_problem``: the overlap rule, before the drop).

With a department picked, only the Multis covering it are shown and every
row is in exactly one column; the first column is then the rows with their
own workfile for that department.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es

from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_HOVER, BG_DARK, BG_DARKEST, BG_LIGHT, BG_MID, BORDER, DANGER,
    FONT_FAMILY, FONT_SMALL, TEXT_DIM, TEXT_PRIMARY, WARNING, scaled,
)

if TYPE_CHECKING:
    from .entity_editor import _MultiPane

MIME = 'application/x-tumblepipe-multi-cards'

#: The convention's department hues (dept-1 .. dept-8), by pool order.
DEPARTMENT_HUES = (
    '#4a405c', '#3a4860', '#345256', '#3c5440',
    '#545238', '#604834', '#603c3c', '#5a3c50',
)

COLUMN_WIDTH = 240


def department_hue(pool, department: str) -> str:
    pool = list(pool)
    index = pool.index(department) if department in pool else sum(map(ord, department))
    return DEPARTMENT_HUES[index % len(DEPARTMENT_HUES)]


def names_label(labels: list[str]) -> str:
    """``SH010``, ``SH010 and SH020``, ``SH010, SH020 and 3 more``."""
    if len(labels) <= 2:
        return ' and '.join(labels)
    return f'{labels[0]}, {labels[1]} and {len(labels) - 2} more'


def drop_text(
    sheet: es.Sheet, keys: list, source: int | None, target: int | None,
    copy: bool, problem: str, placed_at: int | None = None,
) -> str:
    """What a column says while cards hover over it: the drop, or why it
    is refused."""
    labels = [sheet.card_label(k) for k in keys]
    names = names_label(labels)
    src = sheet.multi(source) if source is not None else None
    if problem:
        text = problem
        if copy and target is not None and not sheet.drop_problem(keys, source, target, False):
            text += '. Let go of Ctrl to move it here instead'
        return text
    if target is None:
        return f'Take {names} out of {src.name}' if src is not None else ''
    if src is not None and src.id == target:
        return f'Move {names} here'
    parts = [f'{"Add" if copy else "Move"} {names} here']
    if src is not None:
        parts.append(f'stays in {src.name}' if copy else f'leaves {src.name}')
    if placed_at is not None:
        parts.append(f'placed at {placed_at}')
    return ' · '.join(parts)


def _payload(mime: QMimeData) -> dict | None:
    if not mime.hasFormat(MIME):
        return None
    try:
        data = json.loads(bytes(mime.data(MIME)).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('keys'), list):
        return None
    return data


class _Entry:
    """One drawn line of a column: a group header ('seq'), a card or a
    shot of a stacked group card ('child')."""

    def __init__(self, kind, key=None, label='', right='', pending=False,
                 also=(), stack=False, expanded=False, tip=''):
        self.kind, self.key, self.label, self.right = kind, key, label, right
        self.pending, self.also = pending, tuple(also)
        self.stack, self.expanded, self.tip = stack, expanded, tip
        self.rect = QRect()


class _CardList(QWidget):
    """A column's cards, drawn here, dragged from and dropped on here."""

    def __init__(self, board: 'MultiBoard', column: '_Column') -> None:
        super().__init__()
        self.board, self.column = board, column
        self.entries: list[_Entry] = []
        self.insert_y: int | None = None
        self._press: QPoint | None = None
        self._press_entry: _Entry | None = None
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._font = QFont(FONT_FAMILY)
        self._font.setPixelSize(scaled(13))
        self._small = QFont(FONT_FAMILY)
        self._small.setPixelSize(FONT_SMALL)
        self._bold = QFont(self._font)
        self._bold.setBold(True)

    # ── Layout ───────────────────────────────────────────

    def set_entries(self, entries: list[_Entry]) -> None:
        self.entries = entries
        self._layout()
        self.update()

    def _layout(self) -> None:
        pad, gap = scaled(6), scaled(4)
        y, width = pad, max(self.width(), scaled(COLUMN_WIDTH) - 2) - 2 * pad
        for entry in self.entries:
            height = {'seq': scaled(24), 'child': scaled(26)}.get(entry.kind, scaled(30))
            indent = scaled(14) if entry.kind == 'child' else 0
            entry.rect = QRect(pad + indent, y, width - indent, height)
            y += height + gap + (scaled(4) if entry.stack else 0)
        self.setMinimumHeight(y + scaled(40))

    def resizeEvent(self, event) -> None:
        self._layout()
        super().resizeEvent(event)

    def entry_at(self, pos: QPoint) -> _Entry | None:
        for entry in self.entries:
            if entry.rect.contains(pos):
                return entry
        return None

    def key_before(self, y: int):
        """The card a drop at ``y`` lands before (None: last), and the y of
        the line drawn there."""
        cards = [e for e in self.entries if e.kind == 'card']
        for entry in cards:
            if y < entry.rect.center().y():
                return entry.key, entry.rect.top() - scaled(2)
        last = cards[-1].rect.bottom() + scaled(3) if cards else scaled(6)
        return None, last

    # ── Painting ─────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.board.selected_keys(self.column.multi_id)
        for entry in self.entries:
            self._paint_entry(painter, entry, entry.key in selected and entry.kind != 'child')
        if self.insert_y is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(ACCENT_HOVER))
            x, w = scaled(8), self.width() - scaled(16)
            painter.drawRoundedRect(QRect(x, self.insert_y - 1, w, scaled(2)), 1, 1)
            painter.drawEllipse(QPoint(x, self.insert_y), scaled(3), scaled(3))
        painter.end()

    def _paint_entry(self, painter: QPainter, entry: _Entry, selected: bool) -> None:
        r = entry.rect
        if entry.kind == 'seq':
            painter.setFont(self._small)
            painter.setPen(QColor(TEXT_DIM))
            dots = r.adjusted(scaled(4), scaled(6), 0, -scaled(6))
            for dy in range(0, dots.height(), scaled(4)):
                for dx in (0, scaled(3)):
                    painter.drawPoint(dots.left() + dx, dots.top() + dy)
            bold = QFont(self._small)
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(r.adjusted(scaled(14), 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, entry.label)
            painter.setFont(self._small)
            painter.drawText(r.adjusted(0, 0, -scaled(4), 0), Qt.AlignVCenter | Qt.AlignRight, entry.right)
            return
        radius = scaled(4)
        if entry.stack:
            painter.setPen(QPen(QColor(BORDER)))
            painter.setBrush(QColor(BG_DARK))
            painter.drawRoundedRect(r.adjusted(scaled(3), scaled(4), -scaled(3), scaled(4)), radius, radius)
        if entry.kind == 'child':
            painter.setPen(QPen(QColor(BORDER)))
            painter.setBrush(Qt.NoBrush)
        else:
            painter.setPen(QPen(QColor(ACCENT if selected else BG_LIGHT)) if selected else Qt.NoPen)
            painter.setBrush(QColor(BG_LIGHT))
        painter.drawRoundedRect(r.adjusted(0, 0, -1, -1), radius, radius)
        x = r.left() + scaled(8)
        if entry.kind == 'child' or entry.stack:
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(self._small)
            mark = ('▾' if entry.expanded else '▸') if entry.stack else '·'
            painter.drawText(QRect(x, r.top(), scaled(12), r.height()), Qt.AlignVCenter, mark)
            x += scaled(14)
        name_color = ACCENT_HOVER if entry.pending else (TEXT_DIM if entry.kind == 'child' else TEXT_PRIMARY)
        painter.setPen(QColor(name_color))
        painter.setFont(self._bold if entry.kind != 'child' else self._font)
        metrics = QFontMetrics(painter.font())
        painter.drawText(QRect(x, r.top(), r.width(), r.height()), Qt.AlignVCenter, entry.label)
        x += metrics.horizontalAdvance(entry.label) + scaled(6)
        right_edge = r.right() - scaled(8)
        painter.setFont(self._small)
        small = QFontMetrics(self._small)
        if entry.right:
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(QRect(r.left(), r.top(), right_edge - r.left(), r.height()),
                             Qt.AlignVCenter | Qt.AlignRight, entry.right)
            right_edge -= small.horizontalAdvance(entry.right) + scaled(6)
        for name in entry.also:
            text = f'M {name}'
            w = small.horizontalAdvance(text) + scaled(10)
            if x + w > right_edge:
                break
            chip = QRect(x, r.center().y() - scaled(9), w, scaled(18))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG_MID))
            painter.drawRoundedRect(chip, radius, radius)
            painter.setPen(QColor(WARNING))
            painter.drawText(chip, Qt.AlignCenter, text)
            x += w + scaled(4)

    # ── Mouse ────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        entry = self.entry_at(event.position().toPoint())
        self._press = event.position().toPoint()
        self._press_entry = entry
        if entry is not None and entry.kind == 'card':
            self.board.select(self.column.multi_id, entry.key, event.modifiers())
        elif entry is None:
            self.board.select(self.column.multi_id, None, event.modifiers())

    def mouseDoubleClickEvent(self, event) -> None:
        entry = self.entry_at(event.position().toPoint())
        if entry is not None and entry.stack:
            self.board.toggle_expanded(self.column.multi_id, entry.key)

    def mouseMoveEvent(self, event) -> None:
        entry = self.entry_at(event.position().toPoint())
        self.setToolTip(entry.tip if entry is not None else '')
        if self._press is None or not (event.buttons() & Qt.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        pressed, self._press = self._press_entry, None
        if pressed is None or pressed.kind == 'child':
            if pressed is not None:
                self.board.say(pressed.tip)
            return
        if pressed.kind == 'seq':
            keys = [pressed.key]
        else:
            selected = self.board.selected_keys(self.column.multi_id)
            keys = [e.key for e in self.entries if e.kind == 'card' and e.key in selected] \
                if pressed.key in selected else [pressed.key]
        self._drag(keys, pressed)

    def mouseReleaseEvent(self, event) -> None:
        self._press = None
        super().mouseReleaseEvent(event)

    def _drag(self, keys: list, pressed: _Entry) -> None:
        mime = QMimeData()
        mime.setData(MIME, json.dumps({'source': self.column.multi_id, 'keys': keys}).encode('utf-8'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = QPixmap(pressed.rect.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        ghost = _Entry('card', pressed.key, pressed.label if len(keys) == 1 else f'{pressed.label} +{len(keys) - 1}',
                       pressed.right if len(keys) == 1 else '')
        ghost.rect = QRect(QPoint(0, 0), pressed.rect.size())
        painter.setOpacity(0.9)
        self._paint_entry(painter, ghost, True)
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(scaled(16), pressed.rect.height() // 2))
        self.board.dragging = True
        try:
            drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)
        finally:
            self.board.dragging = False
            self.board.clear_drop_state()

    # ── Drops ────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if _payload(event.mimeData()) is None:
            return event.ignore()
        event.acceptProposedAction()
        self.board.drag_over(self.column, event)

    def dragMoveEvent(self, event) -> None:
        self.board.drag_over(self.column, event)

    def dragLeaveEvent(self, event) -> None:
        self.board.clear_drop_state()

    def dropEvent(self, event) -> None:
        self.board.drop(self.column, event)


class _Column(QFrame):
    """A Multi's column (``multi_id``), or the rows no Multi has (None)."""

    def __init__(self, board: 'MultiBoard', multi_id: int | None) -> None:
        super().__init__()
        self.board, self.multi_id = board, multi_id
        self.setFixedWidth(scaled(COLUMN_WIDTH))
        self.setObjectName('mbColumn')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.head = QWidget()
        self.head_layout = QVBoxLayout(self.head)
        self.head_layout.setContentsMargins(scaled(10), scaled(8), scaled(8), scaled(8))
        self.head_layout.setSpacing(scaled(6))
        outer.addWidget(self.head)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f'background: {BORDER};')
        outer.addWidget(rule)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        self.banner.setContentsMargins(scaled(8), scaled(6), scaled(8), scaled(6))
        outer.addWidget(self.banner)
        self.cards = _CardList(board, self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.cards)
        scroll.setStyleSheet('QScrollArea { background: transparent; } QWidget { background: transparent; }')
        outer.addWidget(scroll, 1)
        self.problem = ''
        self.set_state('')

    def set_state(self, state: str, text: str = '') -> None:
        """'' at rest, 'ok' when a drop here would go through, 'refused'
        when not, 'new' for a Multi not applied yet."""
        edge = {'ok': ACCENT_HOVER, 'refused': DANGER}.get(state, BORDER)
        style = 'dashed' if self.board.is_new(self.multi_id) and state == '' else 'solid'
        if state == '' and self.board.is_new(self.multi_id):
            edge = ACCENT
        fill = '#2a1c1c' if state == 'refused' else BG_DARKEST
        self.setStyleSheet(
            f'QFrame#mbColumn {{ background: {fill}; border: 1px {style} {edge}; '
            f'border-radius: {scaled(8)}px; }}'
        )
        shown = text or self.problem
        refused = state == 'refused' or (not text and bool(self.problem))
        self.banner.setText(shown)
        self.banner.setVisible(bool(shown))
        self.banner.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; border-radius: {scaled(4)}px; '
            + (f'color: #f28b7d; border: 1px solid {DANGER}; background: {BG_DARK};' if refused
               else f'color: {TEXT_PRIMARY}; background: rgba(34, 139, 107, 0.19);')
        )


class _NewColumn(QFrame):
    """The column to drop cards on to start a Multi with them."""

    def __init__(self, board: 'MultiBoard') -> None:
        super().__init__()
        self.board = board
        self.multi_id = 'new'
        self.setAcceptDrops(True)
        self.setFixedWidth(scaled(150))
        self.setObjectName('mbNew')
        layout = QVBoxLayout(self)
        layout.addStretch()
        title = QLabel('+  New Multi')
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f'color: {TEXT_PRIMARY}; font-weight: bold; font-family: "{FONT_FAMILY}";')
        layout.addWidget(title)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.note)
        layout.addStretch()
        self.set_state('')

    def set_state(self, state: str, text: str = '') -> None:
        edge = {'ok': ACCENT_HOVER, 'refused': DANGER}.get(state, BORDER)
        self.setStyleSheet(
            f'QFrame#mbNew {{ border: 1px dashed {edge}; border-radius: {scaled(8)}px; }}'
        )
        department = self.board.department
        self.note.setText(text or (
            f'Drop shots here to start one covering {department}' if department
            else 'Drop cards here to start one with them'
        ).replace('shots', self.board.item_plural))
        self.note.setStyleSheet(f'color: {TEXT_DIM}; font-size: {FONT_SMALL}px; font-family: "{FONT_FAMILY}";')

    def mouseDoubleClickEvent(self, event) -> None:
        self.board.pane.add_dialog(from_selection=False)

    def dragEnterEvent(self, event) -> None:
        if _payload(event.mimeData()) is None:
            return event.ignore()
        event.acceptProposedAction()
        self.board.drag_over(self, event)

    def dragMoveEvent(self, event) -> None:
        self.board.drag_over(self, event)

    def dragLeaveEvent(self, event) -> None:
        self.board.clear_drop_state()

    def dropEvent(self, event) -> None:
        self.board.drop(self, event)


class MultiBoard(QWidget):
    """The Multis tab's Board view; see the module docstring."""

    def __init__(self, pane: '_MultiPane') -> None:
        super().__init__(pane)
        self.pane = pane
        self.department: str | None = None
        self.dragging = False
        self._columns: list = []
        self._selection: tuple[object, set] = (None, set())
        self._expanded: set[tuple] = set()
        self._focus_name: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))
        self.hint = QLabel(
            'Drag a card to move it to another Multi · Ctrl + drag adds it and '
            'leaves it where it was · drop it on the first column to take it out'
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f'color: {TEXT_DIM}; font-size: {FONT_SMALL}px; font-family: "{FONT_FAMILY}";')
        outer.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.row = QHBoxLayout(self.host)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(scaled(10))
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)
        self.impact = QLabel()
        self.impact.setWordWrap(True)
        self.impact.setTextFormat(Qt.RichText)
        self.impact.setVisible(False)
        self.impact.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; color: {TEXT_PRIMARY}; '
            f'background: rgba(240, 160, 48, 0.12); border: 1px solid rgba(240, 160, 48, 0.45); '
            f'border-radius: {scaled(4)}px; padding: {scaled(6)}px {scaled(10)}px;'
        )
        outer.addWidget(self.impact)

    # ── State ────────────────────────────────────────────

    @property
    def sheet(self) -> es.Sheet:
        return self.pane.sheet

    @property
    def item_plural(self) -> str:
        return 'shots' if self.sheet.kind.key == 'shots' else 'assets'

    def is_new(self, multi_id) -> bool:
        if not isinstance(multi_id, int):
            return False
        try:
            return self.sheet.multi(multi_id).is_new
        except KeyError:
            return False

    def say(self, text: str, warn: bool = False) -> None:
        if text:
            self.pane.say(text, warn)

    def selected_keys(self, multi_id) -> set:
        column, keys = self._selection
        return keys if column == multi_id else set()

    def select(self, multi_id, key, modifiers) -> None:
        column, keys = self._selection
        keys = set(keys) if column == multi_id else set()
        if key is None:
            keys = set() if not modifiers & Qt.ControlModifier else keys
        elif modifiers & Qt.ControlModifier:
            keys ^= {key}
        elif key not in keys:
            keys = {key}
        self._selection = (multi_id, keys)
        for each in self._columns:
            if isinstance(each, _Column):
                each.cards.update()

    def toggle_expanded(self, multi_id, key) -> None:
        self._expanded ^= {(multi_id, key)}
        self.refresh()

    def set_department(self, department: str | None) -> None:
        self.department = department or None
        self.refresh()

    # ── Building ─────────────────────────────────────────

    def _versions(self, multi: es.Multi) -> dict:
        if multi.is_new:
            return {}
        return self.pane.workfiles.versions(multi)

    def refresh(self) -> None:
        if self.dragging:
            return
        sheet = self.sheet
        if self.department is not None and self.department not in sheet.base.pool:
            self.department = None
        x = self.scroll.horizontalScrollBar().value()
        for widget in self._columns:
            widget.hide()
            self.row.removeWidget(widget)
            widget.deleteLater()
        self._columns = []
        while self.row.count():
            self.row.takeAt(0)
        problems = sheet.multi_problems()
        for column in sheet.board_columns(self.department):
            widget = _Column(self, column.multi_id)
            if column.multi_id is None:
                self._fill_free(widget, column)
            else:
                widget.problem = problems.get(column.multi_id, '')
                self._fill_multi(widget, sheet.multi(column.multi_id), column)
                widget.set_state('')
            self._columns.append(widget)
            self.row.addWidget(widget)
        new = _NewColumn(self)
        self._columns.append(new)
        self.row.addWidget(new)
        self.row.addStretch()
        live = {c.multi_id for c in self._columns}
        if self._selection[0] not in live:
            self._selection = (None, set())
        QTimer.singleShot(0, lambda: self.scroll.horizontalScrollBar().setValue(x))
        self._fill_impact()
        if self._focus_name is not None:
            focus, self._focus_name = self._focus_name, None
            self.edit_name(focus)

    def _title(self, widget: _Column, text: str, count: int, multi: es.Multi | None = None) -> None:
        line = QHBoxLayout()
        line.setSpacing(scaled(6))
        if multi is None:
            name = QLabel(text)
            name.setStyleSheet(f'font-weight: bold; color: {TEXT_PRIMARY};')
        else:
            name = QLineEdit(multi.name)
            name.setObjectName(f'multi_name_{multi.id}')
            name.setFrame(False)
            name.setStyleSheet(
                f'QLineEdit {{ font-weight: bold; color: {TEXT_PRIMARY}; background: transparent; }}'
                f'QLineEdit:focus {{ background: {BG_DARK}; border: 1px solid {ACCENT}; }}'
            )
            name.setToolTip('The Multi\'s name: type to rename it (on Apply)')
            name.editingFinished.connect(lambda n=name, i=multi.id: self._rename(i, n.text()))
            widget.name_edit = name
        line.addWidget(name, 1)
        counter = QLabel(str(count))
        counter.setStyleSheet(f'color: {TEXT_DIM}; font-size: {FONT_SMALL}px;')
        line.addWidget(counter)
        if multi is not None:
            more = QToolButton()
            more.setText('⋯')
            more.setAutoRaise(True)
            more.setToolTip(f'{multi.name}: workfiles, timeline, revert, retire')
            more.clicked.connect(lambda _c=False, i=multi.id, b=more: self._multi_menu(i, b))
            line.addWidget(more)
        widget.head_layout.addLayout(line)

    def _meta(self, widget: _Column, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f'color: {TEXT_DIM}; font-size: {FONT_SMALL}px;')
        widget.head_layout.addWidget(label)

    def _fill_free(self, widget: _Column, column: es.BoardColumn) -> None:
        title = f'Own {self.department} workfile' if self.department else 'Not in a Multi'
        self._title(widget, title, len(column.rows()))
        self._meta(widget, f'No Multi covers {self.department} for these' if self.department
                   else 'Each opens its own workfiles')
        entries = []
        member = self.sheet.group_member
        for group in column.groups:
            entries.append(_Entry(
                'seq', member(group.group), group.group, f'{len(group.cards)} of {group.total}',
                tip=f'Drag {group.group} to list it whole: what it has now and anything made in it later',
            ))
            for card in group.cards:
                entries.append(self._card_entry(card, None))
        widget.cards.set_entries(entries)

    def _also(self, row: es.Row, multi_id) -> list[str]:
        return [m.name for m in self.sheet.row_multis(row) if m.id != multi_id]

    def _card_entry(self, card: es.BoardCard, multi_id, right: str = '') -> _Entry:
        sheet = self.sheet
        row = card.rows[0] if card.rows else None
        if row is not None and not right and sheet.kind.key == 'shots':
            length = sheet.value(row, es.LENGTH)
            right = f'{length} f' if isinstance(length, int) else ''
        return _Entry(
            'card', card.key, card.label, right, card.pending,
            also=self._also(row, multi_id) if row is not None else (),
            tip=(f'{row.group}/{row.name}' if row is not None else
                 f'{card.label}: no longer in the project; kept as stored'),
        )

    def _fill_multi(self, widget: _Column, multi: es.Multi, column: es.BoardColumn) -> None:
        sheet = self.sheet
        self._title(widget, multi.name, len(column.rows()), multi)
        chips = QHBoxLayout()
        chips.setSpacing(scaled(4))
        versions = self._versions(multi)
        flow = _Flow(scaled(COLUMN_WIDTH) - scaled(18) - 2)
        for department in multi.departments:
            version = versions.get(department)
            chip = QToolButton()
            chip.setText(f'{department} {version or "none"}' if not multi.is_new else department)
            dim = self.department is not None and department != self.department
            chip.setStyleSheet(
                f'QToolButton {{ background: {department_hue(sheet.base.pool, department)}; '
                f'color: {TEXT_PRIMARY}; border: none; border-radius: {scaled(4)}px; '
                f'padding: {scaled(1)}px {scaled(6)}px; font-size: {FONT_SMALL}px; }}'
                + ('QToolButton { color: rgba(232,232,236,0.45); }' if dim else '')
            )
            chip.setToolTip(
                f'{multi.name} covers {department}' + (f'; latest workfile {version}' if version else '')
                + '. Click for its workfile, or to stop covering it.'
            )
            chip.clicked.connect(lambda _c=False, d=department, b=chip: self._chip_menu(multi.id, d, b))
            flow.add(chip)
        add = QToolButton()
        add.setText('+')
        add.setToolTip('Cover another department')
        add.setStyleSheet(
            f'QToolButton {{ border: 1px dashed {BORDER}; border-radius: {scaled(4)}px; '
            f'color: {TEXT_DIM}; padding: 0 {scaled(5)}px; }}'
        )
        add.clicked.connect(lambda _c=False, b=add: self._cover_menu(multi.id, b))
        flow.add(add)
        widget.head_layout.addWidget(flow)
        starts = sheet.predicted_starts(multi)
        if sheet.kind.key == 'shots':
            if multi.shared_timeline and starts:
                ends = [
                    starts[row.id] + self._length(row) - 1
                    for row in column.rows() if row.id in starts
                ]
                self._meta(widget, f'Shared timeline · {min(starts.values())}–{max(ends)}')
            else:
                self._meta(widget, 'Shared timeline' if multi.shared_timeline else 'Per shot')
        if multi.is_new:
            self._meta(widget, 'New: made on Apply')
        entries = []
        for card in column.cards:
            right = ''
            if card.kind == 'row' and card.rows and card.rows[0].id in starts:
                row = card.rows[0]
                right = f'{starts[row.id]}–{starts[row.id] + self._length(row) - 1}'
            if card.kind == 'group':
                expanded = (multi.id, card.key) in self._expanded
                entry = _Entry(
                    'card', card.key, card.label, f'{len(card.rows)} {self.item_plural}', card.pending,
                    stack=True, expanded=expanded,
                    tip=f'{card.label} is listed whole: everything in it, and anything '
                        'made in it later, is a member. Double-click to show them.',
                )
                entries.append(entry)
                if expanded:
                    for row in card.rows:
                        entries.append(_Entry(
                            'child', row.id, row.name, f'{starts[row.id]}' if row.id in starts else '',
                            tip=f'{row.name} comes from {card.label}: drag {card.label} to move it',
                        ))
                continue
            entries.append(self._card_entry(card, multi.id, right))
        widget.cards.set_entries(entries)

    def _length(self, row: es.Row) -> int:
        start, end = self.sheet.value(row, es.FRAME_START), self.sheet.value(row, es.FRAME_END)
        if isinstance(start, int) and isinstance(end, int):
            return end - start + 1
        return 1

    def _fill_impact(self) -> None:
        sheet = self.sheet
        by_uri = {str(m.origin.uri): m for m in sheet.multis if m.origin is not None}

        def latest(uri, department):
            multi = by_uri.get(str(uri))
            return self._versions(multi).get(department) if multi is not None else None

        try:
            impact = sheet.workfile_impact(latest, lambda d: d in sheet.base.publishing)
        except Exception:
            impact = []
        if not impact:
            self.impact.setVisible(False)
            return
        lines = []
        for each in impact:
            files = ', '.join(f'{d} {v}' for d, v in each.workfiles)
            bits = []
            if each.leaves:
                bits.append(f'remove the graphs of {names_label(list(each.leaves))}, which leave it')
            if each.joins:
                bits.append(f'add a graph for {names_label(list(each.joins))}')
            lines.append(f'<b>{each.multi}</b> · {files}: its next save offers to {" and ".join(bits)}.')
        self.impact.setText(
            '<b>Workfiles these changes reach.</b> Apply writes the Multis only; '
            'each workfile is brought in step on its next save, which asks first.<br>'
            + '<br>'.join(lines)
        )
        self.impact.setVisible(True)

    def edit_name(self, multi_id: int) -> None:
        for widget in self._columns:
            if getattr(widget, 'multi_id', None) == multi_id and hasattr(widget, 'name_edit'):
                widget.name_edit.setFocus()
                widget.name_edit.selectAll()
                self.scroll.ensureWidgetVisible(widget)

    def scroll_to(self, multi_id: int) -> None:
        for widget in self._columns:
            if getattr(widget, 'multi_id', None) == multi_id:
                self.scroll.ensureWidgetVisible(widget)

    # ── Menus ────────────────────────────────────────────

    def _rename(self, multi_id: int, text: str) -> None:
        try:
            multi = self.sheet.multi(multi_id)
        except KeyError:
            return
        if text.strip() and text.strip() != multi.name:
            QTimer.singleShot(0, lambda: self.pane.edit(lambda: self.sheet.rename_multi(multi_id, text)))

    def _chip_menu(self, multi_id: int, department: str, button: QToolButton) -> None:
        multi = self.sheet.multi(multi_id)
        source = self.pane.window_.source
        version = self._versions(multi).get(department)
        menu = QMenu(self)
        ready = not multi.is_new and not multi.touched
        if version:
            action = menu.addAction(f'Open {department} {version}')
            action.triggered.connect(lambda: source.open_multi_workfile(multi.origin.uri, department))
        else:
            action = menu.addAction(f'New {department} from Template')
            action.triggered.connect(lambda: self._new_workfile(multi, department))
        action.setEnabled(ready)
        if not ready:
            action.setToolTip('Apply first: the workfiles follow the Multi as stored')
        menu.addSeparator()
        stop = menu.addAction(f'Stop covering {department}')
        stop.triggered.connect(lambda: self.pane.edit(
            lambda: self.sheet.set_covers([multi_id], department, False)
        ))
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))

    def _new_workfile(self, multi: es.Multi, department: str) -> None:
        self.pane.workfiles.new(multi, department)

    def _cover_menu(self, multi_id: int, button: QToolButton) -> None:
        multi = self.sheet.multi(multi_id)
        menu = QMenu(self)
        left = [d for d in self.sheet.base.pool if d not in multi.departments]
        for department in left:
            action = menu.addAction(department)
            action.triggered.connect(lambda _c=False, d=department: self.pane.edit(
                lambda: self.sheet.set_covers([multi_id], d, True)
            ))
        if not left:
            menu.addAction('It covers every department').setEnabled(False)
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))

    def _multi_menu(self, multi_id: int, button: QToolButton) -> None:
        from tumblepipe.config.multi_timeline import TIMELINE_CHOICES, timeline_settings
        multi = self.sheet.multi(multi_id)
        source = self.pane.window_.source
        menu = QMenu(self)
        ready = not multi.is_new and not multi.touched
        versions = self._versions(multi)
        opens = menu.addMenu('Open Workfile')
        for department in multi.departments:
            version = versions.get(department)
            if version:
                action = opens.addAction(f'{department} {version}')
                action.triggered.connect(lambda _c=False, d=department: source.open_multi_workfile(multi.origin.uri, d))
            else:
                action = opens.addAction(f'{department}: New from Template')
                action.triggered.connect(lambda _c=False, d=department: self._new_workfile(multi, d))
            action.setEnabled(ready)
        opens.setEnabled(bool(multi.departments))
        if self.sheet.kind.key == 'shots':
            timeline = menu.addMenu('Timeline')
            for choice in TIMELINE_CHOICES:
                action = timeline.addAction(choice)
                action.setCheckable(True)
                action.setChecked(timeline_settings(choice) == multi.timeline)
                action.triggered.connect(lambda _c=False, c=choice: self.pane.edit(
                    lambda: self.sheet.set_multi_timeline([multi_id], *timeline_settings(c))
                ))
            if multi.shared_timeline and not multi.is_new:
                repack = menu.addAction('Repack Timeline…')
                repack.setEnabled(ready)
                repack.triggered.connect(lambda: source.repack(multi.origin.uri))
        menu.addSeparator()
        rename = menu.addAction('Rename')
        rename.triggered.connect(lambda: self.edit_name(multi_id))
        table = menu.addAction('Show in Table')
        table.triggered.connect(lambda: self.pane.show_in_table(multi_id))
        menu.addSeparator()
        revert = menu.addAction('Revert')
        revert.setEnabled(multi.touched)
        revert.triggered.connect(lambda: self.pane.edit(lambda: self.sheet.revert_multis([multi_id])))
        retire = menu.addAction('Remove' if multi.is_new else 'Retire')
        retire.triggered.connect(lambda: self.pane.edit(lambda: self.sheet.retire_multis([multi_id])))
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))

    # ── Drag and drop ────────────────────────────────────

    def clear_drop_state(self) -> None:
        for widget in self._columns:
            try:
                widget.set_state('')
                if isinstance(widget, _Column):
                    widget.cards.insert_y = None
                    widget.cards.update()
            except RuntimeError:
                pass

    def _drop_plan(self, widget, event):
        """(keys, source, target, before, copy, problem, line y) for cards
        hovering over ``widget``; None when they are not the board's."""
        data = _payload(event.mimeData())
        if data is None:
            return None
        keys, source = list(data['keys']), data.get('source')
        sheet = self.sheet
        try:
            if source is not None:
                sheet.multi(source)
        except KeyError:
            return None
        copy = bool(event.modifiers() & Qt.ControlModifier)
        if isinstance(widget, _NewColumn):
            problem = sheet.new_multi_problem(
                keys, source, copy, [self.department] if self.department else None,
            )
            return keys, source, 'new', None, copy, problem, None
        target = widget.multi_id
        before, line = None, None
        if target is not None:
            before, line = widget.cards.key_before(event.position().toPoint().y())
            problem = sheet.drop_problem(keys, source, target, copy, before)
        else:
            copy = False
            problem = sheet.drop_problem(keys, source, None) if source is not None else ''
        return keys, source, target, before, copy, problem, line

    def _placed_at(self, target) -> int | None:
        """The first frame after a shared timeline's members: where a
        joining shot is placed. None without a shared timeline."""
        multi = self.sheet.multi(target)
        if not multi.shared_timeline:
            return None
        starts = self.sheet.predicted_starts(multi)
        rows = {r.id: r for r in self.sheet.rows}
        ends = [starts[i] + self._length(rows[i]) - 1 for i in starts if i in rows]
        return max(ends) + 1 if ends else None

    def drag_over(self, widget, event) -> None:
        plan = self._drop_plan(widget, event)
        self.clear_drop_state()
        if plan is None:
            return event.ignore()
        keys, source, target, before, copy, problem, line = plan
        if target is None and source is None:
            event.ignore()
            return
        if isinstance(widget, _NewColumn):
            names = names_label([self.sheet.card_label(k) for k in keys])
            text = problem or f'Start a new Multi with {names}' + (
                '' if copy or source is None else f' · leaves {self.sheet.multi(source).name}'
            )
        else:
            placed = self._placed_at(target) if (
                target is not None and target != source and not problem
            ) else None
            text = drop_text(self.sheet, keys, source, target, copy, problem, placed)
        widget.set_state('refused' if problem else 'ok', text)
        if isinstance(widget, _Column) and line is not None and not problem:
            widget.cards.insert_y = line
            widget.cards.update()
        if problem:
            event.ignore()
        else:
            event.setDropAction(Qt.CopyAction if copy else Qt.MoveAction)
            event.accept()

    def drop(self, widget, event) -> None:
        plan = self._drop_plan(widget, event)
        self.clear_drop_state()
        if plan is None:
            return event.ignore()
        keys, source, target, before, copy, problem, _line = plan
        if problem:
            self.say(problem, warn=True)
            return event.ignore()
        event.setDropAction(Qt.CopyAction if copy else Qt.MoveAction)
        event.accept()
        sheet = self.sheet
        department = self.department
        if target == 'new':
            created = []

            def change():
                created.append(sheet.add_multi_from(
                    keys, source, copy, [department] if department else None,
                ))

            def run():
                self._focus_name = None
                self.pane.edit(change)
                if created:
                    self._focus_name = created[0]
                    self._selection = (created[0], set(keys))
                    self.refresh()
            QTimer.singleShot(0, run)
            return

        def run_move():
            self.pane.edit(lambda: sheet.move_to_multi(keys, source, target, before, copy))
            if target is not None:
                self._selection = (target, set(keys))
                self.refresh()
        QTimer.singleShot(0, run_move)


class _Flow(QWidget):
    """Chips laid out left to right, wrapping at a fixed width: the
    column's, so the head knows how tall they are before it is shown."""

    def __init__(self, width: int) -> None:
        super().__init__()
        self._items: list[QWidget] = []
        self.setFixedWidth(width)

    def add(self, widget: QWidget) -> None:
        widget.setParent(self)
        self._items.append(widget)
        self.setFixedHeight(self._place())

    def _place(self) -> int:
        x = y = line = 0
        gap = scaled(4)
        for item in self._items:
            hint = item.sizeHint()
            if x and x + hint.width() > self.width():
                x, y = 0, y + line + gap
                line = 0
            item.setGeometry(x, y, hint.width(), hint.height())
            x += hint.width() + gap
            line = max(line, hint.height())
        return y + line
