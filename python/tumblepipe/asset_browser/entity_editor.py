"""The Entity Editor: a project's assets and shots as spreadsheets.

**Entity Editor...** in the Asset Browser opens one window per project,
with an **Assets** and a **Shots** tab laid out like Farm Submit's. Every
entity is a row: category or sequence, name, start, end, length, pre/post
roll and fps, plus Animatable for assets and the Root and the assets it
imports for shots. The sheet edits them the way a spreadsheet does: select
cells and type to set them all, copy and paste blocks, fill down, shift
frame ranges, add rows from a name pattern or a pasted list, rename,
retire, undo.

Values look the way the Database Editor shows them: inherited values are
grey, values set on the entity itself are bright and bold, and edits not
applied yet are tinted. Nothing is written until **Apply**, which writes
both tabs.

The model, its checks and the writes are
:mod:`tumblepipe.config.entity_sheet`. This module is only the widgets: it
takes everything project-specific through :class:`EntityEditorSource`,
never touches ``hou``, and runs headless
(``scripts/verify_entity_editor.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelectionModel, QModelIndex, QSortFilterProxyModel,
    Qt, QTimer,
)
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QSpinBox, QStackedWidget, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTabBar, QTableView,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es
from tumblepipe.config import shot_batch
from tumblepipe.util.uri import Uri
from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_HOVER, BG_DARK, BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE,
    COMBO_STYLE, DANGER, FONT_FAMILY, FONT_SMALL, SUCCESS, TEXT_DIM,
    TEXT_PRIMARY, WARNING, scaled,
)

STATUS = 'status'
_TIMING = (es.FRAME_START, es.FRAME_END, es.LENGTH, es.ROLL_START, es.ROLL_END, es.FPS)
# Status sits beside the name it is about; the last column takes the spare
# width.
COLUMNS = {
    'assets': (es.GROUP, es.NAME, STATUS, *_TIMING, es.ANIMATABLE),
    'shots': (es.GROUP, es.NAME, STATUS, *_TIMING, es.ROOT, es.ASSETS),
}
_WIDTHS = {
    es.GROUP: 100, es.NAME: 110, STATUS: 150, es.FRAME_START: 64,
    es.FRAME_END: 64, es.LENGTH: 64, es.ROLL_START: 70, es.ROLL_END: 70,
    es.FPS: 56, es.ANIMATABLE: 90, es.ROOT: 120, es.ASSETS: 220,
}
_NUMBERS = frozenset({
    es.FRAME_START, es.FRAME_END, es.LENGTH, es.ROLL_START, es.ROLL_END, es.FPS,
})

#: The Root editor's "Inherit" entry, and an empty pasted value cell.
INHERIT = '__inherit__'
_NEW_ROOT = '__new_root__'

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


def _over(brush: QBrush, base: str) -> str:
    """``brush`` painted over ``base``, as a hex colour for rich text."""
    top, under = brush.color(), QColor(base)
    a = top.alphaF()
    mixed = QColor(*(
        round(t * a + u * (1 - a)) for t, u in (
            (top.red(), under.red()), (top.green(), under.green()),
            (top.blue(), under.blue()),
        )
    ))
    return mixed.name()


def _legend(kind: es.Kind) -> str:
    """One line keyed by sample cells drawn the way the sheet draws them."""
    def sample(style: str) -> str:
        return f'<span style="{style}">&nbsp;1001&nbsp;</span>'
    return (
        f'{sample(f"color: {_INHERITED.color().name()}")} inherited'
        f' &nbsp; {sample(f"color: {_OVERRIDE.color().name()}; font-weight: bold")}'
        f' set on the {kind.item}'
        f' &nbsp; {sample(f"color: {_OVERRIDE.color().name()}; font-weight: bold; background-color: {_over(_PENDING, BG_DARK)}")}'
        ' waiting for Apply'
    )


_LEGEND_TIP = (
    'Select cells and type to set a value on every selected {item}. Delete '
    'puts a value back to inheriting from the {group} or the project. '
    'New rows have green names; a row to retire is struck through.'
)


def _font(bold: bool = False, italic: bool = False, strike: bool = False) -> QFont:
    font = QFont()
    font.setBold(bold)
    font.setItalic(italic)
    font.setStrikeOut(strike)
    return font


_BOLD = _font(bold=True)
_ITALIC = _font(italic=True)
_STRUCK = _font(strike=True)


@dataclass(frozen=True)
class EntityEditorSource:
    """What the window needs from the project.

    ``retire`` and ``rename`` are ``pipe.entity_files.delete_entity`` and
    ``rename_entity`` on this project; ``rename_blocker`` says why an
    entity cannot be renamed ('' if it can). ``on_applied`` hears that the
    database changed.
    """
    project: str
    config: object
    retire: Callable[[Uri], object]
    rename: Callable[[Uri, str], object]
    rename_blocker: Callable[[Uri], str]
    on_applied: Callable[[], None]


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


def _spin(value: int, low: int = 0, high: int = 999_999) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(value)
    return spin


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


def assets_label(assets) -> str:
    return ', '.join(entry.label for entry in assets)


def cell_text(value, column: str) -> str:
    if column == es.ASSETS:
        return assets_label(value or ())
    return es.format_value(column, value)


def _short(names: list[str], limit: int = 6) -> str:
    return ', '.join(names[:limit]) + (f' and {len(names) - limit} more' if len(names) > limit else '')


# ---------------------------------------------------------------------------
# Model, filter, delegate
# ---------------------------------------------------------------------------


class _SheetModel(QAbstractTableModel):
    """The sheet as a table; row ``i`` is the sheet's ``i``-th row."""

    def __init__(self, pane: '_SheetPane') -> None:
        super().__init__()
        self._pane = pane
        self.sheet = pane.sheet
        self.columns = COLUMNS[self.sheet.kind.key]
        self._ids = [row.id for row in self.sheet.rows]

    def set_sheet(self, sheet: es.Sheet) -> None:
        self.beginResetModel()
        self.sheet = sheet
        self._ids = [row.id for row in sheet.rows]
        self.endResetModel()

    def refresh(self) -> bool:
        """Show the sheet's current state; True when rows came, went or
        moved (the view was reset)."""
        ids = [row.id for row in self.sheet.rows]
        if ids != self._ids:
            self.beginResetModel()
            self._ids = ids
            self.endResetModel()
            return True
        if ids:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(ids) - 1, len(self.columns) - 1),
            )
        return False

    def row_at(self, source_row: int) -> es.Row:
        return self.sheet.row(self._ids[source_row])

    def _shown(self, index) -> es.Row | None:
        """The row ``index`` shows, or None when the sheet has just dropped
        it (a new row retired) and the view has not caught up yet."""
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        try:
            return self.row_at(index.row())
        except KeyError:
            return None

    def id_at(self, source_row: int) -> int:
        # The id the view shows, even when the sheet has just dropped it.
        return self._ids[source_row]

    def source_row(self, row_id: int) -> int:
        return self._ids.index(row_id) if row_id in self._ids else -1

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            column = self.columns[section]
            return 'Status' if column == STATUS else self.sheet.kind.label(column)
        return None

    @staticmethod
    def editable(row: es.Row, column: str) -> bool:
        if row.retire or column == STATUS:
            return False
        if column == es.GROUP:
            return row.is_new
        return True

    def flags(self, index):
        row = self._shown(index)
        if row is None:
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        column = self.columns[index.column()]
        if self.editable(row, column):
            flags |= Qt.ItemIsEditable
            if column == es.ANIMATABLE:
                flags |= Qt.ItemIsUserCheckable
        return flags

    def status(self, row: es.Row) -> tuple[str, QBrush]:
        problem = self.sheet.problems().get(row.id)
        if problem:
            return problem, _RED
        if row.retire:
            return 'Retire', _RED
        if row.is_new:
            if row.group not in self.sheet.base.groups:
                return f'New, in a new {self.sheet.kind.group}', _GREEN
            return 'New', _GREEN
        parts = []
        if row.renamed:
            parts.append(f'Renamed from {row.origin.name}')
        if row.edits:
            parts.append('Changed')
        root = self.sheet.value(row, es.ROOT) if es.ROOT in self.columns else None
        if isinstance(root, str) and self.sheet.root_pending(root):
            parts.append('Root assets changed')
        return ', '.join(parts), _AMBER

    def data(self, index, role=Qt.DisplayRole):
        row = self._shown(index)
        if row is None:
            return None
        column = self.columns[index.column()]
        sheet = self.sheet
        if role in (Qt.DisplayRole, Qt.EditRole):
            if column == STATUS:
                return self.status(row)[0]
            if column == es.GROUP:
                return row.group
            if column == es.NAME:
                return row.name
            value = sheet.value(row, column)
            if role == Qt.EditRole and column == es.ROOT:
                return value if isinstance(value, str) else ''
            # A shot with no Root shows an empty Assets cell; its tooltip
            # says why and how to give it assets.
            return cell_text(value, column)
        if role == Qt.CheckStateRole and column == es.ANIMATABLE:
            return Qt.Checked if sheet.value(row, column) else Qt.Unchecked
        if column == STATUS:
            return self.status(row)[1] if role == Qt.ForegroundRole else (
                self._tooltip(row, column) if role == Qt.ToolTipRole else None
            )
        valued = column not in (es.GROUP, es.NAME)
        state = sheet.state(row, column) if valued else None
        if role == Qt.ForegroundRole:
            if row.retire or state in (es.INHERITED, es.CLEARED):
                return _INHERITED
            if state in (es.OWN, es.SET):
                return _OVERRIDE
            return None
        if role == Qt.FontRole:
            if row.retire:
                return _STRUCK
            if state in (es.OWN, es.SET):
                return _BOLD
            if state == es.CLEARED:
                return _ITALIC
            return None
        if role == Qt.BackgroundRole:
            if row.retire:
                return None
            if state in (es.SET, es.CLEARED):
                return _PENDING
            if column in (es.GROUP, es.NAME) and row.is_new:
                return _NEW
            if column == es.NAME and row.renamed:
                return _PENDING
            return None
        if role == Qt.TextAlignmentRole and column in _NUMBERS:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return self._tooltip(row, column)
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        # A ticked Animatable box; typed values arrive through the delegate.
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        checked = value in (Qt.Checked, getattr(Qt.Checked, 'value', 2), 2)
        self._pane.commit(index_of_proxy(self._pane, index), checked)
        return True

    def _tooltip(self, row: es.Row, column: str) -> str:
        sheet = self.sheet
        kind = sheet.kind
        if column == STATUS:
            return self.status(row)[0]
        if row.retire:
            return (
                f'{row.origin.group}/{row.origin.name} is retired on Apply: '
                'it leaves the project and its folders move to _deleted/. '
                'Revert or Restore keeps it.'
            )
        if column == es.GROUP:
            return (
                f'The {kind.group} the new {kind.item} is created in.' if row.is_new
                else f'A {kind.item} cannot move to another {kind.group}.'
            )
        if column == es.NAME:
            return f'Renamed from {row.origin.name} on Apply.' if row.renamed else ''
        if column == es.LENGTH:
            return 'Frames from start to end. Typing a length moves the end frame.'
        if column == es.ASSETS:
            return self._assets_tooltip(row)
        state = sheet.state(row, column)
        text = cell_text(sheet.value(row, column), column) or 'nothing'
        source = sheet.source(row, column) or 'the project'
        if state == es.INHERITED:
            return f'{text}, inherited from {source}. Type a value to set it on this {kind.item}.'
        if state == es.CLEARED:
            return f'Goes back to inheriting {text} from {source} on Apply.'
        if state == es.SET and row.origin is not None:
            before = row.origin.own.get(column, sheet.inherited(row).values.get(column))
            return f'Set to {text} on Apply (now {cell_text(before, column) or "nothing"}).'
        if state == es.SET:
            return f'{text}, set on the new {kind.item}.'
        inherited = cell_text(sheet.inherited(row).values.get(column), column) or 'nothing'
        return (
            f'{text}, set on this {kind.item}. Delete puts it back to inheriting '
            f'{inherited} from {source}.'
        )

    def _assets_tooltip(self, row: es.Row) -> str:
        sheet = self.sheet
        root = sheet.value(row, es.ROOT)
        if not isinstance(root, str):
            return (
                'No Root: the shot imports no assets. Double-click to pick '
                'assets; that gives it a Root of its own.'
            )
        lines = [f'The assets of Root {root_label(root)}:']
        own = sheet.root_own(root)
        lines.append('  ' + (assets_label(own) or 'none of its own'))
        for parent in es._root_parents(root):
            parent_own = sheet.root_own(parent)
            if parent_own:
                lines.append(f'From parent Root {root_label(parent)}: {assets_label(parent_own)}')
        users = [r for r in sheet.root_users(root) if r is not row]
        if users:
            lines.append(f'{_plural(len(users), "other shot")} use this Root too.')
        if sheet.root_pending(root):
            lines.append('Changed; saved on Apply.')
        lines.append('Double-click to change.')
        return '\n'.join(lines)


