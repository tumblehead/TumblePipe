"""Qt item stack for the JSON tree editor.

Menu/action helpers, row-item factories, and every ``QStandardItem`` subclass.
Factories and item classes are mutually recursive (a container item builds child
rows via the factories, which construct further item classes), so they live
together. Item classes never reference the model/view — the dependency runs one
way: ``view`` imports ``items``.
"""

from functools import partial

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFont,
    QStandardItem,
    QStandardItemModel,
)
from qtpy.QtWidgets import (
    QInputDialog,
    QMenu,
    QMessageBox,
)

from .types import FieldOrigin, JsonValue, _default_value
from .path import JsonPath, _json_path_parts
from .ops import (
    _change_index_insert, _change_index_update, _change_index_reorder, _change_index_remove,
    _change_field_insert, _change_field_update, _change_field_rename, _change_field_remove, _change_field_reorder,
)


INHERITED_FG = QColor(95, 95, 100)  # Darker grey for inherited fields


OVERRIDE_FG = QColor(220, 220, 225)  # Brighter for override fields (non-default values)


def _add_index_section(menu: QMenu, callback):
    menu.addAction("Add Item").triggered.connect(partial(callback, "add"))


def _remove_index_section(menu: QMenu, callback):
    menu.addAction("Remove Item").triggered.connect(partial(callback, "remove"))


def _add_field_section(menu: QMenu, callback):
    menu.addAction("Add Field").triggered.connect(partial(callback, "add"))


def _remove_field_section(menu: QMenu, callback):
    menu.addAction("Remove Field").triggered.connect(
        partial(callback, "remove")
    )


def _reorder_index_section(menu: QMenu, item, callback):
    # Check in what way item is reorderable
    sibling_count = len(_parent(item)._items)
    if sibling_count <= 1:
        return
    curr_index = item._index
    max_index = sibling_count - 1
    is_top = curr_index <= 0
    is_bottom = curr_index >= max_index

    # Add the reorder actions
    move_menu = menu.addMenu("Move")
    if not is_top:
        move_menu.addAction("Up").triggered.connect(partial(callback, "up"))
    if not is_bottom:
        move_menu.addAction("Down").triggered.connect(partial(callback, "down"))
    if not is_top:
        move_menu.addAction("Top").triggered.connect(partial(callback, "top"))
    if not is_bottom:
        move_menu.addAction("Bottom").triggered.connect(
            partial(callback, "bottom")
        )


def _change_type_section(menu: QMenu, item, callback):
    def _is_included(item, value_type):
        if not isinstance(item, (FieldBasicItem, IndexBasicItem)):
            return True
        return not isinstance(item.to_json(), value_type)

    type_menu = menu.addMenu("Convert")
    if _is_included(item, type(None)):
        type_menu.addAction("null").triggered.connect(partial(callback, None))
    if _is_included(item, bool):
        type_menu.addAction("boolean").triggered.connect(
            partial(callback, bool)
        )
    if _is_included(item, int):
        type_menu.addAction("integer").triggered.connect(partial(callback, int))
    if _is_included(item, float):
        type_menu.addAction("float").triggered.connect(partial(callback, float))
    if _is_included(item, str):
        type_menu.addAction("string").triggered.connect(partial(callback, str))
    if not isinstance(item, (FieldArrayItem, IndexArrayItem)):
        type_menu.addAction("array").triggered.connect(partial(callback, list))
    if not isinstance(item, (FieldObjectItem, IndexObjectItem)):
        type_menu.addAction("object").triggered.connect(partial(callback, dict))


def _add_field_action(widget, parent):
    key, ok = QInputDialog.getText(widget, "Add Field", "Enter field name:")
    if not ok or len(key) == 0:
        return
    if key in parent:
        QMessageBox.warning(widget, "Error", f'Field "{key}" already exists.')
        return
    value = _default_value(None)
    parent._add_field(key, value)


def _remove_field_action(widget, item):
    key = item._key
    ok = QMessageBox.question(
        widget,
        "Remove Field",
        f'Are you sure you want to remove the field "{key}"?',
    )
    if ok != QMessageBox.Yes:
        return
    parent = _parent(item)
    parent._remove_field(key)


