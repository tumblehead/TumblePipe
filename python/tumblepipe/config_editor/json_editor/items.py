"""Qt item stack for the JSON tree editor.

Menu/action helpers, row-item factories, and every ``QStandardItem`` subclass.
Factories and item classes are mutually recursive (a container item builds child
rows via the factories, which construct further item classes), so they live
together. Item classes never reference the model/view — the dependency runs one
way: ``view`` imports ``items``.
"""

from functools import partial

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QInputDialog,
    QMenu,
    QMessageBox,
)

from .types import PINNED, FieldOrigin, JsonValue, _default_value, field_origin
from .path import JsonPath, _json_path_parts
from .ops import (
    _change_index_insert, _change_index_update, _change_index_reorder, _change_index_remove,
    _change_field_insert, _change_field_update, _change_field_rename, _change_field_remove, _change_field_reorder,
)


INHERITED_FG = QColor(95, 95, 100)  # Darker grey for inherited fields


OVERRIDE_FG = QColor(220, 220, 225)  # Brighter for override fields (non-default values)


def _add_index_section(menu: QMenu, callback):
    menu.addAction("Add Item").triggered.connect(lambda _checked=False: callback())


def _remove_index_section(menu: QMenu, callback):
    menu.addAction("Remove Item").triggered.connect(lambda _checked=False: callback())


def _add_field_section(menu: QMenu, callback):
    menu.addAction("Add Field").triggered.connect(lambda _checked=False: callback())


def _remove_field_section(menu: QMenu, callback):
    menu.addAction("Remove Field").triggered.connect(lambda _checked=False: callback())


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
        # exact type: a bool is an int to isinstance, which hid "integer"
        # from a boolean row
        return type(item.to_json()) is not value_type

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
    while isinstance(parent, _Slot):
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


def _back_to_inherited(parent, key: str):
    """Field ``key`` of object ``parent`` (the model or an object item) back
    to the value ``parent`` inherits for it; removed when it inherits none."""
    if key in parent._inherited_data:
        parent._set_field_to_inherited(key, parent._inherited_data[key])
    else:
        parent._remove_field(key)


def _revert_field_action(item):
    """Restore a field to its state in the pristine document: its value
    there, else what it inherits (:func:`_back_to_inherited`)."""
    parent = _parent(item)
    key = item._key
    found, original = _original_lookup(item.model(), parent.path() / key)
    if not found:
        _back_to_inherited(parent, key)
        return
    inherited = parent._inherited_data
    origin = field_origin(True, key in inherited, original, inherited.get(key))
    parent._set_field(key, original, origin, inherited.get(key))


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


def _origin_brush(origin: FieldOrigin) -> QBrush | None:
    """A row's text colour: grey inherited, bright pinned, plain local."""
    if origin == FieldOrigin.INHERITED:
        return QBrush(INHERITED_FG)
    if origin in PINNED:
        return QBrush(OVERRIDE_FG)
    return None


def _origin_font(origin: FieldOrigin) -> QFont | None:
    """A pinned row's bold font; None otherwise."""
    if origin not in PINNED:
        return None
    font = QFont()
    font.setBold(True)
    return font


class _ScalarItem(QStandardItem):
    """The Value cell of a null-free scalar row, coloured by its row's
    origin (the slot in column 0). Subclasses differ only in how the value
    reads (``__str__``); the delegate tells them apart for the editor."""

    def __init__(self, value):
        super().__init__()
        self._value = value

    def data(self, role=None):
        match role:
            case Qt.EditRole:
                return self._value
            case Qt.DisplayRole:
                return str(self)
            case Qt.ForegroundRole:
                return _origin_brush(_sibling(self, 0)._origin)
            case Qt.FontRole:
                return _origin_font(_sibling(self, 0)._origin)
            case _:
                return super().data(role)

    def _on_value_changed(self, value):
        if self._value == value:
            return
        self._value = value
        _sibling(self, 0)._on_value_changed(value)

    def _on_context_menu(self, position):
        return _sibling(self, 0)._on_context_menu(position)

    def to_json(self):
        return self._value


class BooleanItem(_ScalarItem):
    def __str__(self):
        return "true" if self._value else "false"


class IntegerItem(_ScalarItem):
    def __str__(self):
        return str(self._value)


class FloatItem(_ScalarItem):
    def __str__(self):
        return str(self._value)


class StringItem(_ScalarItem):
    def __str__(self):
        return self._value


