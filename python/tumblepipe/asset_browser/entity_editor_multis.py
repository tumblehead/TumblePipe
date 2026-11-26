"""The Entity Editor's Multis tab: the Multis as a table (members,
covered departments, a shots Multi's Timeline) and, in its side panel, the
selected Multi's workfiles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelectionModel, QModelIndex, QSortFilterProxyModel,
    Qt, QTimer,
)
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QPushButton, QSplitter,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es
from tumblepipe.config.multi_timeline import (
    TIMELINE_CHOICES, timeline_choice, timeline_settings,
)
from tumblepipe.util.qt_lifetime import release_kept_references

from tumbletrove.asset_browser.core.theme import (
    COMBO_STYLE, scaled,
)
from .entity_editor_widgets import STATUS, _AMBER, _BOLD, _BUTTON_ADD_STYLE, _BUTTON_DANGER_STYLE, _Delegate, _GREEN, _HINT_STYLE, _INHERITED, _NEW, _OVERRIDE, _PENDING, _RED, _STRUCK, _Table, _button, _hint, _plural, _short, _side_panel
from .entity_editor_dialogs import MultiMembersDialog, NewMultiDialog, _TIMELINE_TIP

if TYPE_CHECKING:
    from .entity_editor import EntityEditorSource, EntityEditorWindow, _SheetPane


# ---------------------------------------------------------------------------
# Multis
# ---------------------------------------------------------------------------

#: The Multis tab's own columns; the departments of the pool follow them.
MULTI_NAME = 'multi'
MEMBERS = 'members'
TIMELINE = 'timeline'
_MULTI_TEXT_COLUMNS = (MULTI_NAME, STATUS, MEMBERS, TIMELINE)


class _MultiModel(QAbstractTableModel):
    """One kind's Multis as a table: name, status, members, then a tick
    box per department of the pool."""

    def __init__(self, pane: 'MultiPane') -> None:
        super().__init__()
        self._pane = pane
        self._ids: list[int] = []
        self.columns: list[str] = []

    @property
    def sheet(self) -> es.Sheet:
        return self._pane.sheet

    def _wanted(self) -> tuple[list[int], list[str]]:
        sheet = self.sheet
        # Only shots have frames to lay end to end.
        timeline = [TIMELINE] if sheet.kind is es.SHOTS_KIND else []
        return [m.id for m in sheet.multis], [
            MULTI_NAME, STATUS, MEMBERS, *timeline, *sheet.base.pool,
        ]

    def refresh(self) -> bool:
        """Show the sheet's Multis; True when rows or columns changed."""
        ids, columns = self._wanted()
        if ids != self._ids or columns != self.columns:
            self.beginResetModel()
            self._ids, self.columns = ids, columns
            self.endResetModel()
            return True
        if ids:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(ids) - 1, len(columns) - 1),
            )
        return False

    def id_at(self, source_row: int) -> int:
        return self._ids[source_row]

    def source_row(self, multi_id: int) -> int:
        return self._ids.index(multi_id) if multi_id in self._ids else -1

    def multi_at(self, index) -> es.Multi | None:
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        try:
            return self.sheet.multi(self._ids[index.row()])
        except KeyError:
            return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            column = self.columns[section]
            return {
                MULTI_NAME: 'Multi', STATUS: 'Status', MEMBERS: 'Members',
                TIMELINE: 'Timeline',
            }.get(column, column)
        return None

    def flags(self, index):
        multi = self.multi_at(index)
        if multi is None:
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        column = self.columns[index.column()]
        if multi.retire or column == STATUS:
            return flags
        if column in (MULTI_NAME, MEMBERS, TIMELINE):
            return flags | Qt.ItemIsEditable
        return flags | Qt.ItemIsUserCheckable

    def status(self, multi: es.Multi) -> tuple[str, QBrush]:
        problem = self.sheet.multi_problems().get(multi.id)
        if problem:
            return problem, _RED
        if multi.retire:
            return 'Retire', _RED
        if multi.is_new:
            return 'New', _GREEN
        parts = []
        if multi.renamed:
            parts.append(f'Renamed from {multi.origin.name}')
        if multi.members_changed:
            parts.append('Members changed')
        if multi.departments_changed:
            parts.append('Departments changed')
        if multi.timeline_changed:
            parts.append('Timeline changed')
        return ', '.join(parts), _AMBER

    def _members_text(self, multi: es.Multi) -> str:
        count = len(self.sheet.multi_covered_rows(multi)) + len(self.sheet.multi_strays(multi))
        return _plural(count, self.sheet.kind.item)

    def data(self, index, role=Qt.DisplayRole):
        multi = self.multi_at(index)
        if multi is None:
            return None
        column = self.columns[index.column()]
        covers = column not in _MULTI_TEXT_COLUMNS
        if role in (Qt.DisplayRole, Qt.EditRole):
            if column == MULTI_NAME:
                return multi.name
            if column == STATUS:
                return self.status(multi)[0]
            if column == MEMBERS:
                return self._members_text(multi)
            if column == TIMELINE:
                return timeline_choice(*multi.timeline)
            return None
        if role == Qt.CheckStateRole and covers:
            return Qt.Checked if column in multi.departments else Qt.Unchecked
        if role == Qt.ForegroundRole:
            if column == STATUS:
                return self.status(multi)[1]
            return _INHERITED if multi.retire else _OVERRIDE
        if role == Qt.FontRole:
            if multi.retire:
                return _STRUCK
            return _BOLD if column == MULTI_NAME else None
        if role == Qt.BackgroundRole and not multi.retire:
            if column == MULTI_NAME:
                return _NEW if multi.is_new else (_PENDING if multi.renamed else None)
            if column == MEMBERS and not multi.is_new and multi.members_changed:
                return _PENDING
            if column == TIMELINE and multi.timeline_changed:
                return _PENDING
            if covers and not multi.is_new and (
                (column in multi.departments) != (column in multi.origin.departments)
            ):
                return _PENDING
            return None
        if role == Qt.ToolTipRole:
            return self._tooltip(multi, column)
        return None

    def _tooltip(self, multi: es.Multi, column: str) -> str:
        sheet = self.sheet
        kind = sheet.kind
        if column == STATUS:
            return self.status(multi)[0]
        if multi.retire:
            return (
                f'Multi {multi.origin.name} leaves the project on Apply; its '
                'workfiles stay on disk. Revert or Restore keeps it.'
            )
        if column == MULTI_NAME:
            extra = [d for d in multi.departments if d not in sheet.base.pool]
            lines = ['Type to rename. A Multi with workfiles on disk cannot be renamed.']
            if extra:
                lines.append(f'Also covers {", ".join(extra)}, no longer a department; kept.')
            return '\n'.join(lines)
        if column == MEMBERS:
            groups = sheet.multi_groups(multi)
            names = [f'{r.group}/{r.name}' for r in sheet.multi_rows(multi)]
            strays = sheet.multi_strays(multi)
            lines = []
            if groups:
                lines.append(
                    f'Whole {kind.group}: {_short(groups)}, every {kind.item} in '
                    'it and any made in it later.'
                )
            if names or not groups:
                lines.append(_short(names, 30) or f'No {kind.item}s yet.')
            if strays:
                lines.append(
                    f'{_plural(len(strays), "member")} no longer in the project: '
                    f'{_short(strays)}; kept.'
                )
            lines.append('Double-click to choose them.')
            return '\n'.join(lines)
        if column == TIMELINE:
            return f'{_TIMELINE_TIP}\nDouble-click to choose; the selected Multis follow.'
        if column in multi.departments:
            return (
                f'{multi.name} covers {column}: every member\'s {column} '
                'workfile is the Multi\'s. Untick to give them back their own.'
            )
        return f'{multi.name} does not cover {column}: members keep their own {column} workfiles.'

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        checked = value in (Qt.Checked, getattr(Qt.Checked, 'value', 2), 2)
        self._pane.tick(self._pane.proxy.mapFromSource(index), checked)
        return True


