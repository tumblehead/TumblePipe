import string
from dataclasses import dataclass
from typing import Optional

NAME_ALPHABET = set(string.ascii_letters + string.digits + "_")


def _valid_name(name: str) -> bool:
    return set(name).issubset(NAME_ALPHABET)


@dataclass(frozen=True)
class UriPath:
    pass


@dataclass(frozen=True)
class WildcardSection(UriPath):
    path: UriPath


@dataclass(frozen=True)
class NamedSection(UriPath):
    name: str
    path: UriPath


@dataclass(frozen=True)
class WildcardItem(UriPath):
    pass


@dataclass(frozen=True)
class NamedItem(UriPath):
    name: str


@dataclass(frozen=True)
class Uri:
    purpose: str | None
    path: UriPath | None

    @staticmethod
    def parse(raw_uri: str) -> Optional["Uri"]:
        def _parse_purpose(raw_uri: str) -> tuple[str | None, str]:
            if ":" not in raw_uri:
                return None, raw_uri
            return raw_uri.split(":", 1)

        def _parse_path(raw_path: str) -> UriPath | None:
            def _parse_part(part: str, path: UriPath) -> UriPath | None:
                if part == "*":
                    return WildcardSection(path)
                if not _valid_name(part):
                    return None
                return NamedSection(part, path)

            def _parse_last(part: str) -> UriPath | None:
                if part == "*":
                    return WildcardItem()
                if part == "":
                    return None
                return NamedItem(part)

            if not raw_path.startswith("/"):
                return None

            # Handle root path case: "/" should return None to represent root
            if raw_path == "/":
                return None

            parts = raw_path[1:].split("/")
            if len(parts) == 0:
                return None
            path = _parse_last(parts[-1])
            if path is None:
                return None
            for name in reversed(parts[:-1]):
                path = _parse_part(name, path)
                if path is None:
                    return None
            return path

        purpose, raw_path = _parse_purpose(raw_uri)
        path = _parse_path(raw_path)
        # path can be None for root URIs (e.g., "asset:/")
        # Only return None if parsing completely failed
        if raw_path != "/" and path is None:
            return None
        return Uri(purpose, path)

    def parts(self) -> tuple[str | None, list[str]]:
        parts = []
        path = self.path
        while path is not None:
            match path:
                case WildcardItem():
                    parts.append("*")
                    path = None
                case NamedItem(name):
                    parts.append(name)
                    path = None
                case WildcardSection(path):
                    parts.append("*")
                    path = path
                case NamedSection(name, path):
                    parts.append(name)
                    path = path
        return self.purpose, parts

    def is_wild(self) -> bool:
        def _is_wild(path: UriPath) -> bool:
            match path:
                case WildcardSection(_):
                    return True
                case NamedSection(_, subpath):
                    return _is_wild(subpath)
                case WildcardItem():
                    return True
                case NamedItem(_):
                    return False
            return False

        if self.path is None:
            return False
        return _is_wild(self.path)

    def is_root(self) -> bool:
        return self.path is None

    def __str__(self) -> str:
        purpose, parts = self.parts()
        purpose = "" if purpose is None else f"{purpose}:"
        path = "/".join(parts)
        return f"{purpose}/{path}"

    def __hash__(self) -> int:
        return hash(str(self))

    def __truediv__(self, other: str) -> "Uri":
        assert isinstance(other, str), f"Invalid other: {other}"

        def _convert(other: str):
            if other == "*":
                return WildcardItem()
            if _valid_name(other):
                return NamedItem(other)
            raise ValueError(f"Invalid other: {other}")

        def _append(path: UriPath | None, item: UriPath) -> UriPath:
            match path:
                case None:
                    return item
                case WildcardItem():
                    return WildcardSection(item)
                case NamedItem(name):
                    return NamedSection(name, item)
                case WildcardSection(subpath):
                    return WildcardSection(_append(subpath, item))
                case NamedSection(name, subpath):
                    return NamedSection(name, _append(subpath, item))
            assert False, f"Invalid path: {path}"

        return Uri(self.purpose, _append(self.path, _convert(other)))
