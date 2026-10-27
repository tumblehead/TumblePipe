"""The Entity Editor's model: a project's assets or shots as an editable sheet.

The Entity Editor (``asset_browser/entity_editor``) has an Assets tab and a
Shots tab. Each shows every entity of its kind as a row: its group (category
or sequence), its name, and its timing (frame range, pre/post roll, fps),
plus ``animatable`` for assets and the Root (and so the assets it imports)
for shots. This module is all of it but the widgets, so it runs and is
tested without Qt:

- :func:`load` reads one :class:`Kind` as stored: each entity's *own*
  values, what each field inherits from its group, the kind's root, the
  project and the schema defaults, and for shots the Roots' asset lists.
- :class:`Sheet` holds the edits not yet applied on top of that (values set
  or put back to inherited, renames, new entities, entities to retire, Root
  asset lists and new Roots) with undo. :meth:`Sheet.problems` names what is
  wrong with the rows that were touched; :meth:`Sheet.changes` turns the
  rest into writes.
- :func:`apply` makes the writes in an order that cannot trip over itself
  (retire, rename, new groups, Roots, new entities, values) and stops at the
  first failure.
- :meth:`Sheet.rebase` carries the edits onto a fresh :func:`load`. After an
  Apply, what was written falls away and what was not stays pending, so a
  failure part-way loses nothing; Reload is the same call.

A value is *own* when it is stored on the entity and *inherited* otherwise.
Typing a different value pins it on the entity; Reset (Delete) removes the
pin. Typing what an inheriting entity already inherits changes nothing
rather than pinning a copy: a pinned copy silently stops following its
group, which is how the old Edit dialog de-inherited 20 shots (see
``JsonConfigStore.set_own_properties``). A pinned value stays pinned when
typed over, even with what the parent says, since a frame range typed for
a shot is that shot's.

A shot imports the assets of its Root (``scenes:/…``): the Root's own list
plus its parent Roots' lists. Roots are shared, so a Root's asset list is
edited as the Root's (:meth:`Sheet.set_root_assets`), not the shot's; a shot
that needs its own list gets its own Root (:meth:`Sheet.add_root`).

Retiring an entity is the browser's Delete: it leaves the database and its
folders move to ``_deleted/`` (``pipe.entity_files.delete_entity``).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Iterator, Mapping

from tumblepipe.config.shot_batch import (
    ASSETS_URI, SHOTS_URI, name_problem, same_but_case,
)
from tumblepipe.util.uri import Uri

FRAME_START = 'frame_start'
FRAME_END = 'frame_end'
ROLL_START = 'roll_start'
ROLL_END = 'roll_end'
FPS = 'fps'
ROOT = 'scene'
ANIMATABLE = 'animatable'
#: Not stored: setting it moves the end frame.
LENGTH = 'length'
#: The names, editable as cells too (a stored entity's group is not).
GROUP = 'group'
NAME = 'name'
#: Not stored on the shot: the assets its Root imports.
ASSETS = 'assets'

SCENES_URI = Uri.parse('scenes:/')
#: How ``config.scene`` stores a Root's asset list entries.
CHANNEL_KEY = 'channel'
DEFAULT_CHANNEL = 'default'

_LABELS = {
    FRAME_START: 'Start', FRAME_END: 'End', LENGTH: 'Length',
    ROLL_START: 'Pre-roll', ROLL_END: 'Post-roll', FPS: 'FPS', ROOT: 'Root',
    ANIMATABLE: 'Animatable', ASSETS: 'Assets',
}

_INT_FIELDS = frozenset({FRAME_START, FRAME_END, ROLL_START, ROLL_END, LENGTH})
_BOOL_FIELDS = frozenset({ANIMATABLE})
_INT = re.compile(r'-?[0-9]+')
_NUMBER = re.compile(r'-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)')
_ROOT_NAME = re.compile(r'[A-Za-z0-9_-]+')
_TRUE = frozenset({'1', 'true', 'yes', 'y', 'on', 'x'})
_FALSE = frozenset({'0', 'false', 'no', 'n', 'off'})

_PROJECT = Uri.parse('entity:/')

# Cell states (:meth:`Sheet.state`).
OWN = 'own'              # stored on the entity
INHERITED = 'inherited'  # comes from the group, the project or defaults
SET = 'set'              # a value not applied yet
CLEARED = 'cleared'      # a pin to be removed: inherits again once applied


@dataclass(frozen=True)
class Kind:
    """Assets or shots: where they live and what the sheet edits on them."""
    key: str               # 'assets' / 'shots'
    root: Uri              # entity:/assets / entity:/shots
    group: str             # 'category' / 'sequence'
    item: str              # 'asset' / 'shot'
    fields: tuple[str, ...]
    everyone: str          # 'all assets' / 'all shots'
    other_root: Uri        # the other kind: no group may share a name with its groups
    other_group: str       # 'a sequence' / 'an asset category'

    def label(self, column: str) -> str:
        if column == GROUP:
            return self.group.capitalize()
        if column == NAME:
            return self.item.capitalize()
        return _LABELS.get(column, column)

    def created(self, name: str) -> dict:
        """The properties a new entity starts with besides its values: an
        asset carries its name, as the browser's New Asset writes it."""
        return {'name': name} if self is ASSETS_KIND else {}


