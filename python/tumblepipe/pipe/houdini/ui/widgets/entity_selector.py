"""The entity picker: the window every th:: node's Browse button opens.

It looks like the asset browser because it is built from the asset
browser's own parts: TumbleTrove's theme (colours, type ramp, search box,
buttons), its Lucide icons, and its grid card renderer, so a shot or asset
card here is drawn exactly as it is in the browser, thumbnail included
(the entity's ``thumbnail.png`` sidecar).

Layout: a search box on top that filters as you type; a From context row
saying what the sentinel resolves to in this workfile; a sidebar of
scopes (Recent, All, each context and its sequences or categories, with
counts); the cards; and a footer with the pick and Select / Cancel.

Keyboard: the search box has focus, typing filters, the arrow keys move
through the cards, Enter picks the selected card (the best match after a
search), Esc cancels. Typing while the cards have focus goes to the
search. Double-click picks.

Everything the window decides that is not drawing (entries, order,
scopes, search, the From context text, recent picks) is in
:mod:`tumblepipe.pipe.entity_picker`, where it is tested.

The asset browser does not know about this window: it imports only
TumbleTrove's generic UI parts, never the other way around.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from PySide6 import QtWidgets
from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPen, QPixmap, QStandardItem, QStandardItemModel,
)

from tumbletrove.asset_browser.core.icons import icon_pixmap
from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_DIM, BG_DARK, BG_DARKEST, BG_LIGHT, BG_MID, BORDER,
    BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE, CARD_RADIUS, FONT_BODY,
    FONT_FAMILY, FONT_SMALL, FONT_TITLE, SCROLLBAR_STYLE, SEARCH_STYLE,
    TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, get_scaled_preset, px_font,
    scaled,
)

from tumblepipe.pipe import entity_picker as ep
from tumblepipe.util.uri import Uri

log = logging.getLogger(__name__)

# The entity URI on a card item and on a sidebar row's scope key.
EntityUriRole = Qt.UserRole + 1
ScopeRole = Qt.UserRole + 2

# Lucide icon per context, in the sidebar and on placeholder cards.
_CONTEXT_ICONS = {'shots': 'clapperboard', 'assets': 'box', 'edits': 'film'}

# Keys the search box hands to the cards.
_NAV_KEYS = {
    Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right,
    Qt.Key_PageUp, Qt.Key_PageDown,
}
_ACCEPT_KEYS = {Qt.Key_Return, Qt.Key_Enter}


def _renderer():
    """The asset browser's grid card renderer, or None when the installed
    TumbleTrove does not have the one this was written against."""
    try:
        from tumbletrove.asset_browser.ui.card_renderer import (
            CardState, GridCardRenderer,
        )
        from tumbletrove.asset_browser.api.types import Asset
    except ImportError:
        log.debug('No asset browser card renderer', exc_info=True)
        return None
    return GridCardRenderer(), CardState, Asset


class _CardDelegate(QtWidgets.QStyledItemDelegate):
    """Paints an entity as an asset browser card."""

    def __init__(self, picker: 'EntitySelectorDialog'):
        super().__init__(picker)
        self._picker = picker
        self._renderer = _renderer()
        preset = get_scaled_preset('normal', 1.0)
        self.card = QSize(preset['card_width'], preset['card_height'])

    def sizeHint(self, option, index) -> QSize:
        return self.card

    def paint(self, painter: QPainter, option, index: QModelIndex):
        entry = self._picker.entry(index.data(EntityUriRole))
        if entry is None:
            return
        rect = QRect(option.rect.topLeft(), self.card)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        pixmap = self._picker.thumbnail(entry.uri)
        painter.save()
        try:
            if self._renderer is not None:
                try:
                    self._paint_card(painter, rect, entry, pixmap, selected, hovered)
                    return
                except Exception:
                    # A TumbleTrove whose renderer changed shape: draw our
                    # own plain card rather than nothing.
                    log.debug('Card renderer failed', exc_info=True)
                    self._renderer = None
                    painter.restore()
                    painter.save()
            self._paint_plain(painter, rect, entry, pixmap, selected, hovered)
        finally:
            painter.restore()

    def _paint_card(self, painter, rect, entry, pixmap, selected, hovered):
        renderer, card_state, asset_type = self._renderer
        asset = asset_type(
            id=entry.uri, name=entry.name, thumbnail_url='',
            tags=frozenset({f'type:{entry.kind}'}),
        )
        state = card_state.from_asset(asset)
        state.selected = selected
        state.hovered = hovered
        state.hover_progress = 1.0 if hovered else 0.0
        # The chip in the card's corner names the sequence or category.
        state.context_label = entry.group_path
        painter.translate(rect.topLeft())
        renderer.paint(
            painter, QRect(0, 0, rect.width(), rect.height()), asset,
            pixmap, state,
        )

    def _paint_plain(self, painter, rect, entry, pixmap, selected, hovered):
        painter.setRenderHint(QPainter.Antialiasing)
        card = rect.adjusted(2, 2, -2, -2)
        pen = QPen(QColor(ACCENT if (selected or hovered) else BORDER), 2 if selected else 1)
        painter.setPen(pen)
        painter.setBrush(QColor(BG_MID))
        painter.drawRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        thumb = card.adjusted(4, 4, -4, -scaled(24))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(BG_DARK))
        painter.drawRoundedRect(thumb, CARD_RADIUS - 1, CARD_RADIUS - 1)
        if pixmap is not None:
            scaled_pm = pixmap.scaled(thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(
                thumb.x() + (thumb.width() - scaled_pm.width()) // 2,
                thumb.y() + (thumb.height() - scaled_pm.height()) // 2,
                scaled_pm,
            )
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.setFont(px_font(FONT_SMALL))
        label = QRect(card.x() + 4, thumb.bottom() + 2, card.width() - 8, card.bottom() - thumb.bottom() - 2)
        painter.drawText(
            label, Qt.AlignCenter,
            painter.fontMetrics().elidedText(entry.name, Qt.ElideRight, label.width()),
        )


def _default_thumbnail_path(api) -> Callable[[str], Path | None]:
    """An entity's ``thumbnail.png`` sidecar, where the asset browser
    reads it: in the entity's ``project:/`` folder."""
    def thumbnail_path(uri: str) -> Path | None:
        entry = ep.entry_for(uri)
        if entry is None:
            return None
        segments = Uri.parse(entry.uri).segments
        return Path(api.storage.resolve(Uri.parse('project:/') / segments)) / 'thumbnail.png'
    return thumbnail_path