def index_of_proxy(pane: '_SheetPane', source_index):
    return pane.proxy.mapFromSource(source_index)


class _Filter(QSortFilterProxyModel):
    """Rows of one group, and names containing some text."""

    def __init__(self) -> None:
        super().__init__()
        self.group = ''
        self.text = ''

    def set_filter(self, group: str, text: str) -> None:
        self.group = group
        self.text = text.strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, parent) -> bool:
        row = self.sourceModel().row_at(source_row)
        if self.group and row.group != self.group:
            return False
        return not self.text or self.text in row.name.casefold()


class _Delegate(QStyledItemDelegate):
    """Line edits, a Root dropdown, the Assets dialog, and every commit
    routed to the pane, which writes it into each selected cell of the
    column."""

    def __init__(self, pane: '_SheetPane') -> None:
        super().__init__(pane)
        self._pane = pane

    def paint(self, painter, option, index) -> None:
        # The cell as if unselected, so its tint still shows, then a light
        # wash and an outline for the selection.
        selected = bool(option.state & QStyle.State_Selected)
        plain = QStyleOptionViewItem(option)
        plain.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
        super().paint(painter, plain, index)
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


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class _AssetPicker(QWidget):
    """A filterable table of assets to tick, each with an instance count.
    ``fixed`` entries (a parent Root's) show ticked, grey and locked."""

    def __init__(self, choices: list[str], own=(), fixed=()) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText('Filter assets')
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._on_filter)
        layout.addWidget(self._filter)
        table = self.table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(['Asset', 'Instances', 'From'])
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(table, 1)
        self._rows: list[tuple[QTableWidgetItem, QSpinBox, str, bool]] = []
        listed = set()
        for entry in own:
            self._add(entry.asset, entry.instances, entry.channel, True, '')
            listed.add(entry.asset)
        for entry, parent in fixed:
            self._add(entry.asset, entry.instances, entry.channel, True,
                      root_label(parent), fixed=True)
        for asset in choices:
            if asset not in listed:
                self._add(asset, 1, es.DEFAULT_CHANNEL, False, '')
        table.itemChanged.connect(lambda *_a: self.changed())

    def _add(self, asset, instances, channel, checked, origin, fixed=False) -> None:
        table = self.table
        r = table.rowCount()
        table.insertRow(r)
        label = asset.removeprefix('entity:/assets/')
        if channel != es.DEFAULT_CHANNEL:
            label += f' ({channel})'
        item = QTableWidgetItem(label)
        item.setData(Qt.UserRole, asset)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        spin = _spin(instances, 1, 9999)
        if fixed:
            item.setFlags(Qt.ItemIsEnabled)
            item.setForeground(_INHERITED)
            spin.setEnabled(False)
        else:
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            spin.valueChanged.connect(lambda *_a: self.changed())
        table.setItem(r, 0, item)
        table.setCellWidget(r, 1, spin)
        origin_item = QTableWidgetItem(origin)
        origin_item.setFlags(Qt.ItemIsEnabled)
        origin_item.setForeground(_INHERITED)
        table.setItem(r, 2, origin_item)
        self._rows.append((item, spin, channel, fixed))

    def changed(self) -> None:
        """Overridden by the owner (a plain callback, no signal needed)."""

    def _on_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for r, (item, *_rest) in enumerate(self._rows):
            self.table.setRowHidden(r, bool(needle) and needle not in item.text().casefold())

    def set_checked(self, asset: str, checked: bool = True, instances: int = 1) -> None:
        for item, spin, _channel, fixed in self._rows:
            if not fixed and item.data(Qt.UserRole) == asset:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                spin.setValue(instances)
                return
        raise KeyError(asset)

    def own(self) -> tuple[es.RootAsset, ...]:
        """The ticked entries that are not a parent's, in list order."""
        return tuple(
            es.RootAsset(item.data(Qt.UserRole), spin.value(), channel)
            for item, spin, channel, fixed in self._rows
            if not fixed and item.checkState() == Qt.Checked
        )


