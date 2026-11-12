"""What the entity picker shows: entries, scopes, search, From context.

The Entity button on every entity-aware th:: node opens the picker window
(``pipe.houdini.ui.widgets.entity_selector``). Everything the window decides
that is not drawing lives here, without Qt or ``hou``, so it can be tested:
which entries there are, how they are ordered, which sidebar scopes they
fall into, what a search keeps, what "From context" reads as, and the
recent picks list.

An entry is an entity URI the picker offers (``entity:/shots/010/sh020``):
its context (``shots``), the groups between context and name (``010``) and
its name (``sh020``). Nothing here asks the config; the window lists the
URIs and hands them over.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

FROM_CONTEXT = 'from_context'

# The order contexts are shown in, in the sidebar and in "All".
CONTEXT_ORDER = ('shots', 'assets', 'edits')

# Singular names for a context, for card types and search placeholders.
CONTEXT_KINDS = {'shots': 'shot', 'assets': 'asset', 'edits': 'edit'}

# How many recent picks the picker remembers per project.
RECENT_LIMIT = 8


@dataclass(frozen=True)
class PickerEntry:
    """One entity the picker offers."""

    uri: str
    context: str
    groups: tuple[str, ...]
    name: str

    @property
    def kind(self) -> str:
        """'shot', 'asset', 'edit', or the context itself for any other."""
        return CONTEXT_KINDS.get(self.context, self.context)

    @property
    def path(self) -> str:
        """The entity below its context: ``010/sh020``."""
        return '/'.join(self.groups + (self.name,))

    @property
    def group_path(self) -> str:
        """The groups only: ``010``; '' for an entity right under its
        context (an edit)."""
        return '/'.join(self.groups)

    @property
    def search_text(self) -> str:
        """What a search matches against, lowercased."""
        return '/'.join((self.context,) + self.groups + (self.name,)).lower()


def _segments(uri: str) -> list[str] | None:
    """An entity URI's segments, None for anything else."""
    if not uri.startswith('entity:/'):
        return None
    segments = [s for s in uri[len('entity:/'):].split('/') if s]
    return segments or None


def entry_for(uri: str) -> PickerEntry | None:
    """The entry for an entity URI with a context and a name; None for
    'from_context', a non-entity URI or a bare context."""
    segments = _segments(str(uri))
    if segments is None or len(segments) < 2:
        return None
    return PickerEntry(
        uri='entity:/' + '/'.join(segments),
        context=segments[0],
        groups=tuple(segments[1:-1]),
        name=segments[-1],
    )


def _natural(text: str) -> tuple:
    """Sort key that orders digit runs by value: sh9 before sh10."""
    return tuple(
        (0, int(part), '') if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r'(\d+)', text) if part
    )


def _context_rank(context: str) -> tuple:
    if context in CONTEXT_ORDER:
        return (CONTEXT_ORDER.index(context), '')
    return (len(CONTEXT_ORDER), context)


def entry_key(entry: PickerEntry) -> tuple:
    """The picker's order: context, then groups and name naturally."""
    return (
        _context_rank(entry.context),
        tuple(_natural(g) for g in entry.groups),
        _natural(entry.name),
        entry.uri,
    )


def entries_from_uris(uris) -> list[PickerEntry]:
    """The entries for ``uris``, deduplicated and in picker order.
    Anything that is not an entity with a context and a name is left out."""
    entries = {}
    for uri in uris:
        entry = entry_for(str(uri))
        if entry is not None:
            entries[entry.uri] = entry
    return sorted(entries.values(), key=entry_key)


# Search

def query_tokens(query: str) -> list[str]:
    """A search's words, lowercased: split on whitespace and '/', so
    ``010/sh02`` and ``010 sh02`` search alike."""
    return [t for t in re.split(r'[\s/]+', query.lower()) if t]


def matches(entry: PickerEntry, query: str) -> bool:
    """Whether every word of ``query`` is in the entry's context, groups
    or name. An empty query matches everything."""
    text = entry.search_text
    return all(token in text for token in query_tokens(query))


def _rank(entry: PickerEntry, tokens: list[str]) -> int:
    """0 for a name equal to the search, 1 for a name starting with its
    last word, 2 otherwise: what the artist typed comes first."""
    if not tokens:
        return 2
    name = entry.name.lower()
    if name == ''.join(tokens) or name == tokens[-1]:
        return 0
    if name.startswith(tokens[-1]):
        return 1
    return 2


