"""The Database Editor's URI tree: one row per purpose, entities below.

Edits made in the tree (add, remove, rename, reorder an entity) are emitted
as :class:`EntityChange` and persisted by the window. Purposes are the
database's files and are not edited here.
"""

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStyledItemDelegate,
    QTreeView,
)
from tumblepipe.util.uri import Uri


def _uri_lookup(model: QStandardItemModel, uri: Uri):
    purpose, parts = uri.parts()
    if purpose not in model._items:
        return None
    item = model._items[purpose]
    for part in parts:
        if part not in item._items:
            return None
        item = item._items[part]
    return item


def _collect_descendants(item):
    """Recursively collect all descendant labels from an entity."""
    descendants = []
    for label, child in item._items.items():
        descendants.append(label)
        descendants.extend(_collect_descendants(child))
    return descendants


@dataclass(frozen=True)
class EntityOpInsert:
    label: str


@dataclass(frozen=True)
class EntityOpUpdate:
    from_label: str
    to_label: str


@dataclass(frozen=True)
class EntityOpRemove:
    label: str


@dataclass(frozen=True)
class EntityOpReorder:
    from_label: str
    to_label: str


EntityOp = EntityOpInsert | EntityOpUpdate | EntityOpRemove | EntityOpReorder


@dataclass(frozen=True)
class EntityChange:
    """``op`` applied to a child of the entity (or purpose root) ``uri``."""
    uri: Uri
    op: EntityOp


class _UriNode(QStandardItem, QObject):
    """A tree row whose children are entities: a purpose root or an entity.

    Every edit to its children is emitted on :attr:`change`, and a child's
    own ``change`` is forwarded, so the model sees each edit once.
    """

    change = Signal(object)

    def __init__(self, label: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)
        self._label = label
        self._items: dict[str, "UriPathItem"] = {}

    def uri(self) -> Uri:
        raise NotImplementedError

    def _attach(self, label: str) -> "UriPathItem":
        item = UriPathItem(label)
        item.change.connect(self.change)
        self._items[label] = item
        self.appendRow(item)
        return item

    def _ensure_entity(self, label: str) -> "UriPathItem":
        if label in self._items:
            return self._items[label]
        item = self._attach(label)
        self.change.emit(EntityChange(self.uri(), EntityOpInsert(label)))
        return item

    def _remove_entity(self, label: str) -> None:
        item = self._items.pop(label)
        item.change.disconnect(self.change)
        self.removeRow(item.row())
        self.change.emit(EntityChange(self.uri(), EntityOpRemove(label)))

    def _reorder_entity(self, from_label: str, to_label: str) -> None:
        """Move ``from_label`` to the position of ``to_label``."""
        if from_label == to_label:
            return
        to_row = self._items[to_label].row()
        row_items = self.takeRow(self._items[from_label].row())
        self.insertRow(to_row, row_items)
        self._items = {
            child._label: child
            for child in (self.child(row, 0) for row in range(self.rowCount()))
        }
        self.change.emit(
            EntityChange(self.uri(), EntityOpReorder(from_label, to_label)),
        )

    def add_parts(self, parts: list[str], notify: bool = True) -> None:
        if not parts:
            return
        label = parts.pop(0)
        item = self._items.get(label)
        if item is None:
            item = self._attach(label)
            if notify:
                self.change.emit(EntityChange(self.uri(), EntityOpInsert(label)))
        item.add_parts(parts, notify)

    def _on_add_entity(self) -> None:
        view = self.model().parent()
        uri = self.uri()
        if uri.segments:
            # Below a purpose root: the batch dialog, which writes itself.
            from .batch_entity_dialog import BatchEntityDialog
            dialog = BatchEntityDialog(
                view._adapter._api, list(uri.segments), purpose=uri.purpose,
                parent=view,
            )
            if dialog.exec_() == BatchEntityDialog.Accepted:
                view.refresh()
            return
        label, ok = QInputDialog.getText(
            view, "Add Entity", "Enter the label for the new entity:",
        )
        if not ok or not label:
            return
        if label in self._items:
            QMessageBox.warning(
                view, "Error", f'An entity with label "{label}" already exists.',
            )
            return
        self._ensure_entity(label)