class _Slot:
    """Column 0 of a row: what the row holds (a key in an object, an index
    in an array), its origin and its colours. Mixed in first, before
    ``QStandardItem`` and ``QObject``; the class gives ``change``."""

    _origin: FieldOrigin

    def _init_slot(self, origin: FieldOrigin, *, editable: bool, container: bool):
        QStandardItem.__init__(self)
        QObject.__init__(self)
        self._origin = origin
        if not editable or origin == FieldOrigin.INHERITED:
            self.setEditable(False)
        if origin != FieldOrigin.INHERITED:
            flags = self.flags() | Qt.ItemIsDragEnabled
            if container:
                flags |= Qt.ItemIsDropEnabled
            self.setFlags(flags)

    def _label(self) -> str:
        raise NotImplementedError

    def data(self, role=None):
        match role:
            case Qt.DisplayRole:
                return f"{self._label()}:"
            case Qt.ForegroundRole:
                return _origin_brush(self._origin)
            case Qt.FontRole:
                return _origin_font(self._origin)
            case _:
                return super().data(role)


class _KeySlot(_Slot):
    """A row under a key of an object (the model, a ``FieldObjectItem`` or
    an ``IndexObjectItem``): its key is its label and can be renamed."""

    _key: str

    def path(self) -> JsonPath:
        return _parent(self).path() / self._key

    def _label(self) -> str:
        return self._key

    def data(self, role=None):
        if role == Qt.EditRole:
            return self._key
        return super().data(role)

    def _on_key_changed(self, to_key: str):
        _rename_field_key(self, to_key)

    def _on_type_changed(self, new_type):
        _parent(self)._set_field(self._key, _default_value(new_type))

    def _on_remove(self):
        _remove_field_action(self.model().parent(), self)

    def _slot_sections(self, menu: QMenu):
        """Convert, Remove Field and Revert Changes; none on an inherited row."""
        if self._origin == FieldOrigin.INHERITED:
            return
        _change_type_section(menu, self, self._on_type_changed)
        _remove_field_section(menu, self._on_remove)
        _revert_field_section(menu, self)


class _IndexSlot(_Slot):
    """A row at an index of an array (a ``FieldArrayItem`` or an
    ``IndexArrayItem``): its index is its label; it moves among its
    siblings."""

    _index: int

    def path(self) -> JsonPath:
        return _parent(self).path() / self._index

    def _label(self) -> str:
        return str(self._index)

    def data(self, role=None):
        assert role != Qt.EditRole, f"{type(self).__name__} is not editable"
        return super().data(role)

    def _on_type_changed(self, new_type):
        _parent(self)._set_item(self._index, _default_value(new_type))

    def _on_remove(self):
        _remove_index_action(self.model().parent(), self)

    def _on_reorder_action(self, action):
        last = len(_parent(self)._items) - 1
        to_index = {
            "up": self._index - 1, "down": self._index + 1, "top": 0, "bottom": last,
        }[action]
        _parent(self)._move_item(self._index, to_index)

    def _slot_sections(self, menu: QMenu):
        """Move, Convert and Remove Item."""
        _reorder_index_section(menu, self, self._on_reorder_action)
        _change_type_section(menu, self, self._on_type_changed)
        _remove_index_section(menu, self._on_remove)


class FieldBasicItem(_KeySlot, QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str, origin: FieldOrigin = FieldOrigin.LOCAL):
        self._init_slot(origin, editable=True, container=False)
        self._key = key

    def _on_value_changed(self, value: JsonValue):
        # When overriding an inherited field, change origin to LOCAL
        if self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL
            _propagate_origin_to_local(self)
        self.change.emit(_change_field_update(_parent(self).path(), self._key, value))

    def _on_context_menu(self, position):
        if self._origin == FieldOrigin.INHERITED:
            return
        menu = QMenu()
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        return _sibling(self, 1).to_json()