def _remove_index_action(widget, item):
    index = item._index
    ok = QMessageBox.question(
        widget,
        "Remove Item",
        f'Are you sure you want to remove the item "{index}"?',
    )
    if ok != QMessageBox.Yes:
        return
    parent = _parent(item)
    parent._remove_item(index)


def _type_item(value: JsonValue) -> QStandardItem:
    if value is None:
        return TypeItem("null")
    if isinstance(value, bool):
        return TypeItem("boolean")
    if isinstance(value, int):
        return TypeItem("integer")
    if isinstance(value, float):
        return TypeItem("float")
    if isinstance(value, str):
        return TypeItem("string")
    if isinstance(value, list):
        return TypeItem("array")
    if isinstance(value, dict):
        return TypeItem("object")
    raise TypeError(f"Unsupported value type: {type(value)}")


def _field_item(
    key: str,
    value: JsonValue,
    origin: FieldOrigin = FieldOrigin.LOCAL,
    inherited_value: JsonValue = None
) -> QStandardItem:
    if value is None or isinstance(value, (str, int, float, bool)):
        field_item = FieldBasicItem(key, origin)
        return field_item
    if isinstance(value, list):
        field_item = FieldArrayItem(key, origin)
        field_item.set_value(value)
        return field_item
    if isinstance(value, dict):
        # For nested dicts, pass inherited sub-dict for recursive origin computation
        inherited_sub = inherited_value if isinstance(inherited_value, dict) else None
        field_item = FieldObjectItem(key, origin, inherited_sub)
        field_item.set_value(value, inherited_data=inherited_sub)
        return field_item
    raise TypeError(f"Unsupported value type: {type(value)}")


def _index_item(
    index: int,
    value: JsonValue,
    origin: FieldOrigin = FieldOrigin.LOCAL
) -> QStandardItem:
    if value is None or isinstance(value, (str, int, float, bool)):
        index_item = IndexBasicItem(index, origin)
        return index_item
    if isinstance(value, list):
        index_item = IndexArrayItem(index, origin)
        index_item.set_value(value)
        return index_item
    if isinstance(value, dict):
        index_item = IndexObjectItem(index, origin)
        index_item.set_value(value)
        return index_item
    raise TypeError(f"Unsupported value type: {type(value)}")


def _value_item(value: JsonValue) -> QStandardItem:
    if value is None:
        return NullItem()
    if isinstance(value, bool):
        return BooleanItem(value)
    if isinstance(value, int):
        return IntegerItem(value)
    if isinstance(value, float):
        return FloatItem(value)
    if isinstance(value, str):
        return StringItem(value)
    if isinstance(value, list):
        return EmptyItem()
    if isinstance(value, dict):
        return EmptyItem()
    raise TypeError(f"Unsupported value type: {type(value)}")


def _parent(item: QStandardItem) -> QStandardItem | QStandardItemModel | None:
    parent = item.parent()
    if parent is not None:
        return parent
    return item.model()


def _propagate_origin_to_local(item):
    """Propagate LOCAL origin up the tree when an inherited value is modified."""
    parent = _parent(item)
    while parent is not None and hasattr(parent, '_origin'):
        if parent._origin == FieldOrigin.INHERITED:
            parent._origin = FieldOrigin.LOCAL
        parent = _parent(parent)


def _child(parent, row, column):
    if isinstance(parent, QStandardItem):
        return parent.child(row, column)
    if isinstance(parent, QStandardItemModel):
        return parent.item(row, column)
    assert False, f"Invalid parent type: {type(parent)}"


def _sibling(item: QStandardItem, column):
    return _child(_parent(item), item.row(), column)


def _json_path_lookup(model, path: JsonPath) -> QStandardItem:
    result = model
    for part in _json_path_parts(path):
        match part:
            case int():
                result = result._items[part]
            case str():
                result = result._fields[part]
            case _:
                assert False, f"Invalid path part type: {type(part)}"
    return result


def _rename_field_key(item, to_key: str):
    """Rename a field's key, keeping the parent's _fields registry in sync."""
    from_key = item._key
    if to_key == from_key or len(to_key) == 0:
        return
    parent = _parent(item)
    if to_key in parent._fields:
        return  # a sibling already uses this key
    item._key = to_key
    parent._fields = {
        to_key if key == from_key else key: field_item
        for key, field_item in parent._fields.items()
    }
    item.change.emit(_change_field_rename(parent.path(), from_key, to_key))