def search(entries, query: str) -> list[PickerEntry]:
    """The entries ``query`` matches, best first and otherwise in the
    order given. Enter in the picker takes the first."""
    tokens = query_tokens(query)
    kept = [e for e in entries if all(t in e.search_text for t in tokens)]
    if not tokens:
        return kept
    return [e for _, e in sorted(
        enumerate(kept), key=lambda pair: (_rank(pair[1], tokens), pair[0])
    )]


# Sidebar scopes

ALL_SCOPE = ''
RECENT_SCOPE = 'recent:'


@dataclass(frozen=True)
class Scope:
    """A sidebar row: '' for all, ``shots`` for a context, ``shots/010``
    for a group, :data:`RECENT_SCOPE` for the recent picks."""

    key: str
    label: str
    depth: int
    count: int


def in_scope(entry: PickerEntry, key: str) -> bool:
    """Whether ``entry`` is under the scope ``key`` (not the recent one)."""
    if key == ALL_SCOPE:
        return True
    parts = key.split('/')
    own = (entry.context,) + entry.groups
    return tuple(parts) == own[:len(parts)]


def scopes(entries) -> list[Scope]:
    """All, then each context and each group under it, with how many
    entries each holds, in picker order."""
    entries = list(entries)
    counts: dict[str, int] = {}
    order: list[str] = []
    for entry in sorted(entries, key=entry_key):
        own = (entry.context,) + entry.groups
        for depth in range(1, len(own) + 1):
            key = '/'.join(own[:depth])
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1
    rows = [Scope(ALL_SCOPE, 'All', 0, len(entries))]
    for key in order:
        parts = key.split('/')
        label = parts[-1]
        if len(parts) == 1:
            label = label.capitalize()
        rows.append(Scope(key, label, len(parts), counts[key]))
    return rows


# From context

def from_context_text(resolved: str | None) -> str:
    """The From context row: what the sentinel resolves to in the open
    workfile, ``From context (010/sh020)``, or that it resolves to
    nothing here."""
    entry = entry_for(resolved) if resolved else None
    if entry is None:
        return 'From context (nothing in this scene)'
    return f'From context ({entry.path})'


def selection_text(uri: str | None, resolved: str | None) -> str:
    """The footer line for the current pick."""
    if not uri:
        return 'Nothing selected'
    if uri == FROM_CONTEXT:
        return from_context_text(resolved)
    entry = entry_for(uri)
    return entry.uri if entry is None else f'{entry.kind.capitalize()} {entry.path}'


# Recent picks
#
# Stored in the pipeline prefs (asset_browser.prefs.PipelinePrefs
# .entity_picker_recent) as {project key: [entity URI, ...]}. The file is
# the artist's and anything may be in it (an older or newer TumblePipe, a
# hand edit, a torn write), so every read goes through clean_recent and
# nothing read from it can make the picker raise.

def clean_recent(value, limit: int | None = RECENT_LIMIT) -> list[str]:
    """``value`` as a recent list: the entity URIs in it, in order, each
    once, at most ``limit`` (None: all); anything that is not a list reads
    as none."""
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        entry = entry_for(item) if isinstance(item, str) else None
        if entry is not None and entry.uri not in cleaned:
            cleaned.append(entry.uri)
    return cleaned if limit is None else cleaned[:limit]


def push_recent(recent, uri: str, limit: int = RECENT_LIMIT) -> list[str]:
    """``recent`` with ``uri`` moved to the front, at most ``limit`` long.
    'from_context' and anything that is not an entity are not remembered."""
    recent = clean_recent(recent, limit=None)
    entry = entry_for(uri) if isinstance(uri, str) else None
    if entry is None:
        return recent[:limit]
    return ([entry.uri] + [u for u in recent if u != entry.uri])[:limit]


def recent_entries(recent, entries) -> list[PickerEntry]:
    """The recent picks that are still on offer, most recent first."""
    by_uri = {e.uri: e for e in entries}
    return [by_uri[u] for u in clean_recent(recent) if u in by_uri]


def project_key(project_path) -> str:
    """The key a project's recent picks are stored under: its path,
    spelled one way (forward slashes, no trailing slash, lowercase)."""
    return str(project_path or '').replace('\\', '/').rstrip('/').lower()


