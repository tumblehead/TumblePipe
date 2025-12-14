from dataclasses import dataclass

from qtpy.QtCore import QObject, QRect, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPen, QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
    QAbstractItemView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStyledItemDelegate,
    QTreeView,
)
from tumblehead.util.uri import Uri


def _add_purpose_action(widget, item):
    purpose, ok = QInputDialog.getText(
        widget, "Add Purpose", "Enter the purpose for the new URI:"
    )
    if not ok or len(purpose) == 0:
        return
    if purpose in item._items:
        QMessageBox.warning(
            widget, "Error", f'A purpose with label "{purpose}" already exists.'
        )
        return
    item._ensure_purpose(purpose)


def _remove_purpose_action(widget, item):
    label = item._label
    ok = QMessageBox.question(
        widget,
        "Remove Purpose",
        f'Are you sure you want to remove the purpose "{label}"?',
    )
    if ok != QMessageBox.Yes:
        return
    item.model()._remove_purpose(label)


def _add_entity_action(widget, item, api=None, on_created=None):
    uri = item.uri()

    # Use batch dialog for all purposes with segments (not just entity)
    if api is not None and uri.segments:
        from ..dialogs.batch_entity import BatchEntityDialog
        path = list(uri.segments)
        purpose = uri.purpose
        dialog = BatchEntityDialog(api, path, purpose=purpose, parent=widget)
        if dialog.exec_() == BatchEntityDialog.Accepted:
            if on_created:
                on_created()
        return

    label, ok = QInputDialog.getText(
        widget, "Add Entity", "Enter the label for the new entity:"
    )
    if not ok or len(label) == 0:
        return
    if label in item._items:
        QMessageBox.warning(
            widget, "Error", f'An entity with label "{label}" already exists.'
        )
        return
    item._ensure_entity(label)


def _remove_entity_action(widget, item):
    label = item._label
    ok = QMessageBox.question(
        widget,
        "Remove Entity",
        f'Are you sure you want to remove the entity "{label}"?',
    )
    if ok != QMessageBox.Yes:
        return
    was_selected = widget.get_selected() == item.uri()
    item.parent()._remove_entity(label)
    if was_selected:
        widget.clearSelection()


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
class PurposeOp:
    pass


@dataclass(frozen=True)
class PurposeOpInsert(PurposeOp):
    label: str


@dataclass(frozen=True)
class PurposeOpUpdate(PurposeOp):
    from_label: str
    to_label: str


@dataclass(frozen=True)
class PurposeOpRemove(PurposeOp):
    label: str


@dataclass(frozen=True)
class PurposeChange:
    op: PurposeOp


def _purpose_change_insert(label: str) -> PurposeChange:
    return PurposeChange(PurposeOpInsert(label))


def _purpose_change_update(from_label: str, to_label: str) -> PurposeChange:
    return PurposeChange(PurposeOpUpdate(from_label, to_label))


def _purpose_change_remove(label: str) -> PurposeChange:
    return PurposeChange(PurposeOpRemove(label))


@dataclass(frozen=True)
class EntityOp:
    pass


@dataclass(frozen=True)
class EntityOpInsert(EntityOp):
    label: str


@dataclass(frozen=True)
class EntityOpUpdate(EntityOp):
    from_label: str
    to_label: str


@dataclass(frozen=True)
class EntityOpRemove(EntityOp):
    label: str


@dataclass(frozen=True)
class EntityOpReorder(EntityOp):
    from_label: str
    to_label: str


@dataclass(frozen=True)
class EntityChange:
    uri: Uri
    op: EntityOp


def _entity_change_insert(uri: Uri, label: str) -> EntityChange:
    return EntityChange(uri, EntityOpInsert(label))


def _entity_change_update(uri: Uri, from_label: str, to_label: str) -> EntityChange:
    return EntityChange(uri, EntityOpUpdate(from_label, to_label))


def _entity_change_remove(uri: Uri, label: str) -> EntityChange:
    return EntityChange(uri, EntityOpRemove(label))


def _entity_change_reorder(uri: Uri, from_label: str, to_label: str) -> EntityChange:
    return EntityChange(uri, EntityOpReorder(from_label, to_label))


@dataclass(frozen=True)
class UriChange:
    pass


@dataclass(frozen=True)
class UriChangePupose(UriChange):
    change: PurposeChange


@dataclass(frozen=True)
class UriChangeEntity(UriChange):
    change: EntityChange


def _uri_change_purpose(change: PurposeChange) -> UriChange:
    return UriChangePupose(change)


