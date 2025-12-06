from dataclasses import dataclass
from functools import partial

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtGui import (
    QIntValidator,
    QStandardItem,
    QStandardItemModel,
    QValidator,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStyledItemDelegate,
    QTreeView,
)

########################################################################################
# Helper Functions
########################################################################################

JsonValue = (
    None | str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"]
)

JsonRoot = dict[str, JsonValue]


def _is_basic(value: JsonValue) -> bool:
    return isinstance(value, (str, int, float, bool, type(None)))


def _default_value(value_type):
    if value_type is None:
        return None
    if value_type is bool:
        return False
    if value_type is int:
        return 0
    if value_type is float:
        return 0.0
    if value_type is str:
        return ""
    if value_type is list:
        return []
    if value_type is dict:
        return {}
    raise TypeError(f"Unsupported type for default value: {value_type}")


def _add_index_section(menu: QMenu, callback):
    menu.addAction("Modify Array").setEnabled(False)
    menu.addSeparator()
    menu.addAction("Add Item").triggered.connect(partial(callback, "add"))


def _remove_index_section(menu: QMenu, callback):
    menu.addAction("Modify Array").setEnabled(False)
    menu.addSeparator()
    menu.addAction("Remove Item").triggered.connect(partial(callback, "remove"))


def _add_field_section(menu: QMenu, callback):
    menu.addAction("Modify Object").setEnabled(False)
    menu.addSeparator()
    menu.addAction("Add Field").triggered.connect(partial(callback, "add"))


def _remove_field_section(menu: QMenu, callback):
    menu.addAction("Modify Object").setEnabled(False)
    menu.addSeparator()
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
    menu.addAction("Reorder Item").setEnabled(False)
    menu.addSeparator()
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

    menu.addAction("Modify Type").setEnabled(False)
    menu.addSeparator()
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


def _field_item(key: str, value: JsonValue) -> QStandardItem:
    if value is None or isinstance(value, (str, int, float, bool)):
        field_item = FieldBasicItem(key)
        return field_item
    if isinstance(value, list):
        field_item = FieldArrayItem(key)
        field_item.set_value(value)
        return field_item
    if isinstance(value, dict):
        field_item = FieldObjectItem(key)
        field_item.set_value(value)
        return field_item
    raise TypeError(f"Unsupported value type: {type(value)}")


def _index_item(index: int, value: JsonValue) -> QStandardItem:
    if value is None or isinstance(value, (str, int, float, bool)):
        index_item = IndexBasicItem(index)
        return index_item
    if isinstance(value, list):
        index_item = IndexArrayItem(index)
        index_item.set_value(value)
        return index_item
    if isinstance(value, dict):
        index_item = IndexObjectItem(index)
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


def _child(parent, row, column):
    if isinstance(parent, QStandardItem):
        return parent.child(row, column)
    if isinstance(parent, QStandardItemModel):
        return parent.item(row, column)
    assert False, f"Invalid parent type: {type(parent)}"


def _sibling(item: QStandardItem, column):
    return _child(_parent(item), item.row(), column)


def _value_tree(tree: JsonValue, value: JsonValue) -> JsonValue:
    # Prepare the stack and worklist
    stack = list()
    worklist = list()

    # Define the default result
    def _result(change=False, value=None):
        return dict(change=change, value=value)

    # Define the push operation
    def _push(tree, index=None):
        next_op = None if index is None else partial(_pop, index)
        if _is_basic(tree):
            stack.append(_result(value))
            return next_op
        match tree:
            case list():
                stack.append(_result(False, [None] * len(tree)))
                worklist.append(
                    list(
                        reversed(
                            [
                                partial(_push, item, index)
                                for index, item in enumerate(tree)
                            ]
                        )
                    )
                )
                return next_op
            case dict():
                stack.append(_result(False, {key: None for key in tree.keys()}))
                worklist.append(
                    list(
                        reversed(
                            [
                                partial(_push, item, key)
                                for key, item in tree.items()
                            ]
                        )
                    )
                )
                return next_op
        assert False, f"Unsupported tree type: {type(tree)}"

    # Define the pop operation
    def _pop(index):
        result = stack.pop()
        stack[-1]["change"] |= result["change"]
        stack[-1]["value"][index] = result

    # Iterate through the tree
    _push(tree)
    while len(worklist) != 0:
        values = worklist[-1]
        if len(values) == 0:
            worklist.pop()
            continue
        next_op = values.pop()()
        if next_op is not None:
            values.append(next_op)

    # Check the stack length and return the result
    assert len(stack) == 1, f"Invalid stack length: {len(stack)}"
    return stack[0]