class UriPathItem(_UriNode):
    """An entity row: draggable among its siblings, renamed in place."""

    def __init__(self, label: str):
        super().__init__(label)
        self.setFlags(self.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)

    def uri(self) -> Uri:
        return self.parent().uri() / self._label

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._label
            case Qt.DisplayRole:
                return self._label if not self._items else f"{self._label}/"
            case _:
                return super().data(role)

    def _on_label_changed(self, to_label: str) -> None:
        from_label = self._label
        if from_label == to_label or not to_label:
            return
        parent = self.parent()
        if to_label in parent._items:
            return  # a sibling already uses this label
        self._label = to_label
        parent._items = {
            to_label if label == from_label else label: item
            for label, item in parent._items.items()
        }
        self.change.emit(
            EntityChange(parent.uri(), EntityOpUpdate(from_label, to_label)),
        )

    def _on_remove_entity(self) -> None:
        view = self.model().parent()
        ok = QMessageBox.question(
            view, "Remove Entity",
            f'Are you sure you want to remove the entity "{self._label}"?',
        )
        if ok != QMessageBox.Yes:
            return
        was_selected = view.get_selected() == self.uri()
        self.parent()._remove_entity(self._label)
        if was_selected:
            view.clearSelection()

    def _on_context_menu(self, position) -> None:
        menu = QMenu()
        menu.addAction("Add Entity", self._on_add_entity)
        menu.addAction("Remove Entity", self._on_remove_entity)
        menu.exec_(position)


class UriPurposeItem(_UriNode):
    """A purpose's root row (``entity:/``, ``departments:/``, ...).

    Not editable: a purpose is one of the database's files, which the
    editor neither renames, adds nor removes. It accepts drops so its
    children can be reordered.
    """

    def __init__(self, label: str):
        super().__init__(label)
        self.setEditable(False)
        self.setFlags(self.flags() | Qt.ItemIsDropEnabled)

    def uri(self) -> Uri:
        return Uri(self._label, None)

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._label
            case Qt.DisplayRole:
                return f"{self._label}:" if not self._items else f"{self._label}:/"
            case _:
                return super().data(role)

    def _on_context_menu(self, position) -> None:
        menu = QMenu()
        menu.addAction("Add Entity", self._on_add_entity)
        menu.exec_(position)


class UriModel(QStandardItemModel):
    """Purpose rows; emits every :class:`EntityChange` made in the tree."""

    change = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(1)
        self.setHorizontalHeaderLabels(["URI"])
        self.invisibleRootItem().setEditable(False)
        self._items: dict[str, UriPurposeItem] = {}

    def _ensure_purpose(self, label: str) -> UriPurposeItem:
        if label in self._items:
            return self._items[label]
        item = UriPurposeItem(label)
        item.change.connect(self.change)
        self._items[label] = item
        self.appendRow(item)
        return item

    def add_entity(self, uri: Uri, notify: bool = True) -> None:
        purpose, parts = uri.parts()
        self._ensure_purpose(purpose).add_parts(parts, notify)

    def clear_all(self) -> None:
        for item in self._items.values():
            item.change.disconnect(self.change)
            self.removeRow(item.row())
        self._items.clear()


class UriDelegate(QStyledItemDelegate):
    # Renames commit once, when the editor closes (Enter, Tab or clicking
    # away — Escape cancels), not live per keystroke: every commit is
    # persisted to the database, so intermediate strings must never land.
    def createEditor(self, parent, option, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, UriPathItem):
            return QLineEdit(parent)
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, UriPathItem):
            editor.setText(item.data(Qt.EditRole))
            return
        return super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        item = model.itemFromIndex(index)
        if isinstance(item, UriPathItem):
            item._on_label_changed(editor.text())
            return
        return super().setModelData(editor, model, index)