def recent_for(stored, project_path) -> list[str]:
    """The recent picks of ``project_path`` in the stored mapping; none
    when the mapping or its entry is not what it should be."""
    if not isinstance(stored, dict):
        return []
    return clean_recent(stored.get(project_key(project_path)))


def with_recent(stored, project_path, uri: str) -> dict:
    """The stored mapping with ``uri`` pushed onto ``project_path``'s
    picks. Other projects' entries are kept as they are."""
    result = dict(stored) if isinstance(stored, dict) else {}
    key = project_key(project_path)
    result[key] = push_recent(recent_for(result, project_path), uri)
    return result


def load_recent(project_path) -> list[str]:
    """``project_path``'s recent picks from the artist's prefs; none when
    the prefs cannot be read. Never raises."""
    try:
        from tumblepipe.asset_browser.prefs import load_prefs
        return recent_for(load_prefs().entity_picker_recent, project_path)
    except Exception:
        log.warning('Could not read the recent entity picks', exc_info=True)
        return []


def remember(project_path, uri: str) -> None:
    """Put ``uri`` first in ``project_path``'s recent picks. A pick that
    cannot be remembered still stands, so this never raises."""
    if entry_for(uri) is None:
        return
    try:
        from tumblepipe.asset_browser.prefs import load_prefs, update_prefs
        stored = load_prefs().entity_picker_recent
        update_prefs(entity_picker_recent=with_recent(stored, project_path, uri))
    except Exception:
        log.warning('Could not remember the entity pick', exc_info=True)


# The window's state

class PickerState:
    """What the picker window shows and what it would pick: the scope, the
    search, the visible entries and the selection. The window mirrors it;
    every decision about what Enter or Select returns is made here.

    ``accept()`` only ever returns an entry that is visible (so offered and
    matching the search) or 'from_context' when that is offered, else None.
    """

    def __init__(self, entries, include_from_context: bool = True,
                 recent=None, current: str | None = None):
        self.entries = list(entries)
        self.include_from_context = include_from_context
        self.recent = clean_recent(recent)
        self.scope = ALL_SCOPE
        self.query = ''
        self.visible: list[PickerEntry] = []
        self.selected: str | None = None
        self._refresh()
        if current == FROM_CONTEXT and include_from_context:
            self.selected = FROM_CONTEXT
        else:
            entry = entry_for(current) if isinstance(current, str) else None
            if entry is not None and entry.uri in self.visible_uris():
                self.selected = entry.uri

    def visible_uris(self) -> list[str]:
        return [e.uri for e in self.visible]

    @property
    def current_row(self) -> int | None:
        """The selected card's row, None when no card is selected."""
        uris = self.visible_uris()
        return uris.index(self.selected) if self.selected in uris else None

    def _refresh(self):
        if self.scope == RECENT_SCOPE:
            pool = recent_entries(self.recent, self.entries)
        else:
            pool = [e for e in self.entries if in_scope(e, self.scope)]
        self.visible = search(pool, self.query)
        uris = self.visible_uris()
        if self.selected in uris:
            return
        if query_tokens(self.query) and uris:
            # After a search the best match is the pick, so Enter takes it.
            self.selected = uris[0]
        elif self.selected != FROM_CONTEXT:
            self.selected = None

    def set_query(self, query: str):
        self.query = query or ''
        self._refresh()

    def set_scope(self, key: str):
        self.scope = key
        self._refresh()

    def select_row(self, row: int):
        if 0 <= row < len(self.visible):
            self.selected = self.visible[row].uri

    def select_uri(self, uri):
        if uri in self.visible_uris():
            self.selected = uri

    def pick_from_context(self):
        if self.include_from_context:
            self.selected = FROM_CONTEXT

    def move(self, delta: int):
        """An arrow key: the card ``delta`` rows on, kept in the list; the
        first card when none is selected."""
        if not self.visible:
            return
        row = self.current_row
        if row is None:
            self.selected = self.visible[0].uri
            return
        self.selected = self.visible[max(0, min(len(self.visible) - 1, row + delta))].uri

    def accept(self) -> str | None:
        if self.selected == FROM_CONTEXT and self.include_from_context:
            return FROM_CONTEXT
        if self.selected in self.visible_uris():
            return self.selected
        return None