def _diff_tree(from_value: JsonRoot, to_value: JsonRoot) -> JsonRoot:
    def _visit_list(from_list, to_list):
        # Prepare
        change = False
        result = list()

        # Check if the lists are empty
        from_length = len(from_list)
        to_length = len(to_list)
        if from_length == 0 and to_length == 0:
            return dict(change=False, value=None)

        # Find list heads
        head_length = min(from_length, to_length)
        from_head = from_list[:head_length]
        to_head = to_list[:head_length]

        # Find list tails
        from_tail = from_list[head_length:]
        to_tail = to_list[head_length:]
        tail = from_tail if len(from_tail) > len(to_tail) else to_tail

        # Visit the head of the lists
        for from_value, to_value in zip(from_head, to_head, strict=False):
            result_value = _visit(from_value, to_value)
            change |= result_value["change"]
            result.append(result_value)

        # Visit the tail of the lists
        for value in tail:
            result_value = _visit(value, None)
            change |= result_value["change"]
            result.append(result_value)

        # Done
        return dict(change=change, value=result)

    def _visit_dict(from_dict, to_dict):
        # Check if the dicts are empty
        if len(from_dict) == 0 and len(to_dict) == 0:
            return dict(change=False, value=dict())

        # Compare the two dicts
        change = False
        result = dict()
        for from_key, from_value in from_dict.items():
            # Check if they share a key
            if from_key not in to_dict:
                result[from_key] = _value_tree(from_value, True)
                change = True
                continue

            # Join their values
            to_value = to_dict[from_key]
            result_value = _visit(from_value, to_value)
            change |= result_value["change"]
            result[from_key] = result_value

        # Done
        return dict(change=change, value=result)

    def _visit(from_value, to_value):
        if type(from_value) != type(to_value):
            return dict(change=True, value=None)
        if _is_basic(from_value):
            return dict(change=from_value != to_value, value=None)
        if isinstance(from_value, list):
            return _visit_list(from_value, to_value)
        if isinstance(from_value, dict):
            return _visit_dict(from_value, to_value)
        assert False, f"Unsupported value type: {type(from_value)}"

    return _visit_dict(from_value, to_value)


########################################################################################
# Json Item Path
########################################################################################


@dataclass(frozen=True)
class JsonPath:
    def __truediv__(self, other: int | str) -> "JsonPath":
        if isinstance(other, int):
            return JsonPathIndex(self, other)
        if isinstance(other, str):
            return JsonPathField(self, other)
        raise TypeError(f"Invalid path part type: {type(other)}")


@dataclass(frozen=True)
class JsonPathRoot(JsonPath):
    pass


@dataclass(frozen=True)
class JsonPathIndex(JsonPath):
    path: JsonPath
    index: int


@dataclass(frozen=True)
class JsonPathField(JsonPath):
    path: JsonPath
    key: str


def _json_path_parts(path: JsonPath) -> list[int | str]:
    parts = []
    while True:
        match path:
            case JsonPathRoot():
                return list(reversed(parts))
            case JsonPathIndex(root_path, index):
                parts.append(index)
                path = root_path
            case JsonPathField(root_path, key):
                parts.append(key)
                path = root_path
            case _:
                assert False, f"Invalid path type: {type(path)}"


def _json_path_contained(prefix: JsonPath, path: JsonPath) -> bool:
    prefix_parts = _json_path_parts(prefix)
    path_parts = _json_path_parts(path)
    if len(prefix_parts) > len(path_parts):
        return False
    return prefix_parts == path_parts[: len(prefix_parts)]


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


