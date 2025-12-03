from dataclasses import dataclass

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtGui import QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
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
class EntityChange:
    uri: Uri
    op: EntityOp


def _entity_change_insert(uri: Uri, label: str) -> EntityChange:
    return EntityChange(uri, EntityOpInsert(label))


def _entity_change_update(uri: Uri, from_label: str, to_label: str) -> EntityChange:
    return EntityChange(uri, EntityOpUpdate(from_label, to_label))


def _entity_change_remove(uri: Uri, label: str) -> EntityChange:
    return EntityChange(uri, EntityOpRemove(label))


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

        self.setSelectionMode(QTreeView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAlternatingRowColors(True)

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
        self._model.clear_all()
        self._load_from_adapter()
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
        if not selected.indexes():
            self.selected.emit(None)
            return
        index = selected.indexes()[0]
        item = self._model.itemFromIndex(index)
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
