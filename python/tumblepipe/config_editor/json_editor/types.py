from enum import Enum, auto


class FieldOrigin(Enum):
    """Origin category for a field in the JSON editor."""
    LOCAL = auto()      # Only in stored (unique to this entity)
    INHERITED = auto()  # Only in inherited (from parent)
    OVERRIDE = auto()   # In both, different values
    REDUNDANT = auto()  # In both, same values


#: Stored on this node *and* inherited: drawn bright and bold, like the
#: Entity Editor's "set here". A stored value equal to the inherited one
#: (REDUNDANT) used to keep the plain style, so a value pinned on a shot
#: (SQ020/SH020's frame_start: 1001) read as inherited (#35).
PINNED = frozenset({FieldOrigin.OVERRIDE, FieldOrigin.REDUNDANT})


def field_origin(in_stored: bool, in_inherited: bool,
                 stored_value=None, inherited_value=None) -> "FieldOrigin | None":
    """Where a field's value comes from; None when it is in neither."""
    if in_stored and not in_inherited:
        return FieldOrigin.LOCAL
    if in_inherited and not in_stored:
        return FieldOrigin.INHERITED
    if not in_stored:
        return None
    if stored_value == inherited_value:
        return FieldOrigin.REDUNDANT
    return FieldOrigin.OVERRIDE


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