def _diff_lookup(diff: JsonRoot, path: JsonPath) -> dict[str, JsonValue] | None:
    result = diff
    for part in _json_path_parts(path):
        match part:
            case int():
                if not isinstance(result["value"], list):
                    return None
                if len(result["value"]) <= part:
                    return None
                result = result["value"][part]
            case str():
                if not isinstance(result["value"], dict):
                    return None
                if part not in result["value"]:
                    return None
                result = result["value"][part]
            case _:
                assert False, f"Invalid path part type: {type(part)}"
    return result


########################################################################################
# Json Operations
########################################################################################


@dataclass(frozen=True)
class JsonOp:
    pass


@dataclass(frozen=True)
class JsonOpIndexInsert(JsonOp):
    index: int
    value: JsonValue


@dataclass(frozen=True)
class JsonOpIndexUpdate(JsonOp):
    index: int
    value: JsonValue


@dataclass(frozen=True)
class JsonOpIndexReorder(JsonOp):
    from_index: int
    to_index: int
    from_value: JsonValue
    to_value: JsonValue


@dataclass(frozen=True)
class JsonOpIndexRemove(JsonOp):
    index: int


@dataclass(frozen=True)
class JsonOpFieldInsert(JsonOp):
    key: str
    value: JsonValue


@dataclass(frozen=True)
class JsonOpFieldUpdate(JsonOp):
    key: str
    value: JsonValue


@dataclass(frozen=True)
class JsonOpFieldRename(JsonOp):
    from_key: str
    to_key: str


@dataclass(frozen=True)
class JsonOpFieldRemove(JsonOp):
    key: str


@dataclass(frozen=True)
class JsonChange:
    path: JsonPath
    op: JsonOp


def _change_index_insert(
    path: JsonPath, index: int, value: JsonValue
) -> JsonChange:
    return JsonChange(path, JsonOpIndexInsert(index, value))


def _change_index_update(
    path: JsonPath, index: int, value: JsonValue
) -> JsonChange:
    return JsonChange(path, JsonOpIndexUpdate(index, value))


def _change_index_reorder(
    path: JsonPath,
    from_index: int,
    to_index: int,
    from_value: JsonValue,
    to_value: JsonValue,
) -> JsonChange:
    return JsonChange(
        path, JsonOpIndexReorder(from_index, to_index, from_value, to_value)
    )


def _change_index_remove(path: JsonPath, index: int) -> JsonChange:
    return JsonChange(path, JsonOpIndexRemove(index))


def _change_field_insert(
    path: JsonPath, key: str, value: JsonValue
) -> JsonChange:
    return JsonChange(path, JsonOpFieldInsert(key, value))


def _change_field_update(
    path: JsonPath, key: str, value: JsonValue
) -> JsonChange:
    return JsonChange(path, JsonOpFieldUpdate(key, value))


def _change_field_rename(
    path: JsonPath, from_key: str, to_key: str
) -> JsonChange:
    return JsonChange(path, JsonOpFieldRename(from_key, to_key))


def _change_field_remove(path: JsonPath, key: str) -> JsonChange:
    return JsonChange(path, JsonOpFieldRemove(key))


########################################################################################
# Value Items
########################################################################################


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


########################################################################################
# Field Items
########################################################################################


class FieldBasicItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._key
            case Qt.DisplayRole:
                return f"{self._key}:"
            case _:
                return super().data(role)

    def _on_key_changed(self, to_key):
        from_key = self._key
        if from_key == to_key:
            return
        self._key = to_key
        self.change.emit(
            _change_field_rename(_parent(self).path(), from_key, to_key)
        )

    def _on_value_changed(self, value: JsonValue):
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
        menu = QMenu()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_field_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        value_item = _sibling(self, 1)
        return value_item.to_json()


class FieldObjectItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key
        self._fields = dict()

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
            case _:
                return super().data(role)

    def _add_field(self, key: str, value: JsonValue, notify: bool = True):
        # Create the row items
        field_item = _field_item(key, value)
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

    def _set_field(self, key: str, value: JsonValue, notify: bool = True):
        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Add the field to the model
        field_item = _field_item(key, value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_update(self.path(), key, value))

    def _remove_field(self, key: str, notify: bool = True):
        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        self.removeRow(field_item.row())
        del self._fields[key]

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_remove(self.path(), key))

    def set_value(self, values: dict[str, JsonValue]):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            self.removeRow(field_item.row())
        self._fields.clear()

        # Add the fields to the object
        for key, value in values.items():
            self._add_field(key, value, False)

    def _on_key_changed(self, to_key: str):
        from_key = self._key
        if from_key == to_key:
            return
        self._key = to_key
        self.change.emit(
            _change_field_rename(_parent(self).path(), from_key, to_key)
        )

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
        menu.addSeparator()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_field_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
        }


class FieldArrayItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Members
        self._key = key
        self._items = list()

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._key
            case Qt.DisplayRole:
                return f"{self._key}:"
            case _:
                return super().data(role)

    def _add_item(self, value: JsonValue, notify: bool = True):
        # Create the row items
        index = self.rowCount()
        index_item = _index_item(index, value)
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

    def set_value(self, values: list[JsonValue]):
        # Clear existing items
        for index in reversed(range(self.rowCount())):
            index_item = self._items[index]
            index_item.change.disconnect(self.change)
            self.removeRow(index)
        self._items.clear()

        # Add the items to the array
        for value in values:
            self._add_item(value, False)

    def _on_key_changed(self, to_key):
        from_key = self._key
        if from_key == to_key:
            return
        self._key = to_key
        self.change.emit(
            _change_field_rename(_parent(self).path(), from_key, to_key)
        )

    def _on_modify_action(self, action):
        match action:
            case "add":
                return self._add_item(_default_value(None))
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
        menu.addSeparator()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_field_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return [index_item.to_json() for index_item in self._items]


########################################################################################
# Index Items
########################################################################################


class IndexBasicItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                assert False, "IndexBasicItem is not editable"
            case Qt.DisplayRole:
                return f"{self._index}:"
            case _:
                return super().data(role)

    def _on_value_changed(self, value: JsonValue):
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
        menu.addSeparator()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        value_item = _sibling(self, 1)
        return value_item.to_json()


class IndexObjectItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index
        self._fields = dict()

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
            case _:
                return super().data(role)

    def _add_field(self, key: str, value: JsonValue, notify: bool = True):
        # Create the row items
        field_item = _field_item(key, value)
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

    def _set_field(self, key: str, value: JsonValue, notify: bool = True):
        # Remove the item from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Add the item to the model
        field_item = _field_item(key, value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

        # Emit the change signal
        if not notify:
            return
        self.change.emit(_change_field_update(self.path(), key, value))

    def _remove_field(self, key: str, notify: bool = True):
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

    def set_value(self, values: dict[str, JsonValue]):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            index = field_item.row()
            self.removeRow(index)
        self._fields.clear()

        # Add the fields to the object
        for key, value in values.items():
            self._add_field(key, value, False)

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
        menu.addSeparator()
        _reorder_index_section(menu, self, self._on_reorder_action)
        menu.addSeparator()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
        }


class IndexArrayItem(QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int):
        QStandardItem.__init__(self)
        QObject.__init__(self)

        # Settings
        self.setEditable(False)

        # Members
        self._index = index
        self._items = list()

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                assert False, "IndexArrayItem is not editable"
            case Qt.DisplayRole:
                return f"{self._index}:"
            case _:
                return super().data(role)

    def _add_item(self, value: JsonValue, notify: bool = True):
        # Create the row items
        index = self.rowCount()
        index_item = _index_item(index, value)
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

    def set_value(self, values: list[JsonValue]):
        # Clear existing items
        for index in reversed(range(self.rowCount())):
            index_item = self._items[index]
            index_item.change.disconnect(self.change)
            self.removeRow(index)
        self._items.clear()

        # Add the items to the array
        for value in values:
            self._add_item(value, False)

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
        menu.addSeparator()
        _reorder_index_section(menu, self, self._on_reorder_action)
        menu.addSeparator()
        _change_type_section(menu, self, self._on_type_changed)
        menu.addSeparator()
        _remove_index_section(menu, self._on_modify_action)
        menu.exec_(position)

    def to_json(self):
        return [item.to_json() for item in self._items]


########################################################################################
# Model
########################################################################################


