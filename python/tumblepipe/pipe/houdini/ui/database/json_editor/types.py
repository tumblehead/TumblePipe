from enum import Enum, auto


class FieldOrigin(Enum):
    """Origin category for a field in the JSON editor."""
    LOCAL = auto()      # Only in stored (unique to this entity)
    INHERITED = auto()  # Only in inherited (from parent)
    OVERRIDE = auto()   # In both, different values
    REDUNDANT = auto()  # In both, same values


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
