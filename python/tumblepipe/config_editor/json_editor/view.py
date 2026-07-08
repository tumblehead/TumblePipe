"""Qt model/delegate/view stack for the JSON tree editor.

``JsonModel`` (the QStandardItemModel), ``FloatValidator``, ``JsonItemDelegate``
and ``JsonView`` (the QTreeView). Builds on the item classes and factories in
``items`` — the dependency runs one way (view -> items).
"""

from qtpy.QtCore import QRect, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QIntValidator,
    QPainter,
    QPen,
    QStandardItemModel,
    QValidator,
)
from qtpy.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QTreeView,
)

from .types import FieldOrigin, JsonValue, JsonRoot
from .diff import _value_tree, _diff_tree
from .path import (
    JsonPath, JsonPathRoot, _json_path_contained, _diff_lookup,
)
from .ops import (
    JsonOpIndexInsert, JsonOpFieldInsert, JsonChange,
    _change_field_insert, _change_field_update, _change_field_remove, _change_field_reorder,
)
from .items import (
    BooleanItem, IntegerItem, FloatItem, StringItem,
    FieldBasicItem, FieldObjectItem, FieldArrayItem,
    _type_item, _field_item, _value_item,
    _parent, _json_path_lookup,
    _add_field_action, _add_field_section,
)


