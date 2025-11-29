from dataclasses import dataclass
import string

NAME_ALPHABET = set(string.ascii_letters + string.digits + '_')

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
    purpose: str
    path: UriPath | None

    @staticmethod
    def parse_unsafe(raw_uri: str) -> 'Uri':
        def _fail():
            raise ValueError(f'Invalid URI "{raw_uri}"')

        def _parse_purpose(raw_uri: str) -> tuple[str, str]:
            if ':' not in raw_uri: _fail()
            purpose, raw_segments = raw_uri.split(':', 1)
            return purpose, raw_segments

        def _parse_path(raw_segments: str) -> UriPath | None:
            def _parse_part(part: str, path: UriPath) -> UriPath:
                if part == '*': return WildcardSection(path)
                if not _valid_name(part): _fail()
                return NamedSection(part, path)

            def _parse_last(part: str) -> UriPath:
                if part == '': _fail()
                if part == '*': return WildcardItem()
                return NamedItem(part)

            if not raw_segments.startswith('/'): _fail()
            if raw_segments == '/': return None
            segments = raw_segments[1:].split('/')
            if len(segments) == 0: _fail()
            path = _parse_last(segments[-1])
            for name in reversed(segments[:-1]):
                path = _parse_part(name, path)
            return path

        purpose, raw_segments = _parse_purpose(raw_uri)
        path = _parse_path(raw_segments)
        return Uri(purpose, path)
    
    @staticmethod
    def parse(raw_uri: str) -> 'Uri | None':
        try: return Uri.parse_unsafe(raw_uri)
        except ValueError: return None

    def parts(self) -> tuple[str | None, list[str]]:
        parts = []
        path = self.path
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

        if self.path is None: return False
        return _is_wild(self.path)

    def is_root(self) -> bool:
        return self.path is None

    @property
    def segments(self) -> list[str]:
        return self.parts()[1]

    def __len__(self) -> int:
        return len(self.parts()[1])

    def __getitem__(self, index):
        return self.parts()[1][index]

    def __iter__(self):
        return iter(self.parts()[1])

    def get(self, index: int, default=None):
        segments = self.parts()[1]
        if -len(segments) <= index < len(segments):
            return segments[index]
        return default

    def first(self):
        segments = self.parts()[1]
        return segments[0] if segments else None

    def last(self):
        segments = self.parts()[1]
        return segments[-1] if segments else None

    def display_name(self) -> str:
        """Generate a display name for entities (e.g., 'sequence/shot' or 'category/asset')."""
        segments = self.segments
        if len(segments) >= 3:
            return f"{segments[1]}/{segments[2]}"
        return "/".join(segments)

    def contains(self, other: 'Uri') -> bool:
        if self.purpose != other.purpose: return False
        if len(self.segments) >= len(other.segments): return False
        for self_part, other_part in zip(self.segments, other.segments):
            if self_part == other_part: continue
            return False
        return True

    def __str__(self) -> str:
        purpose, parts = self.parts()
        purpose = '' if purpose is None else f'{purpose}:'
        path = '/'.join(parts)
        return f'{purpose}/{path}'

    def __hash__(self) -> int:
        return hash(str(self))

    def __truediv__(self, other: str | list[str]) -> 'Uri':

        def _is_valid(other: str):
            if other == '*': return True
            if _valid_name(other): return True
            return False
    
        if isinstance(other, str):
            if not _is_valid(other):
                raise ValueError(f'Invalid other: {other}')
            self_str = str(self)
            separator = '' if self_str.endswith('/') else '/'
            return Uri.parse_unsafe(f'{self_str}{separator}{other}')

        if isinstance(other, list):
            if not all(map(_is_valid, other)):
                raise ValueError(f'Invalid other: {other}')
            self_str = str(self)
            separator = '' if self_str.endswith('/') else '/'
            other_path = '/'.join(other)
            return Uri.parse_unsafe(f'{self_str}{separator}{other_path}')
        
        raise ValueError(f'Invalid other: {other}')