class NewRootDialog(QDialog):
    """Name a Root and tick its assets. It joins the sheet; Apply creates
    it, if a shot uses it."""

    def __init__(self, pane: '_SheetPane', name: str = '', parent=None) -> None:
        super().__init__(parent or pane)
        self._sheet = pane.sheet
        self.setWindowTitle('New Root')
        self.resize(scaled(420), scaled(480))
        layout = QFormLayout(self)
        self._name = QLineEdit(name)
        self._name.setPlaceholderText('e.g. forest or outdoor/forest')
        self._name.textChanged.connect(self._update)
        layout.addRow('Name', self._name)
        self.picker = _AssetPicker(pane.window_.asset_choices())
        self.picker.changed = self._update
        layout.addRow(self.picker)
        layout.addRow(_hint(
            'Every shot using the Root imports its assets. The Root is '
            'created on Apply.'
        ))
        self._problem = _hint()
        layout.addRow(self._problem)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._create = _button('Add Root', self.accept, primary=True)
        self._create.setDefault(True)
        buttons.addWidget(self._create)
        layout.addRow(buttons)
        self._update()

    def path(self) -> str:
        return '/'.join(s.strip() for s in self._name.text().strip().split('/'))

    def problem(self) -> str:
        return self._sheet.root_problem(self.path()) or (
            '' if self.picker.own() else 'Tick its assets'
        )

    def _update(self, *_args) -> None:
        problem = self.problem()
        self._problem.setText(problem)
        self._create.setEnabled(not problem)


class RootAssetsDialog(QDialog):
    """What one shot imports: its Root's asset list, ticked.

    The list is the Root's, shared by every shot using it, so the dialog
    says who else uses it and offers **Give <shot> its own Root**: a new
    Root beside the current one (inheriting the same parents) holding the
    ticked list. ``action`` is ``('root', uri, assets)`` or ``('own',
    path, assets)`` after OK.
    """

    def __init__(self, pane: '_SheetPane', row: es.Row) -> None:
        super().__init__(pane)
        sheet = self._sheet = pane.sheet
        self.action: tuple | None = None
        root = sheet.value(row, es.ROOT)
        self._root = root if isinstance(root, str) and sheet.root_exists(root) else None
        self.setWindowTitle(f'Assets of {row.group}/{row.name}')
        self.resize(scaled(520), scaled(560))
        layout = QVBoxLayout(self)

        others = [
            f'{r.group}/{r.name}' for r in sheet.root_users(self._root) if r is not row
        ] if self._root else []
        if self._root is None:
            intro = (
                f'{row.name} has no Root, so it imports no assets. Tick the '
                'assets it should import: it gets a Root of its own.'
            )
        elif others:
            intro = (
                f'{row.name} imports the assets of Root '
                f'<b>{root_label(self._root)}</b>, which '
                f'{_plural(len(others), "other shot")} use too: {_short(others)}. '
                'Changing the Root changes what they import as well.'
            )
        else:
            intro = (
                f'{row.name} imports the assets of Root '
                f'<b>{root_label(self._root)}</b>. No other shot uses it.'
            )
        label = QLabel(intro)
        label.setWordWrap(True)
        layout.addWidget(label)

        fixed = sheet.root_inherited(self._root) if self._root else []
        self.picker = _AssetPicker(
            pane.window_.asset_choices(),
            own=sheet.root_own(self._root) if self._root else (),
            fixed=fixed,
        )
        self.picker.changed = self._update
        layout.addWidget(self.picker, 1)
        if fixed:
            layout.addWidget(_hint(
                'Grey rows come from a parent Root; change that Root to '
                'change them.'
            ))

        own_row = QHBoxLayout()
        own_row.addWidget(QLabel('Own Root'))
        parent = self._root.rsplit('/', 1)[0] if self._root and '/' in es.root_path(self._root) else ''
        suggested = f'{row.group}_{row.name}'
        self._own_name = QLineEdit(
            f'{es.root_path(parent)}/{suggested}' if parent else suggested
        )
        self._own_name.setToolTip(
            'Name of the new Root for this shot. Beside its current Root, it '
            'keeps that Root\'s parents (the grey rows).'
        )
        self._own_name.textChanged.connect(self._update)
        own_row.addWidget(self._own_name, 1)
        layout.addLayout(own_row)
        self._problem = _hint()
        layout.addWidget(self._problem)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._own = _button(f'Give {row.name} its own Root', self._on_own)
        buttons.addWidget(self._own)
        self._change = None
        if self._root is not None:
            self._change = _button(
                f'Change Root {root_label(self._root)}', self._on_change,
                primary=not others,
            )
            buttons.addWidget(self._change)
        (self._change or self._own).setDefault(True)
        if self._root is None or others:
            self._own.setStyleSheet(BUTTON_PRIMARY_STYLE)
        layout.addLayout(buttons)
        self._update()

    def _update(self, *_args) -> None:
        problem = self._sheet.root_problem(self._own_name.text().strip())
        self._own.setEnabled(not problem)
        same = self._root is not None and self.picker.own() == self._sheet.root_own(self._root)
        if self._change is not None:
            self._change.setEnabled(not same)
        self._problem.setText(f'Own Root: {problem}' if problem else '')

    def _on_change(self) -> None:
        self.action = ('root', self._root, self.picker.own())
        self.accept()

    def _on_own(self) -> None:
        self.action = ('own', self._own_name.text().strip(), self.picker.own())
        self.accept()