def _list_entity_uris(api, entity_filter: str) -> list[str]:
    """The entities the picker offers for ``entity_filter``."""
    from tumblepipe.config.entities import is_terminal_entity

    contexts = {
        'assets': ('assets',), 'shots': ('shots',), 'both': ('assets', 'shots'),
    }.get(entity_filter, ('assets', 'shots'))
    uris = []
    # list_entities is coherent: entities created elsewhere (Database
    # Editor, another artist) are picked up on read with no refresh. The
    # coherent() scope batches the listings and the per-uri terminal
    # checks into one coherency check.
    with api.config.coherent():
        for context in contexts:
            uris.extend(api.config.list_entity_uris(
                Uri.parse(f'entity:/{context}'), closure=True,
            ))
        # list_entities(closure=True) returns any childless node, so an
        # empty category comes back as if it were an asset: keep only real
        # terminal entities (config.entities).
        uris = [u for u in uris if is_terminal_entity(api.config, u)]
    return [str(u) for u in uris]


def _project_path() -> str:
    """The open project, whose recent picks the picker shows."""
    return os.environ.get('TH_PROJECT_PATH', '')


class EntitySelectorDialog(QtWidgets.QDialog):
    """The entity picker window.

    Usage::

        dialog = EntitySelectorDialog(
            api=api,
            entity_filter='shots',          # 'assets', 'shots' or 'both'
            include_from_context=True,
            current_selection=node.parm('entity').eval(),
            from_context_uri='entity:/shots/010/sh020',  # what it resolves to
            title='Select Shot',
            parent=hou.qt.mainWindow(),
        )
        if dialog.exec_():
            selected_uri = dialog.get_selected_uri()

    ``entity_uris``, ``thumbnail_path`` and ``recent`` stand in for the
    project (tests and offscreen renders); left out, they come from
    ``api`` and the artist's prefs.
    """

    def __init__(
        self,
        api,
        entity_filter: str = 'both',
        include_from_context: bool = True,
        current_selection: str | None = None,
        title: str = 'Select Entity',
        parent=None,
        from_context_uri: str | None = None,
        entity_uris=None,
        thumbnail_path: Callable[[str], Path | None] | None = None,
        recent: list[str] | None = None,
    ):
        super().__init__(parent)
        self._api = api
        self._include_from_context = include_from_context
        self._from_context_uri = str(from_context_uri) if from_context_uri else None
        self._selected_uri: str | None = None
        self._remember = recent is None
        uris = entity_uris if entity_uris is not None else _list_entity_uris(api, entity_filter)
        self._entries = ep.entries_from_uris(uris)
        self._by_uri = {e.uri: e for e in self._entries}
        self._state = ep.PickerState(
            self._entries,
            include_from_context=include_from_context,
            recent=recent if recent is not None else ep.load_recent(_project_path()),
            current=current_selection,
        )
        self._recent = self._state.recent
        self._syncing = False
        self._thumbnail_path = thumbnail_path or (
            _default_thumbnail_path(api) if api is not None else (lambda uri: None)
        )
        self._pixmaps: dict[str, QPixmap | None] = {}
        contexts = {e.context for e in self._entries}
        self._noun = (
            f'{ep.CONTEXT_KINDS.get(next(iter(contexts)), "entity")}s'
            if len(contexts) == 1 else 'entities'
        )

        self.setWindowTitle(title)
        self.setObjectName('entityPicker')
        self.setMinimumSize(scaled(640), scaled(480))
        self.resize(scaled(900), scaled(640))
        self._create_ui(title)
        self._fill_sidebar()
        self._show()
        self._search.setFocus()

    # Entries and thumbnails (for the delegate)

    def entry(self, uri) -> ep.PickerEntry | None:
        return self._by_uri.get(uri)

    def thumbnail(self, uri: str) -> QPixmap | None:
        """The entity's thumbnail, read once per window; None without one."""
        if uri not in self._pixmaps:
            pixmap = None
            try:
                path = self._thumbnail_path(uri)
                if path is not None and Path(path).is_file():
                    loaded = QPixmap(str(path))
                    if not loaded.isNull():
                        pixmap = loaded.scaled(
                            scaled(256), scaled(256),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation,
                        )
            except Exception:
                log.debug('Thumbnail of %s unreadable', uri, exc_info=True)
            self._pixmaps[uri] = pixmap
        return self._pixmaps[uri]

    # UI

    def _create_ui(self, title: str):
        self.setStyleSheet(f"""
            QDialog#entityPicker {{ background-color: {BG_DARKEST}; }}
            QLabel {{ color: {TEXT_PRIMARY}; font-family: "{FONT_FAMILY}"; }}
        """ + SCROLLBAR_STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        pad = scaled(12)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(scaled(8))

        heading = QtWidgets.QLabel(title)
        heading.setStyleSheet(f'font-size: {FONT_TITLE}px; font-weight: bold;')
        layout.addWidget(heading)

        self._search = QtWidgets.QLineEdit()
        self._search.setStyleSheet(SEARCH_STYLE)
        self._search.setPlaceholderText(
            f'Search {self._noun}: type to filter, Enter to pick'
        )
        self._search.addAction(
            QIcon(icon_pixmap('search', scaled(16), TEXT_DIM)),
            QtWidgets.QLineEdit.LeadingPosition,
        )
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh)
        self._search.installEventFilter(self)
        layout.addWidget(self._search)

        self._from_context = QtWidgets.QPushButton(
            ep.from_context_text(self._from_context_uri)
        )
        self._from_context.setCheckable(True)
        self._from_context.setIcon(QIcon(icon_pixmap('map-pin', scaled(16), ACCENT)))
        self._from_context.setIconSize(QSize(scaled(16), scaled(16)))
        self._from_context.setToolTip(
            'Follow the workfile: the node addresses whatever entity the scene '
            'it is in belongs to, also after the scene is copied to another '
            'entity.'
        )
        self._from_context.setStyleSheet(BUTTON_GHOST_STYLE + f"""
            QPushButton {{ text-align: left; padding: {scaled(6)}px {scaled(10)}px; }}
            QPushButton:checked {{ background-color: {ACCENT_DIM}; color: {TEXT_PRIMARY}; }}
        """)
        self._from_context.clicked.connect(self._pick_from_context)
        self._from_context.setVisible(self._include_from_context)
        layout.addWidget(self._from_context)

        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(scaled(6))
        splitter.setStyleSheet('QSplitter::handle { background-color: transparent; }')

        self._sidebar = QtWidgets.QTreeWidget()
        self._sidebar.setHeaderHidden(True)
        self._sidebar.setColumnCount(2)
        self._sidebar.setRootIsDecorated(False)
        self._sidebar.setIndentation(0)
        self._sidebar.setFocusPolicy(Qt.NoFocus)
        self._sidebar.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {BG_DARK}; border: 1px solid {BORDER};
                border-radius: {CARD_RADIUS}px; color: {TEXT_SECONDARY};
                font-family: "{FONT_FAMILY}"; font-size: {FONT_BODY}px;
                padding: {scaled(4)}px; outline: 0;
            }}
            QTreeWidget::item {{ padding: {scaled(4)}px {scaled(2)}px; border: none; }}
            QTreeWidget::item:hover {{ background-color: {BG_LIGHT}; }}
            QTreeWidget::item:selected {{ background-color: {ACCENT}; color: white; }}
        """)
        self._sidebar.currentItemChanged.connect(self._on_scope_changed)
        splitter.addWidget(self._sidebar)

        self._grid = QtWidgets.QListView()
        self._grid.setViewMode(QtWidgets.QListView.IconMode)
        self._grid.setMovement(QtWidgets.QListView.Static)
        self._grid.setResizeMode(QtWidgets.QListView.Adjust)
        self._grid.setUniformItemSizes(True)
        self._grid.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._grid.setMouseTracking(True)
        self._grid.setWrapping(True)
        self._delegate = _CardDelegate(self)
        self._grid.setItemDelegate(self._delegate)
        gap = get_scaled_preset('normal', 1.0)['card_gap']
        self._grid.setGridSize(self._delegate.card + QSize(gap, gap))
        self._grid.setStyleSheet(f"""
            QListView {{
                background-color: {BG_DARKEST}; border: none; outline: 0;
            }}
        """)
        self._grid.doubleClicked.connect(self._accept_index)
        self._grid.installEventFilter(self)
        self._model = QStandardItemModel(self)
        self._grid.setModel(self._model)
        self._grid.selectionModel().currentChanged.connect(self._on_card_changed)

        self._empty = QtWidgets.QLabel('')
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(f'color: {TEXT_DIM}; font-size: {FONT_BODY}px;')
        right = QtWidgets.QWidget()
        stack = QtWidgets.QStackedLayout(right)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self._grid)
        stack.addWidget(self._empty)
        self._stack = stack
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([scaled(200), scaled(700)])
        layout.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        self._status = QtWidgets.QLabel('')
        self._status.setStyleSheet(f'color: {TEXT_SECONDARY}; font-size: {FONT_SMALL}px;')
        footer.addWidget(self._status, 1)
        cancel = QtWidgets.QPushButton('Cancel')
        cancel.setStyleSheet(BUTTON_GHOST_STYLE + f"""
            QPushButton {{ padding: {scaled(5)}px {scaled(14)}px; }}
        """)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self._ok_button = QtWidgets.QPushButton('Select')
        self._ok_button.setStyleSheet(BUTTON_PRIMARY_STYLE + f"""
            QPushButton {{ padding: {scaled(5)}px {scaled(18)}px; }}
        """)
        self._ok_button.setAutoDefault(False)
        self._ok_button.setEnabled(False)
        self._ok_button.clicked.connect(self._on_accept)
        footer.addWidget(cancel)
        footer.addWidget(self._ok_button)
        layout.addLayout(footer)

    def _fill_sidebar(self):
        self._sidebar.clear()
        recent = ep.recent_entries(self._recent, self._entries)
        if recent:
            self._sidebar_row(ep.RECENT_SCOPE, 'Recent', 0, len(recent), 'clock')
        for scope in ep.scopes(self._entries):
            if scope.depth == 0:
                icon = 'layout-grid'
            elif scope.depth == 1:
                icon = _CONTEXT_ICONS.get(scope.key, 'folder')
            else:
                icon = 'folder'
            self._sidebar_row(scope.key, scope.label, scope.depth, scope.count, icon)
        self._sidebar.resizeColumnToContents(1)
        self._sidebar.header().setStretchLastSection(False)
        self._sidebar.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._sidebar.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        for i in range(self._sidebar.topLevelItemCount()):
            item = self._sidebar.topLevelItem(i)
            if item.data(0, ScopeRole) == ep.ALL_SCOPE:
                self._sidebar.blockSignals(True)
                self._sidebar.setCurrentItem(item)
                self._sidebar.blockSignals(False)
                break

    def _sidebar_row(self, key, label, depth, count, icon):
        item = QtWidgets.QTreeWidgetItem(['    ' * max(0, depth - 1) + label, str(count)])
        item.setData(0, ScopeRole, key)
        item.setIcon(0, QIcon(icon_pixmap(icon, scaled(16), TEXT_SECONDARY)))
        item.setForeground(1, QColor(TEXT_DIM))
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        self._sidebar.addTopLevelItem(item)

    # Filtering and selection: decided by self._state, mirrored here

    def _refresh(self, *_):
        self._state.set_query(self._search.text())
        self._show()

    def _show(self):
        visible = self._state.visible
        self._model.clear()
        for entry in visible:
            item = QStandardItem(entry.name)
            item.setEditable(False)
            item.setData(entry.uri, EntityUriRole)
            item.setToolTip(entry.uri)
            self._model.appendRow(item)
        if visible:
            self._stack.setCurrentWidget(self._grid)
        else:
            self._empty.setText(
                f'No {self._noun} match "{self._search.text()}"'
                if self._search.text() else f'No {self._noun} here'
            )
            self._stack.setCurrentWidget(self._empty)
        self._sync_selection()

    def _sync_selection(self):
        """Show the state's selection: the current card, the From context
        row, the footer and whether Select can be pressed."""
        row = self._state.current_row
        self._syncing = True
        try:
            if row is None:
                self._grid.selectionModel().clearSelection()
                self._grid.setCurrentIndex(QModelIndex())
            else:
                index = self._model.index(row, 0)
                self._grid.setCurrentIndex(index)
                self._grid.scrollTo(index)
        finally:
            self._syncing = False
        uri = self._state.selected
        self._ok_button.setEnabled(self._state.accept() is not None)
        self._from_context.setChecked(uri == ep.FROM_CONTEXT)
        self._status.setText(ep.selection_text(uri, self._from_context_uri))

    def _pick_from_context(self, *_):
        self._state.pick_from_context()
        self._sync_selection()

    def _on_card_changed(self, current: QModelIndex, _previous):
        if self._syncing or not current.isValid():
            return
        self._state.select_uri(current.data(EntityUriRole))
        self._sync_selection()

    def _on_scope_changed(self, item, _previous):
        if item is None:
            return
        self._state.set_scope(item.data(0, ScopeRole))
        self._show()

    def _nav_delta(self, key) -> int:
        """How many cards an arrow or page key moves in the grid as laid out."""
        grid = self._grid.gridSize()
        viewport = self._grid.viewport().size()
        columns = max(1, viewport.width() // max(1, grid.width()))
        rows = max(1, viewport.height() // max(1, grid.height()))
        return {
            Qt.Key_Left: -1, Qt.Key_Right: 1,
            Qt.Key_Up: -columns, Qt.Key_Down: columns,
            Qt.Key_PageUp: -columns * rows, Qt.Key_PageDown: columns * rows,
        }[key]

    # Accepting

    def _accept_index(self, index: QModelIndex):
        self._state.select_uri(index.data(EntityUriRole))
        self._on_accept()

    def _on_accept(self):
        uri = self._state.accept()
        if uri is None:
            return
        self._selected_uri = uri
        if self._remember:
            ep.remember(_project_path(), uri)
        self.accept()

    def eventFilter(self, watched, event):
        if event.type() != QEvent.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if key in _ACCEPT_KEYS:
            self._on_accept()
            return True
        if watched is self._search and key in _NAV_KEYS:
            self._state.move(self._nav_delta(key))
            self._sync_selection()
            return True
        if watched is self._grid and event.text() and event.text().isprintable():
            self._search.setFocus()
            self._search.insert(event.text())
            return True
        if watched is self._grid and key == Qt.Key_Backspace:
            self._search.setFocus()
            self._search.backspace()
            return True
        return super().eventFilter(watched, event)

    def get_selected_uri(self) -> str | None:
        """The pick: an entity URI or 'from_context'; None for none. Only
        ever an entry on offer and visible when picked (PickerState)."""
        return self._selected_uri if self._selected_uri else self._state.accept()