class JsonModel(QStandardItemModel):
    change = Signal(object)

    def __init__(self, value: JsonRoot = dict(), inherited_data: dict = None, parent=None):
        super().__init__(parent)

        # Settings
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Structure", "Value", "Type"])
        root_item = self.invisibleRootItem()
        root_item.setEditable(False)

        # Members
        self._value = value
        self._inherited_data = inherited_data or {}
        self._diff = _value_tree(value, False)
        self._fields = dict()
        self._changes = list()

        # Connect signals
        self.change.connect(self._on_change)

        # Set the initial value
        self.set_value(value, inherited_data)

    def path(self) -> JsonPath:
        return JsonPathRoot()

    def __contains__(self, key: str):
        assert isinstance(key, str), f"Expected str, got {type(key)}"
        return key in self._fields

    def data(self, index, role=None):
        if not index.isValid():
            return None
        if role != Qt.DisplayRole:
            return super().data(index, role)
        if index.column() != 0:
            return super().data(index, role)
        item = self.itemFromIndex(index)
        label = item.data(role)
        return f"* {label}" if self.has_change(item.path()) else label

    def _add_field(
        self,
        key: str,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        inherited_value: JsonValue = None,
        notify: bool = True
    ):
        # Create the row items with origin info
        field_item = _field_item(key, value, origin, inherited_value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)

        # Insert the row into the model
        self.insertRow(self.rowCount(), [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_insert(self.path(), key, value))

    def _set_field(
        self,
        key: str,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        inherited_value: JsonValue = None,
        notify: bool = True
    ):
        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Add the field to the model
        field_item = _field_item(key, value, origin, inherited_value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Rebuild _fields in row order (del + re-add pushed the key to the end)
        new_fields = {}
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if hasattr(item, '_key'):
                new_fields[item._key] = item
        self._fields = new_fields

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_update(self.path(), key, value))

    def _remove_field(self, key: str, notify: bool = True):
        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_remove(self.path(), key))

    def _set_field_to_inherited(self, key: str, inherited_value: JsonValue, notify: bool = True):
        """Revert an overridden field back to its inherited value."""
        # Remove the current field
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Re-add as inherited field with inherited value
        field_item = _field_item(key, inherited_value, FieldOrigin.INHERITED)
        field_item.change.connect(self.change)
        value_item = _value_item(inherited_value)
        type_item = _type_item(inherited_value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Emit change signal (removal of override)
        if notify:
            self.change.emit(_change_field_remove(self.path(), key))

    def _reorder_field(self, from_key: str, to_key: str, notify: bool = True):
        """Move field from_key to the position of to_key."""
        if from_key == to_key:
            return

        # Get items
        from_item = self._fields[from_key]
        to_item = self._fields[to_key]

        # Get rows
        from_row = from_item.row()
        to_row = to_item.row()

        # Move in model using takeRow/insertRow
        row_items = self.takeRow(from_row)
        self.insertRow(to_row, row_items)

        # Rebuild _fields dict in new order
        new_fields = {}
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if hasattr(item, '_key'):
                new_fields[item._key] = item
        self._fields = new_fields

        # Emit change signal
        if not notify:
            return
        self.change.emit(
            _change_field_reorder(
                self.path(),
                from_key,
                to_key,
                from_item.to_json(),
                to_item.to_json(),
            )
        )

    def set_value(self, values: JsonRoot, inherited_data: dict = None):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            index = field_item.row()
            self.removeRow(index)
        self._fields.clear()

        # Clear existing changes
        self._changes.clear()

        # Update the value and diff
        self._value = values
        self._diff = _value_tree(values, False)

        # Update inherited data
        inherited = inherited_data if inherited_data is not None else self._inherited_data
        self._inherited_data = inherited

        # Order: inherited keys first (in their order), then local-only keys (in their order)
        ordered_keys = list(inherited.keys()) + [k for k in values.keys() if k not in inherited]

        # Add fields with computed origins
        for key in ordered_keys:
            in_stored = key in values
            in_inherited = key in inherited

            if in_stored and not in_inherited:
                origin = FieldOrigin.LOCAL
                value = values[key]
                inherited_value = None
            elif in_inherited and not in_stored:
                origin = FieldOrigin.INHERITED
                value = inherited[key]
                inherited_value = inherited[key]
            else:
                # Both - compare values
                stored_val = values[key]
                inherited_val = inherited[key]
                if stored_val == inherited_val:
                    origin = FieldOrigin.REDUNDANT
                else:
                    origin = FieldOrigin.OVERRIDE
                value = stored_val
                inherited_value = inherited_val

            self._add_field(key, value, origin, inherited_value, notify=False)

    def has_change(self, path: JsonPath = JsonPathRoot()) -> bool:
        if len(self._changes) == 0:
            return False
        result = _diff_lookup(self._diff, path)
        if result is None:
            return False
        return result["change"]

    def list_changes(self, path: JsonPath = JsonPathRoot()) -> list[JsonChange]:
        if len(self._changes) == 0:
            return []
        return list(
            filter(
                lambda change: (
                    _json_path_contained(path, change.path)
                    or _json_path_contained(change.path, path)
                ),
                self._changes,
            )
        )

    def discard_changes(self):
        self.set_value(self._value, self._inherited_data)

    def _on_modify_action(self, action):
        match action:
            case "add":
                return _add_field_action(_parent(self), self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_field_section(menu, self._on_modify_action)
        menu.exec_(position)

    def _on_change(self, change: JsonChange):
        self._changes.append(change)
        self._diff = _diff_tree(self._value, self.to_json())

    def to_json(self):
        # Only return non-inherited fields (exclude fields that are purely inherited)
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
            if field_item._origin != FieldOrigin.INHERITED
        }


class FloatValidator(QValidator):
    def validate(self, input: str, _pos: int) -> QValidator.State:
        try:
            float(input)
            return QValidator.Acceptable
        except ValueError:
            return QValidator.Invalid

    def fixup(self, input: str) -> str:
        try:
            return str(float(input))
        except ValueError:
            return "0.0"


class JsonItemDelegate(QStyledItemDelegate):
    # Commits go through setModelData (invoked by commitData on Enter, Tab and
    # focus-out alike) rather than the editor's editingFinished signal: a
    # commit triggers dataChanged, which makes the view rewrite the still-open
    # editor via setEditorData — clobbering the typed text before
    # editingFinished ever fires.
    def createEditor(self, parent, option, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, BooleanItem):

            def _on_state_changed(state):
                item._on_value_changed(bool(state))

            editor = QCheckBox(parent)
            editor.stateChanged.connect(_on_state_changed)
            return editor
        if isinstance(item, IntegerItem):
            editor = QLineEdit(parent)
            editor.setValidator(QIntValidator(editor))
            return editor
        if isinstance(item, FloatItem):
            editor = QLineEdit(parent)
            editor.setValidator(FloatValidator(editor))
            return editor
        if isinstance(item, (
            StringItem, FieldBasicItem, FieldObjectItem, FieldArrayItem,
        )):
            return QLineEdit(parent)
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, BooleanItem):
            editor.setChecked(item.data(Qt.EditRole))
            return
        if isinstance(item, IntegerItem):
            editor.setText(str(item.data(Qt.EditRole)))
            return
        if isinstance(item, FloatItem):
            editor.setText(str(item.data(Qt.EditRole)))
            return
        if isinstance(item, StringItem):
            editor.setText(item.data(Qt.EditRole))
            return
        if isinstance(item, (FieldBasicItem, FieldObjectItem, FieldArrayItem)):
            editor.setText(item.data(Qt.EditRole))
            return
        return super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        item = model.itemFromIndex(index)
        if isinstance(item, BooleanItem):
            item._on_value_changed(editor.isChecked())
            return
        if isinstance(item, IntegerItem):
            try:
                item._on_value_changed(int(editor.text()))
            except ValueError:
                pass  # incomplete input (e.g. "" or "-") — keep the old value
            return
        if isinstance(item, FloatItem):
            try:
                item._on_value_changed(float(editor.text()))
            except ValueError:
                pass
            return
        if isinstance(item, StringItem):
            item._on_value_changed(editor.text())
            return
        if isinstance(item, (FieldBasicItem, FieldObjectItem, FieldArrayItem)):
            item._on_key_changed(editor.text())
            return
        return super().setModelData(editor, model, index)