class _MultiFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.text = ''

    def set_text(self, text: str) -> None:
        self.text = text.strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, parent) -> bool:
        if not self.text:
            return True
        multi = self.sourceModel().multi_at(self.sourceModel().index(source_row, 0))
        return multi is not None and self.text in multi.name.casefold()


class _MultiDelegate(_Delegate):
    """The name as a line edit (a rename), Members as a dialog, Timeline
    as a choice."""

    def createEditor(self, parent, option, index):
        column = self._pane.columns[index.column()]
        if column == MEMBERS:
            QTimer.singleShot(0, lambda i=QModelIndex(index): self._pane.edit_members(i))
            return None
        if column == MULTI_NAME:
            return QLineEdit(parent)
        if column == TIMELINE:
            editor = QComboBox(parent)
            editor.addItems(list(TIMELINE_CHOICES))
            return editor
        return None

    def setEditorData(self, editor, index) -> None:
        if isinstance(editor, QComboBox):
            editor.setCurrentText(index.data(Qt.EditRole) or TIMELINE_CHOICES[0])
            return
        editor.setText(index.data(Qt.EditRole) or '')

    def setModelData(self, editor, model, index) -> None:
        if isinstance(editor, QComboBox):
            self._pane.set_timeline(index, editor.currentText())
            return
        self._pane.rename(index, editor.text())


