from dataclasses import dataclass

from .types import JsonValue
from .path import JsonPath


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
class JsonOpFieldReorder(JsonOp):
    from_key: str
    to_key: str
    from_value: JsonValue
    to_value: JsonValue


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


def _change_field_reorder(
    path: JsonPath,
    from_key: str,
    to_key: str,
    from_value: JsonValue,
    to_value: JsonValue,
) -> JsonChange:
    return JsonChange(
        path, JsonOpFieldReorder(from_key, to_key, from_value, to_value)
    )
