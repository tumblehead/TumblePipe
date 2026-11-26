"""The Entity Editor's shared pieces: the colours and fonts its cells
show states with, the column keys every tab uses, small widget makers,
and the table view and cell delegate both table tabs build on."""

from __future__ import annotations

from typing import TYPE_CHECKING


from PySide6.QtCore import (
    QModelIndex, QPersistentModelIndex, Qt, QTimer,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHeaderView, QLabel, QLineEdit,
    QPushButton, QScrollArea, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableView,
    QWidget,
)

from tumblepipe.config import entity_sheet as es

from .table_scroll import (
    hidden_under_frozen, horizontal_wheel_steps,
    keep_view_on_click, scrolls_sideways,
)
from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_HOVER, BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE,
    DANGER, FONT_FAMILY, FONT_SMALL, SUCCESS, TEXT_DIM,
    TEXT_PRIMARY, WARNING, scaled,
)

STATUS = 'status'

#: The Root editor's "Inherit" entry, and an empty pasted value cell.
INHERIT = '__inherit__'

_HINT_STYLE = (
    f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
    f'color: {TEXT_DIM}; border: none; background: transparent;'
)

# Add is the row action to reach for, Retire and Discard All the ones not
# to hit by accident: Add has the accent outline, the other two turn red
# under the pointer. Apply alone is filled.
_BUTTON_ADD_STYLE = BUTTON_GHOST_STYLE + (
    f'QPushButton {{ color: {ACCENT_HOVER}; border-color: {ACCENT}; }}'
)
_BUTTON_DANGER_STYLE = BUTTON_GHOST_STYLE + (
    f'QPushButton:hover {{ color: {DANGER}; border-color: {DANGER}; }}'
)

if TYPE_CHECKING:
    from .entity_editor import _SheetPane


def _brush(color, alpha: int = 255) -> QBrush:
    c = QColor(color)
    c.setAlpha(alpha)
    return QBrush(c)


# The Database Editor's colours (config_editor/json_editor/items.py:
# INHERITED_FG, OVERRIDE_FG), so a value reads the same in both windows.
_INHERITED = _brush(QColor(95, 95, 100))
_OVERRIDE = _brush(QColor(220, 220, 225))
_PENDING = _brush(WARNING, 46)
_NEW = _brush(SUCCESS, 46)
_RED = _brush(DANGER)
_GREEN = _brush(SUCCESS)
_AMBER = _brush(WARNING)
# Selection is an outline, not a fill: amber, green and red already say
# what Apply does with a cell, and Houdini's amber highlight hid that.
_SELECTED_FILL = _brush(TEXT_PRIMARY, 22)
_SELECTED_EDGE = QColor(TEXT_PRIMARY)
# The current cell's whole row, fainter than a selection, so the eye can
# follow it across a wide sheet.
_CURRENT_ROW_FILL = _brush(TEXT_PRIMARY, 16)


def _font(bold: bool = False, italic: bool = False, strike: bool = False) -> QFont:
    font = QFont()
    font.setBold(bold)
    font.setItalic(italic)
    font.setStrikeOut(strike)
    return font


_BOLD = _font(bold=True)
_STRUCK = _font(strike=True)


def _hint(text: str = '') -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(_HINT_STYLE)
    return label


def _button(
    text: str, slot, primary: bool = False, tip: str = '', style: str = '',
) -> QPushButton:
    button = QPushButton(text)
    # A dialog's push buttons press on Enter when they are "auto default":
    # Enter in the sheet must never press Apply.
    button.setAutoDefault(False)
    button.setStyleSheet(style or (BUTTON_PRIMARY_STYLE if primary else BUTTON_GHOST_STYLE))
    # clicked sends its ``checked`` flag, which a slot with an optional
    # argument (add_dialog's group) would take as that argument.
    button.clicked.connect(lambda: slot())
    if tip:
        button.setToolTip(tip)
    return button


def _plurals(word: str) -> str:
    if word.endswith('y') and word[-2:-1] not in 'aeiou':
        return word[:-1] + 'ies'
    return word + 's'


def _plural(count: int, word: str) -> str:
    return f'{count} {word if count == 1 else _plurals(word)}'


def root_label(value) -> str:
    """A Root value as the sheet shows it: its path without ``scenes:/``."""
    if value is None:
        return ''
    if not isinstance(value, str):
        return '(old asset list)'
    return es.root_path(value)


def _short(names: list[str], limit: int = 6) -> str:
    return ', '.join(names[:limit]) + (f' and {len(names) - limit} more' if len(names) > limit else '')