def _uri_change_entity(change: EntityChange) -> UriChange:
    return UriChangeEntity(change)


class UriPathItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, label: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        self._label = label
        self._items = dict()

        # Enable drag and drop for reordering
        flags = self.flags()
        flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
        self.setFlags(flags)

    def uri(self):
        return self.parent().uri() / self._label

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._label
            case Qt.DisplayRole:
                return (
                    self._label if len(self._items) == 0 else f"{self._label}/"
                )
            case _:
                return super().data(role)

    def _ensure_entity(self, label: str):
        if label in self._items:
            return self._items[label]
        item = UriPathItem(label)
        item.change.connect(self.change)
        self._items[label] = item
        self.appendRow(item)
        self.change.emit(_entity_change_insert(self.uri(), label))
        return item

    def _remove_entity(self, label: str):
        item = self._items[label]
        item.change.disconnect(self.change)
        index = item.row()
        self.removeRow(index)
        del self._items[label]
        self.change.emit(_entity_change_remove(self.uri(), label))

    def _reorder_entity(self, from_label: str, to_label: str):
        """Move entity from_label to the position of to_label."""
        if from_label == to_label:
            return

        # Get items
        from_item = self._items[from_label]
        to_item = self._items[to_label]

        # Get rows
        from_row = from_item.row()
        to_row = to_item.row()

        # Move in model using takeRow/insertRow
        row_items = self.takeRow(from_row)
        self.insertRow(to_row, row_items)

        # Rebuild _items dict in new order
        new_items = {}
        for row in range(self.rowCount()):
            child = self.child(row, 0)
            if isinstance(child, UriPathItem):
                new_items[child._label] = child
        self._items = new_items

        # Emit change signal
        self.change.emit(_entity_change_reorder(self.uri(), from_label, to_label))

    def add_parts(self, parts: list[str], notify: bool = True):
        if len(parts) == 0:
            return
        label = parts.pop(0)
        if label in self._items:
            item = self._items[label]
        else:
            item = UriPathItem(label)
            item.change.connect(self.change)
            self._items[label] = item
            self.appendRow(item)
            if notify:
                self.change.emit(_entity_change_insert(self.uri(), label))
        item.add_parts(parts, notify)

    def _on_label_changed(self, to_label: str):
        from_label = self._label
        if from_label == to_label:
            return
        self._label = to_label
        self.change.emit(
            _entity_change_update(self.parent().uri(), from_label, to_label)
        )

    def _on_add_entity(self):
        view = self.model().parent()
        api = view._adapter._api if hasattr(view, '_adapter') else None
        _add_entity_action(view, self, api, on_created=view.refresh)

    def _on_remove_entity(self):
        view = self.model().parent()
        _remove_entity_action(view, self)

    def _on_context_menu(self, position):
        menu = QMenu()
        menu.addAction("Add Entity", self._on_add_entity)
        menu.addAction("Remove Entity", self._on_remove_entity)
        menu.exec_(position)


