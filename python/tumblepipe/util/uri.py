"""Pipeline URIs: ``purpose:/seg/seg[?k=v&...]``.

A URI is a flat, frozen value: a ``purpose`` string, path ``segments``,
and a query (stored canonically so equal URIs compare and hash equal
regardless of query order). This replaced an earlier design that modelled
the path as a linked hierarchy of wildcard/named nodes — that structure
only ever encoded "is this segment the literal ``*``", which the flat
string already carries, and it was the source of subtle join/hash bugs.
Segment-level wildcards (``*``) remain valid literal segments; nothing
depends on a *typed* wildcard distinction anymore.

Segments are stored as a tuple (so the value is hashable) but ``.segments``
returns a fresh list, matching the long-standing public contract.
"""

from dataclasses import dataclass
import string
import urllib.parse

NAME_ALPHABET = set(string.ascii_letters + string.digits + '_-.')


def _valid_name(name: str) -> bool:
    # Reject empty: an interior empty segment ('entity:/a//b') would
    # otherwise produce a silently-wrong address.
    return len(name) > 0 and set(name).issubset(NAME_ALPHABET)


def _valid_segment(segment: str) -> bool:
    return segment == '*' or _valid_name(segment)


@dataclass(frozen=True, init=False)
class Uri:
    purpose: str
    # Stored canonically as tuples so the dataclass is hashable and __eq__
    # is query-order insensitive. Read via the .segments / .query properties.
    _segments: tuple
    _query: tuple  # sorted tuple of (key, value) string pairs

    def __init__(self, purpose: str, segments=(), query=None):
        object.__setattr__(self, 'purpose', purpose)
        object.__setattr__(self, '_segments', tuple(segments) if segments else ())
        if query is None:
            items: tuple = ()
        elif isinstance(query, dict):
            items = tuple(sorted(query.items()))
        else:
            items = tuple(sorted(tuple(pair) for pair in query))
        object.__setattr__(self, '_query', items)

    # ── Construction ─────────────────────────────────────────────

    @staticmethod
    def parse_unsafe(raw_uri: str) -> 'Uri':
        """Parse a URI string, raising ``ValueError`` on anything invalid."""
        if ':' not in raw_uri:
            raise ValueError(f'Invalid URI "{raw_uri}"')
        purpose, rest = raw_uri.split(':', 1)

        query: dict = {}
        if '?' in rest:
            rest, query_string = rest.split('?', 1)
            for key, value in urllib.parse.parse_qsl(query_string, keep_blank_values=True):
                query[key] = value

        if not rest.startswith('/'):
            raise ValueError(f'Invalid URI "{raw_uri}"')
        if rest == '/':
            segments: tuple = ()
        else:
            parts = rest[1:].split('/')
            for part in parts:
                if not _valid_segment(part):
                    raise ValueError(f'Invalid URI "{raw_uri}"')
            segments = tuple(parts)

        return Uri(purpose, segments, query)

    # ── Properties ───────────────────────────────────────────────

    @property
    def segments(self) -> list:
        return list(self._segments)

    @property
    def query(self) -> dict:
        return dict(self._query)

    # ── Segment access ───────────────────────────────────────────

    def parts(self) -> tuple:
        """``(purpose, [segments])`` — retained for callers that unpack both."""
        return self.purpose, list(self._segments)

    def is_wild(self) -> bool:
        return '*' in self._segments

    def is_root(self) -> bool:
        return len(self._segments) == 0

    def __len__(self) -> int:
        return len(self._segments)

    def __getitem__(self, index):
        return self._segments[index]

    def __iter__(self):
        return iter(self._segments)

    def get(self, index: int, default=None):
        segments = self._segments
        if -len(segments) <= index < len(segments):
            return segments[index]
        return default

    def first(self):
        return self._segments[0] if self._segments else None

    def last(self):
        return self._segments[-1] if self._segments else None

    def display_name(self) -> str:
        segments = self._segments
        if len(segments) >= 3:
            return f"{segments[1]}/{segments[2]}"
        return "/".join(segments)

    def contains(self, other: 'Uri') -> bool:
        if self.purpose != other.purpose:
            return False
        if len(self._segments) >= len(other._segments):
            return False
        for self_part, other_part in zip(self._segments, other._segments):
            if self_part != other_part:
                return False
        return True

    # ── Serialisation / joining ──────────────────────────────────

    def __str__(self) -> str:
        base = f'{self.purpose}:/' + '/'.join(self._segments)
        if not self._query:
            return base
        query = '&'.join(
            f'{urllib.parse.quote(k, safe="")}={urllib.parse.quote(v, safe="")}'
            for k, v in self._query
        )
        return f'{base}?{query}'

    def __truediv__(self, other) -> 'Uri':
        if isinstance(other, str):
            others = [other]
        elif isinstance(other, (list, tuple)):
            others = list(other)
        else:
            raise ValueError(f'Invalid other: {other}')
        for segment in others:
            if not (isinstance(segment, str) and _valid_segment(segment)):
                raise ValueError(f'Invalid other: {other}')
        # Joining preserves purpose and query; only segments grow.
        return Uri(self.purpose, self._segments + tuple(others), self.query)