class AddShotsDialog(QDialog):
    """New rows from a name pattern: SH### from 10 by 10, a frame range,
    and a Root. The rows only join the sheet; Apply creates them."""

    def __init__(self, pane: '_SheetPane', sequence: str = '') -> None:
        super().__init__(pane)
        self._pane = pane
        self._sheet = pane.sheet
        self.setWindowTitle('Add Shots')
        self.resize(scaled(520), 0)
        form = QFormLayout(self)

        self._sequence = QComboBox()
        self._sequence.setEditable(True)
        self._sequence.setStyleSheet(COMBO_STYLE)
        self._sequence.addItems(self._sheet.groups())
        self._sequence.lineEdit().setPlaceholderText('Pick or type a sequence')
        self._sequence.setCurrentText(sequence)
        self._sequence.currentTextChanged.connect(self._on_sequence_changed)
        self._sequence_note = _hint()
        row = QHBoxLayout()
        row.addWidget(self._sequence, 1)
        row.addWidget(self._sequence_note, 1)
        form.addRow('Sequence', row)

        names = QHBoxLayout()
        self._pattern = QLineEdit(shot_batch.DEFAULT_PATTERN)
        self._pattern.setToolTip(
            'The shot name; # is the number, zero-padded to as many digits '
            'as there are #.'
        )
        self._first = _spin(10)
        self._step = _spin(10, 1)
        self._count = _spin(5, 1, 999)
        names.addWidget(self._pattern, 1)
        for label, widget in (('from', self._first), ('step', self._step), ('count', self._count)):
            names.addWidget(QLabel(label))
            names.addWidget(widget)
        form.addRow('Names', names)

        frames = QHBoxLayout()
        self._frame_start = _spin(1001, -999_999)
        self._frame_end = _spin(1100, -999_999)
        self._length = _hint()
        frames.addWidget(self._frame_start)
        frames.addWidget(QLabel('to'))
        frames.addWidget(self._frame_end)
        frames.addWidget(self._length, 1)
        form.addRow('Frames', frames)

        self._root = QComboBox()
        self._root.setStyleSheet(COMBO_STYLE)
        self._fill_roots()
        self._root.activated.connect(self._on_root_picked)
        form.addRow('Root', self._root)

        self._preview = _hint()
        form.addRow(self._preview)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._add = _button('Add', self.accept, primary=True)
        self._add.setDefault(True)
        self._add.setMinimumWidth(scaled(110))
        buttons.addWidget(self._add)
        form.addRow(buttons)

        for widget in (self._first, self._step, self._count,
                       self._frame_start, self._frame_end):
            widget.valueChanged.connect(self._update)
        self._pattern.textChanged.connect(self._update)
        self._on_sequence_changed()

    def _fill_roots(self, select: str | None = None) -> None:
        self._root.clear()
        self._root.addItem('Inherit from the sequence', INHERIT)
        for uri in self._sheet.roots():
            self._root.addItem(root_label(uri), uri)
        self._root.addItem('New Root from assets…', _NEW_ROOT)
        if select is not None:
            self._root.setCurrentIndex(max(self._root.findData(select), 0))

    def _on_root_picked(self, _index: int) -> None:
        if self._root.currentData() != _NEW_ROOT:
            return
        created = self._pane.new_root(self.sequence(), parent=self)
        self._fill_roots(select=created or INHERIT)

    def sequence(self) -> str:
        return self._sequence.currentText().strip()

    def _on_sequence_changed(self, *_args) -> None:
        sequence = self.sequence()
        existing = self._sheet.names_in(sequence) if sequence else set()
        if not sequence:
            self._sequence_note.setText('')
        elif sequence in self._sheet.base.groups:
            note = f'{_plural(len(existing), "shot")} already'
            if existing and shot_batch.guess_pattern(existing) is None:
                # Say so, or the new shots silently follow another scheme.
                note += ', named without a number pattern'
            self._sequence_note.setText(note)
        else:
            self._sequence_note.setText('New sequence, created with its first shot')
        pattern, first = shot_batch.suggest(existing, self._step.value())
        self._pattern.setText(pattern)
        self._first.setValue(first)
        inherited = self._sheet.base.inherited(sequence).values
        for spin, name in ((self._frame_start, es.FRAME_START), (self._frame_end, es.FRAME_END)):
            value = inherited.get(name)
            if isinstance(value, int):
                spin.setValue(value)
        self._update()

    def _names(self) -> list[str]:
        return shot_batch.expand_names(
            self._pattern.text().strip(), self._first.value(),
            self._step.value(), self._count.value(),
        )

    def _update(self, *_args) -> None:
        length = self._frame_end.value() - self._frame_start.value() + 1
        self._length.setText(f'{length} frames' if length > 0 else 'ends before it starts')
        try:
            names = self._names()
        except ValueError as exc:
            self._preview.setText(str(exc))
            self._add.setEnabled(False)
            return
        if not self.sequence():
            self._preview.setText('Pick a sequence first')
            self._add.setEnabled(False)
            return
        shown = ', '.join(names[:3]) + (f', … {names[-1]}' if len(names) > 3 else '')
        self._preview.setText(f'{shown}, frames {self._frame_start.value()}-{self._frame_end.value()}')
        self._add.setText(f'Add {_plural(len(names), "Shot")}')
        self._add.setEnabled(bool(names))

    def rows(self) -> list[tuple[str, str, dict]]:
        """The rows to add: (sequence, shot, values to pin)."""
        values = {
            es.FRAME_START: self._frame_start.value(),
            es.FRAME_END: self._frame_end.value(),
        }
        root = self._root.currentData()
        if root not in (INHERIT, _NEW_ROOT, None):
            values[es.ROOT] = root
        return [(self.sequence(), name, dict(values)) for name in self._names()]