_TIMING = (FRAME_START, FRAME_END, ROLL_START, ROLL_END, FPS)
SHOTS_KIND = Kind(
    'shots', SHOTS_URI, 'sequence', 'shot', (*_TIMING, ROOT),
    'all shots', ASSETS_URI, 'an asset category',
)
ASSETS_KIND = Kind(
    'assets', ASSETS_URI, 'category', 'asset', (*_TIMING, ANIMATABLE),
    'all assets', SHOTS_URI, 'a sequence',
)
KINDS = (ASSETS_KIND, SHOTS_KIND)


class _Reset:
    def __repr__(self) -> str:
        return 'RESET'


#: A pending edit removing the entity's own value, so it inherits again.
RESET = _Reset()
_MISSING = object()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_value(name: str, value) -> object:
    """``value`` (typed text or a number) as field ``name`` stores it.

    Frames, rolls and lengths are whole numbers; fps is a number, kept whole
    when it is (``25``, ``23.976``); Animatable is yes or no; a Root is a
    ``scenes:/`` URI string. Raises ValueError saying what the field takes.
    """
    label = _LABELS.get(name, name)
    if name == ROOT:
        text = str(value).strip()
        if not text:
            raise ValueError('Pick a Root')
        # The sheet shows a Root as its path: that reads back as the URI.
        return text if text.startswith('scenes:/') else root_uri(text)
    if name in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f'{label} takes yes or no, not "{value}"')
    if name in _INT_FIELDS:
        if _is_int(value):
            return value
        text = str(value).strip()
        if _INT.fullmatch(text):
            return int(text)
        raise ValueError(f'{label} takes a whole number, not "{text}"')
    if name == FPS:
        if _is_number(value):
            number = float(value)
        else:
            text = str(value).strip()
            if not _NUMBER.fullmatch(text):
                raise ValueError(f'FPS takes a number, not "{text}"')
            number = float(text)
        return int(number) if number.is_integer() else number
    raise ValueError(f'The Entity Editor does not edit {name!r}')


def format_value(name: str, value) -> str:
    """``value`` of field ``name`` as the sheet shows (and copies) it; it
    reads back through :func:`parse_value` as the same value. A float is
    written in full: ``f'{v:g}'`` cut 29.97002997 to 29.97, so copying an
    fps from one row to another changed it."""
    if value is None:
        return ''
    if name == ROOT:
        return root_path(value) if isinstance(value, str) else '(old asset list)'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RootAsset:
    """One entry of a Root's asset list, as ``config.scene`` stores it."""
    asset: str                       # entity:/assets/<category>/<asset>
    instances: int = 1
    channel: str = DEFAULT_CHANNEL

    @property
    def label(self) -> str:
        name = self.asset.removeprefix('entity:/assets/')
        return name if self.instances == 1 else f'{name} ×{self.instances}'

    def stored(self) -> dict:
        return {'asset': self.asset, 'instances': self.instances, CHANNEL_KEY: self.channel}


def _root_assets(own: Mapping | None) -> tuple[RootAsset, ...]:
    return tuple(
        RootAsset(
            str(item['asset']), int(item.get('instances', 1)),
            str(item.get(CHANNEL_KEY, DEFAULT_CHANNEL)),
        )
        for item in (own or {}).get('assets', [])
        if isinstance(item, dict) and 'asset' in item
    )


def root_uri(path: str) -> str:
    """``outdoor/forest`` as the ``scenes:/outdoor/forest`` a shot stores."""
    return f'scenes:/{path.strip("/")}'


def root_path(uri: str) -> str:
    return uri.removeprefix('scenes:/')


def _root_parents(uri: str) -> list[str]:
    """``scenes:/a/b/c``'s parent Roots, closest first."""
    segments = root_path(uri).split('/')
    return [root_uri('/'.join(segments[:i])) for i in range(len(segments) - 1, 0, -1)]


@dataclass(frozen=True)
class Record:
    """An entity as stored: its names, URI and own values of the kind's
    fields."""
    group: str
    name: str
    uri: Uri
    own: Mapping[str, object]


@dataclass(frozen=True)
class Inherited:
    """What an entity in one group inherits: each field's value, and where
    it comes from ("sequence sq010", "the project's defaults", ...)."""
    values: Mapping[str, object]
    sources: Mapping[str, str]


@dataclass(frozen=True)
class Base:
    """A project's assets or shots as :func:`load` read them."""
    kind: Kind
    records: tuple[Record, ...]
    groups: tuple[str, ...]             # every group, empty ones too
    other_groups: frozenset[str]        # the other kind's groups (no name sharing)
    by_group: Mapping[str, Inherited]
    for_new_group: Inherited
    #: Shots only: every Root's own asset list, by ``scenes:/`` URI string.
    roots: Mapping[str, tuple[RootAsset, ...]] = field(default_factory=dict)
    #: Every asset URI string (what a Root can list).
    assets: tuple[str, ...] = ()

    def inherited(self, group: str) -> Inherited:
        return self.by_group.get(group, self.for_new_group)


