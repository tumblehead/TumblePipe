"""The Entity Editor: a project's assets and shots as spreadsheets.

**Entity Editor...** in the Asset Browser opens one window per project,
with an **Assets** and a **Shots** tab laid out like Farm Submit's. Every
entity is a row: category or sequence, name, start, end, length, pre/post
roll and fps, plus Animatable for assets and the Root and the assets it
imports for shots, and the Multis each belongs to. The sheet edits them
the way a spreadsheet does: select cells and type to set them all, copy and
paste blocks, fill down, shift frame ranges, add rows from a name pattern or
a pasted list, add empty sequences and categories, rename, retire, undo. A third tab, **Multis**, sets Multis
up: their members, a tick box per department they cover, a shots Multi's
Timeline, and in the side panel the selected Multi's workfile per covered
department, to open or to make from the department template.

Each sequence or category has a row of its own above its entities, which
are nested under it: its values are what they inherit unless they set their
own, so a sequence's Root gives its assets to every shot in it with no Root
of its own. Clicking a group's name folds its entities away.

The tabs, the tab's sequence or category picker and its search share the
top row, as in the asset browser. The Shots and Multis tabs have a side
panel right of the table (Render settings; a Multi's workfiles), resized
by dragging its edge and hidden with **Details**. Columns drag to a new
place and hide from the header's right-click menu. The window remembers
all of that (``entity_editor_layout``).

Values look the way the Database Editor shows them: inherited values are
grey, values set on the entity itself are bright and bold, and edits not
applied yet are tinted. Nothing is written until **Apply**, which writes
every tab.

The model, its checks and the writes are
:mod:`tumblepipe.config.entity_sheet`. This module and its
``entity_editor_*`` siblings (dialogs, render panel, Multis tab, shared
widgets) are only the widgets: they take everything project-specific
through :class:`EntityEditorSource`, never touch ``hou``, and run headless
(``scripts/verify_entity_editor.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelectionModel, QModelIndex, QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLineEdit,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es
from tumblepipe.config.entity_sheet import GroupKey
from tumblepipe.config import render_config
from tumblepipe.config import shot_batch
from tumblepipe.config.multi_timeline import (
    new_overlaps,
)
from tumblepipe.util.qt_lifetime import release_kept_references
from tumblepipe.util.uri import Uri

from . import entity_editor_layout as layout_prefs
from .table_scroll import (
    FROZEN_COLUMNS,
)
from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_HOVER, BG_DARK, BUTTON_GHOST_STYLE, COMBO_STYLE, TEXT_DIM,
    TEXT_PRIMARY, WARNING, scaled,
)
from .entity_editor_widgets import INHERIT, STATUS, _AMBER, _BOLD, _BUTTON_ADD_STYLE, _BUTTON_DANGER_STYLE, _Delegate, _GREEN, _HINT_STYLE, _INHERITED, _NEW, _OVERRIDE, _PENDING, _RED, _STRUCK, _Table, _brush, _button, _font, _hint, _plural, _plurals, _side_panel, root_label
from .entity_editor_dialogs import AddAssetsDialog, AddShotsDialog, DepartmentsOffDialog, LayersDialog, MultiMembershipDialog, NewGroupDialog, NewMultiDialog, NewRootDialog, RootAssetsDialog
from .entity_editor_render import _RenderSettings
from .entity_editor_multis import MultiPane
_TIMING = (es.FRAME_START, es.FRAME_END, es.LENGTH, es.ROLL_START, es.ROLL_END, es.FPS)
# Status sits beside the name it is about; the last column takes the spare
# width.
COLUMNS = {
    'assets': (es.GROUP, es.NAME, STATUS, *_TIMING, es.ANIMATABLE, es.MULTIS),
    'shots': (
        es.GROUP, es.NAME, STATUS, *_TIMING, es.RENDER_PINS, es.ROOT, es.MULTIS, es.LAYERS,
        es.DEPARTMENTS_OFF, es.ASSETS,
    ),
}
_WIDTHS = {
    es.GROUP: 100, es.NAME: 110, STATUS: 150, es.FRAME_START: 64,
    es.FRAME_END: 64, es.LENGTH: 64, es.ROLL_START: 70, es.ROLL_END: 70,
    es.FPS: 56, es.ANIMATABLE: 90, es.ROOT: 120, es.MULTIS: 150, es.ASSETS: 220,
    es.LAYERS: 200, es.DEPARTMENTS_OFF: 130, es.RENDER_PINS: 90,
}
#: Edited through a dialog, never typed or pasted into.
_DIALOG_COLUMNS = frozenset({es.ASSETS, es.MULTIS})
_NUMBERS = frozenset({
    es.FRAME_START, es.FRAME_END, es.LENGTH, es.ROLL_START, es.ROLL_END, es.FPS,
})
# A cell's text without the notes the display adds (", rigged"): what
# Copy puts on the clipboard, so it pastes back as the value
_PLAIN_ROLE = Qt.UserRole + 1


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
    'Each {group}\'s own row, above its {item}s, sets what they inherit; '
    'click its name to fold them away. '
    'New rows have green names; a row to retire is struck through.'
)


_LAYERS_TIP = (
    'Render layers, bottom to top: each channel and the prims it holds. On '
    'the farm every layer renders as its own pass, the layers above it held '
    'out and the ones below hidden from camera (both still cast shadows), '
    'and the slapcomp stacks them back up. default, at the bottom, holds '
    'every prim no other layer names. Double-click to edit; Paste takes '
    '"characters: /CHAR/*; fx: /FX/*".'
)
_DEPARTMENTS_OFF_TIP = (
    'Departments turned off on the shot: their layers stay out of its '
    'staged build, no new workfile is made for them, and Farm Submit and '
    'the publish leave them alone. Their workfiles and exports are kept. '
    'Empty: every department is on. Double-click to tick them; Paste takes '
    '"fx, lighting" ("none" turns every department on here).'
)
_COLUMN_TIPS = {es.LAYERS: _LAYERS_TIP, es.DEPARTMENTS_OFF: _DEPARTMENTS_OFF_TIP}
_ITALIC = _font(italic=True)


@dataclass(frozen=True)
class EntityEditorSource:
    """What the window needs from the project.

    ``retire`` and ``rename`` are ``pipe.entity_files.delete_entity`` and
    ``rename_entity`` on this project; ``rename_blocker`` says why an
    entity cannot be renamed ('' if it can), ``multi_rename_blocker`` the
    same of a Multi (whose workfiles sit in a folder named after it).
    ``on_applied`` hears that the database changed. ``stage_roots`` brings
    the shots up to date with the Roots after an Apply
    (``pipe.root_staging.sync_roots`` on this project) and returns what it
    did, for the footer. ``rigged`` says whether an asset has a rig
    published (``pipe.usd.instances.has_published_rig`` on this project),
    shown beside its Animatable box. ``place`` gives new members a place
    on their Multis' shared timelines (``config.groups.place_new_members``
    on this project), handed the Multis and shots an Apply wrote.

    A Multi's workfiles, for the Multis tab: ``multi_workfiles`` gives the
    latest workfile version of each department a Multi has one for
    (``{department: 'v0003'}``); ``open_multi_workfile`` opens it, and
    ``new_multi_workfile`` makes the department's next one from its
    template, then calls ``done`` (made or not). Both replace the open
    scene, asking to save it first, as the browser's Open and New from
    Template do.

    A shot's Layers, for the Shots tab: ``channel_exports`` names the
    channels it has department exports under (``pipe.render_layers.
    was_exported``), which its Layers must keep. ``selected_prims`` is the
    prims selected in the Scene Graph Tree or a viewer, for **Use
    Selection**; ``count_prims`` how many prims a pattern names on the open
    stage when the open workfile is that shot's (None otherwise), matched
    the way the farm matches it.

    Shared timelines (#31): ``overlaps`` maps each shots Multi whose
    members overlap to the pairs (``config.multi_timeline.
    overlapping_multis`` on this project), read before and after an Apply;
    ``repack`` lays one out afresh, when the artist asks.

    The window's layout (the side panel shown, its width, each tab's
    columns): ``load_layout`` reads what was remembered and ``save_layout``
    keeps it (``PipelinePrefs.entity_editor``, shaped by
    ``entity_editor_layout``).

    ``flat_bucket`` names the implicit category (``'assets'``) or sequence
    (``'shots'``) when the project lists that kind flat
    (``config.flat_entities.flat_bucket_in``), else None. Its tab then shows
    no Category / Sequence: no column, no group rows, no group picker, no
    New Category / New Sequence, and new rows go into the bucket.
    """
    project: str
    config: object
    retire: Callable[[Uri], object]
    rename: Callable[[Uri, str], object]
    rename_blocker: Callable[[Uri], str]
    on_applied: Callable[[], None]
    stage_roots: Callable[[], str] = lambda: ''
    rigged: Callable[[Uri], bool] = lambda uri: False
    multi_rename_blocker: Callable[[Uri], str] = lambda uri: ''
    place: Callable[[list[Uri]], None] = lambda uris: None
    multi_workfiles: Callable[[Uri], dict] = lambda uri: {}
    open_multi_workfile: Callable[[Uri, str], None] = lambda uri, department: None
    new_multi_workfile: Callable[[Uri, str, Callable[[], None]], None] = (
        lambda uri, department, done: done()
    )
    channel_exports: Callable[[Uri], Iterable[str]] = lambda uri: ()
    selected_prims: Callable[[], list[str]] = list
    count_prims: Callable[[Uri, str], int | None] = lambda uri, pattern: None
    overlaps: Callable[[], dict] = dict
    repack: Callable[[Uri], object] = lambda uri: None
    load_layout: Callable[[], dict] = dict
    save_layout: Callable[[dict], None] = lambda data: None
    flat_bucket: Callable[[str], str | None] = lambda kind: None


def assets_label(assets) -> str:
    return ', '.join(entry.label for entry in assets)


def cell_text(value, column: str) -> str:
    if column == es.ASSETS:
        return assets_label(value or ())
    if column == es.MULTIS:
        return ', '.join(value or ())
    return es.format_value(column, value)


# ---------------------------------------------------------------------------
# Model, filter, delegate
# ---------------------------------------------------------------------------


_GROUP_FILL = _brush(TEXT_PRIMARY, 14)


class _SheetModel(QAbstractTableModel):
    """The sheet as a table: each group's row, then its entities' rows in
    sheet order. ``collapsed`` groups show their own row only."""

    def __init__(self, pane: '_SheetPane') -> None:
        super().__init__()
        self._pane = pane
        self.sheet = pane.sheet
        self.columns = COLUMNS[self.sheet.kind.key]
        self.collapsed: set[str] = set()
        self._ids = self._layout()
        self._rigged: dict[str, bool] = {}

    def _layout(self) -> list:
        # Natural order by name, as a reload lists them: a row just added
        # sits among its neighbours, not after the last one.
        if self.sheet.flat_group is not None:
            # A flat kind: no group rows, every entity in one list by name
            # (a project that turned flat late still has several groups).
            return [row.id for row in sorted(
                self.sheet.rows,
                key=lambda row: (es.natural_key(row.name), es.natural_key(row.group)),
            )]
        keys = []
        for group in self.sheet.sorted_groups():
            keys.append(GroupKey(group))
            keys += [row.id for row in self.sheet.rows_in(group)]
        # A new row not given a group yet.
        keys += [row.id for row in self.sheet.rows if not row.group]
        return keys

    def set_sheet(self, sheet: es.Sheet) -> None:
        self.beginResetModel()
        self.sheet = sheet
        self._ids = self._layout()
        self._rigged.clear()    # a reload may follow a rig export
        self.endResetModel()

    def rigged(self, row: es.Row) -> bool:
        """Whether the stored asset has a rig published, asked once per
        asset: a rig makes a shot's copies plain references whatever its
        Animatable box says (``copies_are_animated``)."""
        if row.origin is None:
            return False
        key = str(row.origin.uri)
        if key not in self._rigged:
            try:
                self._rigged[key] = bool(
                    self._pane.window_.source.rigged(row.origin.uri)
                )
            except Exception:
                self._rigged[key] = False
        return self._rigged[key]

    def _rig_note(self, row: es.Row, column: str) -> bool:
        """An unticked Animatable on a rigged asset, which the cell notes."""
        return (
            column == es.ANIMATABLE and not row.retire
            and not self.sheet.value(row, column) and self.rigged(row)
        )

    def refresh(self) -> bool:
        """Show the sheet's current state; True when rows came, went or
        moved (the view was reset)."""
        ids = self._layout()
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

    def row_at(self, source_row: int) -> es.Row | GroupKey:
        key = self._ids[source_row]
        return key if isinstance(key, GroupKey) else self.sheet.row(key)

    def _shown(self, index) -> es.Row | GroupKey | None:
        """The row or group ``index`` shows, or None when the sheet has
        just dropped it (a new row retired) and the view has not caught up
        yet."""
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        try:
            return self.row_at(index.row())
        except KeyError:
            return None

    def id_at(self, source_row: int) -> int | GroupKey:
        # The id the view shows, even when the sheet has just dropped it.
        return self._ids[source_row]

    def source_row(self, row_id: int | GroupKey) -> int:
        return self._ids.index(row_id) if row_id in self._ids else -1

    def toggle(self, group: str) -> None:
        """Fold a group's entities away under its row, or show them."""
        self.collapsed ^= {group}
        row = self.source_row(GroupKey(group))
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, 0))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            column = self.columns[section]
            return 'Status' if column == STATUS else self.sheet.kind.label(column)
        return None

    def editable(self, row: es.Row | GroupKey, column: str) -> bool:
        if column == es.RENDER_PINS:
            return False    # a summary: the Render settings panel edits them
        if isinstance(row, GroupKey):
            # A group's values, and the Multis listing it whole; its name
            # and entities are edited elsewhere.
            return column in self._pane.values_columns or column == es.MULTIS
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
        if self.sheet.membership_pending(row):
            parts.append('Multis changed')
        return ', '.join(parts), _AMBER

    def group_status(self, group: str) -> tuple[str, QBrush]:
        sheet = self.sheet
        problem = sheet.group_value_problems().get(group) or sheet.group_problems().get(group)
        if problem:
            return problem, _RED
        if group not in sheet.base.groups:
            return f'New {sheet.kind.group}', _GREEN
        return ('Changed' if sheet.group_pending(group) else ''), _AMBER

    def _members(self, group: str) -> list[es.Row]:
        return [r for r in self.sheet.rows if r.group == group and not r.retire]

    def _render_cell(self, what: str, pins, value_of, pending: bool, role):
        """The Render column: the render settings a row pins itself, in a
        word each, and in full in its tooltip."""
        if role in (Qt.DisplayRole, Qt.EditRole, _PLAIN_ROLE):
            return render_config.pins_text(pins)
        if role == Qt.ToolTipRole:
            if not pins:
                text = f'{what.capitalize()} sets no render setting of its own: it inherits them all.'
            else:
                lines = [
                    f'  {render_config.LABELS[name]}: {es.format_value(name, value_of(name))}'
                    for name in pins
                ]
                text = f'Set on {what}:\n' + '\n'.join(lines) + '\nThe rest it inherits.'
            if pending:
                text += '\nChanged; saved on Apply.'
            return text + '\nSelect the row to see or change them in Render settings.'
        if role == Qt.ForegroundRole:
            return _OVERRIDE if pins else _INHERITED
        if role == Qt.FontRole:
            return _BOLD if pins else None
        if role == Qt.BackgroundRole and pending:
            return _PENDING
        return None

    def _group_data(self, group: str, column: str, role):
        sheet = self.sheet
        kind = sheet.kind
        if column == es.RENDER_PINS:
            value = self._render_cell(
                f'{kind.group} {group}', sheet.render_pins(GroupKey(group)),
                lambda name: sheet.cell(GroupKey(group), name).value,
                sheet.render_pending(GroupKey(group)), role,
            )
            if value is None and role == Qt.BackgroundRole:
                return _GROUP_FILL
            return value
        if role in (Qt.DisplayRole, Qt.EditRole, _PLAIN_ROLE):
            if column == STATUS:
                return self.group_status(group)[0]
            if column == es.GROUP:
                if role != Qt.DisplayRole:
                    return group
                return f'{"▸" if group in self.collapsed else "▾"} {group}'
            if column == es.NAME:
                return '' if role != Qt.DisplayRole else _plural(len(self._members(group)), kind.item)
            if column == es.MULTIS:
                return cell_text([m.name for m in sheet.group_multis(group)], column)
            if column == es.ASSETS:
                return assets_label(sheet.root_assets(sheet.cell(GroupKey(group), es.ROOT).value))
            value = sheet.cell(GroupKey(group), column).value
            if role == Qt.EditRole and column == es.ROOT:
                return value if isinstance(value, str) else ''
            return cell_text(value, column)
        if role == Qt.CheckStateRole and column == es.ANIMATABLE:
            return Qt.Checked if sheet.cell(GroupKey(group), column).value else Qt.Unchecked
        if role == Qt.ToolTipRole:
            tooltip = self._group_tooltip(group, column)
            extra = _COLUMN_TIPS.get(column)
            return f'{tooltip}\n\n{extra}' if extra else tooltip
        if column == STATUS:
            return self.group_status(group)[1] if role == Qt.ForegroundRole else None
        valued = column in self._pane.values_columns
        state = sheet.cell(GroupKey(group), column).state if valued else None
        if role == Qt.ForegroundRole:
            if column == es.NAME or state in (es.INHERITED, es.CLEARED):
                return _INHERITED
            return _OVERRIDE
        if role == Qt.FontRole:
            if column == es.GROUP or state in (es.OWN, es.SET):
                return _BOLD
            if column == es.NAME or state == es.CLEARED:
                return _ITALIC
            return None
        if role == Qt.BackgroundRole:
            if state in (es.SET, es.CLEARED):
                return _PENDING
            if column == es.GROUP and group not in sheet.base.groups:
                return _NEW
            return _GROUP_FILL
        if role == Qt.TextAlignmentRole and column in _NUMBERS:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def _group_tooltip(self, group: str, column: str) -> str:
        sheet = self.sheet
        kind = sheet.kind
        what = f'{kind.group} {group}'
        if column == STATUS:
            return self.group_status(group)[0]
        if column == es.GROUP:
            return (
                f'The {kind.group}\'s own values, which every {kind.item} in it '
                'inherits unless it sets its own. Click to fold its '
                f'{kind.item}s away or show them.'
            )
        if column == es.NAME:
            return f'The {kind.item}s in {what}.'
        if column == es.MULTIS:
            names = [m.name for m in sheet.group_multis(group)]
            listed = (
                f'Listed whole in {", ".join(names)}: every {kind.item} in it, '
                'and any made in it later, is a member. ' if names else ''
            )
            return f'{listed}Click to choose the Multis that list {what} whole.'
        if column == es.ASSETS:
            root = sheet.cell(GroupKey(group), es.ROOT).value
            if not isinstance(root, str):
                return (
                    f'{what.capitalize()} has no Root. Give it one in the Root '
                    f'column and its {kind.item}s with none of their own import '
                    'its assets.'
                )
            return (
                f'The assets of Root {root_label(root)}, which the {kind.item}s '
                f'of {what} with no Root of their own import. Change the list '
                f'from one of those {kind.item}s\' Assets cell.'
            )
        state = sheet.cell(GroupKey(group), column).state
        text = cell_text(sheet.cell(GroupKey(group), column).value, column) or 'nothing'
        source = sheet.cell(GroupKey(group), column).source or 'the project'
        users = sum(
            1 for row in self._members(group)
            if sheet.state(row, column) in (es.INHERITED, es.CLEARED)
        )
        reach = (
            f' {_plural(users, kind.item)} of it inherit{"s" if users == 1 else ""} this.'
            if users else ''
        )
        if state == es.INHERITED:
            return (
                f'{text}, inherited from {source}. Type a value to set it on '
                f'{what}: its {kind.item}s inherit it unless they set their own.{reach}'
            )
        if state == es.CLEARED:
            return f'Goes back to inheriting {text} from {source} on Apply.{reach}'
        if state == es.SET:
            return f'Set to {text} on {what} on Apply.{reach}'
        inherited = cell_text(sheet.base.for_new_group.values.get(column), column) or 'nothing'
        return (
            f'{text}, set on {what}.{reach} Delete puts it back to inheriting '
            f'{inherited} from {source}.'
        )

    def data(self, index, role=Qt.DisplayRole):
        row = self._shown(index)
        if row is None:
            return None
        column = self.columns[index.column()]
        if isinstance(row, GroupKey):
            return self._group_data(row.name, column, role)
        sheet = self.sheet
        if column == es.RENDER_PINS:
            if row.retire and role not in (Qt.DisplayRole, Qt.EditRole, _PLAIN_ROLE):
                return _STRUCK if role == Qt.FontRole else (
                    _INHERITED if role == Qt.ForegroundRole else None)
            return self._render_cell(
                f'{row.group}/{row.name}', sheet.render_pins(row.id),
                lambda name: sheet.value(row, name), sheet.render_pending(row.id), role,
            )
        if role in (Qt.DisplayRole, Qt.EditRole, _PLAIN_ROLE):
            if column == STATUS:
                return self.status(row)[0]
            if column == es.GROUP:
                return row.group
            if column == es.NAME:
                return row.name
            value = sheet.value(row, column)
            if role == Qt.EditRole and column == es.ROOT:
                return value if isinstance(value, str) else ''
            if role == Qt.DisplayRole and self._rig_note(row, column):
                return f'{cell_text(value, column)}, rigged'
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
            # A stored entity's group is its row's above: dim, as nested.
            if row.retire or state in (es.INHERITED, es.CLEARED) \
                    or (column == es.GROUP and not row.is_new):
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
            tooltip = self._tooltip(row, column)
            if column in _COLUMN_TIPS:
                tooltip = f'{tooltip}\n\n{_COLUMN_TIPS[column]}'
            if self._rig_note(row, column):
                tooltip = (
                    'Has a rig published, so a shot\'s copies of it are plain '
                    'references and each can carry its own animation, ticked '
                    'or not. Ticking it also makes its own exports write '
                    'every frame instead of the first.\n\n' + tooltip
                )
            return tooltip
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
        if column == es.MULTIS:
            return self._multis_tooltip(row)
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

    def _multis_tooltip(self, row: es.Row) -> str:
        multis = self.sheet.row_multis(row)
        kind = self.sheet.kind
        if not multis:
            lines = [f'In no Multi: the {kind.item} has its own workfiles.']
        else:
            lines = [
                f'{multi.name}: covers {", ".join(multi.departments) or "no departments"}'
                for multi in multis
            ]
            lines.append(
                'For the departments a Multi covers, the workfile is the Multi\'s.'
            )
        if self.sheet.membership_pending(row):
            lines.append('Changed; saved on Apply.')
        lines.append('Double-click to change.')
        return '\n'.join(lines)

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
    """Rows of one group, and names containing some text. A group's own row
    shows while it or one of its entities matches; a collapsed group's
    entities show only when it is the group picked."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.group = ''
        self.text = ''

    def set_filter(self, group: str, text: str) -> None:
        self.group = group
        self.text = text.strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, parent) -> bool:
        model = self.sourceModel()
        row = model.row_at(source_row)
        if isinstance(row, GroupKey):
            if self.group and row.name != self.group:
                return False
            return not self.text or self.text in row.name.casefold() or any(
                self.text in r.name.casefold()
                for r in model.sheet.rows if r.group == row.name
            )
        if self.group and row.group != self.group:
            return False
        if not self.group and row.group in model.collapsed:
            return False
        return not self.text or self.text in row.name.casefold()


#: The side panel's width when nothing is remembered.
_SIDE_WIDTH = 420


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
            c for c in self.columns if c in kind.values or c == es.LENGTH
        )
        source = window.source
        # The implicit bucket of a flat kind (config.flat_entities): no
        # Category / Sequence shown, new rows go into it.
        try:
            self.flat_group: str | None = source.flat_bucket(kind.key)
        except Exception:
            self.flat_group = None
        self.sheet = es.Sheet(
            es.load(source.config, kind), source.rename_blocker,
            source.multi_rename_blocker, source.channel_exports,
            flat_group=self.flat_group,
        )
        self._errors: list[str] = []
        self._build_ui()

    @property
    def noun(self) -> str:
        return self.kind.item

    @property
    def values_columns(self) -> frozenset[str]:
        """The columns that hold a value (not names, Status or dialogs)."""
        return self._values

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        kind = self.kind
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))

        # The group picker and search sit in the window's top row, beside
        # the tabs, as the asset browser lays them out (the window places
        # this bar there).
        self.filter_bar = QWidget()
        top = QHBoxLayout(self.filter_bar)
        top.setContentsMargins(0, 0, 0, 0)
        self._group_filter = QComboBox()
        self._group_filter.setStyleSheet(COMBO_STYLE)
        self._group_filter.setMinimumWidth(scaled(150))
        self._group_filter.setToolTip(f'Show one {kind.group}, or all of them.')
        # The artist's choice; code that sets the picker filters itself
        # (``currentIndexChanged`` also fires as the combo empties when the
        # window is deleted).
        self._group_filter.activated.connect(self._apply_filter)
        top.addWidget(self._group_filter)
        # A flat kind has no groups to pick from.
        self._group_filter.setVisible(self.flat_group is None)
        self._search = QLineEdit()
        self._search.setPlaceholderText(f'Filter {kind.item}s')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search, 1)

        # The table on the left, the side panel (Shots: Render settings)
        # on the right, in a splitter: resizable, and hidden with Details.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(scaled(6))
        legend = _hint(_legend(kind))
        legend.setWordWrap(False)
        legend.setToolTip(_LEGEND_TIP.format(group=kind.group, item=kind.item))
        self.legend = legend
        left_layout.addWidget(legend)

        # The proxy is the pane's child and made before the model: Qt
        # deletes children in creation order, and a proxy left alive (it was
        # parentless) kept a reference to a model the pane had already
        # deleted, which the cyclic collector later freed into
        # (qt_lifetime.release_kept_references).
        self.proxy = _Filter(self)
        self.model = _SheetModel(self)
        self.proxy.setSourceModel(self.model)
        table = self.table = _Table()
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
        self._default_widths()
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._context_menu)
        table.clicked.connect(self._on_clicked)
        # The group and name stay in view while the columns to their
        # right scroll (#40).
        table.freeze_columns(FROZEN_COLUMNS)
        if self.flat_group is not None:
            # A flat kind shows no Category / Sequence column.
            table.hide_frozen_column(self.columns.index(es.GROUP))
        # Columns drag to a new place and hide from the header's menu,
        # remembered per tab; the frozen group and name stay first.
        self._arranging = False
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._on_section_moved)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self._header_menu(header.mapToGlobal(pos)))
        frozen_header = table.frozen_view().horizontalHeader()
        frozen_header.setContextMenuPolicy(Qt.CustomContextMenu)
        frozen_header.customContextMenuRequested.connect(
            lambda pos: self._header_menu(frozen_header.mapToGlobal(pos)))
        left_layout.addWidget(table, 1)
        self.splitter.addWidget(left)
        outer.addWidget(self.splitter, 1)
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
        self.new_group_button = _button(
            f'New {kind.group.capitalize()}…', self.new_group,
            tip=f'A new, empty {kind.group}, created on Apply.',
        )
        # A flat kind has no category / sequence level to add to.
        self.new_group_button.setVisible(self.flat_group is None)
        actions.addWidget(self.new_group_button)
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
        # Shots: the render settings of the selection (#70) in the side
        # panel, as the asset browser shows an entity's details; the
        # Multis tab shows a Multi's workfiles there. Assets have none.
        self.render_settings: _RenderSettings | None = None
        self.side: QScrollArea | None = None
        if kind is es.SHOTS_KIND:
            self.render_settings = _RenderSettings(self)
            self.side = _side_panel(self.render_settings)
            self.splitter.addWidget(self.side)
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 0)
        outer.addLayout(actions)
        if self.render_settings is not None:
            table.selectionModel().selectionChanged.connect(
                lambda *_a: self.render_settings.fill()
            )
            self.render_settings.fill()
        table.selectionModel().selectionChanged.connect(
            lambda *_a: self.window_.update_footer()
        )
        # Qt repaints only the old and new current cells; the row tint
        # (see _Delegate.paint) needs both rows.
        table.selectionModel().currentRowChanged.connect(
            lambda *_a: table.viewport().update()
        )
        # A reset of the rows (one added or gone) must not undo the
        # artist's column order or hidden columns.
        self._column_state: tuple[list[str], set[str]] | None = None
        self.proxy.modelReset.connect(self._keep_columns)

    # ── Columns: order, shown, remembered ─────────────────

    def _default_widths(self) -> None:
        for i, column in enumerate(self.columns):
            if column in _WIDTHS:
                self.table.setColumnWidth(i, scaled(_WIDTHS[column]))

    @property
    def locked_columns(self) -> tuple[str, ...]:
        """The frozen group and name: always first, never hidden."""
        return tuple(self.columns[:FROZEN_COLUMNS])

    def column_layout(self) -> tuple[list[str], set[str]]:
        """The columns as the header shows them, left to right, and the
        hidden ones."""
        header = self.table.horizontalHeader()
        order = [self.columns[header.logicalIndex(v)] for v in range(header.count())]
        hidden = {
            c for i, c in enumerate(self.columns)
            if header.isSectionHidden(i) and not self._flat_hidden(c)
        }
        return order, hidden

    def _flat_hidden(self, name: str) -> bool:
        """The group column of a flat kind: hidden for good, never one the
        artist hid (so not remembered, nor offered in the header menu)."""
        return name == es.GROUP and self.flat_group is not None

    def set_column_layout(self, order: list[str], hidden: set[str]) -> None:
        header = self.table.horizontalHeader()
        self._arranging = True
        try:
            for visual, name in enumerate(order):
                current = header.visualIndex(self.columns.index(name))
                if current != visual:
                    header.moveSection(current, visual)
            for i, name in enumerate(self.columns):
                header.setSectionHidden(i, name in hidden or self._flat_hidden(name))
        finally:
            self._arranging = False
        self._column_state = (list(order), set(hidden))
        self.table._place_frozen()

    def restore_columns(self, data) -> None:
        """The order and hidden columns ``data`` (the remembered layout)
        holds for this tab; the defaults for anything it lacks."""
        self.set_column_layout(*layout_prefs.column_layout(
            self.columns, data, self.kind.key, self.locked_columns,
        ))

    def _keep_columns(self) -> None:
        if self._column_state is not None and self.column_layout() != self._column_state:
            self.set_column_layout(*self._column_state)

    def _visible_order(self) -> list[int]:
        """The shown columns' indexes, left to right as the header has them."""
        header = self.table.horizontalHeader()
        found = [header.logicalIndex(v) for v in range(header.count())]
        return [i for i in found if not header.isSectionHidden(i)]

    def _on_section_moved(self, *_args) -> None:
        if self._arranging:
            return
        # The frozen columns stay first: one dragged out of place, or a
        # column dragged in front of them, goes back.
        header = self.table.horizontalHeader()
        self._arranging = True
        try:
            for i in range(FROZEN_COLUMNS):
                if header.visualIndex(i) != i:
                    header.moveSection(header.visualIndex(i), i)
        finally:
            self._arranging = False
        self._column_state = self.column_layout()
        self.table._place_frozen()
        self.window_.remember_columns(self)

    def _header_menu(self, global_pos) -> None:
        menu = self.header_menu()
        menu.exec(global_pos)

    def header_menu(self) -> QMenu:
        """The header's right-click menu: a tick per column (the frozen
        group and name always shown), then Reset Columns."""
        menu = QMenu(self)
        order, hidden = self.column_layout()
        for name in order:
            if self._flat_hidden(name):
                continue
            action = menu.addAction(self.model.headerData(
                self.columns.index(name), Qt.Horizontal, Qt.DisplayRole))
            action.setCheckable(True)
            action.setChecked(name not in hidden)
            action.setEnabled(name not in self.locked_columns)
            action.setData(name)
            action.toggled.connect(lambda on, n=name: self.set_column_shown(n, on))
        menu.addSeparator()
        reset = menu.addAction('Reset Columns')
        reset.setToolTip('Every column shown, in the order and widths it started with.')
        reset.triggered.connect(self.reset_columns)
        return menu

    def set_column_shown(self, name: str, shown: bool) -> None:
        if name in self.locked_columns:
            return
        order, hidden = self.column_layout()
        hidden = hidden - {name} if shown else hidden | {name}
        if hidden >= set(self.columns) - set(self.locked_columns):
            self.say('Keep at least one column besides the names.', warn=True)
            return
        self.set_column_layout(order, hidden)
        self.window_.remember_columns(self)

    def reset_columns(self) -> None:
        self.set_column_layout(list(self.columns), set())
        self._default_widths()
        self.window_.remember_columns(self, reset=True)
        self.say('Columns back to their first order and widths, all shown.')

    # ── Refresh ──────────────────────────────────────────

    def _row_id(self, index) -> int | GroupKey:
        """The entity row id ``index`` shows, or the :class:`GroupKey` of a
        group's own row."""
        return self.model.id_at(self.proxy.mapToSource(index).row())

    def _selected(self) -> list:
        """Selected proxy indexes, top to bottom, left to right."""
        return sorted(
            self.table.selectionModel().selectedIndexes(),
            key=lambda i: (i.row(), i.column()),
        )

    def selected_row_ids(self) -> list[int]:
        """The entity rows selected; groups' own rows are not entities."""
        return [
            key for key in dict.fromkeys(self._row_id(i) for i in self._selected())
            if not isinstance(key, GroupKey)
        ]

    def selected_groups(self) -> list[str]:
        return [
            key.name for key in dict.fromkeys(self._row_id(i) for i in self._selected())
            if isinstance(key, GroupKey)
        ]

    def _on_clicked(self, index) -> None:
        # A click on a group's name folds its entities away, or shows them.
        key = self._row_id(index)
        if isinstance(key, GroupKey) and self.columns[index.column()] == es.GROUP:
            self.model.toggle(key.name)
            self.proxy.invalidateFilter()

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
            moved = self.model.refresh()
            if moved:
                self.select_cells(selection, current)
        self.table.verticalScrollBar().setValue(scroll)
        if sheet is None and moved:
            # A row that moved into its place (a new shot among the older
            # ones) stays in view; a no-op when it is already shown.
            keep = self.table.currentIndex()
            if keep.isValid():
                self.table.scrollTo(keep)
        self._fill_group_filter()
        if getattr(self, 'render_settings', None) is not None:
            self.render_settings.fill()
        # The Multis tab shows this sheet's Multis too.
        multis = getattr(self.window_, 'multis_pane', None)
        if multis is not None and multis.key == self.kind.key:
            multis.refresh()
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
        wanted = [''] + self.sheet.sorted_groups()
        combo = self._group_filter
        have = [combo.itemData(i) for i in range(combo.count())]
        tips = [combo.itemData(i, Qt.ToolTipRole) or '' for i in range(combo.count())]
        problems = self.sheet.group_problems()
        if have == wanted and tips == ['', *(problems.get(g, '') for g in wanted[1:])]:
            return
        current = combo.currentData() or ''
        combo.clear()
        combo.addItem(f'All {_plurals(self.kind.group)}', '')
        stored = set(self.sheet.base.groups)
        for group in wanted[1:]:
            combo.addItem(group if group in stored else f'{group} (new)', group)
            if group in problems:
                combo.setItemData(combo.count() - 1, problems[group], Qt.ToolTipRole)
        combo.setCurrentIndex(max(combo.findData(current), 0))
        if (combo.currentData() or '') != current:
            # The group shown is gone (undone, removed): show them all, not
            # an empty sheet still filtered on it.
            self._apply_filter()

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

    def _set_cell(self, target: es.Target, column: str, value) -> None:
        """One cell of an entity's or a group's row, as typed or pasted: ''
        or Inherit in a value column puts it back to inheriting."""
        shown = target if isinstance(target, GroupKey) else self.sheet.row(target)
        if not self.model.editable(shown, column) or column in _DIALOG_COLUMNS:
            return
        try:
            if column in self._values and (value == INHERIT or str(value).strip() == ''):
                self.sheet.reset(target, column)
            elif column == es.ROOT:
                self.sheet.set_value(target, column, self._root_value(value))
            else:
                self.sheet.set_value(target, column, value)
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
        # Left to right as the artist arranged them; hidden ones left out.
        picked = {i.column() for i in cells}
        columns = [c for c in self._visible_order() if c in picked]
        chosen = {(i.row(), i.column()) for i in cells}
        QApplication.clipboard().setText('\n'.join(
            '\t'.join(
                (self.proxy.index(r, c).data(_PLAIN_ROLE) or '') if (r, c) in chosen else ''
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
        # A block's columns run left to right as the header shows them.
        shown = self._visible_order()
        lefts = [shown.index(i.column()) for i in cells if i.column() in shown]
        if not lefts:
            return
        left = min(lefts)
        # A block runs down the entity rows: a group's row below the first
        # line is passed over, not pasted into (it is not one of them).
        rows = [
            key for p in range(top, self.proxy.rowCount())
            if not isinstance(key := self._row_id(self.proxy.index(p, 0)), GroupKey) or p == top
        ]
        targets = [
            (rows[r], self.columns[shown[left + c]], value)
            for r, line in enumerate(lines) if r < len(rows)
            for c, value in enumerate(line) if left + c < len(shown)
        ]
        missed = max(0, len(lines) - len(rows))
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
                # A flat kind puts every new row in its implicit bucket, a
                # pasted group too: it would be one the list never shows.
                group = self.flat_group if self.flat_group is not None else row.sequence
                added.append(self.sheet.add(group, row.shot, values))
        self.edit(change)
        self.select_cells([(row_id, 1) for row_id in added])
        if added:
            self.table.scrollTo(self._index_of(added[0], 1))
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
        self.edit(lambda: [self.sheet.reset(key, column) for key, column in cells])

    def _fill_down(self) -> None:
        by_column: dict[int, list] = {}
        for index in self._selected():
            by_column.setdefault(index.column(), []).append(index)

        def change():
            for col, indexes in by_column.items():
                column = self.columns[col]
                if column not in self._values or len(indexes) < 2:
                    continue
                top = self._row_id(indexes[0])
                value = self.sheet.cell(top, column).value
                if value is None:
                    continue
                for index in indexes[1:]:
                    key = self._row_id(index)
                    if not isinstance(key, GroupKey):   # down the entities only
                        self._set_cell(key, column, value)
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
        groups = self.selected_groups()
        self.edit(lambda: (self.sheet.revert(ids), self.sheet.revert_groups(groups)))

    def undo(self) -> None:
        if self.sheet.undo():
            self.refresh()

    def redo(self) -> None:
        if self.sheet.redo():
            self.refresh()

    def add_dialog(self, group: str | None = None) -> None:
        group = group if group is not None else self.proxy.group
        if self.flat_group is not None:
            # A flat kind: the dialog asks no category / sequence.
            group = self.flat_group
        dialog = (
            AddShotsDialog(self, group) if self.kind is es.SHOTS_KIND
            else AddAssetsDialog(self, group)
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.add_rows(dialog.rows())

    def new_group(self) -> None:
        """New Sequence… / New Category…: an empty one, shown at once."""
        dialog = NewGroupDialog(self.sheet, parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.problem():
            return
        added = []
        self.edit(lambda: added.append(self.sheet.add_group(dialog.name())))
        if added:
            self.focus(added[0])
            kind = self.kind
            self.say(
                f'New {kind.group} {added[0]}, created on Apply. '
                f'Add {kind.item.capitalize()}s… fills it.'
            )

    def remove_new_group(self, name: str) -> None:
        self.edit(lambda: self.sheet.remove_group(name))

    def add_rows(self, rows: list[tuple[str, str, dict]]) -> None:
        added = []
        self.edit(lambda: [added.append(self.sheet.add(*row)) for row in rows])
        if rows and self.proxy.group and rows[0][0] != self.proxy.group:
            self.focus(rows[0][0])
        self.select_cells([(row_id, 1) for row_id in added])
        if added:
            self.table.scrollTo(self._index_of(added[0], 1))

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
        if not index.isValid() or isinstance(self._row_id(index), GroupKey):
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

    def _target_rows(self, index) -> list[int]:
        """The rows an action on ``index`` is about: the selected ones when
        it is among them, else its own; never a row being retired."""
        ids = [self._row_id(index)] if index.isValid() else []
        ids = [i for i in ids if not isinstance(i, GroupKey)]
        selected = self.selected_row_ids()
        if not ids or ids[0] in selected:
            ids = selected
        return [i for i in ids if not self.sheet.row(i).retire]

    def _target_groups(self, index) -> list[str]:
        """The groups an action on ``index`` is about when it is a group's
        own row: the selected groups when it is among them, else its own.
        Empty on an entity row."""
        if not index.isValid():
            return []
        key = self._row_id(index)
        if not isinstance(key, GroupKey):
            return []
        selected = self.selected_groups()
        return selected if key.name in selected else [key.name]

    def edit_multis(self, index) -> None:
        """The Multis cell: which Multis the rows belong to, ticked. On a
        group's own row: which Multis list the group whole."""
        groups = self._target_groups(index)
        ids = [] if groups else self._target_rows(index)
        if not ids and not groups:
            return
        dialog = MultiMembershipDialog(self, ids, groups)
        if dialog.exec() != QDialog.Accepted:
            return

        def change():
            for multi_id, member in dialog.changes.items():
                self._set_membership(ids, groups, multi_id, member)
            if dialog.new_multi is not None:
                name, departments = dialog.new_multi
                multi_id = self.sheet.add_multi(name, ids, departments)
                self.sheet.set_group_membership(groups, multi_id, True)
        self.edit(change)

    def edit_layers(self, index) -> None:
        """The Layers cell: the render layers of the selected shots, or, on
        a sequence's own row, of the selected sequences (what their shots
        inherit). Opens on the clicked cell's layers; OK sets them on every
        target."""
        sheet = self.sheet

        def dialog(first: es.Target, targets: list) -> LayersDialog:
            cell = sheet.cell(first, es.LAYERS)
            rows = [sheet.row(t) for t in targets if not isinstance(t, GroupKey)]
            row = None if isinstance(first, GroupKey) else sheet.row(first)
            return LayersDialog(
                self, [self._target_label(t) for t in targets],
                cell.value if isinstance(cell.value, es.Layers) else es.Layers(),
                inherited=cell.inherited if isinstance(cell.inherited, es.Layers) else es.Layers(),
                inherited_from=cell.source or 'the project',
                own=cell.state in (es.OWN, es.SET),
                exported=frozenset().union(*(
                    sheet.exported_channels(r.origin.uri) for r in rows if r.origin is not None
                )),
                uri=row.origin.uri if row is not None and row.origin is not None else None,
                source=self.window_.source,
            )
        self._edit_cells(index, es.LAYERS, dialog)

    def edit_departments_off(self, index) -> None:
        """The Departments off cell: the shot pool with a tick per
        department, for the selected shots or, on a sequence's own row, the
        selected sequences (what their shots inherit). Opens on the clicked
        cell's value; OK sets it on every target."""
        sheet = self.sheet

        def dialog(first: es.Target, targets: list) -> DepartmentsOffDialog:
            cell = sheet.cell(first, es.DEPARTMENTS_OFF)
            return DepartmentsOffDialog(
                self, [self._target_label(t) for t in targets], tuple(cell.value or ()),
                pool=sheet.base.pool, inherited=tuple(cell.inherited or ()),
                inherited_from=cell.source or 'the project',
                own=cell.state in (es.OWN, es.SET),
            )
        self._edit_cells(index, es.DEPARTMENTS_OFF, dialog)

    def _target_label(self, target: es.Target) -> str:
        if isinstance(target, GroupKey):
            return f'{self.kind.group} {target.name}'
        row = self.sheet.row(target)
        return f'{row.group}/{row.name}'

    def _edit_cells(self, index, name: str, make_dialog) -> None:
        """A dialog column's cell: the selected groups on a group's row, else
        the selected rows. ``make_dialog(first, targets)`` opens on the
        clicked target's cell; OK sets its value, or Inherit, on every
        target, as one undo step."""
        if not index.isValid():
            return
        groups = self._target_groups(index)
        targets = [GroupKey(g) for g in groups] or self._target_rows(index)
        if not targets:
            return
        key = self._row_id(index)
        dialog = make_dialog(key if key in targets else targets[0], targets)
        if dialog.exec() != QDialog.Accepted:
            return

        def change():
            for target in targets:
                try:
                    if dialog.inherit:
                        self.sheet.reset(target, name)
                    else:
                        self.sheet.set_value(target, name, dialog.value)
                except ValueError as exc:
                    self._errors.append(f'{self._target_label(target)}: {exc}')
        self.edit(change)

    def _set_membership(self, ids, groups, multi_id: int, member: bool) -> None:
        if groups:
            self.sheet.set_group_membership(groups, multi_id, member)
        else:
            self.sheet.set_membership(ids, multi_id, member)

    def set_membership(self, multi_id: int, member: bool) -> None:
        current = self.table.currentIndex()
        groups = self._target_groups(current)
        ids = [] if groups else self._target_rows(current)
        self.edit(lambda: self._set_membership(ids, groups, multi_id, member))

    def new_multi_from_selected(self) -> None:
        current = self.table.currentIndex()
        groups = self._target_groups(current)
        ids = [] if groups else self._target_rows(current)
        if not ids and not groups:
            return
        what = (
            _plural(len(groups), self.kind.group) + ' (whole)' if groups
            else _plural(len(ids), self.kind.item)
        )
        dialog = NewMultiDialog(
            self.sheet, parent=self,
            note=f'Starts with the {what} selected. Created on Apply.',
        )
        if dialog.exec() != QDialog.Accepted or dialog.problem():
            return

        def change():
            multi_id = self.sheet.add_multi(dialog.name(), ids, dialog.chosen())
            self.sheet.set_group_membership(groups, multi_id, True)
        self.edit(change)

    def _multi_menus(self, menu: QMenu) -> None:
        current = self.table.currentIndex()
        groups = self._target_groups(current)
        ids = [] if groups else self._target_rows(current)
        live = [m for m in self.sheet.multis if not m.retire]
        add = menu.addMenu('Add to Multi')
        remove = menu.addMenu('Remove from Multi')
        rows = {i: self.sheet.row(i) for i in ids}
        listed = {g: self.sheet.group_member(g) for g in groups}
        for multi in live:
            if groups:
                can_add = any(m not in multi.members for m in listed.values())
                can_remove = any(m in multi.members for m in listed.values())
            else:
                can_add = any(not self.sheet.covers(multi, rows[i]) for i in ids)
                can_remove = any(i in multi.members for i in ids)
            if can_add:
                action = add.addAction(multi.name)
                action.triggered.connect(lambda _c=False, m=multi.id: self.set_membership(m, True))
            if can_remove:
                action = remove.addAction(multi.name)
                action.triggered.connect(lambda _c=False, m=multi.id: self.set_membership(m, False))
        targets = bool(ids) or bool(groups)
        add.setEnabled(targets and not add.isEmpty())
        remove.setEnabled(targets and not remove.isEmpty())
        action = menu.addAction('New Multi from Selected…')
        action.setEnabled(targets)
        action.triggered.connect(self.new_multi_from_selected)
        action = menu.addAction('Edit Multis…')
        action.setEnabled(targets)
        action.triggered.connect(lambda: self.edit_multis(current))

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
            entries.append(('Edit Layers…', lambda: self.edit_layers(current), current.isValid()))
        group = self.proxy.group
        if group in self.sheet.new_groups:
            entries += [None, (
                f'Remove New {self.kind.group.capitalize()} {group}',
                lambda: self.remove_new_group(group), True,
            )]
        entries += [
            None,
            'multis',
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
            if entry == 'multis':
                self._multi_menus(menu)
                continue
            text, slot, enabled = entry
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def focus(self, group: str = '', name: str = '') -> None:
        """Show ``group`` ('' for all) with ``name`` selected."""
        self._fill_group_filter()
        # A flat kind's picker is hidden: always the whole list.
        found = self._group_filter.findData(group) if self.flat_group is None else 0
        self._group_filter.setCurrentIndex(max(found, 0))
        self._apply_filter()
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
        self.multis_pane: MultiPane | None = None
        # The remembered layout (side panel, its width, columns); a prefs
        # file that will not read starts from the defaults, and says so.
        layout_problem = ''
        try:
            self._layout = dict(source.load_layout() or {})
        except Exception as exc:
            self._layout = {}
            layout_problem = f'Could not read the remembered layout, using the defaults: {exc}'
        self._build_ui()
        self.show_kind(kind)
        if layout_problem:
            self.say(layout_problem, warn=True)
        self.update_footer()

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(scaled(8))
        # One top row, as the asset browser has it: the tabs, the tab's
        # sequence or category and search, then Reload and Details.
        top = QHBoxLayout()
        self._tabs = QTabBar()
        self._tabs.setDrawBase(False)
        self._tabs.setExpanding(False)
        top.addWidget(self._tabs)
        top.addSpacing(scaled(8))
        self._filters = QStackedWidget()
        top.addWidget(self._filters, 1)
        top.addWidget(_button(
            'Reload', self._reload,
            tip='Read the assets and shots again, keeping the edits not applied yet.',
        ))
        self._details = QPushButton('Details')
        self._details.setCheckable(True)
        self._details.setAutoDefault(False)
        self._details.setStyleSheet(BUTTON_GHOST_STYLE + (
            f'QPushButton:checked {{ color: {ACCENT_HOVER}; border-color: {ACCENT}; }}'
        ))
        self._details.setToolTip(
            'Show or hide the side panel: Render settings on the Shots tab, '
            'the selected Multi\'s workfiles on the Multis tab. Drag its edge '
            'to make it wider.'
        )
        self._details.setChecked(layout_prefs.details_shown(self._layout))
        self._details.toggled.connect(self.set_details_shown)
        top.addWidget(self._details)
        outer.addLayout(top)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)
        for kind in es.KINDS:
            pane = _SheetPane(self, kind)
            self.panes[kind.key] = pane
            self._stack.addWidget(pane)
            self._filters.addWidget(pane.filter_bar)
            index = self._tabs.addTab(f'{kind.key.capitalize()}')
            self._tabs.setTabData(index, kind.key)
        self.multis_pane = MultiPane(self)
        self._stack.addWidget(self.multis_pane)
        self._filters.addWidget(self.multis_pane.filter_bar)
        index = self._tabs.addTab('Multis')
        self._tabs.setTabData(index, 'multis')
        for key, pane in self._all_panes().items():
            if isinstance(pane, _SheetPane):
                pane.restore_columns(self._layout)
            if pane.side is not None:
                sizes = layout_prefs.splitter_sizes(self._layout, key)
                pane.splitter.setSizes(sizes or [
                    max(self.width() - scaled(_SIDE_WIDTH), scaled(300)), scaled(_SIDE_WIDTH),
                ])
                pane.side.setVisible(self._details.isChecked())
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
            tip='Drop every edit, in every tab, that is not applied.',
        )
        footer.addWidget(self._discard)
        footer.addSpacing(scaled(12))
        self._summary = _hint()
        self._summary.setWordWrap(False)
        footer.addWidget(self._summary, 1)
        self._apply_button = _button(
            'Apply', self.apply, primary=True,
            tip='Write the edits of every tab to the database.',
        )
        self._apply_button.setMinimumWidth(scaled(110))
        footer.addWidget(self._apply_button)
        outer.addLayout(footer)

    @property
    def pane(self):
        """The tab in front: a :class:`_SheetPane`, or the Multis tab."""
        return self._stack.currentWidget()

    def show_kind(self, key: str) -> None:
        """Show the ``assets``, ``shots`` or ``multis`` tab."""
        pane = self.multis_pane if key == 'multis' else self.panes[key]
        self._stack.setCurrentWidget(pane)
        self._filters.setCurrentWidget(pane.filter_bar)
        index = next(i for i in range(self._tabs.count()) if self._tabs.tabData(i) == key)
        if self._tabs.currentIndex() != index:
            self._tabs.setCurrentIndex(index)
        # The Assets tab has no side panel to show.
        self._details.setEnabled(pane.side is not None)
        self.update_footer()

    # ── The remembered layout ────────────────────────────

    def _all_panes(self) -> dict:
        """Every tab by key: ``assets``, ``shots``, ``multis``."""
        found = dict(self.panes)
        if self.multis_pane is not None:
            found['multis'] = self.multis_pane
        return found

    def set_details_shown(self, shown: bool) -> None:
        """Show or hide every tab's side panel (Details), remembered."""
        if self._details.isChecked() != shown:
            self._details.setChecked(shown)     # comes back here through toggled
            return
        for pane in self._all_panes().values():
            if pane.side is not None:
                pane.side.setVisible(shown)
        self._remember(details=shown)

    def remember_columns(self, pane: '_SheetPane', reset: bool = False) -> None:
        """Keep ``pane``'s column order and hidden columns for next time."""
        if reset:
            self._remember(reset_columns=pane.kind.key)
        else:
            self._remember(columns=(pane.kind.key, *pane.column_layout()))

    def remember_splitters(self) -> None:
        """Keep each side panel's width (as the artist dragged it)."""
        changed = False
        for key, pane in self._all_panes().items():
            # isHidden, not isVisible: a tab behind the front one is not
            # visible, though its panel is shown.
            if pane.side is not None and not pane.side.isHidden():
                sizes = pane.splitter.sizes()
                if len(sizes) == 2 and min(sizes) > 0 \
                        and sizes != layout_prefs.splitter_sizes(self._layout, key):
                    self._layout = layout_prefs.remember(self._layout, splitter=(key, sizes))
                    changed = True
        if changed:
            self._remember()

    def _remember(self, **changes) -> None:
        self._layout = layout_prefs.remember(self._layout, **changes)
        try:
            self.source.save_layout(self._layout)
        except Exception as exc:
            self.say(f'Could not remember the layout: {exc}', warn=True)

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
        if not hasattr(self, '_summary') or len(self.panes) < len(es.KINDS) \
                or self.multis_pane is None:
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
            elif summary.new_groups:
                bits.append(_plural(len(summary.new_groups), f'new {kind.group}'))
            if summary.everyone_changed:
                bits.append(f'{kind.everyone} render settings changed')
            if summary.groups_changed:
                bits.append(f'{_plural(summary.groups_changed, kind.group)} changed')
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
            if summary.multis_new:
                bits.append(_plural(summary.multis_new, f'new {kind.item} Multi'))
            if summary.multis_changed:
                bits.append(f'{_plural(summary.multis_changed, f"{kind.item} Multi")} changed')
            if summary.multis_retired:
                bits.append(f'{_plural(summary.multis_retired, f"{kind.item} Multi")} to retire')
            parts.extend(bits)
            label = kind.key.capitalize()
            index = self._tab_index(key)
            self._tabs.setTabText(index, f'{label} *' if pane.sheet.dirty else label)
            pane.update_buttons()
        multis_dirty = any(
            multi.touched for pane in self.panes.values() for multi in pane.sheet.multis
        )
        self._tabs.setTabText(self._tab_index('multis'), 'Multis *' if multis_dirty else 'Multis')
        self.multis_pane.update_buttons()
        return parts, problems

    def _tab_index(self, key: str) -> int:
        return next(i for i in range(self._tabs.count()) if self._tabs.tabData(i) == key)

    def say(self, text: str = '', warn: bool = False) -> None:
        """Show ``text`` in the footer in place of the summary until the
        next edit ('' goes back to the summary); ``warn`` colours it."""
        self._message = (text, warn)
        self.update_footer()

    def focus(self, kind: str = 'shots', group: str = '', name: str = '') -> None:
        """Show ``kind``'s tab on ``group`` ('' for all) with ``name``
        selected. For ``multis``, ``group`` is the Multi's kind."""
        self.show_kind(kind)
        if kind == 'multis':
            self.multis_pane.focus(group, name)
        else:
            self.panes[kind].focus(group, name)

    def new_multi(self, context: str = 'shots', group: str = '') -> None:
        """The browser's New Multi…: the Multis tab of ``context``, asking
        for a new Multi there, offered to start with ``group`` (the
        category or sequence row it was picked on). It is pending like any
        other edit."""
        self.focus('multis', context)
        self.multis_pane.add_dialog(from_selection=False, group=group)

    def _discard_all(self) -> None:
        if not self.dirty:
            return
        answer = QMessageBox.question(
            self, 'Discard All', 'Drop every edit, in every tab, that is not applied?',
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
        """Write the edits of every tab, assets first (a Root may list a
        new asset), each kind's Multis after its entities; True when nothing
        is left pending."""
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
            if pane.sheet.group_problems():
                self.show_kind(pane.kind.key)
                name, reason = next(iter(pane.sheet.group_problems().items()))
                self.say(f'New {pane.kind.group} {name}: {reason}.', warn=True)
                return False
            if pane.sheet.group_value_problems():
                self.show_kind(pane.kind.key)
                name, reason = next(iter(pane.sheet.group_value_problems().items()))
                self.say(f'{pane.kind.group.capitalize()} {name}: {reason}.', warn=True)
                return False
            if pane.sheet.multi_problems():
                self.show_kind('multis')
                self.multis_pane.set_context(pane.kind.key)
                self.say(f'{_plural(len(pane.sheet.multi_problems()), "Multi")} need fixing first (see Status).', warn=True)
                return False
        changes = [(pane, pane.sheet.changes()) for pane in self.panes.values()]
        done = ', '.join(self._pending()[0])
        retire = [
            '/'.join(uri.segments) for _pane, change in changes for uri in change.retire
        ]
        retire_multis = [
            f'Multi {uri.segments[-1]} ({uri.segments[0]})'
            for _pane, change in changes for uri in change.multis_retire
        ]
        if retire or retire_multis:
            text = []
            if retire:
                shown = '\n'.join(retire[:12]) + ('\n…' if len(retire) > 12 else '')
                text.append(
                    f'Retire {len(retire)}? They leave the project, and their '
                    'workfiles, exports and renders move to the _deleted folder '
                    f'of each storage location.\n\n{shown}'
                )
            if retire_multis:
                text.append(
                    f'Retire {_plural(len(retire_multis), "Multi")}? They leave '
                    'the project; their workfiles stay on disk, and their '
                    'members go back to their own.\n\n' + '\n'.join(retire_multis)
                )
            answer = QMessageBox.question(self, 'Retire', '\n\n'.join(text))
            if answer != QMessageBox.Yes:
                return False
        error = None
        staged = ''
        # What failed after the writes landed, each said in the warning.
        follow_ups: list[str] = []
        overlaps_before = self.source.overlaps()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for _pane, change in changes:
                es.apply(
                    self.source.config, change,
                    retire=self.source.retire, rename=self.source.rename,
                )
        except es.ApplyError as exc:
            error = exc
        else:
            # Members the Apply added (a Multi's list, or a shot made in a
            # listed sequence) get their place on a shared timeline, as a
            # member added from the browser does.
            try:
                self.source.place([
                    uri for _pane, change in changes
                    for uri in (
                        *(u for u, _ in change.create),
                        *(u for u, _ in change.multis_create),
                        *(u for u, _ in change.multis_update),
                    )
                ])
            except Exception as exc:
                follow_ups.append(
                    f'The new members could not be placed on their shared '
                    f'timeline:\n\n{exc}'
                )
            # A shot imports its Root's assets from files (the Root's
            # export, its root layer, its staged build), not from the
            # database: stage them now, or the layout workfile opened next
            # imports none of what was just ticked.
            try:
                staged = self.source.stage_roots()
            except Exception as exc:
                follow_ups.append(
                    f'The shots could not be brought up to date with their '
                    f'Roots:\n\n{exc}\n\nRun Stage Root & Rebuild Shots… on '
                    'the Root card in the browser.'
                )
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
        if follow_ups:
            QMessageBox.warning(
                self, 'Entity Editor', 'Applied, but:\n\n' + '\n\n'.join(follow_ups),
            )
        # A longer shot can now run into the next on a shared timeline:
        # say so and offer Repack, never repack unasked.
        from .timeline_repack import offer_repack
        found = new_overlaps(overlaps_before, self.source.overlaps())
        if offer_repack(self, found, self.source.repack):
            self._rebase()
        applied = f'Applied: {done}' if done else 'Applied'
        self.say(f'{applied}; {staged}.' if staged else f'{applied}.')
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
        self.remember_splitters()
        # WA_DeleteOnClose: drop what PySide keeps on the sheets' views (the
        # frozen-column views share their table's model, selection model and
        # delegate) before Qt deletes them in creation order.
        release_kept_references(self)
        event.accept()