class AddAssetsDialog(QDialog):
    """New asset rows: a category and names. Their timing is inherited
    until set in the sheet."""

    def __init__(self, pane: '_SheetPane', category: str = '') -> None:
        super().__init__(pane)
        self._sheet = pane.sheet
        self.setWindowTitle('Add Assets')
        form = QFormLayout(self)
        self._category = QComboBox()
        self._category.setEditable(True)
        self._category.setStyleSheet(COMBO_STYLE)
        self._category.addItems(self._sheet.groups())
        self._category.lineEdit().setPlaceholderText('Pick or type a category')
        self._category.setCurrentText(category)
        self._category.currentTextChanged.connect(self._update)
        form.addRow('Category', self._category)
        self._names = QLineEdit()
        self._names.setPlaceholderText('e.g. tree rock bush, or tree, rock, bush')
        self._names.textChanged.connect(self._update)
        form.addRow('Names', self._names)
        self._preview = _hint()
        form.addRow(self._preview)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._add = _button('Add', self.accept, primary=True)
        self._add.setDefault(True)
        self._add.setMinimumWidth(scaled(110))
        buttons.addWidget(self._add)
        form.addRow(buttons)
        self._update()
        # Its content's height: a height of 0 is raised to the window
        # system's minimum, which left a gap under the buttons.
        self.resize(scaled(480), self.sizeHint().height())

    def category(self) -> str:
        return self._category.currentText().strip()

    def names(self) -> list[str]:
        text = self._names.text().replace(',', ' ').replace(';', ' ')
        return list(dict.fromkeys(name for name in text.split() if name))

    def set_names(self, text: str) -> None:
        self._names.setText(text)

    def _update(self, *_args) -> None:
        names = self.names()
        category = self.category()
        if not category:
            self._preview.setText('Pick a category first')
        elif not names:
            self._preview.setText('Type the asset names')
        else:
            new = '' if category in self._sheet.base.groups else ' (a new category)'
            self._preview.setText(f'{_plural(len(names), "asset")} in {category}{new}')
        self._add.setText(f'Add {_plural(len(names), "Asset")}' if names else 'Add')
        self._add.setEnabled(bool(category and names))

    def rows(self) -> list[tuple[str, str, dict]]:
        return [(self.category(), name, {}) for name in self.names()]


# ---------------------------------------------------------------------------
# One tab: a kind's sheet
# ---------------------------------------------------------------------------