def _inherited(config, kind: Kind, group: str | None) -> Inherited:
    """What an entity in ``group`` (None: a group not created yet)
    inherits: ``get_properties`` without the entity's own layer, which is
    ``get_inherited_properties`` for an entity that need not exist yet."""
    # A schema is found by position alone, so any name will do.
    schema = config.get_entity_schema(kind.root / (group or 'new') / 'new')
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    if schema is not None:
        for name in kind.fields:
            if name in schema.fields:
                values[name] = schema.fields[name].default
                sources[name] = "the project's defaults"
    chain = [(_PROJECT, 'the project'), (kind.root, kind.everyone)]
    if group is not None:
        chain.append((kind.root / group, f'{kind.group} {group}'))
    for uri, label in chain:
        own = config.get_own_properties(uri) or {}
        for name in kind.fields:
            if name in own:
                values[name] = own[name]
                sources[name] = label
    return Inherited(values, sources)


def _load_roots(config) -> dict[str, tuple[RootAsset, ...]]:
    found: dict[str, tuple[RootAsset, ...]] = {}
    for uri in config.list_entity_uris(SCENES_URI, closure=True):
        # A parent Root is a Root too (config.scene creates them with an
        # empty list), but only leaves are listed.
        for depth in range(1, len(uri.segments) + 1):
            key = root_uri('/'.join(uri.segments[:depth]))
            if key not in found:
                path = SCENES_URI
                for segment in uri.segments[:depth]:
                    path = path / segment
                own = config.get_own_properties(path)
                if own is not None:
                    found[key] = _root_assets(own)
    return dict(sorted(found.items()))


def load(config, kind: Kind = SHOTS_KIND) -> Base:
    """The project's ``kind`` entities, in database order, with what they
    inherit."""
    with config.coherent():
        records: list[Record] = []
        groups: list[str] = []
        for uri in config.list_entity_uris(kind.root, closure=True):
            segments = uri.segments[1:]
            if not segments:
                continue
            if segments[0] not in groups:
                groups.append(segments[0])
            if len(segments) == 2:
                own = config.get_own_properties(uri) or {}
                records.append(Record(
                    segments[0], segments[1], kind.root / segments[0] / segments[1],
                    {name: own[name] for name in kind.fields if name in own},
                ))
        other_groups = frozenset(
            uri.segments[1]
            for uri in config.list_entity_uris(kind.other_root, closure=True)
            if len(uri.segments) >= 2
        )
        assets = tuple(
            str(uri)
            for uri in config.list_entity_uris(ASSETS_URI, closure=True)
            if len(uri.segments) == 3
        )
        return Base(
            kind=kind,
            records=tuple(records),
            groups=tuple(groups),
            other_groups=other_groups,
            by_group={g: _inherited(config, kind, g) for g in groups},
            for_new_group=_inherited(config, kind, None),
            roots=_load_roots(config) if kind is SHOTS_KIND else {},
            assets=assets,
        )


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


@dataclass
class Row:
    """One row: a stored entity (``origin``) or a new one (``origin`` None).

    ``edits`` holds the pending values (or :data:`RESET`) by field; a new
    row's are the values it will be created with. Read-only outside
    :class:`Sheet`.
    """
    id: int
    origin: Record | None
    group: str
    name: str
    edits: dict[str, object] = field(default_factory=dict)
    retire: bool = False

    @property
    def is_new(self) -> bool:
        return self.origin is None

    @property
    def renamed(self) -> bool:
        return self.origin is not None and self.name != self.origin.name

    @property
    def touched(self) -> bool:
        return self.is_new or self.renamed or bool(self.edits) or self.retire


@dataclass(frozen=True)
class Changes:
    """The writes an Apply makes, in the order :func:`apply` makes them."""
    retire: tuple[Uri, ...] = ()
    rename: tuple[tuple[Uri, str], ...] = ()
    groups: tuple[Uri, ...] = ()
    #: Roots to create (parents first) or whose asset list changes.
    roots: tuple[tuple[str, tuple[RootAsset, ...], bool], ...] = ()  # (uri, assets, new)
    create: tuple[tuple[Uri, Mapping[str, object]], ...] = ()
    update: tuple[tuple[Uri, Mapping[str, object]], ...] = ()  # URI after any rename

    def __bool__(self) -> bool:
        return any((
            self.retire, self.rename, self.groups, self.roots,
            self.create, self.update,
        ))


@dataclass(frozen=True)
class Summary:
    new: int
    changed: int
    renamed: int
    retired: int
    problems: int
    new_groups: tuple[str, ...]
    roots_changed: int = 0
    roots_new: int = 0