class JsonView(QTreeView):
    change = Signal(object)

    def __init__(self, value: JsonRoot = dict(), inherited_data: dict = None, parent=None):
        super().__init__(parent)

        # Settings
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

        # Connect signals
        self.customContextMenuRequested.connect(self._on_context_menu)

        # Initialize the model
        self._model = JsonModel(value, inherited_data, self)
        self._model.change.connect(self._on_change)
        self._model.change.connect(self.change)
        self.setModel(self._model)

        # Header settings - allow user to resize columns
        self._header = QHeaderView(Qt.Horizontal, self)
        self.setHeader(self._header)
        if self._header.count() > 0:
            self._header.setSectionResizeMode(QHeaderView.Interactive)
        self._header.setStretchLastSection(True)
        self.setColumnWidth(0, 200)  # Structure
        self.setColumnWidth(1, 150)  # Value
        self.setColumnWidth(2, 80)   # Type

        # Set the item delegate
        self.setItemDelegate(JsonItemDelegate(self))

    def set_value(self, value: JsonRoot, inherited_data: dict = None, preserve_state: bool = False):
        assert isinstance(value, dict), f"Expected dict, got {type(value)}"

        # Save expanded state if preserving
        expanded = self._get_expanded_paths() if preserve_state else None

        self._model.set_value(value, inherited_data)

        if expanded is not None:
            # Restore saved state
            self.expandAll()  # First expand all to create indexes
            self._restore_expanded_paths(expanded)
        else:
            # Default behavior for initial load
            self.expandAll()
            self._collapse_inherited()

    def _has_override_descendant(self, item) -> bool:
        """Check if item or any descendant has OVERRIDE origin."""
        if hasattr(item, '_origin') and item._origin == FieldOrigin.OVERRIDE:
            return True
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child and self._has_override_descendant(child):
                return True
        return False

    def _collapse_inherited(self):
        """Collapse inherited object/array items (unless they contain overrides)."""
        def collapse_recursive(item):
            # Check if this is a collapsible inherited item
            if hasattr(item, '_origin') and item._origin == FieldOrigin.INHERITED:
                if isinstance(item, (FieldObjectItem, FieldArrayItem)):
                    # Only collapse if no OVERRIDE descendants
                    if not self._has_override_descendant(item):
                        self.collapse(item.index())
                        return  # Don't recurse into collapsed items
            # Recurse into children
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child:
                    collapse_recursive(child)

        # Start from root items in the model
        for key, field_item in self._model._fields.items():
            collapse_recursive(field_item)

    def _get_expanded_paths(self) -> set[str]:
        """Get paths of all expanded items."""
        expanded = set()

        def collect(item, path_parts):
            if item is None:
                return
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child is None:
                    continue
                if hasattr(child, '_key'):
                    child_path = path_parts + [child._key]
                elif hasattr(child, '_index'):
                    child_path = path_parts + [str(child._index)]
                else:
                    continue
                path_str = '/'.join(child_path)
                if self.isExpanded(child.index()):
                    expanded.add(path_str)
                collect(child, child_path)

        # Start from model root
        for row in range(self._model.rowCount()):
            item = self._model.item(row, 0)
            if item is None:
                continue
            if hasattr(item, '_key'):
                path = [item._key]
            else:
                continue
            path_str = '/'.join(path)
            if self.isExpanded(item.index()):
                expanded.add(path_str)
            collect(item, path)
        return expanded

    def _restore_expanded_paths(self, expanded: set[str]):
        """Restore expanded state from saved paths."""
        def restore(item, path_parts):
            if item is None:
                return
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child is None:
                    continue
                if hasattr(child, '_key'):
                    child_path = path_parts + [child._key]
                elif hasattr(child, '_index'):
                    child_path = path_parts + [str(child._index)]
                else:
                    continue
                path_str = '/'.join(child_path)
                if path_str in expanded:
                    self.expand(child.index())
                else:
                    self.collapse(child.index())
                restore(child, child_path)

        # Start from model root
        for row in range(self._model.rowCount()):
            item = self._model.item(row, 0)
            if item is None:
                continue
            if hasattr(item, '_key'):
                path = [item._key]
            else:
                continue
            path_str = '/'.join(path)
            if path_str in expanded:
                self.expand(item.index())
            else:
                self.collapse(item.index())
            restore(item, path)

    def has_change(self, path: JsonPath = JsonPathRoot()) -> bool:
        return self._model.has_change(path)

    def list_changes(self, path: JsonPath = JsonPathRoot()) -> list[JsonChange]:
        return self._model.list_changes(path)

    def discard_changes(self):
        self._model.discard_changes()
        self.expandAll()
        self._collapse_inherited()

    def to_json(self):
        return self._model.to_json()

    def _on_change(self, change: JsonChange):
        match change.op:
            case JsonOpFieldInsert(key, _value):
                self._on_item_added(change.path / key)
            case JsonOpIndexInsert(index, _value):
                self._on_item_added(change.path / index)
            case _:
                pass

    def _on_item_added(self, path: JsonPath):
        item = _json_path_lookup(self._model, path)
        parent = item.index().parent()
        if parent is None:
            return
        self.expand(parent)

    def _on_context_menu(self, position):
        index = self.indexAt(position)
        target = (
            self._model
            if not index.isValid()
            else self._model.itemFromIndex(index)
        )
        target._on_context_menu(self.mapToGlobal(position))

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
        else:
            super().keyPressEvent(event)

    def _delete_selected_items(self):
        """Delete selected fields - OVERRIDE fields revert to inherited, LOCAL fields are removed."""
        indexes = self.selectedIndexes()
        if not indexes:
            return

        # Filter to column 0 only (field/index items)
        items_to_process = []
        for index in indexes:
            if index.column() != 0:
                continue
            item = self._model.itemFromIndex(index)
            # Skip inherited fields - they cannot be deleted
            if hasattr(item, '_origin') and item._origin == FieldOrigin.INHERITED:
                continue
            items_to_process.append(item)

        if not items_to_process:
            return

        # Sort by row descending within each parent to handle index shifts
        items_to_process.sort(key=lambda x: x.row(), reverse=True)

        for item in items_to_process:
            parent = item.parent()
            # For top-level items, parent is None - use model itself
            if parent is None:
                parent = self._model

            if hasattr(item, '_key'):  # Field item
                key = item._key
                if key not in getattr(parent, '_fields', {}):
                    continue
                # OVERRIDE and REDUNDANT fields: revert to inherited value
                if item._origin in (FieldOrigin.OVERRIDE, FieldOrigin.REDUNDANT):
                    # Get inherited value from parent's _inherited_data
                    inherited_data = getattr(parent, '_inherited_data', {})
                    inherited_value = inherited_data.get(key)
                    if inherited_value is not None:
                        parent._set_field_to_inherited(key, inherited_value)
                    else:
                        parent._remove_field(key)
                else:  # LOCAL fields: remove entirely
                    parent._remove_field(key)
            elif hasattr(item, '_index'):  # Index item - always remove
                if item._index < len(getattr(parent, '_items', [])):
                    parent._remove_item(item._index)

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

        # Only allow drops on siblings (same parent)
        if source_item.parent() != target_item.parent():
            self._drop_indicator_rect = QRect()
            event.ignore()
            self.viewport().update()
            return

        # Check origin compatibility - inherited items cannot be reordered
        if hasattr(source_item, '_origin') and source_item._origin == FieldOrigin.INHERITED:
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
        """Handle drop for reordering items within same parent."""
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

        # Validate same parent (sibling reorder only)
        if source_item.parent() != target_item.parent():
            event.ignore()
            return

        # Handle inherited items - cannot reorder
        if hasattr(source_item, '_origin') and source_item._origin == FieldOrigin.INHERITED:
            event.ignore()
            return

        parent = source_item.parent()
        if parent is None:
            parent = self._model

        # Determine if this is field reorder or index reorder
        if hasattr(source_item, '_key') and hasattr(target_item, '_key'):
            # Field reorder (object children)
            from_key = source_item._key

            # Determine actual target based on drop indicator position
            if indicator_pos == QAbstractItemView.BelowItem:
                # Insert after target - find next sibling
                target_row = target_item.row()
                if target_row + 1 < parent.rowCount():
                    next_item = parent.child(target_row + 1, 0)
                    if next_item and hasattr(next_item, '_key'):
                        to_key = next_item._key
                    else:
                        to_key = target_item._key
                else:
                    # Target is last item
                    to_key = target_item._key
            else:
                to_key = target_item._key

            if from_key == to_key:
                event.ignore()
                return
            if hasattr(parent, '_reorder_field'):
                parent._reorder_field(from_key, to_key)
                event.accept()
                return
        elif hasattr(source_item, '_index') and hasattr(target_item, '_index'):
            # Index reorder (array children)
            from_idx = source_item._index

            # Determine actual target based on drop indicator position
            if indicator_pos == QAbstractItemView.BelowItem:
                # Insert after target
                to_idx = target_item._index + 1
            else:
                to_idx = target_item._index

            if from_idx == to_idx:
                event.ignore()
                return
            if hasattr(parent, '_move_item'):
                parent._move_item(from_idx, to_idx)
                event.accept()
                return

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
