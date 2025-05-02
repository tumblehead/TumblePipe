from typing import Optional, Tuple
from dataclasses import dataclass
import string

@dataclass(frozen=True)
class UriPath: pass

@dataclass(frozen=True)
class Uri:
    purpose: Optional[str]
    path: UriPath

@dataclass(frozen=True)
class WildcardSection(UriPath):
    path: UriPath

@dataclass(frozen=True)
class NamedSection(UriPath):
    name: str
    path: UriPath

@dataclass(frozen=True)
class WildcardItem(UriPath): pass

@dataclass(frozen=True)
class NamedItem(UriPath):
    name: str

NAME_ALPHABET = set(string.ascii_letters + string.digits + '_')

def parse(raw_uri: str) -> Optional[Uri]:
    def _valid_name(name: str) -> bool:
        return set(name).issubset(NAME_ALPHABET)

    def _parse_purpose(raw_uri: str) -> Tuple[Optional[str], str]:
        if ':' not in raw_uri: return None, raw_uri
        return raw_uri.split(':', 1)

    def _parse_path(raw_path: str) -> Optional[UriPath]:
        def _parse_part(part: str, path: UriPath) -> Optional[UriPath]:
            if part == '*': return WildcardSection(path)
            if not _valid_name(part): return None
            return NamedSection(part, path)

        def _parse_last(part: str) -> Optional[UriPath]:
            if part == '*': return WildcardItem()
            if part == '': return None
            return NamedItem(part)

        if not raw_path.startswith('/'): return None
        parts = raw_path[1:].split('/')
        if len(parts) == 0: return None
        path = _parse_last(parts[-1])
        if path is None: return None
        for name in reversed(parts[:-1]):
            path = _parse_part(name, path)
            if path is None: return None
        return path

    purpose, raw_path = _parse_purpose(raw_uri)
    path = _parse_path(raw_path)
    if path is None: return None
    return Uri(purpose, path)

def parts(uri: Uri) -> Tuple[Optional[str], list[str]]:
    parts = []
    path = uri.path
    while path is not None:
        match path:
            case WildcardItem():
                parts.append('*')
                path = None
            case NamedItem(name):
                parts.append(name)
                path = None
            case WildcardSection(path):
                parts.append('*')
                path = path
            case NamedSection(name, path):
                parts.append(name)
                path = path
    return uri.purpose, list(reversed(parts))

def to_string(uri: Uri) -> str:
    purpose, parts = parts(uri)
    purpose = '' if purpose is None else f'{purpose}:'
    return f'{purpose}/{'/'.join(reversed(parts))}'