class Sheet:
    """A project's assets or shots with the edits not yet applied; see the
    module docstring. ``rename_blocker`` says why a stored entity cannot be
    renamed ('' when it can): ``pipe.entity_files`` refuses one with files
    on disk."""

    def __init__(
        self, base: Base, rename_blocker: Callable[[Uri], str] | None = None,
    ) -> None:
        self.base = base
        self.kind = base.kind
        self._rename_blocker = rename_blocker
        self._blocked: dict[str, str] = {}
        self._rows = [
            Row(i, record, record.group, record.name)
            for i, record in enumerate(base.records)
        ]
        self._next_id = len(self._rows)
        self._by_id = {row.id: row for row in self._rows}
        # Pending asset lists of stored Roots, and Roots not created yet.
        self._root_edits: dict[str, tuple[RootAsset, ...]] = {}
        self._new_roots: dict[str, tuple[RootAsset, ...]] = {}
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._batch_depth = 0
        self._batch_saved = False
        self._problems: dict[int, str] | None = None

    # ── Reading ──────────────────────────────────────────

    @property
    def rows(self) -> tuple[Row, ...]:
        return tuple(self._rows)

    def row(self, row_id: int) -> Row:
        return self._by_id[row_id]

    def groups(self) -> list[str]:
        """Every group, stored ones first, then those new rows name."""
        found = list(self.base.groups)
        for row in self._rows:
            if row.group and row.group not in found:
                found.append(row.group)
        return found

    def names_in(self, group: str) -> set[str]:
        """Names ``group`` holds or will hold, stored and pending."""
        names = set()
        for row in self._rows:
            if row.origin is not None and row.origin.group == group:
                names.add(row.origin.name)
            if row.group == group:
                names.add(row.name)
        return names

    def inherited(self, row: Row) -> Inherited:
        return self.base.inherited(row.group)

    def value(self, row: Row, name: str) -> object:
        """What the entity holds for ``name`` once applied (None: nothing)."""
        if name == LENGTH:
            start = self.value(row, FRAME_START)
            end = self.value(row, FRAME_END)
            return end - start + 1 if _is_int(start) and _is_int(end) else None
        if name == ASSETS:
            return self.shot_assets(row)
        edit = row.edits.get(name, _MISSING)
        if edit is not _MISSING and edit is not RESET:
            return edit
        if edit is _MISSING and row.origin is not None and name in row.origin.own:
            return row.origin.own[name]
        return self.inherited(row).values.get(name)

    def state(self, row: Row, name: str) -> str:
        """:data:`OWN`, :data:`INHERITED`, :data:`SET` or :data:`CLEARED`.
        A shot's Assets follow its Root: SET while that Root's list is
        pending, else the Root cell's state."""
        if name == LENGTH:
            states = {self.state(row, FRAME_START), self.state(row, FRAME_END)}
            if states & {SET, CLEARED}:
                return SET
            return OWN if OWN in states else INHERITED
        if name == ASSETS:
            root = self.value(row, ROOT)
            if isinstance(root, str) and self.root_pending(root):
                return SET
            state = self.state(row, ROOT)
            return INHERITED if state == CLEARED else state
        edit = row.edits.get(name, _MISSING)
        if edit is RESET:
            return CLEARED
        if edit is not _MISSING:
            return SET
        if row.origin is not None and name in row.origin.own:
            return OWN
        return INHERITED

    def source(self, row: Row, name: str) -> str:
        """Where an inherited value of ``name`` comes from."""
        if name == ASSETS:
            name = ROOT
        return self.inherited(row).sources.get(name, '')

    @property
    def dirty(self) -> bool:
        return (
            any(row.touched for row in self._rows)
            or bool(self._root_edits) or bool(self._used_new_roots())
        )

    def summary(self) -> Summary:
        known = set(self.base.groups)
        rows = self._rows
        return Summary(
            new=sum(row.is_new for row in rows),
            changed=sum(
                1 for row in rows
                if not row.is_new and not row.retire and row.edits
            ),
            renamed=sum(1 for row in rows if row.renamed and not row.retire),
            retired=sum(row.retire for row in rows),
            problems=len(self.problems()),
            new_groups=tuple(dict.fromkeys(
                row.group for row in rows
                if row.is_new and row.group not in known
            )),
            roots_changed=len(self._root_edits),
            roots_new=len(self._used_new_roots()),
        )

    # ── Roots (shots) ────────────────────────────────────

    def roots(self) -> list[str]:
        """Every Root URI string, stored and new, sorted."""
        return sorted({*self.base.roots, *self._new_roots})

    def root_exists(self, uri: str) -> bool:
        return uri in self.base.roots or uri in self._new_roots

    def root_is_new(self, uri: str) -> bool:
        return uri in self._new_roots

    def root_pending(self, uri: str) -> bool:
        return uri in self._root_edits or uri in self._new_roots

    def root_own(self, uri: str) -> tuple[RootAsset, ...]:
        """The Root's own asset list, as it will be once applied."""
        if uri in self._new_roots:
            return self._new_roots[uri]
        if uri in self._root_edits:
            return self._root_edits[uri]
        return self.base.roots.get(uri, ())

    def root_inherited(self, uri: str) -> list[tuple[RootAsset, str]]:
        """The assets ``uri`` gets from its parent Roots, closest first,
        with the Root each comes from."""
        return [
            (entry, parent)
            for parent in _root_parents(uri)
            for entry in self.root_own(parent)
        ]

    def shot_assets(self, row: Row) -> tuple[RootAsset, ...]:
        """What the shot imports: its Root's own assets, then its parents'
        (closest first), one entry per asset. The pipeline keeps one per
        asset too, the Root's own entry winning over a parent's
        (``pipe.build._iter_scene_assets`` with ``setdefault``,
        ``import_shot``'s override), so a parent's tree ×1 under the Root's
        tree ×3 imports three trees, not four."""
        root = self.value(row, ROOT)
        if not isinstance(root, str) or not root:
            return ()
        found: dict[str, RootAsset] = {}
        for entry in (*self.root_own(root), *(e for e, _ in self.root_inherited(root))):
            found.setdefault(entry.asset, entry)
        return tuple(found.values())

    def root_users(self, uri: str) -> list[Row]:
        """The rows that will use ``uri``: their Root is it or a child of it."""
        prefix = uri + '/'
        return [
            row for row in self._rows
            if not row.retire
            and isinstance(root := self.value(row, ROOT), str)
            and (root == uri or root.startswith(prefix))
        ]

    def _used_new_roots(self) -> list[str]:
        """New Roots some row will use (directly, or as a parent of its
        Root): an unused new Root is not created."""
        used = set()
        for row in self._rows:
            root = self.value(row, ROOT) if not row.retire else None
            if isinstance(root, str):
                used.add(root)
                used.update(_root_parents(root))
        return [uri for uri in self._new_roots if uri in used]

    def root_problem(self, path: str) -> str:
        """Why ``path`` (``outdoor/forest``) cannot name a new Root, or ''."""
        segments = [s.strip() for s in path.split('/')]
        if not path.strip() or not all(segments):
            return 'Name the Root'
        for segment in segments:
            # Roots are config only (no folders), so they take what a URI
            # segment does short of '.': config.scene names them shots_010_010.
            if not _ROOT_NAME.fullmatch(segment):
                return f"Root name '{segment}' may only use letters, digits, _ and -"
        uri = root_uri('/'.join(segments))
        if self.root_exists(uri):
            return f"A Root named '{root_path(uri)}' already exists"
        other = same_but_case(uri, self.roots())
        if other is not None:
            return f"Root '{root_path(uri)}' differs only by case from '{root_path(other)}'"
        return ''

    def set_root_assets(self, uri: str, assets: Iterable[RootAsset]) -> bool:
        """Change a Root's own asset list; False when that changes nothing."""
        assets = tuple(assets)
        if not self.root_exists(uri):
            raise ValueError(f"No Root named '{root_path(uri)}'")
        if assets == self.root_own(uri):
            return False
        self._changing()
        if uri in self._new_roots:
            self._new_roots[uri] = assets
        elif assets == self.base.roots.get(uri, ()):
            self._root_edits.pop(uri, None)
        else:
            self._root_edits[uri] = assets
        return True

    def add_root(self, path: str, assets: Iterable[RootAsset] = ()) -> str:
        """A new Root, created on Apply (with any missing parent Roots,
        empty) if a shot uses it; its URI string."""
        reason = self.root_problem(path)
        if reason:
            raise ValueError(reason)
        uri = root_uri('/'.join(s.strip() for s in path.split('/')))
        self._changing()
        self._new_roots[uri] = tuple(assets)
        return uri

    def revert_root(self, uri: str) -> bool:
        """Drop a stored Root's pending asset list."""
        if uri not in self._root_edits:
            return False
        self._changing()
        del self._root_edits[uri]
        return True

    # ── Undo ─────────────────────────────────────────────

    def _snapshot(self) -> tuple:
        return (
            [replace(row, edits=dict(row.edits)) for row in self._rows],
            self._next_id, dict(self._root_edits), dict(self._new_roots),
        )

    def _restore(self, state: tuple) -> None:
        self._rows, self._next_id, self._root_edits, self._new_roots = state
        self._by_id = {row.id: row for row in self._rows}
        self._problems = None

    def _changing(self) -> None:
        """Called right before every change: one undo step per edit, or
        per :meth:`batch`."""
        self._problems = None
        if self._batch_depth and self._batch_saved:
            return
        self._undo.append(self._snapshot())
        self._redo.clear()
        if self._batch_depth:
            self._batch_saved = True

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Make the edits inside one undo step."""
        if self._batch_depth == 0:
            self._batch_saved = False
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    # ── Editing ──────────────────────────────────────────

    def set_value(self, row_id: int, name: str, value) -> bool:
        """Set one cell; False when that changes nothing. A retiring row
        and a stored entity's group are not editable. Raises ValueError for
        a value the field cannot hold."""
        row = self._by_id[row_id]
        if row.retire:
            return False
        if name in (GROUP, NAME):
            if name == GROUP and not row.is_new:
                return False
            text = str(value).strip()
            if text == getattr(row, name):
                return False
            self._changing()
            setattr(row, name, text)
            return True
        if name == LENGTH:
            length = parse_value(LENGTH, value)
            if length < 1:
                raise ValueError('Length must be at least 1 frame')
            start = self.value(row, FRAME_START)
            if not _is_int(start):
                raise ValueError(f'Give the {self.kind.item} a start frame first')
            return self.set_value(row_id, FRAME_END, start + length - 1)
        if name not in self.kind.fields:
            raise ValueError(f'{self.kind.item.capitalize()}s have no {self.kind.label(name)}')
        value = parse_value(name, value)
        if name == ROOT and not self.root_exists(value):
            raise ValueError(f"No Root named '{root_path(value)}'")
        if value == self.value(row, name):
            return False
        own = row.origin.own.get(name, _MISSING) if row.origin else _MISSING
        self._changing()
        if own is not _MISSING:
            # A pinned value stays pinned, even to what the parent says:
            # a shot's frame range is its own. Delete (reset) un-pins.
            if value == own:
                row.edits.pop(name, None)        # back to what is stored
            else:
                row.edits[name] = value
        elif value == self.inherited(row).values.get(name, _MISSING):
            row.edits.pop(name, None)            # still inheriting: no copy pinned
        else:
            row.edits[name] = value
        return True

    def reset(self, row_id: int, name: str) -> bool:
        """Put a cell back to inherited (Length: the end frame)."""
        row = self._by_id[row_id]
        if row.retire:
            return False
        if name == LENGTH:
            name = FRAME_END
        if name not in self.kind.fields:
            return False
        if row.origin is not None and name in row.origin.own:
            if row.edits.get(name, _MISSING) is RESET:
                return False
            self._changing()
            row.edits[name] = RESET
            return True
        if name not in row.edits:
            return False
        self._changing()
        del row.edits[name]
        return True

    def add(
        self, group: str, name: str, values: Mapping[str, object] | None = None,
    ) -> int:
        """A new entity after its group's last row; its row id. ``values``
        are pinned on it as given (Add Shots' frame range), None skipped."""
        edits = {
            key: parse_value(key, value)
            for key, value in (values or {}).items()
            if value is not None
        }
        unknown = set(edits) - set(self.kind.fields)
        if unknown:
            raise ValueError(f'Not {self.kind.item} fields: {sorted(unknown)}')
        root = edits.get(ROOT)
        if root is not None and not self.root_exists(root):
            raise ValueError(f"No Root named '{root_path(root)}'")
        self._changing()
        row = Row(self._next_id, None, group.strip(), name.strip(), edits)
        self._next_id += 1
        self._insert(row)
        return row.id

    def _insert(self, row: Row) -> None:
        after = max(
            (i for i, other in enumerate(self._rows) if other.group == row.group),
            default=len(self._rows) - 1,
        )
        self._rows.insert(after + 1, row)
        self._by_id[row.id] = row

    def _remove(self, row: Row) -> None:
        self._rows.remove(row)
        del self._by_id[row.id]

    def set_retired(self, row_ids: Iterable[int], retired: bool = True) -> bool:
        """Mark stored entities to retire (or not); a new row is just removed."""
        changed = False
        with self.batch():
            for row_id in list(row_ids):
                row = self._by_id.get(row_id)
                if row is None:
                    continue
                if row.is_new:
                    if retired:
                        self._changing()
                        self._remove(row)
                        changed = True
                elif row.retire != retired:
                    self._changing()
                    row.retire = retired
                    changed = True
        return changed

    def revert(self, row_ids: Iterable[int]) -> bool:
        """Drop every pending edit of these rows; new rows go. Root asset
        lists are the Roots', not the rows': :meth:`revert_root`."""
        changed = False
        with self.batch():
            for row_id in list(row_ids):
                row = self._by_id.get(row_id)
                if row is None or not row.touched:
                    continue
                self._changing()
                changed = True
                if row.is_new:
                    self._remove(row)
                else:
                    row.edits.clear()
                    row.name = row.origin.name
                    row.retire = False
        return changed

    def revert_all(self) -> bool:
        with self.batch():
            changed = self.revert([row.id for row in self._rows])
            if self._root_edits or self._new_roots:
                self._changing()
                self._root_edits.clear()
                self._new_roots.clear()
                changed = True
        return changed

    def shift(self, row_ids: Iterable[int], offset: int) -> bool:
        """Move these rows' frame ranges by ``offset``, keeping lengths."""
        changed = False
        with self.batch():
            for row_id in list(row_ids):
                row = self._by_id.get(row_id)
                if row is None or row.retire:
                    continue
                start = self.value(row, FRAME_START)
                end = self.value(row, FRAME_END)
                if not (_is_int(start) and _is_int(end)):
                    continue
                changed |= self.set_value(row_id, FRAME_START, start + offset)
                changed |= self.set_value(row_id, FRAME_END, end + offset)
        return changed

    # ── Problems and changes ─────────────────────────────

    def problems(self) -> dict[int, str]:
        """Why each touched row cannot be applied, by row id. Rows nobody
        touched are not checked, so old data never blocks an Apply."""
        if self._problems is None:
            self._problems = self._find_problems()
        return self._problems

    def _find_problems(self) -> dict[int, str]:
        known = set(self.base.groups)
        new_spelling: dict[str, str] = {}
        # A stored entity's name stays taken until Apply moves it.
        claimed: dict[tuple[str, str], Row] = {
            (row.origin.group, row.origin.name.casefold()): row
            for row in self._rows if row.origin is not None
        }
        found = {}
        for row in self._rows:
            if row.retire or not row.touched:
                continue
            reason = self._row_problem(row, known, new_spelling, claimed)
            if reason:
                found[row.id] = reason
        return found

    def _row_problem(self, row, known, new_spelling, claimed) -> str:
        kind = self.kind
        if row.is_new:
            reason = name_problem(kind.group, row.group) \
                or name_problem(kind.item, row.name)
            if reason:
                return reason
            if row.group not in known:
                other = next(
                    (g for g in self.base.other_groups
                     if g.casefold() == row.group.casefold()),
                    None,
                )
                if other is not None:
                    return f"'{other}' is {kind.other_group}; a {kind.group} cannot share its name"
                other = same_but_case(row.group, known)
                if other is not None:
                    return f"{kind.group.capitalize()} '{row.group}' differs only by case from '{other}'"
                first = new_spelling.setdefault(row.group.casefold(), row.group)
                if first != row.group:
                    return f"{kind.group.capitalize()} '{row.group}' differs only by case from '{first}' above"
        elif row.renamed:
            reason = name_problem(kind.item, row.name)
            if reason:
                return reason
            if row.name.casefold() == row.origin.name.casefold():
                return (
                    f"'{row.name}' differs from '{row.origin.name}' only by "
                    'case; names may not differ only by case'
                )
        if row.is_new or row.renamed:
            key = (row.group, row.name.casefold())
            holder = claimed.get(key)
            if holder is not None and holder is not row:
                return self._taken(row, holder)
            claimed[key] = row
            if row.renamed:
                reason = self._blocked_reason(row.origin)
                if reason:
                    return reason
        return self._value_problem(row)

    def _taken(self, row: Row, holder: Row) -> str:
        folded = row.name.casefold()
        origin = holder.origin
        if origin is not None and origin.group == row.group \
                and origin.name.casefold() == folded:
            name = f'{row.group}/{origin.name}'
            if holder.retire:
                return f'{name} is being retired; Apply that first, then reuse its name'
            if holder.renamed:
                return (
                    f'{name} keeps its name until its rename to {holder.name} '
                    'is applied; Apply that first'
                )
            if origin.name != row.name:
                return f"{self.kind.item.capitalize()} '{row.name}' differs only by case from '{origin.name}'"
            return f'{name} already exists'
        if holder.renamed:
            return f'{holder.origin.name} is being renamed to {holder.name} too'
        return f'{row.group}/{holder.name} is also a new row above'

    def _blocked_reason(self, origin: Record) -> str:
        if self._rename_blocker is None:
            return ''
        key = str(origin.uri)
        if key not in self._blocked:
            self._blocked[key] = self._rename_blocker(origin.uri) or ''
        return self._blocked[key]

    def _value_problem(self, row: Row) -> str:
        start, end = self.value(row, FRAME_START), self.value(row, FRAME_END)
        if _is_int(start) and _is_int(end) and end < start:
            return f'Ends ({end}) before it starts ({start})'
        for name in (ROLL_START, ROLL_END):
            value = self.value(row, name)
            if _is_int(value) and value < 0:
                return f'{_LABELS[name]} cannot be negative'
        fps = self.value(row, FPS)
        if _is_number(fps) and fps <= 0:
            return 'FPS must be above 0'
        return ''

    def changes(self) -> Changes:
        """The writes an Apply makes. Rows with a problem are left out; the
        editor does not Apply while there are any."""
        problems = self.problems()
        known = set(self.base.groups)
        kind = self.kind
        retire, rename, groups, create, update = [], [], [], [], []
        for row in self._rows:
            if row.id in problems or not row.touched:
                continue
            if row.retire:
                retire.append(row.origin.uri)
                continue
            if row.is_new:
                group = kind.root / row.group
                if row.group not in known and group not in groups:
                    groups.append(group)
                create.append((
                    group / row.name, {**kind.created(row.name), **row.edits},
                ))
                continue
            uri = row.origin.uri
            if row.renamed:
                rename.append((uri, row.name))
                uri = kind.root / row.group / row.name
            if row.edits:
                update.append((uri, dict(row.edits)))
        roots = []
        new = set(self._used_new_roots())
        for uri in sorted(new | {
            parent for uri in new for parent in _root_parents(uri)
            if not self.root_exists(parent)
        }, key=lambda u: (u.count('/'), u)):
            roots.append((uri, self.root_own(uri), True))
        for uri, assets in self._root_edits.items():
            roots.append((uri, assets, False))
        return Changes(
            tuple(retire), tuple(rename), tuple(groups), tuple(roots),
            tuple(create), tuple(update),
        )

    # ── Rebase ───────────────────────────────────────────

    def rebase(self, base: Base) -> tuple['Sheet', list[str]]:
        """These edits on top of ``base`` (a fresh :func:`load`), and a note
        for each edit that no longer applies.

        An edit already in ``base`` falls away: after an Apply, what was
        written is no longer pending and what was not still is. A new row
        whose entity now exists becomes edits to it where its values
        differ; so does a new Root that now exists. Undo starts over.
        """
        sheet = Sheet(base, self._rename_blocker)
        notes = []
        for uri, assets in self._new_roots.items():
            if uri in base.roots:
                if assets != base.roots[uri]:
                    sheet._root_edits[uri] = assets
            else:
                sheet._new_roots[uri] = assets
        for uri, assets in self._root_edits.items():
            if uri not in base.roots:
                notes.append(
                    f'Root {root_path(uri)} is no longer in the project; '
                    'its asset changes were dropped'
                )
            elif assets != base.roots[uri]:
                sheet._root_edits[uri] = assets
        stored = {(row.origin.group, row.origin.name): row for row in sheet._rows}
        for old in self._rows:
            if old.origin is None:
                target = stored.get((old.group, old.name))
                if target is None:
                    sheet._insert(Row(
                        sheet._next_id, None, old.group, old.name, dict(old.edits),
                    ))
                    sheet._next_id += 1
                else:
                    _carry(target, old.edits)
                continue
            target = stored.get((old.origin.group, old.origin.name))
            renamed_done = False
            # A retired entity is never renamed: gone means retired, and
            # the entity under its new name is somebody else.
            if target is None and old.renamed and not old.retire:
                target = stored.get((old.group, old.name))
                renamed_done = target is not None
            if target is None:
                if not old.retire and (old.edits or old.renamed):
                    notes.append(
                        f'{old.origin.group}/{old.origin.name} is no longer '
                        'in the project; its changes were dropped'
                    )
                continue
            target.retire = old.retire
            if old.renamed and not renamed_done:
                target.name = old.name
            _carry(target, old.edits)
        return sheet, notes


def _carry(target: Row, edits: Mapping[str, object]) -> None:
    """Pending ``edits`` onto a freshly loaded stored row, where they still
    change something."""
    for name, value in edits.items():
        own = target.origin.own.get(name, _MISSING)
        if value is RESET:
            if own is not _MISSING:
                target.edits[name] = RESET
        elif own is _MISSING or own != value:
            target.edits[name] = value


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class ApplyError(RuntimeError):
    """Apply stopped at a write that failed; every write before it was made."""


def _label(uri: Uri) -> str:
    return '/'.join(uri.segments[1:])


def _scenes(uri: str) -> Uri:
    path = SCENES_URI
    for segment in root_path(uri).split('/'):
        path = path / segment
    return path


def _update(config, uri: Uri, edits: Mapping[str, object]) -> None:
    # The entity's OWN values, read now: another field changed since the
    # sheet was loaded is kept, and nothing inherited gets pinned.
    own = config.get_own_properties(uri)
    if own is None:
        raise ValueError(f'{_label(uri)} is no longer in the project')
    for name, value in edits.items():
        if value is RESET:
            own.pop(name, None)
        else:
            own[name] = value
    config.set_own_properties(uri, own)


def _write_root(config, uri: str, assets: tuple[RootAsset, ...], new: bool) -> None:
    stored = [entry.stored() for entry in assets]
    if new:
        config.add_entity(_scenes(uri), {'assets': stored})
        return
    own = config.get_own_properties(_scenes(uri))
    if own is None:
        raise ValueError('the Root is no longer in the project')
    own['assets'] = stored
    config.set_own_properties(_scenes(uri), own)


def apply(
    config, changes: Changes, *,
    retire: Callable[[Uri], object],
    rename: Callable[[Uri, str], object],
) -> None:
    """Write ``changes``; :class:`ApplyError` names the first that fails.

    ``retire`` and ``rename`` are ``pipe.entity_files.delete_entity`` and
    ``rename_entity`` (the config layer cannot import them): a retired
    entity's folders move to ``_deleted/``, and one with files on disk is
    not renamed. Retires and renames go first so the names they free are
    free; new groups go before the entities in them, and Roots before the
    shots that use them.
    """
    def step(what: str, func, *args) -> None:
        try:
            func(*args)
        except Exception as exc:
            raise ApplyError(f'{what}: {exc}') from exc

    for uri in changes.retire:
        step(f'Retiring {_label(uri)}', retire, uri)
    for uri, name in changes.rename:
        step(f'Renaming {_label(uri)} to {name}', rename, uri, name)
    for uri in changes.groups:
        step(f'Creating {_label(uri)}', config.add_entity, uri, {})
    for uri, assets, new in changes.roots:
        what = 'Creating Root' if new else 'Updating the assets of Root'
        step(f'{what} {root_path(uri)}', _write_root, config, uri, assets, new)
    for uri, own in changes.create:
        step(f'Creating {_label(uri)}', config.add_entity, uri, dict(own))
    for uri, edits in changes.update:
        step(f'Updating {_label(uri)}', _update, config, uri, edits)