class _Table(QTableView):
    """The Entity Editor's table: Shift+wheel (or the wheel when nothing
    scrolls vertically) scrolls sideways, and a click does not scroll the
    cell it lands on away from the pointer (#30, ``table_scroll``).
    :meth:`freeze_columns` keeps its leading columns in view (#40)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # The cell last clicked, until a key moves on. Qt scrolls to a
        # clicked cell on a timer after the press (to tell a double click
        # apart), so "during the press" is not enough.
        self._clicked = QPersistentModelIndex()
        self._frozen: QTableView | None = None
        self._frozen_count = 0
        # Frozen columns not shown at all (a flat kind's group column).
        self._frozen_hidden: set[int] = set()

    # ── Frozen leading columns (#40) ──────────────────────

    def freeze_columns(self, count: int) -> None:
        """Keep the first ``count`` columns in view while the rest scroll
        sideways. Call once the model, delegate, selection and edit
        settings are set: they are shared with, or copied to, a second
        view of those columns drawn over this one's left edge. Keys stay
        with this table; a frozen cell is edited in the frozen view."""
        frozen = self._frozen = QTableView(self)
        self._frozen_count = count
        frozen.setObjectName('frozen_columns')
        frozen.setModel(self.model())
        frozen.setSelectionModel(self.selectionModel())
        frozen.setItemDelegate(self.itemDelegate())
        frozen.setSelectionBehavior(self.selectionBehavior())
        frozen.setSelectionMode(self.selectionMode())
        frozen.setEditTriggers(self.editTriggers())
        frozen.setFocusPolicy(Qt.NoFocus)
        frozen.verticalHeader().setVisible(False)
        frozen.verticalHeader().setDefaultSectionSize(
            self.verticalHeader().defaultSectionSize())
        frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        frozen.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        frozen.setVerticalScrollMode(self.verticalScrollMode())
        frozen.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        frozen.horizontalHeader().setStretchLastSection(False)
        # No frame and no stylesheet of its own: it must look like the
        # table it lies on, whatever palette or app stylesheet styles that.
        frozen.setFrameShape(QFrame.NoFrame)
        frozen.setContextMenuPolicy(Qt.CustomContextMenu)
        frozen.customContextMenuRequested.connect(self.customContextMenuRequested)
        frozen.clicked.connect(self.clicked)
        # One vertical position, one set of widths, whichever view moved.
        frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.verticalScrollBar().valueChanged.connect(frozen.verticalScrollBar().setValue)
        self.horizontalHeader().sectionResized.connect(self._main_section_resized)
        frozen.horizontalHeader().sectionResized.connect(self._frozen_section_resized)
        self.verticalHeader().sectionResized.connect(
            lambda row, _old, new: frozen.setRowHeight(row, new))
        self.horizontalHeader().geometriesChanged.connect(self._place_frozen)
        for signal in (
            self.model().modelReset, self.model().layoutChanged,
            self.model().columnsInserted, self.model().columnsRemoved,
        ):
            signal.connect(self._sync_frozen_columns)
        self.selectionModel().currentRowChanged.connect(
            lambda *_a: frozen.viewport().update())
        self._sync_frozen_columns()
        frozen.show()

    def frozen_view(self) -> QTableView | None:
        return self._frozen

    def frozen_width(self) -> int:
        """The width the frozen columns cover, 0 when none are frozen."""
        return sum(self.columnWidth(i) for i in range(self._frozen_count))

    def hide_frozen_column(self, column: int) -> None:
        """Hide frozen ``column`` in both views, for good (a flat kind's
        group column): the frozen width then leaves it out."""
        self._frozen_hidden.add(column)
        self.setColumnHidden(column, True)
        if self._frozen is not None:
            self._sync_frozen_columns()

    def _sync_frozen_columns(self, *_args) -> None:
        frozen = self._frozen
        for column in range(self.model().columnCount()):
            frozen.setColumnHidden(
                column, column >= self._frozen_count or column in self._frozen_hidden,
            )
            if column < self._frozen_count:
                frozen.setColumnWidth(column, self.columnWidth(column))
        self._place_frozen()

    def _main_section_resized(self, column: int, _old: int, new: int) -> None:
        if column < self._frozen_count:
            self._frozen.setColumnWidth(column, new)
            self._place_frozen()

    def _frozen_section_resized(self, column: int, _old: int, new: int) -> None:
        # The frozen header covers this table's own: a drag there is the
        # artist resizing the column.
        if column < self._frozen_count and self.columnWidth(column) != new:
            self.setColumnWidth(column, new)

    def _place_frozen(self) -> None:
        if self._frozen is None:
            return
        frame = self.frameWidth()
        self._frozen.setGeometry(
            frame, frame, self.frozen_width(),
            self.viewport().height() + self.horizontalHeader().height(),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._place_frozen()

    def edit(self, index, trigger=None, event=None) -> bool:  # noqa: A003 (Qt)
        # A frozen cell's editor must open in the frozen view: in this one
        # it would open underneath it.
        if trigger is None:
            return super().edit(index)
        if (
            self._frozen is not None and index.isValid()
            and index.column() < self._frozen_count
            and trigger != QAbstractItemView.NoEditTriggers
        ):
            return self._frozen.edit(index, trigger, event)
        return super().edit(index, trigger, event)

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt)
        bar = self.horizontalScrollBar()
        steps = horizontal_wheel_steps(
            bool(event.modifiers() & Qt.ShiftModifier),
            self.verticalScrollBar().maximum(),
            event.angleDelta().y(), event.angleDelta().x(),
            bar.singleStep(),
        )
        if steps and bar.maximum() > 0:
            bar.setValue(bar.value() + steps)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt)
        self._clicked = QPersistentModelIndex(self.indexAt(event.position().toPoint()))
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        self._clicked = QPersistentModelIndex()
        super().keyPressEvent(event)

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible) -> None:  # noqa: N802 (Qt)
        rect = self.visualRect(index)
        clicked = self._clicked.isValid() and QModelIndex(self._clicked) == index
        if keep_view_on_click(rect.left(), rect.right(), self.viewport().width(), clicked):
            return
        if not self._frozen_count:
            super().scrollTo(index, hint)
            return
        bar = self.horizontalScrollBar()
        if not scrolls_sideways(index.column(), self._frozen_count):
            # Always in view (the frozen columns): only the rows scroll.
            value = bar.value()
            super().scrollTo(index, hint)
            bar.setValue(value)
            return
        super().scrollTo(index, hint)
        # Qt may have put the cell at the left edge, under the frozen columns.
        per_pixel = self.horizontalScrollMode() == QAbstractItemView.ScrollPerPixel
        for _ in range(self.model().columnCount() + 1):
            hidden = hidden_under_frozen(self.visualRect(index).left(), self.frozen_width())
            if not hidden or bar.value() <= bar.minimum():
                break
            bar.setValue(bar.value() - (hidden if per_pixel else 1))


class _Delegate(QStyledItemDelegate):
    """Line edits, a Root dropdown, the Assets dialog, and every commit
    routed to the pane, which writes it into each selected cell of the
    column."""

    def __init__(self, pane: '_SheetPane') -> None:
        super().__init__(pane)
        self._pane = pane

    def paint(self, painter, option, index) -> None:
        # The cell as if unselected, so its tint still shows, then a light
        # wash and an outline for the selection. The view has already
        # filled a selected cell with the highlight colour, so cover that
        # with the cell's own base first.
        selected = bool(option.state & QStyle.State_Selected)
        plain = QStyleOptionViewItem(option)
        plain.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
        if selected:
            alternate = option.features & QStyleOptionViewItem.Alternate
            painter.fillRect(option.rect, option.palette.alternateBase()
                             if alternate else option.palette.base())
        super().paint(painter, plain, index)
        view = option.widget
        if view is not None and view.currentIndex().row() == index.row():
            painter.fillRect(option.rect, _CURRENT_ROW_FILL)
        if not selected:
            return
        painter.save()
        painter.fillRect(option.rect, _SELECTED_FILL)
        painter.setPen(QPen(_SELECTED_EDGE, 1))
        painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
        painter.restore()

    def createEditor(self, parent, option, index):
        column = self._pane.columns[index.column()]
        if column == es.ASSETS:
            # Not a cell editor: the Root's asset list has its own dialog,
            # opened once the view has finished starting the edit.
            QTimer.singleShot(0, lambda i=QModelIndex(index): self._pane.edit_assets(i))
            return None
        if column == es.MULTIS:
            QTimer.singleShot(0, lambda i=QModelIndex(index): self._pane.edit_multis(i))
            return None
        if column == es.LAYERS:
            # Its own editor (a list, a prim picker); typed and pasted text
            # still reaches the cell through the pane.
            QTimer.singleShot(0, lambda i=QModelIndex(index): self._pane.edit_layers(i))
            return None
        if column == es.DEPARTMENTS_OFF:
            # Ticks over the shot pool; typed and pasted names still reach
            # the cell through the pane.
            QTimer.singleShot(0, lambda i=QModelIndex(index): self._pane.edit_departments_off(i))
            return None
        if column == es.ROOT:
            combo = QComboBox(parent)
            combo.addItem('Inherit', INHERIT)
            for uri in self._pane.sheet.roots():
                combo.addItem(root_label(uri), uri)
            # A dropdown commits when an entry is picked, not on focus-out.
            combo.activated.connect(lambda _i, c=combo: (
                self.commitData.emit(c), self.closeEditor.emit(c),
            ))
            return combo
        return QLineEdit(parent)

    def setEditorData(self, editor, index) -> None:
        if isinstance(editor, QComboBox):
            found = editor.findData(index.data(Qt.EditRole) or INHERIT)
            editor.setCurrentIndex(max(found, 0))
            return
        editor.setText(index.data(Qt.EditRole) or '')

    def setModelData(self, editor, model, index) -> None:
        if isinstance(editor, QComboBox):
            value = editor.currentData()
        else:
            value = editor.text()
        self._pane.commit(index, value)


def _side_panel(content: QWidget) -> QScrollArea:
    """A tab's side panel, right of its table as the asset browser's detail
    panel is: ``content`` in a scroll area, so a short window still reaches
    all of it. The window's Details button hides it."""
    area = QScrollArea()
    area.setObjectName('side_panel')
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(content)
    area.setMinimumWidth(scaled(240))
    return area
