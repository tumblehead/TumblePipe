from dataclasses import dataclass

from .types import JsonValue, JsonRoot


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