class IndexBasicItem(_IndexSlot, QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        self._init_slot(origin, editable=False, container=False)
        self._index = index

    def _on_value_changed(self, value: JsonValue):
        # When overriding an inherited item, change origin to LOCAL
        if self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL
            _propagate_origin_to_local(self)
        self.change.emit(_change_index_update(_parent(self).path(), self._index, value))

    def _on_context_menu(self, position):
        menu = QMenu()
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        return _sibling(self, 1).to_json()


class _ObjectRows:
    """The row operations of a JSON object's item, keyed by field name:
    ``FieldObjectItem`` (an object under a key) and ``IndexObjectItem``
    (an object in an array). The class gives ``change``, ``_fields``,
    ``_inherited_data``, ``_origin`` and ``path()``; every row is a
    :class:`_KeySlot`."""

    def __contains__(self, key: str):
        assert isinstance(key, str), f"Expected str, got {type(key)}"
        return key in self._fields

    def _refresh_fields(self):
        """``_fields`` in row order."""
        self._fields = {
            self.child(row, 0)._key: self.child(row, 0) for row in range(self.rowCount())
        }

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
        self._refresh_fields()

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
        self._refresh_fields()

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


class _ArrayRows:
    """The row operations of a JSON array's item, by index:
    ``FieldArrayItem`` (an array under a key) and ``IndexArrayItem`` (an
    array in an array). The class gives ``change``, ``_items``,
    ``_origin`` and ``path()``; every row is an :class:`_IndexSlot`."""

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

    def _reindex(self):
        for index, item in enumerate(self._items):
            item._index = index

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

    def _remove_item(self, index: int, notify: bool = True):
        # When modifying an inherited container, change origin to LOCAL
        if notify and self._origin == FieldOrigin.INHERITED:
            self._origin = FieldOrigin.LOCAL

        # Remove the item from the model
        index_item = self._items[index]
        index_item.change.disconnect(self.change)
        self.removeRow(index)
        del self._items[index]

        self._reindex()

        # Emit the item removed signal
        if not notify:
            return
        self.change.emit(_change_index_remove(self.path(), index))

    def _move_item(self, from_index: int, to_index: int, notify: bool = True):
        """Move the item at ``from_index`` so it ends at ``to_index``."""
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

        # Insert at its new position
        self._items.insert(to_index, from_item)
        self.insertRow(to_index, row)
        self._reindex()

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


class FieldObjectItem(_KeySlot, _ObjectRows, QStandardItem, QObject):
    change = Signal(object)

    def __init__(
        self,
        key: str,
        origin: FieldOrigin = FieldOrigin.LOCAL,
        inherited_data: dict = None
    ):
        self._init_slot(origin, editable=True, container=True)
        self._key = key
        self._inherited_data = inherited_data or {}
        self._fields = dict()

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

            inherited_value = inherited.get(key)
            value = values[key] if in_stored else inherited_value
            origin = field_origin(in_stored, in_inherited, value, inherited_value)

            self._add_field(key, value, origin, inherited_value, notify=False)

    def _on_add(self):
        _add_field_action(self.model().parent(), self)

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_field_section(menu, self._on_add)
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        # Only return non-inherited fields
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
            if field_item._origin != FieldOrigin.INHERITED
        }


class FieldArrayItem(_KeySlot, _ArrayRows, QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, key: str, origin: FieldOrigin = FieldOrigin.LOCAL):
        self._init_slot(origin, editable=True, container=True)
        self._key = key
        self._items = list()

    def _on_add(self):
        # Infer type from existing items (inherited or local)
        first = self._items[0].to_json() if self._items else None
        # a null first item seeds null (_default_value takes None, not NoneType)
        self._add_item(_default_value(None if first is None else type(first)))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_index_section(menu, self._on_add)
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        return [index_item.to_json() for index_item in self._items]


class IndexObjectItem(_IndexSlot, _ObjectRows, QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        self._init_slot(origin, editable=False, container=True)
        self._index = index
        self._inherited_data = {}  # an object in an array inherits nothing by key
        self._fields = dict()

    def set_value(self, values: dict[str, JsonValue]):
        # Clear existing fields
        for key in self._fields.keys():
            field_item = self._fields[key]
            field_item.change.disconnect(self.change)
            self.removeRow(field_item.row())
        self._fields.clear()

        # If parent is INHERITED, all children are INHERITED too
        origin = FieldOrigin.INHERITED if self._origin == FieldOrigin.INHERITED else FieldOrigin.LOCAL

        # Add the fields to the object
        for key, value in values.items():
            self._add_field(key, value, origin, notify=False)

    def _on_add(self):
        _add_field_action(self.model().parent(), self)

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_field_section(menu, self._on_add)
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        return {
            key: field_item.to_json()
            for key, field_item in self._fields.items()
        }


class IndexArrayItem(_IndexSlot, _ArrayRows, QStandardItem, QObject):
    change = Signal(object)

    def __init__(self, index: int, origin: FieldOrigin = FieldOrigin.LOCAL):
        self._init_slot(origin, editable=False, container=True)
        self._index = index
        self._items = list()

    def _on_add(self):
        self._add_item(_default_value(None))

    def _on_context_menu(self, position):
        menu = QMenu()
        _add_index_section(menu, self._on_add)
        self._slot_sections(menu)
        menu.exec_(position)

    def to_json(self):
        return [item.to_json() for item in self._items]