class _MultiWorkfiles(QWidget):
    """Beside the Multis table, in the side panel: the selected Multi's
    workfile in each department it covers. One that has one shows its
    latest version and
    opens; one that has none can be made from the department template.
    Either replaces the open scene, so one at a time, and only for a Multi
    as stored (a new or changed one is applied first)."""

    def __init__(self, pane: 'MultiPane') -> None:
        super().__init__(pane)
        self._pane = pane
        self._busy = ''        # the department being made, '' when idle
        self._cache: dict[str, dict] = {}
        self._cache_sheet = None
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, scaled(4), 0, 0)
        layout.setSpacing(scaled(4))
        self.title = QLabel('Workfiles')
        self.title.setFont(_BOLD)
        layout.addWidget(self.title)
        host = QWidget()
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(scaled(12))
        self._grid.setVerticalSpacing(scaled(2))
        layout.addWidget(host)
        self.note = _hint()
        layout.addWidget(self.note)
        layout.addStretch()

    @property
    def source(self) -> EntityEditorSource:
        return self._pane.window_.source

    def versions(self, multi: es.Multi) -> dict:
        """``{department: latest version}``, read once per Multi until the
        sheet is replaced (an Apply, a reload) or a workfile is made."""
        sheet = self._pane.sheet
        if sheet is not self._cache_sheet:
            self._cache, self._cache_sheet = {}, sheet
        key = str(multi.origin.uri)
        if key not in self._cache:
            try:
                self._cache[key] = dict(self.source.multi_workfiles(multi.origin.uri) or {})
            except Exception as exc:
                self._cache[key] = {}
                self._pane.say(f'Could not read the workfiles of {multi.name}: {exc}', warn=True)
        return self._cache[key]

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hidden now: deleteLater waits for the event loop, and
                # until then the old row would show under the new one.
                widget.hide()
                release_kept_references(widget)
                widget.deleteLater()
        self.buttons = {}

    def fill(self) -> None:
        """Show the current Multi's workfiles."""
        self._clear()
        multi = self._pane.current_multi()
        if multi is None:
            self.title.setText('Workfiles')
            self.note.setText('Select a Multi to see its workfile in each department it covers.')
            return
        self.title.setText(f'Workfiles of {multi.name}')
        sheet = self._pane.sheet
        departments = [d for d in sheet.base.pool if d in multi.departments]
        departments += [d for d in multi.departments if d not in departments]
        if multi.is_new:
            why = 'Apply first: a new Multi has no workfiles yet.'
        elif multi.retire:
            why = 'Retiring on Apply; its workfiles stay on disk.'
        elif multi.touched:
            why = 'Apply first: the workfiles follow the Multi as stored.'
        else:
            why = ''
        if not departments:
            self.note.setText(why or 'It covers no department, so it has no workfiles.')
            return
        versions = {} if multi.is_new else self.versions(multi)
        ready = not why and not self._busy
        for row, department in enumerate(departments):
            version = versions.get(department)
            name = QLabel(department)
            state = QLabel(version or 'none')
            state.setStyleSheet('' if version else _HINT_STYLE)
            self._grid.addWidget(name, row, 0)
            self._grid.addWidget(state, row, 1)
            if version:
                button = _button(
                    'Open', lambda d=department: self._open(multi, d),
                    tip=f"Open {multi.name}'s latest {department} workfile, "
                        f'{version}. It replaces the open scene.',
                )
                button.setObjectName(f'workfile_open_{department}')
            else:
                button = _button(
                    'Making…' if self._busy == department else 'New from Template',
                    lambda d=department: self.new(multi, d),
                    tip=f"Make {multi.name}'s first {department} workfile from "
                        'the department template, with a graph per member. '
                        'It replaces the open scene.',
                )
                button.setObjectName(f'workfile_new_{department}')
            button.setEnabled(ready)
            self.buttons[button.objectName()] = button
            self._grid.addWidget(button, row, 2)
        self._grid.setColumnStretch(3, 1)
        self.note.setText(why or (
            'Opening or making a workfile replaces the open scene; you are '
            'asked to save it first. One at a time.'
        ))

    def _open(self, multi: es.Multi, department: str) -> None:
        if self._busy:
            return
        self.source.open_multi_workfile(multi.origin.uri, department)

    def new(self, multi: es.Multi, department: str) -> None:
        """Make the department's first workfile of the Multi (the board's
        chips use it too)."""
        if self._busy:
            return
        self._busy = department
        uri = multi.origin.uri
        self.fill()

        def done() -> None:
            self._busy = ''
            self._cache.pop(str(uri), None)
            try:
                self.fill()
            except RuntimeError:
                pass    # the window closed meanwhile

        try:
            self.source.new_multi_workfile(uri, department, done)
        except Exception as exc:
            done()
            self._pane.say(f'Could not make the {department} workfile: {exc}', warn=True)