class DatabaseUriView(QTreeView):
    """URI browser for database editor using DatabaseAdapter"""
    selected = Signal(object)
    change = Signal(object)

    def __init__(self, adapter, parent=None):
        super().__init__(parent)

        self.setSelectionMode(QTreeView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAlternatingRowColors(True)

        # Drag-and-drop settings
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # Disable Qt's default, we draw our own
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drop_indicator_rect = QRect()
        self._drop_indicator_position = None  # Store for use in dropEvent

        self._adapter = adapter
        self._selecting = False

        self.customContextMenuRequested.connect(self._on_context_menu)

        self._model = UriModel(self)
        self._model.change.connect(self._on_change)
        self._model.change.connect(self.change)
        self.setModel(self._model)

        self.setItemDelegate(UriDelegate(self))

        self._load_from_adapter()

    def _load_from_adapter(self):
        """Load entities from adapter into the model"""
        purposes = self._adapter.list_purposes()
        for purpose in purposes:
            root_uri = Uri.parse(f"{purpose}:/")
            entities = self._adapter.list_entities(root_uri)
            if not entities:
                self._model._ensure_purpose(purpose)
            else:
                for uri in entities:
                    self._model.add_entity(uri, notify=False)

    def refresh(self):
        """Refresh the URI tree from adapter"""
        current_uri = self.get_selected()
        self._selecting = True
        try:
            self._model.clear_all()
            self._load_from_adapter()
        finally:
            self._selecting = False
        if current_uri:
            self.set_selected(current_uri)

    def get_selected(self) -> Uri | None:
        """Get the currently selected URI"""
        indexes = self.selectedIndexes()
        if not indexes:
            return None
        item = self._model.itemFromIndex(indexes[0])
        if item is None:
            return None
        return item.uri()

    def set_selected(self, uri: Uri | None):
        if uri is None:
            self.clearSelection()
            return
        item = _uri_lookup(self._model, uri)
        if item is None:
            self.clearSelection()
            return
        index = item.index()
        self._selecting = True
        self.setCurrentIndex(index)
        self.scrollTo(index)
        self._selecting = False

    def selectionChanged(self, selected, _deselected):
        super().selectionChanged(selected, _deselected)
        if self._selecting:
            return
        indexes = self.selectedIndexes()
        if not indexes:
            self.selected.emit(None)
            return
        # Emit the first selected entity's URI for the JSON editor
        item = self._model.itemFromIndex(indexes[0])
        self.selected.emit(item.uri())

    def _on_item_added(self, uri):
        item = _uri_lookup(self._model, uri)
        parent = item.index().parent()
        if parent is None:
            return
        self.expand(parent)
        self.setCurrentIndex(item.index())

    def _on_change(self, change: EntityChange):
        if isinstance(change.op, EntityOpInsert):
            self._on_item_added(change.uri / change.op.label)

    def _on_context_menu(self, position):
        index = self.indexAt(position)
        if not index.isValid():
            return  # empty space: purposes are not added here
        self._model.itemFromIndex(index)._on_context_menu(
            self.mapToGlobal(position),
        )

    def mousePressEvent(self, event):
        # Clicking empty space while an editor is open must commit it. Clicks
        # on other rows commit via currentChanged, but empty space never
        # changes the current index, so the editor would silently stay open.
        if (
            self.state() == QAbstractItemView.EditingState
            and not self.indexAt(event.pos()).isValid()
        ):
            editor = self.indexWidget(self.currentIndex())
            if editor is not None:
                self.commitData(editor)
                self.closeEditor(editor, QAbstractItemDelegate.NoHint)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self._delete_selected_items()
            return
        super().keyPressEvent(event)

    def _delete_selected_items(self):
        # Get all selected items, filter to entities only (UriPathItem)
        indexes = self.selectedIndexes()
        items = []
        for index in indexes:
            item = self._model.itemFromIndex(index)
            if isinstance(item, UriPathItem):
                items.append(item)

        if not items:
            return

        # Build confirmation message showing selected items and their descendants
        lines = []
        total_count = 0
        for item in items:
            descendants = _collect_descendants(item)
            total_count += 1 + len(descendants)
            if descendants:
                lines.append(f"  • {item._label} (and {len(descendants)} children)")
            else:
                lines.append(f"  • {item._label}")

        if total_count == 1:
            message = f'Are you sure you want to remove the entity "{items[0]._label}"?'
        else:
            listing = "\n".join(lines)
            message = f"Are you sure you want to remove {total_count} entities?\n\n{listing}"

        ok = QMessageBox.question(
            self,
            "Remove Entities" if total_count > 1 else "Remove Entity",
            message,
        )
        if ok != QMessageBox.Yes:
            return

        # Delete items (collect info first to avoid issues during deletion)
        deletions = [(item.parent(), item._label) for item in items]
        for parent, label in deletions:
            parent._remove_entity(label)

        self.clearSelection()

    def dragEnterEvent(self, event):
        """Accept drag events from within the same view."""
        if event.source() == self:
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Validate drop target during drag - only allow sibling reordering."""
        target_index = self.indexAt(event.pos())
        source_index = self.currentIndex()

        if not target_index.isValid() or not source_index.isValid():
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        source_item = self._model.itemFromIndex(source_index)
        target_item = self._model.itemFromIndex(target_index)

        if source_item is None or target_item is None:
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        # Only allow UriPathItem reordering (not purposes)
        if not isinstance(source_item, UriPathItem):
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        # Only allow drops on siblings (same parent)
        if source_item.parent() != target_item.parent():
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        # Target must also be a UriPathItem (sibling)
        if not isinstance(target_item, UriPathItem):
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        # Call base class to get standard drag behavior
        super().dragMoveEvent(event)

        # Calculate drop indicator rect based on cursor position within item
        rect = self.visualRect(target_index)
        indicator_pos = self.dropIndicatorPosition()

        if indicator_pos == QAbstractItemView.BelowItem:
            # Line at bottom = will insert AFTER this item
            self._drop_indicator_rect = QRect(rect.left(), rect.bottom(), rect.width(), 0)
        else:
            # AboveItem or OnItem = will insert AT this item's position
            self._drop_indicator_rect = QRect(rect.left(), rect.top(), rect.width(), 0)

        # Store indicator position for use in dropEvent
        self._drop_indicator_position = indicator_pos

        # Force viewport repaint to show indicator
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):
        """Handle drop for reordering URI items within same parent."""
        # Use stored indicator position (dropIndicatorPosition() may differ at drop time)
        indicator_pos = self._drop_indicator_position

        # Clear drop indicator
        self._drop_indicator_rect = QRect()
        self.viewport().update()

        source_index = self.currentIndex()
        target_index = self.indexAt(event.pos())

        if not source_index.isValid() or not target_index.isValid():
            event.ignore()
            return

        source_item = self._model.itemFromIndex(source_index)
        target_item = self._model.itemFromIndex(target_index)

        if source_item is None or target_item is None:
            event.ignore()
            return

        # Only allow UriPathItem reordering
        if not isinstance(source_item, UriPathItem) or not isinstance(target_item, UriPathItem):
            event.ignore()
            return

        # Validate same parent
        if source_item.parent() != target_item.parent():
            event.ignore()
            return

        parent = source_item.parent()
        if parent is None:
            event.ignore()
            return

        from_label = source_item._label

        # Determine actual target based on drop indicator position
        if indicator_pos == QAbstractItemView.BelowItem:
            # Insert after target - find next sibling
            target_row = target_item.row()
            if target_row + 1 < parent.rowCount():
                next_item = parent.child(target_row + 1)
                to_label = next_item._label
            else:
                # Target is last item - insert at end (after target)
                # Use target's label - the reorder will handle putting source after it
                to_label = target_item._label
                # Special case: if source is before target, this is already correct
                # If source is after target (would be same item), no-op
                if from_label == to_label:
                    event.ignore()
                    return
        else:
            # AboveItem or OnItem - insert at target's position
            to_label = target_item._label

        if from_label == to_label:
            event.ignore()
            return

        parent._reorder_entity(from_label, to_label)
        event.accept()

    def paintEvent(self, event):
        """Custom paint to show prominent drop indicator line."""
        super().paintEvent(event)

        # Draw custom drop indicator line
        if self.state() == QAbstractItemView.DraggingState and not self._drop_indicator_rect.isNull():
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor("#4a90d9"), 2)
            painter.setPen(pen)

            rect = self._drop_indicator_rect
            painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
            painter.end()