class JsonModel(QStandardItemModel):
    change = Signal(object)

    def __init__(self, value: JsonRoot = dict(), parent=None):
        super().__init__(parent)

        # Settings
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Structure", "Value", "Type"])
        root_item = self.invisibleRootItem()
        root_item.setEditable(False)

        # Members
        self._value = value
        self._diff = _value_tree(value, False)
        self._fields = dict()
        self._changes = list()

        # Connect signals
        self.change.connect(self._on_change)

        # Set the initial value
        self.set_value(value)

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
        return f"● {label}" if self.has_change(item.path()) else label

    def _add_field(self, key: str, value: JsonValue, notify: bool = True):
        # Create the row items
        field_item = _field_item(key, value)
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

    def _set_field(self, key: str, value: JsonValue, notify: bool = True):
        # Remove the field from the model
        field_item = self._fields[key]
        field_item.change.disconnect(self.change)
        index = field_item.row()
        self.removeRow(index)
        del self._fields[key]

        # Add the field to the model
        field_item = _field_item(key, value)
        field_item.change.connect(self.change)
        value_item = _value_item(value)
        type_item = _type_item(value)
        self.insertRow(index, [field_item, value_item, type_item])
        self._fields[key] = field_item

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

    def set_value(self, values: JsonRoot):
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

        # Add the fields to the model
        for key, value in values.items():
            self._add_field(key, value, False)

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
        self.set_value(self._value)

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
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
        }


########################################################################################
# Delegate
########################################################################################


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
    def createEditor(self, parent, option, index):
        item = index.model().itemFromIndex(index)
        if isinstance(item, BooleanItem):

            def _on_state_changed(state):
                item._on_value_changed(bool(state))

            editor = QCheckBox(parent)
            editor.stateChanged.connect(_on_state_changed)
            return editor
        if isinstance(item, IntegerItem):

            def _on_editing_finished():
                value = editor.text()
                item._on_value_changed(int(value) if value else 0)

            editor = QLineEdit(parent)
            editor.setValidator(QIntValidator(editor))
            editor.editingFinished.connect(_on_editing_finished)
            return editor
        if isinstance(item, FloatItem):

            def _on_editing_finished():
                value = editor.text()
                item._on_value_changed(float(value) if value else 0.0)

            editor = QLineEdit(parent)
            editor.setValidator(FloatValidator(editor))
            editor.editingFinished.connect(_on_editing_finished)
            return editor
        if isinstance(item, StringItem):

            def _on_editing_finished():
                value = editor.text()
                item._on_value_changed(value)

            editor = QLineEdit(parent)
            editor.editingFinished.connect(_on_editing_finished)
            return editor
        if isinstance(item, (FieldBasicItem, FieldObjectItem, FieldArrayItem)):

            def _on_editing_finished():
                value = editor.text()
                item._on_key_changed(value)

            editor = QLineEdit(parent)
            editor.setText(item.data(Qt.EditRole))
            editor.editingFinished.connect(_on_editing_finished)
            return editor
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


########################################################################################
# View
########################################################################################


class JsonView(QTreeView):
    change = Signal(object)

    def __init__(self, value: JsonRoot = dict(), parent=None):
        super().__init__(parent)

        # Settings
        self.setSelectionMode(QTreeView.NoSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAlternatingRowColors(True)

        # Connect signals
        self.customContextMenuRequested.connect(self._on_context_menu)

        # Initialize the model
        self._model = JsonModel(value, self)
        self._model.change.connect(self._on_change)
        self._model.change.connect(self.change)
        self.setModel(self._model)

        # Header settings
        self._header = QHeaderView(Qt.Horizontal, self)
        self.setHeader(self._header)
        self._header.setSectionResizeMode(0, QHeaderView.Stretch)

        # Set the item delegate
        self.setItemDelegate(JsonItemDelegate(self))

    def set_value(self, value: JsonRoot):
        assert isinstance(value, dict), f"Expected dict, got {type(value)}"
        self._model.set_value(value)
        self.expandAll()

    def has_change(self, path: JsonPath = JsonPathRoot()) -> bool:
        return self._model.has_change(path)

    def list_changes(self, path: JsonPath = JsonPathRoot()) -> list[JsonChange]:
        return self._model.list_changes(path)

    def discard_changes(self):
        self._model.discard_changes()
        self.expandAll()

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
