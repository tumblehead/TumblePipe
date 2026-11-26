"""The Entity Editor's dialogs: picking assets, new and edited Roots,
Departments off, Layers, adding shots and assets, new groups, and the
Multis' new / membership / members dialogs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es
from tumblepipe.config import shot_batch
from tumblepipe.config.multi_timeline import (
    TIMELINE_CHOICES, timeline_settings,
)
from tumblepipe.util import value_ladder_qt
from tumblepipe.util.uri import Uri

from tumbletrove.asset_browser.core.theme import (
    BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE,
    COMBO_STYLE, DANGER, TEXT_DIM,
    scaled,
)
from .entity_editor_widgets import INHERIT, _BUTTON_ADD_STYLE, _HINT_STYLE, _INHERITED, _RED, _button, _hint, _plural, _short, root_label

if TYPE_CHECKING:
    from .entity_editor import EntityEditorSource, _SheetPane
_NEW_ROOT = '__new_root__'


def _spin(value: int, low: int = 0, high: int = 999_999) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(value)
    # Middle-drag steps it, Ctrl + middle-click resets it, as in Houdini.
    value_ladder_qt.install(spin, default=value)
    return spin


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


class DepartmentsOffDialog(QDialog):
    """The Departments off cell's editor: the shot pool, one tick per
    department, ticked while it is on.

    A department turned off (#64) stays out of the shot's staged build, gets
    no new workfile and is not offered by Farm Submit or the publish for the
    shot; its workfiles and exports stay on disk, so ticking it again brings
    it all back. After OK, ``value`` is the departments turned off, or
    ``inherit`` is True to put the cell back to what the shot inherits.
    """

    def __init__(
        self, pane: '_SheetPane', targets: list[str], current: tuple[str, ...], *,
        pool: tuple[str, ...], inherited: tuple[str, ...], inherited_from: str,
        own: bool,
    ) -> None:
        super().__init__(pane)
        current = tuple(current or ())
        # Names the pool no longer has are kept as they were, unseen.
        self._kept = tuple(name for name in current if name not in pool)
        self.value: tuple[str, ...] = current
        self.inherit = False
        title = targets[0] if len(targets) == 1 else f'{targets[0]} and {len(targets) - 1} more'
        self.setWindowTitle(f'Departments of {title}')
        layout = QVBoxLayout(self)
        layout.addWidget(_hint(
            'Untick a department to turn it off: its layers stay out of the '
            'shot\'s staged build, no new workfile is made for it, and Farm '
            'Submit and the publish leave it alone. Its workfiles and exports '
            'are kept, so ticking it again brings it back.'
        ))
        if len(targets) > 1:
            layout.addWidget(_hint(f'OK sets these on {_short(targets)}.'))
        if not own:
            layout.addWidget(_hint(f'Inherited from {inherited_from}; OK sets them here.'))
        self.boxes: dict[str, QCheckBox] = {}
        for name in pool:
            box = QCheckBox(name)
            box.setChecked(name not in current)
            layout.addWidget(box)
            self.boxes[name] = box
        if not pool:
            layout.addWidget(_hint('The project has no shot departments.'))
        buttons = QHBoxLayout()
        self._inherit = _button(
            'Inherit', self._on_inherit,
            tip=f'Back to what it inherits from {inherited_from}: '
                f'{", ".join(inherited) + " off" if inherited else "every department on"}.',
        )
        self._inherit.setVisible(own)
        buttons.addWidget(self._inherit)
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        ok = _button('OK', self._on_ok, primary=True)
        ok.setDefault(True)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def off(self) -> tuple[str, ...]:
        """The departments unticked, in pool order, then any kept name."""
        return tuple(
            name for name, box in self.boxes.items() if not box.isChecked()
        ) + self._kept

    def _on_inherit(self) -> None:
        self.inherit = True
        self.accept()

    def _on_ok(self) -> None:
        self.value = self.off()
        self.accept()


class LayersDialog(QDialog):
    """The Layers cell's editor: a shot's (or a sequence's) render layers,
    bottom to top, each a channel and the prims it holds.

    ``default`` is always the first row and cannot be renamed or removed; a
    channel ``exported`` holds department exports and cannot be removed
    either. **Use Selection** puts the prims selected in the Scene Graph
    Tree or a viewer into the current row. Each row's Matches is counted on
    the open stage when the open workfile is ``uri``'s (the farm's own
    matcher). After OK, ``value`` is the Layers, or ``inherit`` is True to
    put them back to what the shot inherits.
    """

    _NAME, _PRIMS, _MATCHES = range(3)

    def __init__(
        self, pane: '_SheetPane', targets: list[str], current: es.Layers, *,
        inherited: es.Layers, inherited_from: str, own: bool,
        exported: frozenset[str], uri: Uri | None, source: EntityEditorSource,
    ) -> None:
        super().__init__(pane)
        self._current = current
        self._exported = exported
        self._uri = uri
        self._source = source
        self.value: es.Layers = current
        self.inherit = False
        title = targets[0] if len(targets) == 1 else f'{targets[0]} and {len(targets) - 1} more'
        self.setWindowTitle(f'Layers of {title}')
        self.resize(scaled(640), scaled(420))
        layout = QVBoxLayout(self)

        layout.addWidget(_hint(
            'First row is the bottom layer. On the farm each layer renders as its own pass: '
            'the layers above it cut a hole in it, the ones below are hidden '
            'from camera, and both still cast shadows; the slapcomp stacks '
            'them back up. <b>default</b> holds every prim no other layer '
            'names. A channel without prims makes no pass.'
        ))
        if len(targets) > 1:
            layout.addWidget(_hint(f'OK sets these layers on {_short(targets)}.'))
        if not own:
            layout.addWidget(_hint(f'Inherited from {inherited_from}; OK sets them here.'))

        table = self._table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(['Layer', 'Prims', 'Matches'])
        header = table.horizontalHeader()
        header.setSectionResizeMode(self._NAME, QHeaderView.Interactive)
        header.setSectionResizeMode(self._PRIMS, QHeaderView.Stretch)
        header.setSectionResizeMode(self._MATCHES, QHeaderView.ResizeToContents)
        table.setColumnWidth(self._NAME, scaled(140))
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.currentCellChanged.connect(lambda *_a: self._update())
        layout.addWidget(table, 1)
        for name, pattern in current.entries:
            self._append(name, pattern)

        actions = QHBoxLayout()
        self._add = _button('Add Layer', self._on_add, style=_BUTTON_ADD_STYLE,
                            tip='A new layer on top.')
        self._remove = _button('Remove', self._on_remove)
        # The list reads like the Layers text, bottom layer first: Move Up
        # puts a layer one place lower in the stack.
        self._up = _button('Move Up', lambda: self._move(-1),
                           tip='One row up: one place lower in the stack.')
        self._down = _button('Move Down', lambda: self._move(1),
                             tip='One row down: one place higher in the stack.')
        self._pick = _button(
            'Use Selection', self._on_pick,
            tip='The prims selected in the Scene Graph Tree or a viewer, as '
                'this layer\'s prims.',
        )
        for button in (self._add, self._remove, self._up, self._down):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self._pick)
        layout.addLayout(actions)
        self._note = _hint()
        layout.addWidget(self._note)
        self._problem = _hint()
        self._problem.setStyleSheet(_HINT_STYLE.replace(TEXT_DIM, DANGER))
        layout.addWidget(self._problem)

        buttons = QHBoxLayout()
        self._inherit = _button(
            'Inherit', self._on_inherit,
            tip=f'Back to what it inherits from {inherited_from}: '
                f'{inherited.text() or "no layers"}.',
        )
        self._inherit.setVisible(own)
        buttons.addWidget(self._inherit)
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._ok = _button('OK', self._on_ok, primary=True)
        self._ok.setDefault(True)
        buttons.addWidget(self._ok)
        layout.addLayout(buttons)
        self._count_all()
        self._update()

    # ── Rows ─────────────────────────────────────────────

    def _append(self, name: str = '', pattern: str = '') -> None:
        table = self._table
        row = table.rowCount()
        table.insertRow(row)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText('characters')
        default = name == es.DEFAULT_CHANNEL and row == 0
        name_edit.setReadOnly(default)
        if default:
            name_edit.setToolTip('The bottom layer; every shot has it.')
        elif name in self._exported:
            name_edit.setReadOnly(True)
            name_edit.setToolTip(
                f'{name} has exports of its own, which its renders read: it '
                'stays a layer.'
            )
        prims_edit = QLineEdit(pattern)
        prims_edit.setPlaceholderText(
            'everything the other layers leave' if default
            else '/CHAR/*  (no prims: renders its own exports, no pass)'
        )
        for edit in (name_edit, prims_edit):
            edit.textChanged.connect(lambda *_a: self._update())
        prims_edit.editingFinished.connect(lambda e=prims_edit: self._count(self._row_of(e)))
        table.setCellWidget(row, self._NAME, name_edit)
        table.setCellWidget(row, self._PRIMS, prims_edit)
        table.setItem(row, self._MATCHES, QTableWidgetItem(''))

    def _row_of(self, widget) -> int:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, self._PRIMS) is widget:
                return row
        return -1

    def _text(self, row: int, column: int) -> str:
        widget = self._table.cellWidget(row, column)
        return widget.text().strip() if widget is not None else ''

    def entries(self) -> list[tuple[str, str]]:
        return [
            (self._text(row, self._NAME), self._text(row, self._PRIMS))
            for row in range(self._table.rowCount())
        ]

    def _set_rows(self, entries: list[tuple[str, str]], current: int) -> None:
        self._table.setRowCount(0)
        for name, pattern in entries:
            self._append(name, pattern)
        self._table.setCurrentCell(current, self._PRIMS)
        self._count_all()
        self._update()

    def _locked(self, row: int) -> bool:
        name = self._text(row, self._NAME)
        return row == 0 or name in self._exported

    # ── Actions ──────────────────────────────────────────

    def _on_add(self) -> None:
        self._append()
        row = self._table.rowCount() - 1
        self._table.setCurrentCell(row, self._NAME)
        self._table.cellWidget(row, self._NAME).setFocus()
        self._update()

    def _on_remove(self) -> None:
        row = self._table.currentRow()
        if row <= 0 or self._locked(row):
            return
        entries = self.entries()
        del entries[row]
        self._set_rows(entries, min(row, len(entries) - 1))

    def _move(self, step: int) -> None:
        row = self._table.currentRow()
        target = row + step
        if row <= 0 or target <= 0 or target >= self._table.rowCount():
            return    # default stays at the bottom
        entries = self.entries()
        entries[row], entries[target] = entries[target], entries[row]
        self._set_rows(entries, target)

    def _on_pick(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        paths = [p for p in self._source.selected_prims() if p]
        if not paths:
            self._note.setText(
                'Nothing is selected: select prims in the Scene Graph Tree '
                'or a viewer, then Use Selection.'
            )
            return
        self._table.cellWidget(row, self._PRIMS).setText(' '.join(paths))
        self._count(row)

    def _on_inherit(self) -> None:
        self.inherit = True
        self.accept()

    def _on_ok(self) -> None:
        try:
            self.value = es.Layers.make(self.entries(), self._current)
        except ValueError as exc:
            self._problem.setText(str(exc))
            return
        self.accept()

    # ── State ────────────────────────────────────────────

    def _count(self, row: int) -> None:
        if row < 0:
            return
        item = self._table.item(row, self._MATCHES)
        pattern = self._text(row, self._PRIMS)
        if not pattern:
            item.setText('the rest' if row == 0 else '')
            return
        count = None
        if self._uri is not None and not es.prim_pattern_problem(pattern):
            try:
                count = self._source.count_prims(self._uri, pattern)
            except Exception:  # noqa: BLE001  a stage that will not compose
                count = None
        item.setText('' if count is None else _plural(count, 'prim'))
        item.setForeground(_RED if count == 0 else QBrush())

    def _count_all(self) -> None:
        for row in range(self._table.rowCount()):
            self._count(row)
        counted = any(
            self._table.item(row, self._MATCHES).text().endswith(('prim', 'prims'))
            for row in range(self._table.rowCount())
        )
        if not counted and self._uri is not None and any(p for _, p in self.entries()):
            self._note.setText(
                'Open a workfile of this shot to see what each layer matches.'
            )

    def _update(self) -> None:
        row = self._table.currentRow()
        last = self._table.rowCount() - 1
        self._remove.setEnabled(row > 0 and not self._locked(row))
        self._up.setEnabled(row > 1)
        self._down.setEnabled(0 < row < last)
        self._pick.setEnabled(row >= 0)
        problem = ''
        try:
            layers = es.Layers.make(self.entries(), self._current)
        except ValueError as exc:
            problem = str(exc)
        else:
            lost = sorted(self._exported - set(layers.names))
            if lost:
                problem = f'{", ".join(lost)} has exports of its own: keep it'
        self._problem.setText(problem)
        self._ok.setEnabled(not problem)


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
        self._sequence.addItems(self._sheet.sorted_groups())
        self._sequence.lineEdit().setPlaceholderText('Pick or type a sequence')
        self._sequence.setCurrentText(sequence)
        self._sequence.currentTextChanged.connect(self._on_sequence_changed)
        self._sequence_note = _hint()
        row = QHBoxLayout()
        row.addWidget(self._sequence, 1)
        row.addWidget(self._sequence_note, 1)
        form.addRow('Sequence', row)
        # A flat shot list asks no sequence: the shots go into its implicit
        # one, which the caller passed as ``sequence``.
        self._flat = pane.flat_group is not None
        form.setRowVisible(0, not self._flat)

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
        if hasattr(self, '_sequence'):
            self._name_inherit()

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
        if getattr(self, '_flat', False):
            # A flat list: the next free names follow every shot shown.
            existing = {row.name for row in self._sheet.rows} | existing
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
        inherited = self._sheet.group_inherited(sequence).values
        for spin, name in ((self._frame_start, es.FRAME_START), (self._frame_end, es.FRAME_END)):
            value = inherited.get(name)
            if isinstance(value, int):
                spin.setValue(value)
        self._name_inherit()
        self._update()

    def _name_inherit(self) -> None:
        """The Inherit entry names the Root the sequence gives its shots."""
        root = self._sheet.group_inherited(self.sequence()).values.get(es.ROOT)
        source = 'Inherit' if getattr(self, '_flat', False) else 'Inherit from the sequence'
        self._root.setItemText(0, (
            f'{source} ({root_label(root)})' if isinstance(root, str)
            else f'{source} (no Root)'
        ))

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
        self._category.addItems(self._sheet.sorted_groups())
        self._category.lineEdit().setPlaceholderText('Pick or type a category')
        self._category.setCurrentText(category)
        self._category.currentTextChanged.connect(self._update)
        form.addRow('Category', self._category)
        # A flat asset list asks no category: the assets go into its
        # implicit one, which the caller passed as ``category``.
        self._flat = pane.flat_group is not None
        form.setRowVisible(0, not self._flat)
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
        elif self._flat:
            self._preview.setText(_plural(len(names), 'asset'))
        else:
            new = '' if category in self._sheet.base.groups else ' (a new category)'
            self._preview.setText(f'{_plural(len(names), "asset")} in {category}{new}')
        self._add.setText(f'Add {_plural(len(names), "Asset")}' if names else 'Add')
        self._add.setEnabled(bool(category and names))

    def rows(self) -> list[tuple[str, str, dict]]:
        return [(self.category(), name, {}) for name in self.names()]

class NewGroupDialog(QDialog):
    """A name for a new, empty sequence or category, checked as typed."""

    def __init__(self, sheet: es.Sheet, parent=None) -> None:
        super().__init__(parent)
        self._sheet = sheet
        kind = sheet.kind
        self.setWindowTitle(f'New {kind.group.capitalize()}')
        self.resize(scaled(380), 0)
        layout = QFormLayout(self)
        self._name = QLineEdit()
        self._name.setPlaceholderText('e.g. sq030' if kind is es.SHOTS_KIND else 'e.g. PROP')
        self._name.textChanged.connect(self._update)
        layout.addRow('Name', self._name)
        layout.addRow(_hint(
            f'Created on Apply, empty: Add {kind.item.capitalize()}s… fills it. '
            f'Its {kind.item}s inherit the values set on it.'
        ))
        self._problem = _hint()
        layout.addRow(self._problem)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._create = _button(f'Add {kind.group.capitalize()}', self.accept, primary=True)
        self._create.setDefault(True)
        buttons.addWidget(self._create)
        layout.addRow(buttons)
        self._update()

    def name(self) -> str:
        return self._name.text().strip()

    def problem(self) -> str:
        return self._sheet.group_problem(self.name())

    def _update(self, *_args) -> None:
        problem = self.problem()
        self._problem.setText(problem if self.name() else '')
        self._create.setEnabled(not problem)


_TIMELINE_TIP = (
    'Per shot: every member keeps its own frames. Shared: the members sit '
    'end to end on one timeline, so one graph can run through all of them; '
    'with bookmarks, the playbar gets one per shot. A member is placed '
    'after the last one when it joins, and keeps its place.'
)

_LATE_MEMBER_NOTE = (
    'A member added to a Multi whose workfile already exists has no graph in '
    'it yet: the next save of that workfile, or Sync with Multi, offers to '
    'add one.'
)


def _multi_label(multi: es.Multi) -> str:
    return multi.name or '(no name)'


class _CheckList(QWidget):
    """A filterable list of tick boxes, with an optional detail column."""

    def __init__(self, placeholder: str, headers: tuple[str, ...] = ('Name',)) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(placeholder)
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._on_filter)
        layout.addWidget(self._filter)
        table = self.table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        header = table.horizontalHeader()
        # The names are what is ticked: never cut them short ("SQ010/..."
        # on every shot row), the detail column takes what is left.
        if len(headers) > 1:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        layout.addWidget(table, 1)
        self._items: list[QTableWidgetItem] = []

    def add(
        self, text: str, data, state=Qt.Unchecked, detail: str = '',
        enabled: bool = True, tristate: bool = False,
    ) -> None:
        table = self.table
        r = table.rowCount()
        table.insertRow(r)
        item = QTableWidgetItem(text)
        item.setData(Qt.UserRole, data)
        flags = Qt.ItemIsEnabled
        if enabled:
            flags |= Qt.ItemIsUserCheckable
            if tristate:
                flags |= Qt.ItemIsUserTristate
        else:
            item.setForeground(_INHERITED)
        item.setFlags(flags)
        item.setCheckState(state)
        table.setItem(r, 0, item)
        if table.columnCount() > 1:
            extra = QTableWidgetItem(detail)
            extra.setFlags(Qt.ItemIsEnabled)
            extra.setForeground(_INHERITED)
            table.setItem(r, 1, extra)
        self._items.append(item)

    def _on_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for r, item in enumerate(self._items):
            self.table.setRowHidden(r, bool(needle) and needle not in item.text().casefold())

    def states(self) -> dict:
        return {item.data(Qt.UserRole): item.checkState() for item in self._items}

    def checked(self) -> list:
        return [
            item.data(Qt.UserRole) for item in self._items
            if item.flags() & Qt.ItemIsUserCheckable and item.checkState() == Qt.Checked
        ]

    def set_state(self, data, state) -> None:
        for item in self._items:
            if item.data(Qt.UserRole) == data:
                item.setCheckState(state)
                return
        raise KeyError(data)

    def items(self) -> list[QTableWidgetItem]:
        return list(self._items)

    def set_locked(self, item: QTableWidgetItem, locked: bool, state) -> None:
        """Show ``item`` ticked as ``state`` and, when ``locked``, greyed
        and not tickable (``checked`` leaves it out)."""
        flags = Qt.ItemIsEnabled if locked else Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
        item.setFlags(flags)
        item.setForeground(_INHERITED if locked else QBrush())
        item.setCheckState(state)


class NewMultiDialog(QDialog):
    """Name a Multi and tick the departments it covers (all of them to
    start with, as the browser's New Multi). It joins the sheet; Apply
    creates it."""

    def __init__(self, sheet: es.Sheet, note: str = '', start_with: str = '', parent=None) -> None:
        super().__init__(parent)
        self._sheet = sheet
        kind = sheet.kind
        self.setWindowTitle(f'New Multi of {kind.item}s')
        self.resize(scaled(420), scaled(480))
        layout = QFormLayout(self)
        self._name = QLineEdit()
        self._name.setPlaceholderText('e.g. Lighting')
        self._name.textChanged.connect(self._update)
        layout.addRow('Name', self._name)
        # Only shots have frames to lay end to end.
        self._timeline = None
        if kind is es.SHOTS_KIND:
            self._timeline = QComboBox()
            self._timeline.setStyleSheet(COMBO_STYLE)
            self._timeline.addItems(list(TIMELINE_CHOICES))
            self._timeline.setToolTip(_TIMELINE_TIP)
            layout.addRow('Timeline', self._timeline)
        self.departments = _CheckList('Filter departments', ('Covers',))
        for department in sheet.base.pool:
            self.departments.add(department, department, Qt.Checked)
        layout.addRow(self.departments)
        self.start_with = None
        if start_with:
            self.start_with = QPushButton()
            self.start_with.setCheckable(True)
            self.start_with.setChecked(True)
            self.start_with.setText(start_with)
            self.start_with.setStyleSheet(BUTTON_GHOST_STYLE)
            layout.addRow(self.start_with)
        layout.addRow(_hint(
            note or f'For the departments it covers, every member {kind.item}\'s '
            'workfile is the Multi\'s. Created on Apply.'
        ))
        self._problem = _hint()
        layout.addRow(self._problem)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        self._create = _button('Add Multi', self.accept, primary=True)
        self._create.setDefault(True)
        buttons.addWidget(self._create)
        layout.addRow(buttons)
        self._update()

    def name(self) -> str:
        return self._name.text().strip()

    def chosen(self) -> list[str]:
        return self.departments.checked()

    def timeline(self) -> tuple[bool, bool]:
        """``(shared timeline, bookmarks)`` as chosen."""
        if self._timeline is None:
            return False, False
        return timeline_settings(self._timeline.currentText())

    def with_selection(self) -> bool:
        return self.start_with is None or self.start_with.isChecked()

    def problem(self) -> str:
        return self._sheet.multi_name_problem(self.name())

    def _update(self, *_args) -> None:
        problem = self.problem()
        self._problem.setText(problem if self.name() else '')
        self._create.setEnabled(not problem)


class MultiMembershipDialog(QDialog):
    """Which Multis some rows belong to, ticked. A Multi only some of the
    rows are in shows partly ticked and is left alone unless clicked.
    ``changes`` is ``{multi id: member}`` after OK; ``new_multi`` is
    ``(name, departments)`` when **New Multi…** was used."""

    def __init__(
        self, pane: '_SheetPane', row_ids: list[int], groups: list[str] = (),
    ) -> None:
        super().__init__(pane)
        sheet = self._sheet = pane.sheet
        kind = sheet.kind
        rows = [sheet.row(i) for i in row_ids]
        groups = list(groups)
        self._row_ids = row_ids
        self.changes: dict[int, bool] = {}
        self.new_multi: tuple[str, list[str]] | None = None
        count = len(groups) or len(rows)
        if groups:
            who = (
                f'{kind.group} {groups[0]}' if len(groups) == 1
                else _plural(len(groups), kind.group)
            )
            text = (
                f'Tick the Multis that list {who} whole: every {kind.item} in '
                f'{"it" if len(groups) == 1 else "them"}, and any made later, is '
                'a member. For the departments a Multi covers, its members\' '
                'workfile is the Multi\'s.'
            )
        else:
            who = rows[0].name if len(rows) == 1 else _plural(len(rows), kind.item)
            text = (
                f'Tick the Multis {who} {"belongs" if len(rows) == 1 else "belong"} '
                f'to. For the departments a Multi covers, its members\' workfile '
                'is the Multi\'s.'
            )
        self.setWindowTitle(f'Multis of {who}')
        self.resize(scaled(480), scaled(460))
        layout = QVBoxLayout(self)
        intro = QLabel(text)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = _CheckList('Filter Multis', ('Multi', 'Covers'))
        self._before: dict[int, object] = {}
        for multi in sheet.multis:
            if multi.retire:
                continue
            if groups:
                inside = sum(sheet.group_member(g) in multi.members for g in groups)
                whole = []
            else:
                inside = sum(sheet.covers(multi, row) for row in rows)
                # A row in a sequence the Multi lists whole stays in while
                # the sequence does: shown ticked, and not changed here.
                whole = [
                    row for row in rows
                    if sheet.covers(multi, row) and row.id not in multi.members
                ]
            state = (
                Qt.Checked if inside == count
                else Qt.Unchecked if inside == 0 else Qt.PartiallyChecked
            )
            self._before[multi.id] = state
            detail = ', '.join(multi.departments) or 'no departments'
            if whole:
                detail += f'; whole {kind.group} {whole[0].group} listed'
            self.list.add(
                _multi_label(multi), multi.id, state, detail,
                enabled=not whole,
                tristate=state == Qt.PartiallyChecked,
            )
        layout.addWidget(self.list, 1)
        if not self._before:
            layout.addWidget(_hint(f'The project has no Multis of {kind.item}s yet.'))
        layout.addWidget(_hint(_LATE_MEMBER_NOTE))
        buttons = QHBoxLayout()
        buttons.addWidget(_button('New Multi…', self._on_new, style=_BUTTON_ADD_STYLE))
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        ok = _button('OK', self._on_ok, primary=True)
        ok.setDefault(True)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _on_ok(self) -> None:
        for multi_id, state in self.list.states().items():
            if state == self._before.get(multi_id) or state == Qt.PartiallyChecked:
                continue
            self.changes[multi_id] = state == Qt.Checked
        self.accept()

    def _on_new(self) -> None:
        dialog = NewMultiDialog(self._sheet, parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.problem():
            return
        self.new_multi = (dialog.name(), dialog.chosen())
        self._on_ok()


class MultiMembersDialog(QDialog):
    """A Multi's members, ticked from every row of its kind."""

    def __init__(self, parent, sheet: es.Sheet, multi: es.Multi) -> None:
        super().__init__(parent)
        kind = sheet.kind
        self.setWindowTitle(f'Members of {_multi_label(multi)}')
        self.resize(scaled(460), scaled(560))
        layout = QVBoxLayout(self)
        self._multi = multi
        self.list = _CheckList(f'Filter {kind.item}s', (kind.item.capitalize(), 'Also in'))
        # Whole sequences (categories) first. Ticked, every shot in it, and
        # any made in it later, is a member; its rows below show ticked and
        # greyed (take the sequence out to change them one by one).
        listed = set(sheet.multi_groups(multi))
        groups = [
            g for g in sheet.sorted_groups()
            if g in listed or any(r.group == g and not r.retire for r in sheet.rows)
        ]
        for group in groups:
            self.list.add(
                f'{group}  (whole {kind.group})', ('group', group),
                Qt.Checked if group in listed else Qt.Unchecked,
                f'every {kind.item} in it, and new ones',
            )
        self._row_group: dict[int, str] = {}
        for row in sheet.rows:
            if row.retire:
                continue
            others = [m.name for m in sheet.row_multis(row) if m is not multi]
            self.list.add(
                f'{row.group}/{row.name}', row.id,
                Qt.Checked if row.id in multi.members else Qt.Unchecked,
                ', '.join(others),
            )
            self._row_group[row.id] = row.group
        # What a row was before its sequence greyed it, to give back when
        # the sequence is unticked again.
        self._own: dict[int, object] = {}
        self._following = False
        self.list.table.itemChanged.connect(lambda _item: self._follow_groups())
        self._follow_groups()
        for stray in sheet.multi_strays(multi):
            self.list.add(
                stray.removeprefix(f'{kind.root}/'), stray, Qt.Checked,
                'no longer in the project; kept', enabled=False,
            )
        layout.addWidget(self.list, 1)
        layout.addWidget(_hint(_LATE_MEMBER_NOTE))
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(_button('Cancel', self.reject))
        ok = _button('OK', self.accept, primary=True)
        ok.setDefault(True)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _ticked_groups(self) -> set[str]:
        return {
            item.data(Qt.UserRole)[1] for item in self.list.items()
            if isinstance(item.data(Qt.UserRole), tuple)
            and item.checkState() == Qt.Checked
        }

    def _follow_groups(self) -> None:
        """Grey (and tick) the rows of every ticked sequence; give the rest
        back the tick they had."""
        if self._following:
            return
        self._following = True
        try:
            groups = self._ticked_groups()
            for item in self.list.items():
                row_id = item.data(Qt.UserRole)
                if not isinstance(row_id, int):
                    continue
                inside = self._row_group.get(row_id) in groups
                if inside and row_id not in self._own:
                    self._own[row_id] = item.checkState()
                    self.list.set_locked(item, True, Qt.Checked)
                elif not inside and row_id in self._own:
                    self.list.set_locked(item, False, self._own.pop(row_id))
        finally:
            self._following = False

    def groups(self) -> list[str]:
        """The sequences (categories) ticked whole."""
        return sorted(self._ticked_groups())

    def row_ids(self) -> list[int]:
        """The rows ticked one by one; a row a ticked sequence greys counts
        as it was before (the sheet keeps it listed only if it was)."""
        ticked = [data for data in self.list.checked() if isinstance(data, int)]
        return ticked + [
            row_id for row_id, state in self._own.items() if state == Qt.Checked
        ]