def _original_lookup(model, path: JsonPath) -> tuple[bool, JsonValue]:
    """Look up a path in the model's pristine document. Returns (found, value)."""
    value = model._value
    for part in _json_path_parts(path):
        match part:
            case int():
                if not isinstance(value, list) or part >= len(value):
                    return False, None
            case str():
                if not isinstance(value, dict) or part not in value:
                    return False, None
        value = value[part]
    return True, value


def _revert_field_action(item):
    """Restore a field to its state in the pristine document."""
    parent = _parent(item)
    key = item._key
    found, original = _original_lookup(item.model(), parent.path() / key)
    inherited = getattr(parent, '_inherited_data', None) or {}
    if found:
        if key not in inherited:
            origin = FieldOrigin.LOCAL
        elif original == inherited[key]:
            origin = FieldOrigin.REDUNDANT
        else:
            origin = FieldOrigin.OVERRIDE
        parent._set_field(key, original, origin, inherited.get(key))
    elif key in inherited:
        parent._set_field_to_inherited(key, inherited[key])
    else:
        parent._remove_field(key)


def _revert_field_section(menu: QMenu, item):
    model = item.model()
    if model is None or not model.has_change(item.path()):
        return
    menu.addSeparator()
    menu.addAction("Revert Changes").triggered.connect(
        partial(_revert_field_action, item)
    )


class TypeItem(QStandardItem):
    def __init__(self, type_name: str):
        super().__init__(type_name)

        # Settings
        self.setEditable(False)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)


class EmptyItem(QStandardItem):
    def __init__(self):
        super().__init__()

        # Settings
        self.setEditable(False)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)


class NullItem(QStandardItem):
    def __init__(self):
        super().__init__()

        # Settings
        self.setEditable(False)

    def data(self, role=None):
        match role:
            case Qt.DisplayRole:
                return "null"
            case _:
                return super().data(role)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)

    def to_json(self):
        return None


class BooleanItem(QStandardItem):
    def __init__(self, value: bool):
        super().__init__()

        # Members
        self._value = value

    def __str__(self):
        return "true" if self._value else "false"

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._value
            case Qt.DisplayRole:
                return str(self)
            case Qt.ForegroundRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin'):
                    if sibling._origin == FieldOrigin.INHERITED:
                        return QBrush(INHERITED_FG)
                    if sibling._origin == FieldOrigin.OVERRIDE:
                        return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin') and sibling._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_value_changed(self, value):
        if self._value == value:
            return
        self._value = value
        item = _sibling(self, 0)
        item._on_value_changed(value)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)

    def to_json(self):
        return self._value


class IntegerItem(QStandardItem):
    def __init__(self, value: int):
        super().__init__()

        # Members
        self._value = value

    def __str__(self):
        return str(self._value)

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._value
            case Qt.DisplayRole:
                return str(self)
            case Qt.ForegroundRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin'):
                    if sibling._origin == FieldOrigin.INHERITED:
                        return QBrush(INHERITED_FG)
                    if sibling._origin == FieldOrigin.OVERRIDE:
                        return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin') and sibling._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_value_changed(self, value):
        if self._value == value:
            return
        self._value = value
        item = _sibling(self, 0)
        item._on_value_changed(value)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)

    def to_json(self):
        return self._value


class FloatItem(QStandardItem):
    def __init__(self, value: float):
        super().__init__()

        # Members
        self._value = value

    def __str__(self):
        return str(self._value)

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._value
            case Qt.DisplayRole:
                return str(self)
            case Qt.ForegroundRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin'):
                    if sibling._origin == FieldOrigin.INHERITED:
                        return QBrush(INHERITED_FG)
                    if sibling._origin == FieldOrigin.OVERRIDE:
                        return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin') and sibling._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_value_changed(self, value):
        if self._value == value:
            return
        self._value = value
        item = _sibling(self, 0)
        item._on_value_changed(value)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)

    def to_json(self):
        return self._value