class UriPurposeItem(QStandardItem, QObject):
    purpose_change = Signal(object)
    entity_change = Signal(object)

    def __init__(self, label: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        self._label = label
        self._items = dict()

        # Enable drop for child entity reordering (but not drag - purposes are fixed)
        flags = self.flags()
        flags |= Qt.ItemIsDropEnabled
        self.setFlags(flags)

    def uri(self):
        return Uri(self._label, None)

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._label
            case Qt.DisplayRole:
                return (
                    f"{self._label}:"
                    if len(self._items) == 0
                    else f"{self._label}:/"
                )
            case _:
                return super().data(role)

    def _ensure_entity(self, label: str):
        if label in self._items:
            return self._items[label]
        item = UriPathItem(label)
        item.change.connect(self.entity_change)
        self._items[label] = item
        self.appendRow(item)
        self.entity_change.emit(_entity_change_insert(self.uri(), label))
        return item

    def _remove_entity(self, label: str):
        item = self._items[label]
        item.change.disconnect(self.entity_change)
        self.removeRow(item.row())
        del self._items[label]
        self.entity_change.emit(_entity_change_remove(self.uri(), label))

    def _reorder_entity(self, from_label: str, to_label: str):
        """Move entity from_label to the position of to_label."""
        if from_label == to_label:
            return

        # Get items
        from_item = self._items[from_label]
        to_item = self._items[to_label]

        # Get rows
        from_row = from_item.row()
        to_row = to_item.row()

        # Move in model using takeRow/insertRow
        row_items = self.takeRow(from_row)
        self.insertRow(to_row, row_items)

        # Rebuild _items dict in new order
        new_items = {}
        for row in range(self.rowCount()):
            child = self.child(row, 0)
            if isinstance(child, UriPathItem):
                new_items[child._label] = child
        self._items = new_items

        # Emit change signal
        self.entity_change.emit(_entity_change_reorder(self.uri(), from_label, to_label))

    def add_parts(self, parts: list[str], notify: bool = True):
        if len(parts) == 0:
            return
        label = parts.pop(0)
        if label in self._items:
            item = self._items[label]
        else:
            item = UriPathItem(label)
            item.change.connect(self.entity_change)
            self._items[label] = item
            self.appendRow(item)
            if notify:
                self.entity_change.emit(_entity_change_insert(self.uri(), label))
        item.add_parts(parts, notify)

    def _on_label_changed(self, to_label: str):
        from_label = self._label
        if from_label == to_label:
            return
        self._label = to_label
        self.purpose_change.emit(_purpose_change_update(from_label, to_label))

    def _on_add_entity(self):
        view = self.model().parent()
        api = view._adapter._api if hasattr(view, '_adapter') else None
        _add_entity_action(view, self, api, on_created=view.refresh)

    def _on_remove_purpose(self):
        view = self.model().parent()
        _remove_purpose_action(view, self)

    def _on_context_menu(self, position):
        menu = QMenu()
        menu.addAction("Add Entity", self._on_add_entity)
        menu.addAction("Remove Purpose", self._on_remove_purpose)
        menu.exec_(position)


class UriModel(QStandardItemModel):
    change = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setColumnCount(1)
        self.setHorizontalHeaderLabels(["URI"])
        root_item = self.invisibleRootItem()
        root_item.setEditable(False)

        self._items = dict()

    def _ensure_purpose(self, label: str, notify: bool = True):
        if label in self._items:
            return self._items[label]
        item = UriPurposeItem(label)
        item.purpose_change.connect(self._on_purpose_change)
        item.entity_change.connect(self._on_entity_change)
        self._items[label] = item
        self.appendRow(item)
        if notify:
            self._on_purpose_change(_purpose_change_insert(label))
        return item

    def _remove_purpose(self, purpose: str):
        item = self._items[purpose]
        item.purpose_change.disconnect(self._on_purpose_change)
        item.entity_change.disconnect(self._on_entity_change)
        self.removeRow(item.row())
        del self._items[purpose]
        self._on_purpose_change(_purpose_change_remove(purpose))

    def add_entity(self, uri: Uri, notify: bool = True):
        purpose, parts = uri.parts()
        item = self._ensure_purpose(purpose, notify)
        item.add_parts(parts, notify)

    def clear_all(self):
        for purpose in list(self._items.keys()):
            item = self._items[purpose]
            item.purpose_change.disconnect(self._on_purpose_change)
            item.entity_change.disconnect(self._on_entity_change)
            self.removeRow(item.row())
        self._items.clear()

    def _on_purpose_change(self, change: PurposeChange):
        self.change.emit(_uri_change_purpose(change))

    def _on_entity_change(self, change: EntityChange):
        self.change.emit(_uri_change_entity(change))

    def _on_add_purpose(self):
        view = self.parent()
        _add_purpose_action(view, self)

    def _on_context_menu(self, position):
        menu = QMenu()
        menu.addAction("Add Purpose", self._on_add_purpose)
        menu.exec_(position)


class UriDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, UriPathItem):
            editor = QLineEdit(parent)
            editor.textChanged.connect(item._on_label_changed)
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, UriPathItem):
            editor.setText(item.data(Qt.EditRole))
            return
        return super().setEditorData(editor, index)


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
            root_uri = Uri.parse_unsafe(f"{purpose}:/")
            entities = self._adapter.list_entities(root_uri)
            if not entities:
                self._model._ensure_purpose(purpose, notify=False)
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

    def _on_change(self, change: UriChange):
        match change:
            case UriChangeEntity(entity_change):
                self._on_entity_change(entity_change)
            case _:
                pass

    def _on_entity_change(self, change: EntityChange):
        match change.op:
            case EntityOpInsert(label):
                self._on_item_added(change.uri / label)
            case _:
                pass

    def _on_context_menu(self, position):
        index = self.indexAt(position)
        target = (
            self._model
            if not index.isValid()
            else self._model.itemFromIndex(index)
        )
        target._on_context_menu(self.mapToGlobal(position))

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

        if hasattr(parent, '_reorder_entity'):
            parent._reorder_entity(from_label, to_label)
            event.accept()
        else:
            event.ignore()

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