class _SheetPane(QWidget):
    """One tab of the window: a kind's filters, sheet and row actions."""

    def __init__(self, window: 'EntityEditorWindow', kind: es.Kind) -> None:
        super().__init__(window)
        self.window_ = window
        self.kind = kind
        self.columns = COLUMNS[kind.key]
        self._values = frozenset(
            c for c in self.columns if c in kind.fields or c == es.LENGTH
        )
        source = window.source
        self.sheet = es.Sheet(es.load(source.config, kind), source.rename_blocker)
        self._errors: list[str] = []
        self._build_ui()

    @property
    def noun(self) -> str:
        return self.kind.item

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        kind = self.kind
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))

        top = QHBoxLayout()
        top.addWidget(QLabel(kind.label(es.GROUP)))
        self._group_filter = QComboBox()
        self._group_filter.setStyleSheet(COMBO_STYLE)
        self._group_filter.setMinimumWidth(scaled(160))
        self._group_filter.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self._group_filter)
        self._search = QLineEdit()
        self._search.setPlaceholderText(f'Filter {kind.item}s')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search, 1)
        outer.addLayout(top)
        legend = _hint(_legend(kind))
        legend.setWordWrap(False)
        legend.setToolTip(_LEGEND_TIP.format(group=kind.group, item=kind.item))
        self.legend = legend
        outer.addWidget(legend)

        self.model = _SheetModel(self)
        self.proxy = _Filter()
        self.proxy.setSourceModel(self.model)
        table = self.table = QTableView()
        table.setModel(self.proxy)
        table.setItemDelegate(_Delegate(self))
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(scaled(24))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for i, column in enumerate(self.columns):
            if column in _WIDTHS:
                table.setColumnWidth(i, scaled(_WIDTHS[column]))
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._context_menu)
        outer.addWidget(table, 1)
        for keys, slot in (
            (QKeySequence.Copy, self._copy),
            (QKeySequence.Paste, self._paste),
            (QKeySequence.Delete, self._reset_cells),
            (QKeySequence('Ctrl+D'), self._fill_down),
        ):
            QShortcut(keys, table, slot, context=Qt.WidgetWithChildrenShortcut)

        actions = QHBoxLayout()
        plural = f'{kind.item.capitalize()}s'
        self.add_button = _button(
            f'Add {plural}…', self.add_dialog, style=_BUTTON_ADD_STYLE,
            tip=(
                'New shots from a name pattern (SH010, SH020, …) and a frame range.'
                if kind is es.SHOTS_KIND else 'New assets in a category.'
            ),
        )
        actions.addWidget(self.add_button)
        actions.addWidget(_button(
            f'Paste as New {plural}', self._paste_new,
            tip=f'Add the rows on the clipboard as new {kind.item}s: one per '
                f'line, as name; {kind.group}, name; name, start, end; or '
                f'{kind.group}, name, start, end.',
        ))
        # The edits to selected rows, kept apart from adding, with Retire
        # last: the one that removes.
        actions.addSpacing(scaled(24))
        actions.addWidget(_button(
            'Revert', self._revert_selected,
            tip='Drop the selected rows\' edits; new rows are removed.',
        ))
        self.retire_button = _button(
            'Retire', self._toggle_retire, style=_BUTTON_DANGER_STYLE,
            tip=f'Retire the selected {kind.item}s on Apply: they leave the '
                'project and their folders move to _deleted/. New rows are '
                'removed.',
        )
        actions.addWidget(self.retire_button)
        actions.addStretch()
        self.undo_button = _button('Undo', self.undo)
        self.redo_button = _button('Redo', self.redo)
        actions.addWidget(self.undo_button)
        actions.addWidget(self.redo_button)
        outer.addLayout(actions)
        table.selectionModel().selectionChanged.connect(
            lambda *_a: self.window_.update_footer()
        )

    # ── Refresh ──────────────────────────────────────────

    def _row_id(self, index) -> int:
        return self.model.id_at(self.proxy.mapToSource(index).row())

    def _selected(self) -> list:
        """Selected proxy indexes, top to bottom, left to right."""
        return sorted(
            self.table.selectionModel().selectedIndexes(),
            key=lambda i: (i.row(), i.column()),
        )

    def selected_row_ids(self) -> list[int]:
        return list(dict.fromkeys(self._row_id(i) for i in self._selected()))

    def refresh(self, sheet: es.Sheet | None = None) -> None:
        """Show the sheet after an edit, keeping the selection by row. A
        new ``sheet`` (a rebase) numbers its rows afresh: nothing stays
        selected."""
        scroll = self.table.verticalScrollBar().value()
        if sheet is not None:
            self.sheet = sheet
            self.model.set_sheet(sheet)
        else:
            selection = [(self._row_id(i), i.column()) for i in self._selected()]
            current = self.table.currentIndex()
            current = (self._row_id(current), current.column()) if current.isValid() else None
            if self.model.refresh():
                self.select_cells(selection, current)
        self.table.verticalScrollBar().setValue(scroll)
        self._fill_group_filter()
        self.window_.update_footer()

    def _index_of(self, row_id: int, column: int):
        source_row = self.model.source_row(row_id)
        if source_row < 0:
            return QModelIndex()
        return self.proxy.mapFromSource(self.model.index(source_row, column))

    def index_of(self, row_id: int, column: str):
        return self._index_of(row_id, self.columns.index(column))

    def select_cells(self, cells, current=None) -> None:
        """Select ``(row id, column)`` cells; the rest of the selection goes."""
        model = self.table.selectionModel()
        model.clearSelection()
        for row_id, column in cells:
            index = self._index_of(row_id, column)
            if index.isValid():
                model.select(index, QItemSelectionModel.Select)
        if current is not None:
            index = self._index_of(*current)
            if index.isValid():
                model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)

    def _fill_group_filter(self) -> None:
        wanted = [''] + self.sheet.groups()
        combo = self._group_filter
        have = [combo.itemData(i) for i in range(combo.count())]
        if have == wanted:
            return
        current = combo.currentData() or ''
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(f'All {_plurals(self.kind.group)}', '')
        for group in wanted[1:]:
            combo.addItem(group, group)
        combo.setCurrentIndex(max(combo.findData(current), 0))
        combo.blockSignals(False)

    def _apply_filter(self, *_args) -> None:
        self.proxy.set_filter(self._group_filter.currentData() or '', self._search.text())

    def update_buttons(self) -> None:
        self.undo_button.setEnabled(self.sheet.can_undo)
        self.redo_button.setEnabled(self.sheet.can_redo)
        rows = [self.sheet.row(i) for i in self.selected_row_ids()]
        stored = [r for r in rows if not r.is_new]
        self.retire_button.setText('Restore' if stored and all(r.retire for r in stored) else 'Retire')

    def say(self, text: str = '', warn: bool = False) -> None:
        """The window's footer line about the last action ('' clears it)."""
        self.window_.say(text, warn)

    def edit(self, change: Callable[[], object]) -> None:
        """Run ``change`` as one undo step, then show the result and any
        value a field refused."""
        self._errors = []
        try:
            with self.sheet.batch():
                change()
        except ValueError as exc:
            self._errors.append(str(exc))
        self.refresh()
        if self._errors:
            self.say(self._errors[0], warn=True)
        else:
            self.say()

    def _set_cell(self, row_id: int, column: str, value) -> None:
        """One cell, as typed or pasted: '' or Inherit in a value column
        puts it back to inheriting."""
        row = self.sheet.row(row_id)
        if not self.model.editable(row, column) or column == es.ASSETS:
            return
        try:
            if column in self._values and (value == INHERIT or str(value).strip() == ''):
                self.sheet.reset(row_id, column)
            elif column == es.ROOT:
                self.sheet.set_value(row_id, column, self._root_value(value))
            else:
                self.sheet.set_value(row_id, column, value)
        except ValueError as exc:
            self._errors.append(str(exc))

    def _root_value(self, value) -> str:
        # A path or a URI; set_value refuses a Root that does not exist.
        return es.parse_value(es.ROOT, value)

    # ── Editing ──────────────────────────────────────────

    def commit(self, index, value) -> None:
        """A cell editor closed on ``index``: write ``value`` into it, and
        into every other selected cell of its column when it holds a value
        (names are one row at a time)."""
        column = self.columns[index.column()]
        targets = [self._row_id(index)]
        if column in self._values:
            selected = [i for i in self._selected() if i.column() == index.column()]
            if any(i.row() == index.row() for i in selected):
                targets = [self._row_id(i) for i in selected]

        def change():
            for row_id in targets:
                self._set_cell(row_id, column, value)
        self.edit(change)

    def _copy(self) -> None:
        cells = self._selected()
        if not cells:
            return
        rows = sorted({i.row() for i in cells})
        columns = sorted({i.column() for i in cells})
        chosen = {(i.row(), i.column()) for i in cells}
        QApplication.clipboard().setText('\n'.join(
            '\t'.join(
                (self.proxy.index(r, c).data() or '') if (r, c) in chosen else ''
                for c in columns
            )
            for r in rows
        ))

    def paste_text(self, text: str) -> None:
        """Paste into the cells: one value fills the selection; a block
        (tab-separated, as spreadsheets copy) starts at its top-left."""
        cells = self._selected()
        lines = [line.split('\t') for line in text.splitlines()]
        while lines and lines[-1] == ['']:
            lines.pop()
        if not cells or not lines:
            return
        if len(lines) == 1 and len(lines[0]) == 1:
            value = lines[0][0]
            self.edit(lambda: [
                self._set_cell(self._row_id(i), self.columns[i.column()], value)
                for i in cells
            ])
            return
        top = min(i.row() for i in cells)
        left = min(i.column() for i in cells)
        targets = [
            (self._row_id(self.proxy.index(top + r, 0)), self.columns[left + c], value)
            for r, line in enumerate(lines) if top + r < self.proxy.rowCount()
            for c, value in enumerate(line) if left + c < len(self.columns)
        ]
        missed = sum(1 for r in range(len(lines)) if top + r >= self.proxy.rowCount())
        self.edit(lambda: [self._set_cell(*target) for target in targets])
        if missed and not self._errors:
            self.say(
                f'{_plural(missed, "pasted line")} ran past the last row; '
                f'Paste as New {self.kind.item.capitalize()}s adds rows.',
                warn=True,
            )

    def _paste(self) -> None:
        self.paste_text(QApplication.clipboard().text())

    def paste_new(self, text: str) -> None:
        """Add the lines of ``text`` as new rows."""
        rows, rejected = shot_batch.parse_table(text, self.proxy.group, None, None)
        added = []

        def change():
            for row in rows:
                values = {}
                if row.frame_start is not None:
                    values = {es.FRAME_START: row.frame_start, es.FRAME_END: row.frame_end}
                added.append(self.sheet.add(row.sequence, row.shot, values))
        self.edit(change)
        self.select_cells([(row_id, 1) for row_id in added])
        if rejected:
            first = rejected[0].replace('\t', '  ')
            self.say(
                f'Skipped {_plural(len(rejected), "pasted line")} that '
                f'{"is" if len(rejected) == 1 else "are"} not {"an" if self.noun[0] in "aeiou" else "a"} '
                f'{self.noun} (frames must be whole numbers), e.g. "{first}".',
                warn=True,
            )

    def _paste_new(self) -> None:
        self.paste_new(QApplication.clipboard().text())

    def _reset_cells(self) -> None:
        cells = [
            (self._row_id(i), self.columns[i.column()]) for i in self._selected()
            if self.columns[i.column()] in self._values
        ]
        self.edit(lambda: [self.sheet.reset(row_id, column) for row_id, column in cells])

    def _fill_down(self) -> None:
        by_column: dict[int, list] = {}
        for index in self._selected():
            by_column.setdefault(index.column(), []).append(index)

        def change():
            for col, indexes in by_column.items():
                column = self.columns[col]
                if column not in self._values or len(indexes) < 2:
                    continue
                top = self.sheet.row(self._row_id(indexes[0]))
                value = self.sheet.value(top, column)
                if value is None:
                    continue
                for index in indexes[1:]:
                    self._set_cell(self._row_id(index), column, value)
        self.edit(change)

    def _shift_frames(self) -> None:
        ids = self.selected_row_ids()
        if not ids:
            return
        offset, ok = QInputDialog.getInt(
            self, 'Shift Frames',
            f'Move the start and end of {_plural(len(ids), self.noun)} by (frames):',
            0, -1_000_000, 1_000_000,
        )
        if ok and offset:
            self.edit(lambda: self.sheet.shift(ids, offset))

    def _toggle_retire(self) -> None:
        ids = self.selected_row_ids()
        stored = [self.sheet.row(i) for i in ids if not self.sheet.row(i).is_new]
        restore = bool(stored) and all(r.retire for r in stored)
        self.edit(lambda: self.sheet.set_retired(ids, not restore))

    def _revert_selected(self) -> None:
        ids = self.selected_row_ids()
        self.edit(lambda: self.sheet.revert(ids))

    def undo(self) -> None:
        if self.sheet.undo():
            self.refresh()

    def redo(self) -> None:
        if self.sheet.redo():
            self.refresh()

    def add_dialog(self, group: str | None = None) -> None:
        group = group if group is not None else self.proxy.group
        dialog = (
            AddShotsDialog(self, group) if self.kind is es.SHOTS_KIND
            else AddAssetsDialog(self, group)
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.add_rows(dialog.rows())

    def add_rows(self, rows: list[tuple[str, str, dict]]) -> None:
        added = []
        self.edit(lambda: [added.append(self.sheet.add(*row)) for row in rows])
        if rows and self.proxy.group and rows[0][0] != self.proxy.group:
            self.focus(rows[0][0])
        self.select_cells([(row_id, 1) for row_id in added])

    def new_root(self, name: str = '', parent=None) -> str | None:
        """A new Root in the sheet (New Root from assets…); its URI, or None."""
        dialog = NewRootDialog(self, name, parent=parent or self)
        if dialog.exec() != QDialog.Accepted or dialog.problem():
            return None
        created = []
        self.edit(lambda: created.append(self.sheet.add_root(dialog.path(), dialog.picker.own())))
        return created[0] if created else None

    def edit_assets(self, index) -> None:
        """The Assets cell of ``index``'s shot: its Root's list, ticked."""
        if not index.isValid():
            return
        row = self.sheet.row(self._row_id(index))
        if row.retire:
            return
        dialog = RootAssetsDialog(self, row)
        if dialog.exec() != QDialog.Accepted or dialog.action is None:
            return
        self.apply_assets_action(row.id, dialog.action)

    def apply_assets_action(self, row_id: int, action: tuple) -> None:
        what, target, assets = action

        def change():
            if what == 'root':
                self.sheet.set_root_assets(target, assets)
            else:
                uri = self.sheet.add_root(target, assets)
                self.sheet.set_value(row_id, es.ROOT, uri)
        self.edit(change)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        cells = self._selected()
        has_values = any(self.columns[i.column()] in self._values for i in cells)
        plural = f'{self.kind.item.capitalize()}s'
        entries = [
            ('Copy', self._copy, bool(cells)),
            ('Paste', self._paste, bool(cells)),
            (f'Paste as New {plural}', self._paste_new, True),
            None,
            ('Reset to Inherited', self._reset_cells, has_values),
            ('Fill Down', self._fill_down, has_values),
            ('Shift Frames…', self._shift_frames, bool(cells)),
        ]
        if self.kind is es.SHOTS_KIND:
            current = self.table.currentIndex()
            entries.append(('Edit Assets…', lambda: self.edit_assets(current), current.isValid()))
        entries += [
            None,
            (self.retire_button.text(), self._toggle_retire, bool(cells)),
            ('Revert', self._revert_selected, bool(cells)),
            None,
            ('Undo', self.undo, self.sheet.can_undo),
            ('Redo', self.redo, self.sheet.can_redo),
        ]
        for entry in entries:
            if entry is None:
                menu.addSeparator()
                continue
            text, slot, enabled = entry
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def focus(self, group: str = '', name: str = '') -> None:
        """Show ``group`` ('' for all) with ``name`` selected."""
        self._fill_group_filter()
        found = self._group_filter.findData(group)
        self._group_filter.setCurrentIndex(max(found, 0))
        if name:
            row = next((r for r in self.sheet.rows if r.group == group and r.name == name), None)
            if row is not None:
                self.select_cells([(row.id, 1)], (row.id, 1))
                self.table.scrollTo(self._index_of(row.id, 1))


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


class EntityEditorWindow(QDialog):
    """A project's assets and shots as spreadsheets; see the module
    docstring."""

    def __init__(self, source: EntityEditorSource, parent=None, kind: str = 'shots') -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.source = source
        # The footer's note about the last action, until the next edit.
        self._message: tuple[str, bool] = ('', False)
        self.setWindowTitle(f'Entity Editor — {source.project}')
        self.resize(scaled(1180), scaled(700))
        self.panes: dict[str, _SheetPane] = {}
        self._build_ui()
        self.show_kind(kind)
        self.update_footer()

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(scaled(8))
        top = QHBoxLayout()
        self._tabs = QTabBar()
        self._tabs.setDrawBase(False)
        self._tabs.setExpanding(False)
        top.addWidget(self._tabs)
        top.addStretch()
        top.addWidget(_button(
            'Reload', self._reload,
            tip='Read the assets and shots again, keeping the edits not applied yet.',
        ))
        outer.addLayout(top)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)
        for kind in es.KINDS:
            pane = _SheetPane(self, kind)
            self.panes[kind.key] = pane
            self._stack.addWidget(pane)
            index = self._tabs.addTab(f'{kind.key.capitalize()}')
            self._tabs.setTabData(index, kind.key)
        for pane in self.panes.values():
            pane.refresh()
        self._tabs.currentChanged.connect(
            lambda i: self.show_kind(self._tabs.tabData(i))
        )
        for keys, slot in (
            (QKeySequence.Undo, lambda: self.pane.undo()),
            (QKeySequence.Redo, lambda: self.pane.redo()),
            (QKeySequence('Ctrl+Y'), lambda: self.pane.redo()),
        ):
            QShortcut(keys, self, slot)

        # Discard All at the far left, away from Apply; between them the one
        # line that says what Apply will do, or what the last action did.
        footer = QHBoxLayout()
        self._discard = _button(
            'Discard All', self._discard_all, style=_BUTTON_DANGER_STYLE,
            tip='Drop every edit, in both tabs, that is not applied.',
        )
        footer.addWidget(self._discard)
        footer.addSpacing(scaled(12))
        self._summary = _hint()
        self._summary.setWordWrap(False)
        footer.addWidget(self._summary, 1)
        self._apply_button = _button(
            'Apply', self.apply, primary=True,
            tip='Write the edits of both tabs to the database.',
        )
        self._apply_button.setMinimumWidth(scaled(110))
        footer.addWidget(self._apply_button)
        outer.addLayout(footer)

    @property
    def pane(self) -> _SheetPane:
        return self._stack.currentWidget()

    def show_kind(self, key: str) -> None:
        pane = self.panes[key]
        self._stack.setCurrentWidget(pane)
        index = next(i for i in range(self._tabs.count()) if self._tabs.tabData(i) == key)
        if self._tabs.currentIndex() != index:
            self._tabs.setCurrentIndex(index)
        self.update_footer()

    @property
    def dirty(self) -> bool:
        return any(pane.sheet.dirty for pane in self.panes.values())

    def asset_choices(self) -> list[str]:
        """Asset URI strings a Root can list: the stored ones not retiring,
        then the new rows of the Assets tab (created first on Apply)."""
        sheet = self.panes['assets'].sheet
        problems = sheet.problems()
        stored = [
            str(row.origin.uri) for row in sheet.rows
            if row.origin is not None and not row.retire and not row.renamed
        ]
        new = [
            str(es.ASSETS_URI / row.group / row.name) for row in sheet.rows
            if row.is_new and row.id not in problems
        ]
        return sorted(stored) + new

    def update_footer(self) -> None:
        if not hasattr(self, '_summary') or len(self.panes) < len(es.KINDS):
            return
        parts, problems = self._pending()
        message, warn = self._message
        if message:
            text = message
        else:
            text = ', '.join(parts) or 'No changes.'
            if problems:
                text += (
                    f'; {problems} row{"s need" if problems != 1 else " needs"} '
                    'fixing (see Status)'
                )
        self._summary.setText(text)
        self._summary.setToolTip(text)
        self._summary.setStyleSheet(
            _HINT_STYLE.replace(TEXT_DIM, WARNING) if problems or warn else _HINT_STYLE
        )
        dirty = self.dirty
        self._apply_button.setEnabled(dirty and not problems)
        self._discard.setEnabled(dirty)
        self.setWindowTitle(
            f'Entity Editor — {self.source.project}{" *" if dirty else ""}'
        )

    def _pending(self) -> tuple[list[str], int]:
        """What Apply would do, in short phrases, and how many rows need
        fixing; also marks the tabs that have edits."""
        parts, problems = [], 0
        for key, pane in self.panes.items():
            summary = pane.sheet.summary()
            problems += summary.problems
            kind = pane.kind
            bits = []
            if summary.new:
                text = _plural(summary.new, f'new {kind.item}')
                if summary.new_groups:
                    text += f' ({_plural(len(summary.new_groups), f"new {kind.group}")})'
                bits.append(text)
            if summary.changed:
                bits.append(f'{_plural(summary.changed, kind.item)} changed')
            if summary.renamed:
                bits.append(f'{summary.renamed} renamed')
            if summary.retired:
                bits.append(f'{summary.retired} to retire')
            if summary.roots_new:
                bits.append(_plural(summary.roots_new, 'new Root'))
            if summary.roots_changed:
                bits.append(f'{_plural(summary.roots_changed, "Root")} with new assets')
            parts.extend(bits)
            label = kind.key.capitalize()
            index = next(i for i in range(self._tabs.count()) if self._tabs.tabData(i) == key)
            self._tabs.setTabText(index, f'{label} *' if pane.sheet.dirty else label)
            pane.update_buttons()
        return parts, problems

    def say(self, text: str = '', warn: bool = False) -> None:
        """Show ``text`` in the footer in place of the summary until the
        next edit ('' goes back to the summary); ``warn`` colours it."""
        self._message = (text, warn)
        self.update_footer()

    def focus(self, kind: str = 'shots', group: str = '', name: str = '') -> None:
        """Show ``kind``'s tab on ``group`` ('' for all) with ``name`` selected."""
        self.show_kind(kind)
        self.panes[kind].focus(group, name)

    def _discard_all(self) -> None:
        if not self.dirty:
            return
        answer = QMessageBox.question(
            self, 'Discard All', 'Drop every edit, in both tabs, that is not applied?',
        )
        if answer == QMessageBox.Yes:
            for pane in self.panes.values():
                pane.edit(pane.sheet.revert_all)

    # ── Reload and Apply ─────────────────────────────────

    def _rebase(self) -> tuple[bool, list[str]]:
        """Carry the edits onto the database as it is now; whether it had
        changed since the sheets were read, and the edits that fell away."""
        changed, notes = False, []
        for pane in self.panes.values():
            fresh = es.load(self.source.config, pane.kind)
            changed |= fresh != pane.sheet.base
            sheet, pane_notes = pane.sheet.rebase(fresh)
            notes += pane_notes
            pane.refresh(sheet)
        return changed, notes

    def _reload(self) -> None:
        changed, notes = self._rebase()
        self.say(' '.join(notes) or ('Reloaded.' if changed else 'Nothing had changed.'))

    def apply(self) -> bool:
        """Write the edits of both tabs, assets first (a Root may list a
        new asset); True when nothing is left pending."""
        if not self.dirty:
            return True
        changed, notes = self._rebase()
        if changed or notes:
            QMessageBox.information(
                self, 'Entity Editor',
                'The assets or shots changed in the database while you were '
                'editing (someone else, or another window). Your edits are '
                'kept on top of the new values'
                + (':\n\n' + '\n'.join(notes) if notes else '.')
                + '\n\nCheck them, then Apply again.',
            )
            return False
        for pane in self.panes.values():
            if pane.sheet.problems():
                self.show_kind(pane.kind.key)
                self.say(f'{_plural(len(pane.sheet.problems()), "row")} need fixing first (see Status).', warn=True)
                return False
        changes = [(pane, pane.sheet.changes()) for pane in self.panes.values()]
        done = ', '.join(self._pending()[0])
        retire = [
            '/'.join(uri.segments) for _pane, change in changes for uri in change.retire
        ]
        if retire:
            shown = '\n'.join(retire[:12]) + ('\n…' if len(retire) > 12 else '')
            answer = QMessageBox.question(
                self, 'Retire',
                f'Retire {len(retire)}? They leave the project, and their '
                'workfiles, exports and renders move to the _deleted folder '
                f'of each storage location.\n\n{shown}',
            )
            if answer != QMessageBox.Yes:
                return False
        error = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for _pane, change in changes:
                es.apply(
                    self.source.config, change,
                    retire=self.source.retire, rename=self.source.rename,
                )
        except es.ApplyError as exc:
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
        for pane in self.panes.values():
            sheet, _notes = pane.sheet.rebase(es.load(self.source.config, pane.kind))
            pane.refresh(sheet)
        self.source.on_applied()
        if error is not None:
            QMessageBox.warning(
                self, 'Entity Editor',
                f'Apply stopped at:\n\n{error}\n\nEverything before it was '
                'applied. The rest is still in the sheets: fix the cause and '
                'Apply again.',
            )
            return False
        self.say(f'Applied: {done}.' if done else 'Applied.')
        return True

    # ── Closing ──────────────────────────────────────────

    def reject(self) -> None:
        # Escape closes through closeEvent, so unapplied edits are asked about.
        self.close()

    def closeEvent(self, event) -> None:
        if self.dirty:
            answer = QMessageBox.question(
                self, 'Entity Editor',
                'Apply your edits before closing?',
                QMessageBox.Apply | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Apply,
            )
            if answer == QMessageBox.Cancel or (
                answer == QMessageBox.Apply and not self.apply()
            ):
                event.ignore()
                return
        event.accept()