class StringItem(QStandardItem):
    def __init__(self, value: str):
        super().__init__()

        # Members
        self._value = value

    def __str__(self):
        return self._value

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._value
            case Qt.DisplayRole:
                return str(self)
            case Qt.ForegroundRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin'):
                    if sibling._origin == FieldOrigin.INHERITED:
                        return QBrush(INHERITED_FG)
                    if sibling._origin == FieldOrigin.OVERRIDE:
                        return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                sibling = _sibling(self, 0)
                if hasattr(sibling, '_origin') and sibling._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_value_changed(self, value):
        if self._value == value:
            return
        self._value = value
        item = _sibling(self, 0)
        item._on_value_changed(value)

    def _on_context_menu(self, position):
        sibling = _sibling(self, 0)
        return sibling._on_context_menu(position)

    def to_json(self):
        return self._value


class FieldBasicItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str, origin: FieldOrigin = FieldOrigin.LOCAL):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key
        self._origin = origin

        # Inherited fields are read-only
        if origin == FieldOrigin.INHERITED:
            self.setEditable(False)
        else:
            # Enable drag for non-inherited field items
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._key
            case Qt.DisplayRole:
                return f"{self._key}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_key_changed(self, to_key):
        _rename_field_key(self, to_key)

    def _on_value_changed(self, value: JsonValue):
        # When overriding an inherited field, change origin to LOCAL
        if self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL
            _propagate_origin_to_local(self)
        self.change.emit(
            _change_field_update(_parent(self).path(), self._key, value)
        )

    def _on_type_changed(self, new_type):
        _parent(self)._set_field(self._key, _default_value(new_type))

    def _on_modify_action(self, action):
        match action:
            case "remove":
                view = self.model().parent()
                return _remove_field_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_context_menu(self, position):
        if self._origin == FieldOrigin.INHERITED:
            return
        menu = QMenu()
        _change_type_section(menu, self, self._on_type_changed)
        _remove_field_section(menu, self._on_modify_action)
        _revert_field_section(menu, self)
        menu.exec_(position)

    def to_json(self):
        value_item = _sibling(self, 1)
        return value_item.to_json()


class FieldObjectItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(
        self,
        key: str,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        inherited_data: dict = None
    ):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key
        self._origin = origin
        self._inherited_data = inherited_data or {}
        self._fields = dict()

        # Inherited fields are read-only
        if origin == FieldOrigin.INHERITED:
            self.setEditable(False)
        else:
            # Enable drag (as field) and drop (as object container)
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def __contains__(self, key: str):
        assert isinstance(key, str), f"Expected str, got {type(key)}"
        return key in self._fields

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._key
            case Qt.DisplayRole:
                return f"{self._key}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _add_field(
        self,
        key: str,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        inherited_value: JsonValue = None,
        notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

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
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

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
            child = self.child(row, 0)
            if hasattr(child, '_key'):
                new_fields[child._key] = child
        self._fields = new_fields

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_update(self.path(), key, value))

    def _remove_field(self, key: str, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        self.removeRow(field_item.row())
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

        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

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
            child = self.child(row, 0)
            if hasattr(child, '_key'):
                new_fields[child._key] = child
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

    def set_value(self, values: dict[str, JsonValue], inherited_data: dict = None):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            self.removeRow(field_item.row())
        self._fields.clear()

        # Update inherited data
        inherited = inherited_data if inherited_data is not None else self._inherited_data
        self._inherited_data = inherited

        # If parent is INHERITED, all children are INHERITED too
        if self._origin == FieldOrigin.INHERITED:
            for key in values.keys():
                value = values[key]
                # Pass same value as inherited_value for nested objects
                inherited_sub = value if isinstance(value, dict) else None
                self._add_field(key, value, FieldOrigin.INHERITED, inherited_sub, notify=False)
            return

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

    def _on_key_changed(self, to_key: str):
        _rename_field_key(self, to_key)

    def _on_modify_action(self, action):
        view = self.model().parent()
        match action:
            case "add":
                return _add_field_action(view, self)
            case "remove":
                return _remove_field_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_type_changed(self, new_type):
        _parent(self)._set_field(self._key, _default_value(new_type))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_field_section(menu, self._on_modify_action)
        if self._origin != FieldOrigin.INHERITED:
            _change_type_section(menu, self, self._on_type_changed)
            _remove_field_section(menu, self._on_modify_action)
            _revert_field_section(menu, self)
        menu.exec_(position)

    def to_json(self):
        # Only return non-inherited fields
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
            if field_item._origin != FieldOrigin.INHERITED
        }


class FieldArrayItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str, origin: FieldOrigin = FieldOrigin.LOCAL):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key
        self._origin = origin
        self._items = list()

        # Inherited fields are read-only
        if origin == FieldOrigin.INHERITED:
            self.setEditable(False)
        else:
            # Enable drag (as field) and drop (for array items)
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._key
            case Qt.DisplayRole:
                return f"{self._key}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _add_item(
        self,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Create the row items
        index = self.rowCount()
        index_item = _index_item(index, value, origin)
        index_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)

        # Insert the row into the model
        self.insertRow(index, [index_item, value_item, type_item])
        self._items.append(index_item)

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_index_insert(self.path(), index, value))

    def _set_item(self, index: int, value: JsonValue, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove item from model
        index_item = self._items[index]
        index_item.change.disconnect(self.change)
        self.removeRow(index)
        del self._items[index]

        # Add the item to the model
        index_item = _index_item(index, value)
        index_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [index_item, value_item, type_item])
        self._items.insert(index, index_item)

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_index_update(self.path(), index, value))

    def _reorder_item(
        self, from_index: int, to_index: int, notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        assert abs(from_index - to_index) == 1, (
            f"Invalid reorder indices: {from_index} -> {to_index}"
        )

        # Move the item in the model
        to_item = self._items[to_index]
        from_item = self._items.pop(from_index)
        self._items.insert(to_index, from_item)
        row = self.takeRow(from_index)
        self.insertRow(to_index, row)

        # Re-index the items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit the change signal
        if not notify:
            return
        self.change.emit(
            _change_index_reorder(
                self.path(),
                from_index,
                to_index,
                from_item.to_json(),
                to_item.to_json(),
            )
        )

    def _remove_item(self, index: int, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
        index_item = self._items[index]
        index_item.change.disconnect(self.change)
        self.removeRow(index)
        del self._items[index]

        # Re-index the items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit the item removed signal
        if not notify:
            return
        self.change.emit(_change_index_remove(self.path(), index))

    def _move_item(self, from_index: int, to_index: int, notify: bool = True):
        """Move item from from_index to to_index (can be non-adjacent)."""
        if from_index == to_index:
            return

        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Get items for change signal
        from_item = self._items[from_index]
        to_item = self._items[to_index]

        # Remove from old position
        self._items.pop(from_index)
        row = self.takeRow(from_index)

        # Insert at new position (adjust if moving down)
        insert_idx = to_index if from_index > to_index else to_index
        self._items.insert(insert_idx, from_item)
        self.insertRow(insert_idx, row)

        # Re-index all items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit change signal
        if not notify:
            return
        self.change.emit(
            _change_index_reorder(
                self.path(),
                from_index,
                to_index,
                from_item.to_json(),
                to_item.to_json(),
            )
        )

    def set_value(self, values: list[JsonValue]):
        # Clear existing items
        for index in reversed(range(self.rowCount())):
            index_item = self._items[index]
            index_item.change.disconnect(self.change)
            self.removeRow(index)
        self._items.clear()

        # If parent is INHERITED, all children are INHERITED too
        origin = FieldOrigin.INHERITED if self._origin == FieldOrigin.INHERITED else FieldOrigin.LOCAL

        # Add the items to the array
        for value in values:
            self._add_item(value, origin, notify=False)

    def _on_key_changed(self, to_key):
        _rename_field_key(self, to_key)

    def _on_modify_action(self, action):
        match action:
            case "add":
                # Infer type from existing items (inherited or local)
                if self._items:
                    first_value = self._items[0].to_json()
                    default = _default_value(type(first_value))
                else:
                    default = _default_value(None)
                return self._add_item(default)
            case "remove":
                view = self.model().parent()
                return _remove_field_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_type_changed(self, new_type):
        _parent(self)._set_field(self._key, _default_value(new_type))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_index_section(menu, self._on_modify_action)
        if self._origin != FieldOrigin.INHERITED:
            _change_type_section(menu, self, self._on_type_changed)
            _remove_field_section(menu, self._on_modify_action)
            _revert_field_section(menu, self)
        menu.exec_(position)

    def to_json(self):
        return [index_item.to_json() for index_item in self._items]


class IndexBasicItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index
        self._origin = origin

        # Enable drag for non-inherited items
        if origin != FieldOrigin.INHERITED:
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                assert False, "IndexBasicItem is not editable"
            case Qt.DisplayRole:
                return f"{self._index}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _on_value_changed(self, value: JsonValue):
        # When overriding an inherited field, change origin to LOCAL
        if self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL
            _propagate_origin_to_local(self)
        self.change.emit(
            _change_index_update(_parent(self).path(), self._index, value)
        )

    def _on_reorder_action(self, action):
        def _to_index(action):
            match action:
                case "up":
                    return self._index - 1
                case "down":
                    return self._index + 1
                case "top":
                    return 0
                case "bottom":
                    return len(_parent(self)._items) - 1

        _parent(self)._reorder_item(self._index, _to_index(action))

    def _on_modify_action(self, action):
        match action:
            case "remove":
                view = self.model().parent()
                return _remove_index_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_type_changed(self, new_type):
        _parent(self)._set_item(self._index, _default_value(new_type))

    def _on_context_menu(self, position):
        menu = QMenu()
        _reorder_index_section(menu, self, self._on_reorder_action)
        _change_type_section(menu, self, self._on_type_changed)
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        value_item = _sibling(self, 1)
        return value_item.to_json()


class IndexObjectItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index
        self._origin = origin
        self._fields = dict()

        # Enable drag (as array item) and drop (as object container)
        if origin != FieldOrigin.INHERITED:
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def __contains__(self, key: str):
        assert isinstance(key, str), f"Expected str, got {type(key)}"
        return key in self._fields

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                assert False, "IndexObjectItem is not editable"
            case Qt.DisplayRole:
                return f"{self._index}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _add_field(
        self,
        key: str,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Create the row items
        field_item = _field_item(key, value, origin)
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
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Add the item to the model
        field_item = _field_item(key, value, origin, inherited_value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Rebuild _fields in row order (del + re-add pushed the key to the end)
        new_fields = {}
        for row in range(self.rowCount()):
            child = self.child(row, 0)
            if hasattr(child, '_key'):
                new_fields[child._key] = child
        self._fields = new_fields

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_update(self.path(), key, value))

    def _remove_field(self, key: str, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
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

        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

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
            child = self.child(row, 0)
            if hasattr(child, '_key'):
                new_fields[child._key] = child
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

    def set_value(self, values: dict[str, JsonValue]):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            index = field_item.row()
            self.removeRow(index)
        self._fields.clear()

        # If parent is INHERITED, all children are INHERITED too
        origin = FieldOrigin.INHERITED if self._origin == FieldOrigin.INHERITED else FieldOrigin.LOCAL

        # Add the fields to the object
        for key, value in values.items():
            self._add_field(key, value, origin, notify=False)

    def _on_reorder_action(self, action):
        def _to_index(action):
            match action:
                case "up":
                    return self._index - 1
                case "down":
                    return self._index + 1
                case "top":
                    return 0
                case "bottom":
                    return len(_parent(self)._items) - 1

        _parent(self)._reorder_item(self._index, _to_index(action))

    def _on_modify_action(self, action):
        view = self.model().parent()
        match action:
            case "add":
                return _add_field_action(view, self)
            case "remove":
                return _remove_index_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_type_changed(self, new_type):
        _parent(self)._set_item(self._index, _default_value(new_type))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_field_section(menu, self._on_modify_action)
        _reorder_index_section(menu, self, self._on_reorder_action)
        _change_type_section(menu, self, self._on_type_changed)
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
        }


class IndexArrayItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index
        self._origin = origin
        self._items = list()

        # Enable drag for non-inherited items, drop for array containers
        if origin != FieldOrigin.INHERITED:
            flags = self.flags()
            flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            self.setFlags(flags)

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                assert False, "IndexArrayItem is not editable"
            case Qt.DisplayRole:
                return f"{self._index}:"
            case Qt.ForegroundRole:
                if self._origin == FieldOrigin.INHERITED:
                    return QBrush(INHERITED_FG)
                if self._origin == FieldOrigin.OVERRIDE:
                    return QBrush(OVERRIDE_FG)
                return None
            case Qt.FontRole:
                if self._origin == FieldOrigin.OVERRIDE:
                    font = QFont()
                    font.setBold(True)
                    return font
                return None
            case _:
                return super().data(role)

    def _add_item(
        self,
        value: JsonValue,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Create the row items
        index = self.rowCount()
        index_item = _index_item(index, value, origin)
        index_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)

        # Insert the row into the model
        self.insertRow(index, [index_item, value_item, type_item])
        self._items.append(index_item)

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_index_insert(self.path(), index, value))

    def _set_item(self, index: int, value: JsonValue, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
        index_item = self._items[index]
        index_item.item_added.disconnect(self._on_item_added)
        index_item.item_removed.disconnect(self._on_item_removed)
        self.removeRow(index)
        del self._items[index]

        # Add the item to the model
        index_item = _index_item(index, value)
        index_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [index_item, value_item, type_item])
        self._items.insert(index, index_item)

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_index_update(self.path(), index, value))

    def _reorder_item(
        self, from_index: int, to_index: int, notify: bool = True
    ):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        assert abs(from_index - to_index) == 1, (
            f"Invalid reorder indices: {from_index} -> {to_index}"
        )

        # Move the item in the model
        to_item = self._items[to_index]
        from_item = self._items.pop(from_index)
        self._items.insert(to_index, from_item)
        row = self.takeRow(from_index)
        self.insertRow(to_index, row)

        # Re-index the items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit the change signal
        if not notify:
            return
        self.change.emit(
            _change_index_reorder(
                self.path(),
                from_index,
                to_index,
                from_item.to_json(),
                to_item.to_json(),
            )
        )

    def _remove_item(self, index: int, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
        index_item = self._items[index]
        index_item.change.disconnect(self.change)
        self.removeRow(index)
        del self._items[index]

        # Re-index the items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit the item removed signal
        if not notify:
            return
        self.change.emit(_change_index_remove(self.path(), index))

    def _move_item(self, from_index: int, to_index: int, notify: bool = True):
        """Move item from from_index to to_index (can be non-adjacent)."""
        if from_index == to_index:
            return

        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Get items for change signal
        from_item = self._items[from_index]
        to_item = self._items[to_index]

        # Remove from old position
        self._items.pop(from_index)
        row = self.takeRow(from_index)

        # Insert at new position (adjust if moving down)
        insert_idx = to_index if from_index > to_index else to_index
        self._items.insert(insert_idx, from_item)
        self.insertRow(insert_idx, row)

        # Re-index all items
        for index, item in enumerate(self._items):
            item._index = index

        # Emit change signal
        if not notify:
            return
        self.change.emit(
            _change_index_reorder(
                self.path(),
                from_index,
                to_index,
                from_item.to_json(),
                to_item.to_json(),
            )
        )

    def set_value(self, values: list[JsonValue]):
        # Clear existing items
        for index in reversed(range(self.rowCount())):
            index_item = self._items[index]
            index_item.change.disconnect(self.change)
            self.removeRow(index)
        self._items.clear()

        # If parent is INHERITED, all children are INHERITED too
        origin = FieldOrigin.INHERITED if self._origin == FieldOrigin.INHERITED else FieldOrigin.LOCAL

        # Add the items to the array
        for value in values:
            self._add_item(value, origin, notify=False)

    def _on_reorder_action(self, action):
        def _to_index(action):
            match action:
                case "up":
                    return self._index - 1
                case "down":
                    return self._index + 1
                case "top":
                    return 0
                case "bottom":
                    return len(_parent(self)._items) - 1

        _parent(self)._reorder_item(self._index, _to_index(action))

    def _on_modify_action(self, action):
        match action:
            case "add":
                return self._add_item(_default_value(None))
            case "remove":
                view = self.model().parent()
                return _remove_index_action(view, self)
            case _:
                assert False, f"Unknown action: {action}"

    def _on_type_changed(self, new_type):
        _parent(self)._set_item(self._index, _default_value(new_type))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_index_section(menu, self._on_modify_action)
        _reorder_index_section(menu, self, self._on_reorder_action)
        _change_type_section(menu, self, self._on_type_changed)
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return [item.to_json() for item in self._items]