class MultiPane(QWidget):
    """The Multis tab: one kind's Multis at a time. Its edits live in that
    kind's sheet, so they undo with the Shots or Assets tab's edits."""

    def __init__(self, window: 'EntityEditorWindow') -> None:
        super().__init__(window)
        self.window_ = window
        self.key = 'shots'
        self._build_ui()

    @property
    def entity_pane(self) -> '_SheetPane':
        return self.window_.panes[self.key]

    @property
    def sheet(self) -> es.Sheet:
        return self.entity_pane.sheet

    @property
    def columns(self) -> list[str]:
        return self.model.columns

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))
        # In the window's top row, beside the tabs (as the sheet tabs' bar).
        self.filter_bar = QWidget()
        top = QHBoxLayout(self.filter_bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel('Multis of'))
        self._context = QComboBox()
        self._context.setStyleSheet(COMBO_STYLE)
        self._context.addItem('Shots', 'shots')
        self._context.addItem('Assets', 'assets')
        # ``activated``: the artist's choice only. ``currentIndexChanged``
        # also fires with index -1 (data None) when the combo empties as the
        # window is deleted, which made this pane look up a None tab.
        self._context.activated.connect(
            lambda i: self.set_context(self._context.itemData(i))
        )
        top.addWidget(self._context)
        self._search = QLineEdit()
        self._search.setPlaceholderText('Filter Multis')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda text: self.proxy.set_text(text))
        top.addWidget(self._search, 1)
        # Board: the search's place goes to a spacer, then the department
        # lens and the Table | Board switch.
        self._board_spacer = QWidget()
        self._board_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top.addWidget(self._board_spacer, 1)
        self._department_label = QLabel('Department')
        top.addWidget(self._department_label)
        self._department = QComboBox()
        self._department.setStyleSheet(COMBO_STYLE)
        self._department.setToolTip(
            'Show only the Multis covering one department: each '
            'shot or asset is then in exactly one column'
        )
        self._department.activated.connect(
            lambda i: self.board.set_department(self._department.itemData(i))
        )
        top.addWidget(self._department)
        self._view_buttons = {}
        for view, text, tip in (
            ('table', 'Table', 'The Multis as a table: one row each'),
            ('board', 'Board', 'The Multis as columns of cards: drag shots '
                               'or assets between them'),
        ):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setObjectName(f'multi_view_{view}')
            button.clicked.connect(lambda _c=False, v=view: self.set_view(v))
            self._view_buttons[view] = button
            top.addWidget(button)
        self.view = 'table'
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(scaled(6))
        left_layout.addWidget(_hint(
            'A Multi is one workfile for several shots or assets. Tick the '
            'departments it covers: for those, every member\'s workfile is '
            'the Multi\'s. Double-click Members to choose them, and a shots '
            'Multi\'s Timeline to lay them end to end.'
        ))

        # Proxy first and parented, as in _SheetPane.
        self.proxy = _MultiFilter(self)
        self.model = _MultiModel(self)
        self.proxy.setSourceModel(self.model)
        table = self.table = _Table()
        table.setModel(self.proxy)
        table.setItemDelegate(_MultiDelegate(self))
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(scaled(24))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._context_menu)
        left_layout.addWidget(table, 1)
        self.splitter.addWidget(left)
        # The selected Multi's workfiles, in the side panel.
        self.workfiles = _MultiWorkfiles(self)
        self.side = _side_panel(self.workfiles)
        self.splitter.addWidget(self.side)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        from .multi_board import MultiBoard
        self.board = MultiBoard(self)
        self.views = QStackedWidget()
        self.views.addWidget(self.splitter)
        self.views.addWidget(self.board)
        outer.addWidget(self.views, 1)

        actions = QHBoxLayout()
        actions.addWidget(_button(
            'Add Multi…', self.add_dialog, style=_BUTTON_ADD_STYLE,
            tip='A new Multi: a name and the departments it covers. It can '
                'start with the rows selected on the Shots or Assets tab.',
        ))
        actions.addSpacing(scaled(24))
        # The table's: on the board each column's ⋯ menu has them.
        self.revert_button = _button(
            'Revert', self._revert_selected,
            tip='Drop the selected Multis\' edits; new Multis are removed.',
        )
        actions.addWidget(self.revert_button)
        self.retire_button = _button(
            'Retire', self._toggle_retire, style=_BUTTON_DANGER_STYLE,
            tip='Retire the selected Multis on Apply: they leave the project; '
                'their workfiles stay on disk and the members go back to '
                'their own. New Multis are removed.',
        )
        actions.addWidget(self.retire_button)
        actions.addStretch()
        self.undo_button = _button('Undo', self.undo)
        self.redo_button = _button('Redo', self.redo)
        actions.addWidget(self.undo_button)
        actions.addWidget(self.redo_button)
        outer.addLayout(actions)
        table.selectionModel().selectionChanged.connect(lambda *_a: self.update_buttons())
        table.selectionModel().selectionChanged.connect(lambda *_a: self.workfiles.fill())
        table.selectionModel().currentRowChanged.connect(
            lambda *_a: table.viewport().update()
        )
        self._show_view()
        self.refresh()

    def set_view(self, view: str) -> None:
        """'table' or 'board'."""
        if view not in ('table', 'board'):
            return
        self.view = view
        self._show_view()
        self.refresh()

    def _show_view(self) -> None:
        board = self.view == 'board'
        self.views.setCurrentWidget(self.board if board else self.splitter)
        for view, button in self._view_buttons.items():
            button.setChecked(view == self.view)
        for widget in (self._department_label, self._department, self._board_spacer):
            widget.setVisible(board)
        self._search.setVisible(not board)
        self.revert_button.setVisible(not board)
        self.retire_button.setVisible(not board)

    def _fill_departments(self) -> None:
        combo, sheet = self._department, self.sheet
        wanted = self.board.department
        combo.clear()
        combo.addItem('All', None)
        for department in sheet.base.pool:
            combo.addItem(department, department)
        index = combo.findData(wanted)
        combo.setCurrentIndex(max(index, 0))

    def show_in_table(self, multi_id: int) -> None:
        self.set_view('table')
        self.select_cells([(multi_id, 0)], (multi_id, 0))
        self.table.scrollTo(self._index_of(multi_id, 0))

    def current_multi(self) -> es.Multi | None:
        """The Multi the side panel shows the workfiles of: the current
        cell's, else the first selected."""
        index = self.table.currentIndex()
        ids = self.selected_ids()
        multi_id = self._multi_id(index) if index.isValid() else (ids[0] if ids else None)
        if multi_id is None or not self._exists(multi_id):
            return None
        return self.sheet.multi(multi_id)

    def set_context(self, key: str) -> None:
        if key == self.key:
            return
        self.key = key
        self._context.setCurrentIndex(self._context.findData(key))
        self.table.selectionModel().clearSelection()
        self.refresh()
        self.window_.update_footer()

    def refresh(self) -> None:
        selection = [(self._multi_id(i), i.column()) for i in self._selected()]
        if self.model.refresh():
            header = self.table.horizontalHeader()
            for i, column in enumerate(self.model.columns):
                self.table.setColumnWidth(i, scaled(
                    {MULTI_NAME: 130, STATUS: 170, MEMBERS: 90, TIMELINE: 150}.get(column, 84)
                ))
            header.setStretchLastSection(False)
            self.select_cells(selection)
        self.update_buttons()
        self.workfiles.fill()
        self._fill_departments()
        if self.view == 'board':
            self.board.refresh()

    def _multi_id(self, index) -> int:
        return self.model.id_at(self.proxy.mapToSource(index).row())

    def _selected(self) -> list:
        return sorted(
            self.table.selectionModel().selectedIndexes(),
            key=lambda i: (i.row(), i.column()),
        )

    def selected_ids(self) -> list[int]:
        return list(dict.fromkeys(self._multi_id(i) for i in self._selected()))

    def _index_of(self, multi_id: int, column: int):
        source_row = self.model.source_row(multi_id)
        if source_row < 0 or column >= len(self.model.columns):
            return QModelIndex()
        return self.proxy.mapFromSource(self.model.index(source_row, column))

    def select_cells(self, cells, current=None) -> None:
        model = self.table.selectionModel()
        model.clearSelection()
        for multi_id, column in cells:
            index = self._index_of(multi_id, column)
            if index.isValid():
                model.select(index, QItemSelectionModel.Select)
        if current is not None:
            index = self._index_of(*current)
            if index.isValid():
                model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)

    def update_buttons(self) -> None:
        sheet = self.sheet
        self.undo_button.setEnabled(sheet.can_undo)
        self.redo_button.setEnabled(sheet.can_redo)
        multis = [sheet.multi(i) for i in self.selected_ids() if self._exists(i)]
        stored = [m for m in multis if not m.is_new]
        self.retire_button.setText('Restore' if stored and all(m.retire for m in stored) else 'Retire')

    def _exists(self, multi_id: int) -> bool:
        try:
            self.sheet.multi(multi_id)
        except KeyError:
            return False
        return True

    def say(self, text: str = '', warn: bool = False) -> None:
        self.window_.say(text, warn)

    def edit(self, change: Callable[[], object]) -> None:
        """One undo step in the kind's sheet; its tab and this one both
        show the result."""
        self.entity_pane.edit(change)

    # ── Editing ──────────────────────────────────────────

    def tick(self, index, checked: bool) -> None:
        """A department box ticked: every selected cell of its column follows."""
        column = self.columns[index.column()]
        targets = [self._multi_id(index)]
        selected = [i for i in self._selected() if i.column() == index.column()]
        if any(i.row() == index.row() for i in selected):
            targets = [self._multi_id(i) for i in selected]
        self.edit(lambda: self.sheet.set_covers(targets, column, checked))

    def rename(self, index, text: str) -> None:
        multi_id = self._multi_id(index)
        self.edit(lambda: self.sheet.rename_multi(multi_id, text))

    def set_timeline(self, index, choice: str) -> None:
        """A Timeline chosen: every selected cell of the column follows."""
        if choice not in TIMELINE_CHOICES:
            return
        targets = [self._multi_id(index)]
        selected = [i for i in self._selected() if i.column() == index.column()]
        if any(i.row() == index.row() for i in selected):
            targets = [self._multi_id(i) for i in selected]
        shared, bookmarks = timeline_settings(choice)
        self.edit(lambda: self.sheet.set_multi_timeline(targets, shared, bookmarks))

    def edit_members(self, index) -> None:
        if not index.isValid():
            return
        multi = self.sheet.multi(self._multi_id(index))
        if multi.retire:
            return
        dialog = MultiMembersDialog(self, self.sheet, multi)
        if dialog.exec() != QDialog.Accepted:
            return
        ids, groups = dialog.row_ids(), dialog.groups()
        self.edit(lambda: self.sheet.set_multi_members(multi.id, ids, groups))

    def add_dialog(self, from_selection: bool = True, group: str = '') -> None:
        """Add Multi…: a new pending Multi. ``from_selection``: offer to
        start it with the rows selected on the kind's tab. ``group``: the
        browser's New Multi… from a category or sequence row, offered to
        start with that group whole (#30); otherwise it starts empty."""
        rows = [
            row_id for row_id in self.entity_pane.selected_row_ids()
            if not self.sheet.row(row_id).retire
        ] if from_selection else []
        kind = self.sheet.kind
        groups = [group] if group and group in self.sheet.groups() else []
        if groups:
            start = f'Start with {kind.group} {group} (whole, and what is made in it later)'
        elif rows:
            start = f'Start with the {_plural(len(rows), kind.item)} selected on the {kind.key.capitalize()} tab'
        else:
            start = ''
        dialog = NewMultiDialog(self.sheet, start_with=start, parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.problem():
            return
        chosen = dialog.with_selection()
        members = rows if chosen and not groups else []
        created = []

        def change():
            multi_id = self.sheet.add_multi(
                dialog.name(), members, dialog.chosen(), dialog.timeline(),
            )
            if chosen and groups:
                self.sheet.set_group_membership(groups, multi_id, True)
            created.append(multi_id)
        self.edit(change)
        if created:
            self.select_cells([(created[0], 0)], (created[0], 0))

    def _toggle_retire(self) -> None:
        ids = [i for i in self.selected_ids() if self._exists(i)]
        stored = [self.sheet.multi(i) for i in ids if not self.sheet.multi(i).is_new]
        restore = bool(stored) and all(m.retire for m in stored)
        self.edit(lambda: self.sheet.retire_multis(ids, not restore))

    def _revert_selected(self) -> None:
        ids = [i for i in self.selected_ids() if self._exists(i)]
        self.edit(lambda: self.sheet.revert_multis(ids))

    def undo(self) -> None:
        self.entity_pane.undo()

    def redo(self) -> None:
        self.entity_pane.redo()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        ids = self.selected_ids()
        current = self.table.currentIndex()
        entries = [
            # triggered sends its ``checked`` flag, which add_dialog would
            # take as from_selection.
            ('Add Multi…', lambda: self.add_dialog(), True),
            ('Members…', lambda: self.edit_members(current), current.isValid()),
            None,
            (self.retire_button.text(), self._toggle_retire, bool(ids)),
            ('Revert', self._revert_selected, bool(ids)),
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

    def focus(self, key: str = '', name: str = '') -> None:
        """Show ``key``'s Multis with ``name`` selected."""
        if key in ('shots', 'assets'):
            self.set_context(key)
        multi = next((m for m in self.sheet.multis if m.name == name), None)
        if multi is not None:
            self.select_cells([(multi.id, 0)], (multi.id, 0))
            self.table.scrollTo(self._index_of(multi.id, 0))
            if self.view == 'board':
                self.board.scroll_to(multi.id)
