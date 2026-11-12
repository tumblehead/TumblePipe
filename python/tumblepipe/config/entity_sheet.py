"""The Entity Editor's model: a project's assets or shots as an editable sheet.

The Entity Editor (``asset_browser/entity_editor``) has an Assets tab and a
Shots tab. Each shows every entity of its kind as a row: its group (category
or sequence), its name, and its timing (frame range, pre/post roll, fps),
plus ``animatable`` for assets and the Root (and so the assets it imports)
for shots. Shots also carry the render settings their root layer applies
(``render.apply_config``, ``render.resolution``, ``render.camera``; #70),
dotted fields that write one key of ``render`` each, and the only values
the kind's root (all shots) can be given here. This module is all of it
but the widgets, so it runs and is tested without Qt:

- :func:`load` reads one :class:`Kind` as stored: each entity's *own*
  values, what each field inherits from its group, the kind's root, the
  project and the schema defaults, and for shots the Roots' asset lists.
- :class:`Sheet` holds the edits not yet applied on top of that (values set
  or put back to inherited, on entities and on their groups; renames, new
  entities, entities to retire, Root asset lists and new Roots) with undo. :meth:`Sheet.problems` names what is
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

A sequence or category holds values too, which its entities inherit unless
they pin their own (a sequence's Root is every shot's in it that has none
of its own). They are edited with :meth:`Sheet.set_group_value`, and an
entity shows what its group will hold once applied.

Retiring an entity is the browser's Delete: it leaves the database and its
folders move to ``_deleted/`` (``pipe.entity_files.delete_entity``).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Iterator, Mapping

from tumblepipe.config.channels import (
    CHANNEL_PRIMS_PROPERTY, CHANNEL_PROPERTY, channel_name_problem,
    prim_pattern_problem, read_channel_names, read_channel_prims,
)
from tumblepipe.config.department_off import DISABLED_DEPARTMENTS
from tumblepipe.config import render_config as rc
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
#: Not stored on the entity: the Multis that list it as a member.
MULTIS = 'multis'
#: Shots only, and not one stored key but two: the shot's channels in
#: order and each one's prim pattern (``channels`` + ``channel_prims``), its
#: render layers (:class:`Layers`).
LAYERS = 'layers'
#: Shots only: the departments turned off on the shot (#64), a list of
#: names in pool order (``config.department_off``). Empty or unset: every
#: department of the pool applies.
DEPARTMENTS_OFF = DISABLED_DEPARTMENTS
#: Shots only: the render settings a shot's root layer applies (#70), each a
#: key of the ``render`` object addressed as a dotted field
#: (``config.render_config``). Inherited key by key, as the store merges
#: ``render``; edited in the Shots tab's Render settings section, not as
#: columns. Unlike the other values they can be set on all shots too
#: (:meth:`Sheet.set_everyone_value`), the project's level for shots.
RENDER_APPLY = rc.APPLY
RENDER_RESOLUTION = rc.RESOLUTION
RENDER_CAMERA = rc.CAMERA
RENDER_FIELDS = rc.FIELDS
#: The values a kind's root (all shots) can be given in the sheet.
EVERYONE_FIELDS = frozenset(RENDER_FIELDS)

SCENES_URI = Uri.parse('scenes:/')
GROUPS_URI = Uri.parse('groups:/')
DEPARTMENTS_URI = Uri.parse('departments:/')
#: How ``config.scene`` stores a Root's asset list entries.
CHANNEL_KEY = 'channel'
DEFAULT_CHANNEL = 'default'

_LABELS = {
    FRAME_START: 'Start', FRAME_END: 'End', LENGTH: 'Length',
    ROLL_START: 'Pre-roll', ROLL_END: 'Post-roll', FPS: 'FPS', ROOT: 'Root',
    ANIMATABLE: 'Animatable', ASSETS: 'Assets', MULTIS: 'Multis',
    LAYERS: 'Layers', DEPARTMENTS_OFF: 'Departments off',
    **rc.LABELS,
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
    #: Whether its entities have render layers (:data:`LAYERS`): shots.
    layered: bool = False

    @property
    def values(self) -> tuple[str, ...]:
        """Every value the sheet edits: the stored ``fields``, then
        :data:`LAYERS` for a layered kind (two stored keys, not one)."""
        return (*self.fields, LAYERS) if self.layered else self.fields

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
    'shots', SHOTS_URI, 'sequence', 'shot',
    (*_TIMING, ROOT, DEPARTMENTS_OFF, *RENDER_FIELDS),
    'all shots', ASSETS_URI, 'an asset category', layered=True,
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


@dataclass(frozen=True)
class Layers:
    """A shot's render layers: its channels bottom to top, ``default``
    first, each with its prim pattern ('' for none).

    What ``config.channels.channel_layers`` makes of it: ``default`` without
    a pattern is every prim the other layers leave, another channel without
    one makes no pass (a channel some department exports renders its own
    stage). Stored as two keys, ``channels`` (the order) and
    ``channel_prims`` (the patterns): see :func:`_pin_layers`.

    As text (what a cell shows, copies and takes when typed or pasted):
    ``characters: /CHAR/*; fx: /FX/*``. ``default`` is written only when it
    has a pattern; a channel with none is its bare name.
    """
    entries: tuple[tuple[str, str], ...] = ((DEFAULT_CHANNEL, ''),)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.entries)

    @property
    def layered(self) -> bool:
        """Whether any channel names prims: a farm render makes passes."""
        return any(pattern for _, pattern in self.entries)

    def pattern(self, name: str) -> str:
        return dict(self.entries).get(name, '')

    def text(self) -> str:
        parts = []
        for name, pattern in self.entries:
            if name == DEFAULT_CHANNEL and not pattern:
                continue
            parts.append(f'{name}: {pattern}' if pattern else name)
        return '; '.join(parts)

    @classmethod
    def make(
        cls, entries: Iterable[tuple[str, str]], keep: 'Layers | None' = None,
    ) -> 'Layers':
        """Layers of ``entries`` (``default`` put first, added when absent);
        ValueError names the first that cannot be one. What ``keep`` (the
        value being edited) already holds passes as it is: a channel named
        before today's rule, a pattern the farm will refuse at submit."""
        kept = set(keep.entries) if keep is not None else set()
        kept_names = set(keep.names) if keep is not None else set()
        # ``default`` is always there, listed or not: a ``Default`` beside
        # it would be a second channel differing only by case.
        seen: dict[str, str] = {DEFAULT_CHANNEL: DEFAULT_CHANNEL}
        listed: set[str] = set()
        out: list[tuple[str, str]] = []
        for name, pattern in entries:
            name, pattern = str(name).strip(), ' '.join(str(pattern or '').split())
            if name != DEFAULT_CHANNEL and name not in kept_names:
                problem = channel_name_problem(name)
                if problem:
                    raise ValueError(problem)
            if name in listed:
                raise ValueError(f"Layer '{name}' is listed twice")
            # A spelling an earlier layer (or default) took in another case.
            # Twins the edited value already holds stay: stored before this
            # rule, and refusing them would refuse the value unchanged.
            other = seen.setdefault(name.casefold(), name)
            if other != name and not (name in kept_names and other in kept_names):
                raise ValueError(f"Layers '{other}' and '{name}' differ only by case")
            listed.add(name)
            if (name, pattern) not in kept:
                problem = prim_pattern_problem(pattern)
                if problem:
                    raise ValueError(f'{name}: {problem}')
            out.append((name, pattern))
        default = [entry for entry in out if entry[0] == DEFAULT_CHANNEL]
        rest = [entry for entry in out if entry[0] != DEFAULT_CHANNEL]
        return cls(tuple((default or [(DEFAULT_CHANNEL, '')]) + rest))

    @classmethod
    def parse(cls, text: str, keep: 'Layers | None' = None) -> 'Layers':
        """:meth:`text` read back. ValueError says what is wrong."""
        entries = []
        for part in str(text).split(';'):
            part = part.strip()
            if not part:
                continue
            name, _, pattern = part.partition(':')
            if not name.strip():
                raise ValueError(f'"{part}" names no layer (write name: /prims)')
            entries.append((name, pattern))
        return cls.make(entries, keep)

    @classmethod
    def of_properties(cls, properties: Mapping | None) -> 'Layers':
        """The layers resolved ``properties`` give, read as
        ``config.channels`` reads them for the farm; never refused."""
        properties = properties or {}
        names = [DEFAULT_CHANNEL, *read_channel_names(properties)]
        prims = read_channel_prims(properties)
        return cls(tuple(
            (name, _pattern(prims.get(name))) for name in dict.fromkeys(names)
        ))


def _pattern(value) -> str:
    """A stored ``channel_prims`` value as a pattern: blank and non-strings
    are none, as ``config.channels.read_channel_prims`` reads them."""
    return ' '.join(value.split()) if isinstance(value, str) else ''


#: Where a level of the tree leaves the layers: ``(channels, prims)`` as
#: stored, merged root to leaf the way ``get_properties`` merges them (a
#: ``channels`` list replaces, ``channel_prims`` merges key by key).
LayerState = tuple[tuple[str, ...], Mapping[str, object]]
_NO_LAYERS: LayerState = ((DEFAULT_CHANNEL,), {})


def _has_layers(own: Mapping | None) -> bool:
    return bool(own) and (CHANNEL_PROPERTY in own or CHANNEL_PRIMS_PROPERTY in own)


def _layer_keys(own: Mapping) -> dict[str, object]:
    return {key: own[key] for key in (CHANNEL_PROPERTY, CHANNEL_PRIMS_PROPERTY) if key in own}


def _layers_state_of(inherited: LayerState, layers: Layers) -> LayerState:
    """The state ``layers`` leave once written over ``inherited`` (as
    :func:`_pin_layers` writes them)."""
    return layers.names, {**inherited[1], **dict(layers.entries)}


def _layer_state(state: LayerState, own: Mapping | None) -> LayerState:
    channels, prims = state
    if own:
        stored = own.get(CHANNEL_PROPERTY)
        if isinstance(stored, list):
            channels = tuple(str(name) for name in stored)
        stored = own.get(CHANNEL_PRIMS_PROPERTY)
        if isinstance(stored, dict):
            prims = {**prims, **stored}
    return channels, prims


def _layers_of(state: LayerState) -> Layers:
    """The layers a level holds, as ``config.channels.list_channels`` and
    ``read_channel_prims`` read them: ``default`` first."""
    channels, prims = state
    names = [DEFAULT_CHANNEL] + [
        name for name in dict.fromkeys(channels) if name != DEFAULT_CHANNEL
    ]
    return Layers(tuple((name, _pattern(prims.get(name))) for name in names))


_NONE_WORDS = frozenset({'none', '-', 'all on'})
_SEPARATORS = re.compile(r'[\s,;]+')


def _departments_off(value) -> list[str]:
    """A Departments off value as the sheet holds it and the database
    stores it: a list of names, duplicates dropped. Takes a list, a tuple,
    or typed text (names split on commas or spaces; ``none`` is none)."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.casefold() in _NONE_WORDS:
            return []
        names = [part for part in _SEPARATORS.split(text) if part]
    elif isinstance(value, (list, tuple)):
        names = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise ValueError(f'Departments off takes department names, not "{value}"')
    return list(dict.fromkeys(names))


def parse_value(name: str, value) -> object:
    """``value`` (typed text or a number) as field ``name`` stores it.

    Frames, rolls and lengths are whole numbers; fps is a number, kept whole
    when it is (``25``, ``23.976``); Animatable is yes or no; a Root is a
    ``scenes:/`` URI string. Raises ValueError saying what the field takes.
    """
    label = _LABELS.get(name, name)
    if name == LAYERS:
        return value if isinstance(value, Layers) else Layers.parse(value)
    if name == DEPARTMENTS_OFF:
        return _departments_off(value)
    if name in RENDER_FIELDS:
        return rc.parse(name, value)
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
    if name == LAYERS:
        return value.text() if isinstance(value, Layers) else ''
    if name == DEPARTMENTS_OFF:
        # None off shows empty; typing "none" pins it (a shot whose
        # sequence turns departments off).
        if not isinstance(value, (list, tuple)):
            return str(value)
        return ', '.join(_departments_off(value))
    if name in RENDER_FIELDS:
        return rc.format_value(name, value)
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
    #: Its own ``channels`` / ``channel_prims`` as stored (None: neither),
    #: merged onto what it inherits each time its Layers are read, so a
    #: sequence's pending Layers reach a shot that pins only some of them.
    layer_own: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MultiRecord:
    """A Multi as stored (``groups:/<kind>/<name>``): its members' URI
    strings and the departments it covers, in stored order, and its
    Timeline (``config.multi_timeline``: whether the members sit end to end
    on one timeline, with a playbar bookmark each)."""
    name: str
    uri: Uri
    members: tuple[str, ...]
    departments: tuple[str, ...]
    shared_timeline: bool = False
    timeline_bookmarks: bool = False


@dataclass(frozen=True)
class Inherited:
    """What an entity in one group inherits: each field's value, and where
    it comes from ("sequence sq010", "the project's defaults", ...)."""
    values: Mapping[str, object]
    sources: Mapping[str, str]
    #: A layered kind's ``channels`` and ``channel_prims`` as merged down to
    #: here, which an entity's own keys merge onto (``values[LAYERS]`` is
    #: what they make).
    layer_state: LayerState = _NO_LAYERS


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
    #: The kind's Multis, in database order.
    multis: tuple[MultiRecord, ...] = ()
    #: The departments a Multi of this kind can cover, in pipeline order.
    pool: tuple[str, ...] = ()
    #: Each stored group's own values of the kind's fields.
    group_own: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    #: The kind root's (all shots') own values of :data:`EVERYONE_FIELDS`,
    #: and what it inherits from the project and the project's defaults.
    everyone_own: Mapping[str, object] = field(default_factory=dict)
    for_everyone: Inherited = field(default_factory=lambda: Inherited({}, {}))

    def inherited(self, group: str) -> Inherited:
        return self.by_group.get(group, self.for_new_group)


def _inherited(config, kind: Kind, group: str | None, everyone: bool = True) -> Inherited:
    """What an entity in ``group`` (None: a group not created yet)
    inherits: ``get_properties`` without the entity's own layer, which is
    ``get_inherited_properties`` for an entity that need not exist yet.
    ``everyone`` False leaves the kind's root out too: what the root
    itself (all shots) inherits from the project and its defaults."""
    # A schema is found by position alone, so any name will do.
    schema = config.get_entity_schema(kind.root / (group or 'new') / 'new')
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    if schema is not None:
        defaults = {name: field.default for name, field in schema.fields.items()}
        for name in kind.fields:
            if rc.has_field(defaults, name):
                values[name] = rc.read_field(defaults, name)
                sources[name] = "the project's defaults"
    layer_state = _NO_LAYERS
    if kind.layered:
        if schema is not None and CHANNEL_PROPERTY in schema.fields:
            layer_state = _layer_state(
                layer_state, {CHANNEL_PROPERTY: schema.fields[CHANNEL_PROPERTY].default},
            )
        sources[LAYERS] = "the project's defaults"
    chain = [(_PROJECT, 'the project')]
    if everyone:
        chain.append((kind.root, kind.everyone))
    if group is not None:
        chain.append((kind.root / group, f'{kind.group} {group}'))
    for uri, label in chain:
        own = config.get_own_properties(uri) or {}
        for name in kind.fields:
            if rc.has_field(own, name):
                values[name] = rc.read_field(own, name)
                sources[name] = label
            elif rc.wipes(own, name):
                # A ``render`` that is not an object replaces the merge.
                values.pop(name, None)
                sources.pop(name, None)
        if kind.layered and _has_layers(own):
            layer_state = _layer_state(layer_state, own)
            sources[LAYERS] = label
    if kind.layered:
        values[LAYERS] = _layers_of(layer_state)
    return Inherited(values, sources, layer_state)


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


def _load_multis(config, kind: Kind) -> tuple[MultiRecord, ...]:
    found = []
    for uri in config.list_entity_uris(GROUPS_URI / kind.key):
        if len(uri.segments) != 2:
            continue
        # Resolved, as config.groups reads them; the schema gives neither
        # key a default, so a Multi may lack one (config.groups).
        props = config.get_properties(uri) or {}
        shared = bool(props.get('shared_timeline', False))
        found.append(MultiRecord(
            uri.segments[1], uri,
            tuple(props.get('members', [])), tuple(props.get('departments', [])),
            shared, shared and bool(props.get('timeline_bookmarks', False)),
        ))
    return tuple(found)


def _load_pool(config, kind: Kind) -> tuple[str, ...]:
    """``config.department.list_departments(kind, include_generated=False)``
    on ``config``: what New Multi covers, in pipeline order."""
    data = (config.root('departments') or {}).get('children', {})
    pool = []
    for name in data.get(kind.key, {}).get('children', {}):
        props = config.get_properties(DEPARTMENTS_URI / kind.key / name) or {}
        if props.get('generated', False) or not props.get('enabled', True):
            continue
        pool.append(name)
    return tuple(pool)


def load(config, kind: Kind = SHOTS_KIND) -> Base:
    """The project's ``kind`` entities, in database order, with what they
    inherit."""
    with config.coherent():
        records: list[Record] = []
        groups: list[str] = []
        by_group: dict[str, Inherited] = {}
        for uri in config.list_entity_uris(kind.root, closure=True):
            segments = uri.segments[1:]
            if not segments:
                continue
            if segments[0] not in groups:
                groups.append(segments[0])
                by_group[segments[0]] = _inherited(config, kind, segments[0])
            if len(segments) == 2:
                own = config.get_own_properties(uri) or {}
                records.append(Record(
                    segments[0], segments[1], kind.root / segments[0] / segments[1],
                    _own_fields(config, kind, uri, by_group[segments[0]]),
                    _layer_keys(own) if kind.layered and _has_layers(own) else None,
                ))
        for_new_group = _inherited(config, kind, None)
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
            by_group=by_group,
            for_new_group=for_new_group,
            roots=_load_roots(config) if kind is SHOTS_KIND else {},
            assets=assets,
            multis=_load_multis(config, kind),
            pool=_load_pool(config, kind),
            group_own={
                g: _own_fields(config, kind, kind.root / g, for_new_group) for g in groups
            },
            everyone_own={
                name: value
                for name, value in _own_fields(config, kind, kind.root, for_new_group).items()
                if name in EVERYONE_FIELDS
            },
            for_everyone=_inherited(config, kind, None, everyone=False),
        )


def _own_fields(config, kind: Kind, uri: Uri, inherited: Inherited) -> dict[str, object]:
    """``uri``'s own values of the kind's fields. Layers count as its own
    when it stores either key, and are what its keys make of ``inherited``'s."""
    own = config.get_own_properties(uri) or {}
    values = {
        name: rc.read_field(own, name, None) for name in kind.fields
        # A ``render`` that is not an object holds none of its keys, and
        # hides what the levels above hold (it reads as None).
        if rc.has_field(own, name) or rc.wipes(own, name)
    }
    if kind.layered and _has_layers(own):
        values[LAYERS] = _layers_of(_layer_state(inherited.layer_state, own))
    return values


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


@dataclass
class Multi:
    """One Multi in the sheet: a stored one (``origin``) or a new one.

    ``members`` are the sheet's row ids, so a member renamed or retired in
    the sheet stays itself: its URI is only worked out on Apply. A stored
    member the sheet has no row for (an entity no longer in the project) is
    kept as its URI string. ``base_members`` is what ``members`` was when
    loaded, to tell a changed list. ``shared_timeline`` and
    ``timeline_bookmarks`` are its Timeline (a shots Multi's; bookmarks only
    with a shared timeline). Read-only outside :class:`Sheet`.
    """
    id: int
    origin: MultiRecord | None
    name: str
    members: list
    departments: tuple[str, ...]
    base_members: tuple = ()
    retire: bool = False
    shared_timeline: bool = False
    timeline_bookmarks: bool = False

    @property
    def is_new(self) -> bool:
        return self.origin is None

    @property
    def renamed(self) -> bool:
        return self.origin is not None and self.name != self.origin.name

    @property
    def members_changed(self) -> bool:
        return set(self.members) != set(self.base_members)

    @property
    def departments_changed(self) -> bool:
        return self.origin is not None and set(self.departments) != set(self.origin.departments)

    @property
    def timeline(self) -> tuple[bool, bool]:
        """``(shared timeline, bookmarks)``."""
        return self.shared_timeline, self.timeline_bookmarks

    @property
    def timeline_changed(self) -> bool:
        return self.origin is not None and self.timeline != (
            self.origin.shared_timeline, self.origin.timeline_bookmarks,
        )

    @property
    def touched(self) -> bool:
        return (
            self.is_new or self.renamed or self.retire
            or self.members_changed or self.departments_changed
            or self.timeline_changed
        )


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
    #: Multis, written after the entities (a new shot can be a member).
    multis_retire: tuple[Uri, ...] = ()
    multis_rename: tuple[tuple[Uri, str], ...] = ()
    multis_create: tuple[tuple[Uri, Mapping[str, object]], ...] = ()
    multis_update: tuple[tuple[Uri, Mapping[str, object]], ...] = ()  # URI after any rename
    #: Groups' own values, written after the groups and Roots they need.
    group_update: tuple[tuple[Uri, Mapping[str, object]], ...] = ()
    #: The kind root's (all shots') own values, written before the groups'.
    everyone_update: tuple[tuple[Uri, Mapping[str, object]], ...] = ()

    def __bool__(self) -> bool:
        return any((
            self.retire, self.rename, self.groups, self.roots,
            self.create, self.update, self.multis_retire, self.multis_rename,
            self.multis_create, self.multis_update, self.group_update,
            self.everyone_update,
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
    multis_new: int = 0
    multis_changed: int = 0
    multis_retired: int = 0
    groups_changed: int = 0
    #: Whether the kind's root (all shots) has values pending.
    everyone_changed: bool = False


class Sheet:
    """A project's assets or shots with the edits not yet applied; see the
    module docstring. ``rename_blocker`` says why a stored entity cannot be
    renamed ('' when it can): ``pipe.entity_files`` refuses one with files
    on disk. ``multi_rename_blocker`` says the same of a stored Multi, whose
    workfiles live in a folder named after it. ``channel_exports`` names
    the channels a stored shot has department exports under
    (``pipe.render_layers.was_exported``): Layers that leave one out are a
    problem, since that channel's renders read those exports."""

    def __init__(
        self, base: Base, rename_blocker: Callable[[Uri], str] | None = None,
        multi_rename_blocker: Callable[[Uri], str] | None = None,
        channel_exports: Callable[[Uri], Iterable[str]] | None = None,
    ) -> None:
        self.base = base
        self.kind = base.kind
        self._rename_blocker = rename_blocker
        self._multi_rename_blocker = multi_rename_blocker
        self._channel_exports = channel_exports
        self._blocked: dict[str, str] = {}
        self._exported: dict[str, frozenset[str]] = {}
        self._rows = [
            Row(i, record, record.group, record.name)
            for i, record in enumerate(base.records)
        ]
        self._next_id = len(self._rows)
        self._by_id = {row.id: row for row in self._rows}
        # Pending asset lists of stored Roots, and Roots not created yet.
        self._root_edits: dict[str, tuple[RootAsset, ...]] = {}
        self._new_roots: dict[str, tuple[RootAsset, ...]] = {}
        # Groups to create on Apply even with no new row in them yet.
        self._new_groups: list[str] = []
        # Pending values (or RESET) of groups, stored and new, by group.
        self._group_edits: dict[str, dict[str, object]] = {}
        # Pending values (or RESET) of the kind's root, all shots: only
        # EVERYONE_FIELDS, the project level of the render settings.
        self._everyone_edits: dict[str, object] = {}
        by_uri = {str(row.origin.uri): row.id for row in self._rows}
        self._multis: list[Multi] = []
        for i, record in enumerate(base.multis):
            members = [by_uri.get(member, member) for member in record.members]
            self._multis.append(Multi(
                i, record, record.name, members, record.departments, tuple(members),
                shared_timeline=record.shared_timeline,
                timeline_bookmarks=record.timeline_bookmarks,
            ))
        self._next_multi_id = len(self._multis)
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._batch_depth = 0
        self._batch_saved = False
        self._problems: dict[int, str] | None = None
        self._multi_problems: dict[int, str] | None = None

    # ── Reading ──────────────────────────────────────────

    @property
    def rows(self) -> tuple[Row, ...]:
        return tuple(self._rows)

    def row(self, row_id: int) -> Row:
        return self._by_id[row_id]

    def groups(self) -> list[str]:
        """Every group, stored ones first, then new ones: those added with
        :meth:`add_group`, then those new rows name."""
        found = list(self.base.groups)
        found += [g for g in self._new_groups if g not in found]
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
        return self.group_inherited(row.group)

    def value(self, row: Row, name: str) -> object:
        """What the entity holds for ``name`` once applied (None: nothing)."""
        if name == LENGTH:
            start = self.value(row, FRAME_START)
            end = self.value(row, FRAME_END)
            return end - start + 1 if _is_int(start) and _is_int(end) else None
        if name == ASSETS:
            return self.shot_assets(row)
        if name == MULTIS:
            return tuple(multi.name for multi in self.row_multis(row))
        edit = row.edits.get(name, _MISSING)
        if edit is not _MISSING and edit is not RESET:
            return edit
        if edit is _MISSING and row.origin is not None and name in row.origin.own:
            if name == LAYERS and row.origin.layer_own is not None:
                # Its own keys over what it inherits now, pending group
                # Layers included: it may pin only the order, or one pattern.
                return _layers_of(_layer_state(
                    self.inherited(row).layer_state, row.origin.layer_own,
                ))
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
        if name == MULTIS:
            if self.membership_pending(row):
                return SET
            return OWN if self.row_multis(row) else INHERITED
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
        if name == MULTIS:
            return ''
        return self.inherited(row).sources.get(name, '')

    @property
    def dirty(self) -> bool:
        return (
            any(row.touched for row in self._rows)
            or bool(self._root_edits) or bool(self._used_new_roots())
            or any(self._multi_pending(multi) for multi in self._multis)
            or bool(self._new_groups) or bool(self._live_group_edits())
            or bool(self._everyone_edits)
        )

    def _multi_pending(self, multi: Multi) -> bool:
        """Whether Apply writes anything for ``multi``: like
        :attr:`Multi.touched`, but a member Apply leaves out (a group the
        sheet listed whole and then removed) is no change."""
        if multi.is_new or multi.renamed or multi.retire or multi.departments_changed:
            return True
        live = {m for m in multi.members if self._live_member(multi, m)}
        return live != set(multi.base_members)

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
            problems=len(self.problems()) + len(self.multi_problems())
            + len(self.group_problems()) + len(self.group_value_problems()),
            new_groups=tuple(dict.fromkeys([
                *self._new_groups,
                *(row.group for row in rows if row.is_new and row.group not in known),
            ])),
            roots_changed=len(self._root_edits),
            roots_new=len(self._used_new_roots()),
            multis_new=sum(m.is_new for m in self._multis),
            multis_changed=sum(
                1 for m in self._multis
                if not m.is_new and not m.retire and self._multi_pending(m)
            ),
            multis_retired=sum(m.retire for m in self._multis),
            groups_changed=len(self._live_group_edits()),
            everyone_changed=bool(self._everyone_edits),
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
        return self.root_assets(self.value(row, ROOT))

    def root_assets(self, root) -> tuple[RootAsset, ...]:
        """What a Root gives a shot: :meth:`shot_assets` of its shots."""
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
        # A sequence's Root is used even before it has shots.
        roots = [self.value(row, ROOT) for row in self._rows if not row.retire]
        roots += [self.group_own(group).get(ROOT) for group in self._live_group_edits()]
        for root in roots:
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

    # ── The kind's root (all shots) ──────────────────────
    #
    # The project level of the render settings (#70): ``entity:/shots``,
    # which every sequence and shot inherits from unless it pins its own.
    # Only EVERYONE_FIELDS are edited here.

    def everyone_own(self) -> dict[str, object]:
        """All shots' own values, as they will be once applied."""
        own = dict(self.base.everyone_own)
        for name, value in self._everyone_edits.items():
            if value is RESET:
                own.pop(name, None)
            else:
                own[name] = value
        return own

    def new_group_inherited(self) -> Inherited:
        """What a group (and an entity in a group with nothing of its own)
        inherits once applied: all shots' pending values included."""
        base = self.base.for_new_group
        if not self._everyone_edits:
            return base
        values, sources = dict(base.values), dict(base.sources)
        for name, value in self._everyone_edits.items():
            if value is RESET:
                # Back to what all shots inherits from the project.
                if name in self.base.for_everyone.values:
                    values[name] = self.base.for_everyone.values[name]
                    sources[name] = self.base.for_everyone.sources.get(name, '')
                else:
                    values.pop(name, None)
                    sources.pop(name, None)
            else:
                values[name] = value
                sources[name] = self.kind.everyone
        return Inherited(values, sources, base.layer_state)

    def everyone_value(self, name: str) -> object:
        """What all shots hold for ``name`` once applied (None: nothing)."""
        own = self.everyone_own()
        if name in own:
            return own[name]
        return self.base.for_everyone.values.get(name)

    def everyone_state(self, name: str) -> str:
        """:meth:`state` for all shots' value."""
        edit = self._everyone_edits.get(name, _MISSING)
        if edit is RESET:
            return CLEARED
        if edit is not _MISSING:
            return SET
        return OWN if name in self.base.everyone_own else INHERITED

    def everyone_source(self, name: str) -> str:
        """Where all shots' inherited value of ``name`` comes from: the
        project, or the project's defaults ('' when nothing holds it)."""
        return self.base.for_everyone.sources.get(name, '')

    def set_everyone_value(self, name: str, value) -> bool:
        """Set all shots' value of ``name``, which every sequence and shot
        inherits unless it pins its own; False when that changes nothing.
        Raises ValueError as :meth:`set_value` does."""
        if name not in EVERYONE_FIELDS or name not in self.kind.fields:
            raise ValueError(f'{self.kind.everyone.capitalize()} have no {self.kind.label(name)}')
        value = self._parse(name, value, self.everyone_value(name))
        if value == self.everyone_value(name):
            return False
        stored = self.base.everyone_own
        self._changing()
        if name in stored:
            if value == stored[name]:
                self._everyone_edits.pop(name, None)
            else:
                self._everyone_edits[name] = value
        elif value == self.base.for_everyone.values.get(name, _MISSING):
            self._everyone_edits.pop(name, None)   # what it inherits: nothing to pin
        else:
            self._everyone_edits[name] = value
        return True

    def reset_everyone(self, name: str) -> bool:
        """Put all shots' value of ``name`` back to what the project says."""
        if name not in EVERYONE_FIELDS:
            return False
        if name in self.base.everyone_own:
            if self._everyone_edits.get(name, _MISSING) is RESET:
                return False
            self._changing()
            self._everyone_edits[name] = RESET
            return True
        if name not in self._everyone_edits:
            return False
        self._changing()
        del self._everyone_edits[name]
        return True

    def revert_everyone(self) -> bool:
        """Drop all shots' pending values."""
        if not self._everyone_edits:
            return False
        self._changing()
        self._everyone_edits.clear()
        return True

    # ── Group values ─────────────────────────────────────

    def group_own(self, group: str) -> dict[str, object]:
        """The group's own values, as they will be once applied."""
        own = dict(self.base.group_own.get(group, {}))
        for name, value in self._group_edits.get(group, {}).items():
            if value is RESET:
                own.pop(name, None)
            else:
                own[name] = value
        return own

    def group_inherited(self, group: str) -> Inherited:
        """What an entity in ``group`` inherits once applied: the group's
        pending values included."""
        edits = self._group_edits.get(group)
        if not edits and not self._everyone_edits:
            return self.base.inherited(group)
        edits = edits or {}
        base = self.new_group_inherited()
        values, sources = dict(base.values), dict(base.sources)
        for name, value in self.group_own(group).items():
            values[name] = value
            sources[name] = f'{self.kind.group} {group}'
        stored = self.base.by_group.get(group, base)
        layer_state = stored.layer_state
        layers = edits.get(LAYERS, _MISSING)
        if layers is RESET:
            layer_state = base.layer_state
        elif isinstance(layers, Layers):
            layer_state = _layers_state_of(stored.layer_state, layers)
        return Inherited(values, sources, layer_state)

    def group_value(self, group: str, name: str) -> object:
        """What the group holds for ``name`` once applied (None: nothing)."""
        if name == LENGTH:
            start = self.group_value(group, FRAME_START)
            end = self.group_value(group, FRAME_END)
            return end - start + 1 if _is_int(start) and _is_int(end) else None
        own = self.group_own(group)
        if name in own:
            return own[name]
        return self.new_group_inherited().values.get(name)

    def group_state(self, group: str, name: str) -> str:
        """:meth:`state` for a group's value."""
        if name == LENGTH:
            states = {self.group_state(group, FRAME_START), self.group_state(group, FRAME_END)}
            if states & {SET, CLEARED}:
                return SET
            return OWN if OWN in states else INHERITED
        edit = self._group_edits.get(group, {}).get(name, _MISSING)
        if edit is RESET:
            return CLEARED
        if edit is not _MISSING:
            return SET
        return OWN if name in self.base.group_own.get(group, {}) else INHERITED

    def group_source(self, group: str, name: str) -> str:
        """Where a group's inherited value of ``name`` comes from."""
        return self.new_group_inherited().sources.get(name, '')

    def group_pending(self, group: str) -> bool:
        return bool(self._group_edits.get(group))

    def _live_group_edits(self) -> dict[str, dict[str, object]]:
        """Pending group values of groups that will exist: a new group that
        was removed (or whose new rows went) takes its values with it."""
        groups = set(self.groups())
        return {g: e for g, e in self._group_edits.items() if e and g in groups}

    def set_group_value(self, group: str, name: str, value) -> bool:
        """Set a group's value, which its entities inherit unless they pin
        their own; False when that changes nothing. Raises ValueError as
        :meth:`set_value` does."""
        if group not in self.groups():
            raise ValueError(f"No {self.kind.group} named '{group}'")
        if name == LENGTH:
            length = parse_value(LENGTH, value)
            if length < 1:
                raise ValueError('Length must be at least 1 frame')
            start = self.group_value(group, FRAME_START)
            if not _is_int(start):
                raise ValueError(f'Give the {self.kind.group} a start frame first')
            return self.set_group_value(group, FRAME_END, start + length - 1)
        if name not in self.kind.values:
            raise ValueError(f'{self.kind.group.capitalize()}s have no {self.kind.label(name)}')
        value = self._parse(name, value, self.group_value(group, name))
        if name == ROOT and not self.root_exists(value):
            raise ValueError(f"No Root named '{root_path(value)}'")
        if value == self.group_value(group, name):
            return False
        stored = self.base.group_own.get(group, {})
        self._changing()
        edits = self._group_edits.setdefault(group, {})
        if name in stored:
            if value == stored[name]:
                edits.pop(name, None)
            else:
                edits[name] = value
        elif value == self.new_group_inherited().values.get(name, _MISSING):
            edits.pop(name, None)         # what it inherits: nothing to pin
        else:
            edits[name] = value
        if not edits:
            del self._group_edits[group]
        return True

    def reset_group(self, group: str, name: str) -> bool:
        """Put a group's value back to inherited (Length: the end frame)."""
        if name == LENGTH:
            name = FRAME_END
        if name not in self.kind.values:
            return False
        edits = self._group_edits.get(group, {})
        if name in self.base.group_own.get(group, {}):
            if edits.get(name, _MISSING) is RESET:
                return False
            self._changing()
            self._group_edits.setdefault(group, {})[name] = RESET
            return True
        if name not in edits:
            return False
        self._changing()
        del edits[name]
        if not edits:
            del self._group_edits[group]
        return True

    def revert_groups(self, groups: Iterable[str]) -> bool:
        """Drop the pending values of these groups."""
        groups = [g for g in dict.fromkeys(groups) if self._group_edits.get(g)]
        if not groups:
            return False
        self._changing()
        for group in groups:
            del self._group_edits[group]
        return True

    def group_value_problems(self) -> dict[str, str]:
        """Why each group with pending values cannot be applied, by name. A
        new group needs a name it can be created under (its new rows' typed
        group, say ``b d``, may not have one)."""
        known = set(self.base.groups)
        return {
            group: reason for group in self._live_group_edits()
            if (reason := (self.group_problem(group, group) if group not in known else '')
                or self._values_problem(lambda n, g=group: self.group_value(g, n)))
        }

    # ── New groups ───────────────────────────────────────

    @property
    def new_groups(self) -> tuple[str, ...]:
        """Groups added with :meth:`add_group`, to create on Apply."""
        return tuple(self._new_groups)

    def group_problem(self, name: str, group: str | None = None) -> str:
        """Why ``name`` cannot name a new group (``group``, one already
        added, aside), or ''."""
        kind = self.kind
        text = name.strip()
        reason = name_problem(kind.group, text)
        if reason:
            return reason
        other = next(
            (g for g in self.base.other_groups if g.casefold() == text.casefold()), None,
        )
        if other is not None:
            return f"'{other}' is {kind.other_group}; a {kind.group} cannot share its name"
        for existing in self.groups():
            if existing == group or existing.casefold() != text.casefold():
                continue
            if existing != text:
                return f"{kind.group.capitalize()} '{text}' differs only by case from '{existing}'"
            if existing in self.base.groups:
                return f"{kind.group.capitalize()} '{text}' already exists"
            return f"'{text}' is already a new {kind.group}"
        return ''

    def add_group(self, name: str) -> str:
        """A new, empty group, created on Apply; its name. Refused (a
        ``ValueError``) when :meth:`group_problem` has a reason."""
        text = name.strip()
        reason = self.group_problem(text)
        if reason:
            raise ValueError(reason)
        self._changing()
        self._new_groups.append(text)
        return text

    def remove_group(self, name: str) -> bool:
        """Drop a group added with :meth:`add_group`; new rows in it keep
        it (they create it on Apply). A group dropped for good leaves the
        Multis that listed it whole: kept, it would be a member nobody sees
        and Apply never writes, yet one that marks its Multi changed."""
        if name not in self._new_groups:
            return False
        self._changing()
        self._new_groups.remove(name)
        if name not in self.groups():
            self._group_edits.pop(name, None)
            member = self.group_member(name)
            for multi in self._multis:
                if member in multi.members and member not in multi.base_members:
                    multi.members[:] = [m for m in multi.members if m != member]
        return True

    def group_problems(self) -> dict[str, str]:
        """Why each group added with :meth:`add_group` cannot be created
        (after a rebase, say: someone else made the other kind's group of
        that name), by name."""
        return {
            group: reason for group in self._new_groups
            if (reason := self.group_problem(group, group))
        }

    # ── Multis ───────────────────────────────────────────

    @property
    def multis(self) -> tuple[Multi, ...]:
        """Every Multi of the kind, stored ones first, then new ones."""
        return tuple(self._multis)

    def multi(self, multi_id: int) -> Multi:
        for multi in self._multis:
            if multi.id == multi_id:
                return multi
        raise KeyError(multi_id)

    def multi_rows(self, multi: Multi) -> list[Row]:
        """The rows ``multi`` lists, in its order."""
        return [
            self._by_id[member] for member in multi.members
            if isinstance(member, int) and member in self._by_id
        ]

    def group_member(self, group: str) -> str:
        """The member a whole group (sequence or category) is listed as:
        its URI string, as ``config.groups`` stores it."""
        return str(self.kind.root / group)

    def _member_group(self, member) -> str | None:
        """The group ``member`` stands for, when it is a listed group of
        the sheet (every row in it, and any made in it later, is in the
        Multi); None for a row or a stray."""
        if not isinstance(member, str):
            return None
        prefix = f'{self.kind.root}/'
        if not member.startswith(prefix):
            return None
        name = member[len(prefix):]
        return name if '/' not in name and name in self.groups() else None

    def multi_groups(self, multi: Multi) -> list[str]:
        """The groups ``multi`` lists whole, in its order."""
        return [g for g in map(self._member_group, multi.members) if g is not None]

    def multi_strays(self, multi: Multi) -> list[str]:
        """Members the project no longer has: kept as stored. A group the
        sheet listed whole and that then went away (a new sequence removed
        before Apply) is not one: it is dropped, and back if the group is."""
        return [
            member for member in multi.members
            if isinstance(member, str) and self._member_group(member) is None
            and member in multi.base_members
        ]

    def _live_member(self, multi: Multi, member) -> bool:
        """Whether Apply writes ``member`` of ``multi``: a row the sheet
        has, a group it has, or a stray the Multi already stored."""
        if isinstance(member, int):
            return True
        return self._member_group(member) is not None or member in multi.base_members

    def covers(self, multi: Multi, row: Row, base: bool = False) -> bool:
        """Whether ``multi`` has ``row`` as a member: listed itself, or in
        a group it lists. ``base``: as loaded, before the sheet's edits."""
        if base:
            if multi.is_new or row.origin is None:
                return False
            members = multi.base_members
            group = row.origin.uri.segments[-2] if len(row.origin.uri.segments) > 1 else ''
        else:
            members, group = multi.members, row.group
        return row.id in members or (bool(group) and self.group_member(group) in members)

    def multi_covered_rows(self, multi: Multi) -> list[Row]:
        """Every live row ``multi`` has as a member: the rows it lists, then
        the rows of the groups it lists."""
        groups = set(self.multi_groups(multi))
        listed = self.multi_rows(multi)
        seen = {row.id for row in listed}
        return [row for row in listed if not row.retire] + [
            row for row in self._rows
            if not row.retire and row.group in groups and row.id not in seen
        ]

    def row_multis(self, row: Row) -> list[Multi]:
        """The Multis that will have ``row`` as a member; none for a row
        being retired (retiring takes it out of every Multi)."""
        if row.retire:
            return []
        return [m for m in self._multis if not m.retire and self.covers(m, row)]

    def membership_pending(self, row: Row) -> bool:
        """Whether Apply changes which Multis have ``row`` as a member."""
        for multi in self._multis:
            now = self.covers(multi, row) and not multi.retire
            before = self.covers(multi, row, base=True)
            if now != before:
                return True
        return False

    def multi_name_problem(self, name: str, multi: Multi | None = None) -> str:
        """Why ``name`` cannot name a new Multi (or ``multi`` renamed), or ''."""
        text = name.strip()
        reason = name_problem('Multi', text)
        if reason:
            return reason
        folded = text.casefold()
        for other in self._multis:
            if other is multi:
                continue
            if other.name.casefold() == folded:
                if other.name != text:
                    return f"Multi '{text}' differs only by case from '{other.name}'"
                if other.is_new:
                    return f"'{text}' is also a new Multi"
                if other.retire:
                    return f"Multi {text} is being retired; Apply that first, then reuse its name"
                return f"A Multi named '{text}' already exists"
            if other.origin is not None and other.origin.name.casefold() == folded \
                    and other.name != other.origin.name:
                return (
                    f'Multi {other.origin.name} keeps its name until its rename to '
                    f'{other.name} is applied; Apply that first'
                )
        return ''

    def _live_ids(self, row_ids: Iterable[int]) -> list[int]:
        return list(dict.fromkeys(
            row_id for row_id in row_ids
            if row_id in self._by_id and not self._by_id[row_id].retire
        ))

    def _ordered(self, departments: Iterable[str]) -> tuple[str, ...]:
        """Departments in pipeline order; ones the pool no longer has last."""
        wanted = list(dict.fromkeys(departments))
        pool = self.base.pool
        return (
            *(d for d in pool if d in wanted),
            *(d for d in wanted if d not in pool),
        )

    def add_multi(
        self, name: str, members: Iterable[int] = (),
        departments: Iterable[str] | None = None,
        timeline: tuple[bool, bool] = (False, False),
    ) -> int:
        """A new Multi, created on Apply; its id. It covers the whole pool
        unless ``departments`` says otherwise. ``timeline`` is its
        ``(shared timeline, bookmarks)``; only a shots Multi has one. A name
        with a problem is allowed here and shown by :meth:`multi_problems`."""
        self._changing()
        shared, bookmarks = self._timeline(*timeline)
        multi = Multi(
            self._next_multi_id, None, name.strip(), self._live_ids(members),
            self._ordered(self.base.pool if departments is None else departments),
            shared_timeline=shared, timeline_bookmarks=bookmarks,
        )
        self._next_multi_id += 1
        self._multis.append(multi)
        return multi.id

    def _timeline(self, shared: bool, bookmarks: bool) -> tuple[bool, bool]:
        """A Timeline this kind can hold: assets have no frames to lay end
        to end, and bookmarks come only with a shared timeline."""
        shared = bool(shared) and self.kind is SHOTS_KIND
        return shared, shared and bool(bookmarks)

    def set_multi_timeline(self, multi_ids: Iterable[int], shared: bool, bookmarks: bool) -> bool:
        """Set these Multis' Timeline, as one edit. Switching shared timeline
        on places the members not placed yet when Apply writes it, after the
        last frame any placed member reaches; switching it off keeps their
        places (``config.multi_timeline``)."""
        timeline = self._timeline(shared, bookmarks)
        changed = False
        with self.batch():
            for multi_id in list(multi_ids):
                multi = self.multi(multi_id)
                if multi.retire or multi.timeline == timeline:
                    continue
                self._changing()
                multi.shared_timeline, multi.timeline_bookmarks = timeline
                changed = True
        return changed

    def rename_multi(self, multi_id: int, name: str) -> bool:
        multi = self.multi(multi_id)
        text = name.strip()
        if multi.retire or text == multi.name:
            return False
        self._changing()
        multi.name = text
        return True

    def set_membership(self, row_ids: Iterable[int], multi_id: int, member: bool) -> bool:
        """Put these rows into the Multi, or take them out."""
        multi = self.multi(multi_id)
        if multi.retire:
            return False
        ids = self._live_ids(row_ids) if member else [
            row_id for row_id in dict.fromkeys(row_ids) if row_id in multi.members
        ]
        if member:
            ids = [row_id for row_id in ids if row_id not in multi.members]
        if not ids:
            return False
        self._changing()
        if member:
            multi.members.extend(ids)
        else:
            multi.members[:] = [m for m in multi.members if m not in ids]
        return True

    def group_multis(self, group: str) -> list[Multi]:
        """The Multis that list ``group`` whole (not retired)."""
        member = self.group_member(group)
        return [m for m in self._multis if not m.retire and member in m.members]

    def set_group_membership(
        self, groups: Iterable[str], multi_id: int, member: bool,
    ) -> bool:
        """List these groups whole in the Multi, or take them out. Rows of
        a group listed one by one stay listed."""
        multi = self.multi(multi_id)
        if multi.retire:
            return False
        known = set(self.groups())
        wanted = [self.group_member(g) for g in dict.fromkeys(groups) if g in known]
        if member:
            ids = [g for g in wanted if g not in multi.members]
        else:
            ids = [g for g in wanted if g in multi.members]
        if not ids:
            return False
        self._changing()
        if member:
            multi.members.extend(ids)
        else:
            multi.members[:] = [m for m in multi.members if m not in ids]
        return True

    def set_multi_members(
        self, multi_id: int, row_ids: Iterable[int],
        groups: Iterable[str] | None = None,
    ) -> bool:
        """The Multi lists exactly these rows, and these groups whole when
        ``groups`` is given (None keeps the groups it lists). Members the
        project no longer has stay listed. A row in a listed group is not
        listed again, but one listed before stays."""
        multi = self.multi(multi_id)
        known = set(self.groups())
        listed_groups = (
            self.multi_groups(multi) if groups is None
            else [g for g in dict.fromkeys(groups) if g in known]
        )
        group_members = [self.group_member(g) for g in listed_groups]
        wanted = [
            row_id for row_id in self._live_ids(row_ids)
            if row_id in multi.members or self._by_id[row_id].group not in listed_groups
        ]
        members = [
            m for m in multi.members
            if (isinstance(m, str) and self._member_group(m) is None)
            or m in wanted or m in group_members
        ]
        members += [g for g in group_members if g not in members]
        members += [row_id for row_id in wanted if row_id not in members]
        if multi.retire or members == multi.members:
            return False
        self._changing()
        multi.members[:] = members
        return True

    def set_multi_departments(self, multi_id: int, departments: Iterable[str]) -> bool:
        multi = self.multi(multi_id)
        ordered = self._ordered(departments)
        if multi.retire or set(ordered) == set(multi.departments):
            return False
        self._changing()
        multi.departments = ordered
        return True

    def set_covers(self, multi_ids: Iterable[int], department: str, covered: bool) -> bool:
        """Tick (or untick) one department on these Multis, as one edit."""
        changed = False
        with self.batch():
            for multi_id in list(multi_ids):
                multi = self.multi(multi_id)
                has = department in multi.departments
                if has == covered:
                    continue
                departments = (
                    (*multi.departments, department) if covered
                    else tuple(d for d in multi.departments if d != department)
                )
                changed |= self.set_multi_departments(multi_id, departments)
        return changed

    def retire_multis(self, multi_ids: Iterable[int], retired: bool = True) -> bool:
        """Mark stored Multis to retire (or not); a new one is just removed.
        A retired Multi leaves the database; its workfiles stay on disk."""
        changed = False
        with self.batch():
            for multi_id in list(multi_ids):
                multi = self.multi(multi_id)
                if multi.is_new:
                    if retired:
                        self._changing()
                        self._multis.remove(multi)
                        changed = True
                elif multi.retire != retired:
                    self._changing()
                    multi.retire = retired
                    changed = True
        return changed

    def revert_multis(self, multi_ids: Iterable[int]) -> bool:
        """Drop every pending edit of these Multis; new ones go."""
        changed = False
        with self.batch():
            for multi_id in list(multi_ids):
                multi = self.multi(multi_id)
                if not multi.touched and list(multi.members) == list(multi.base_members):
                    continue
                self._changing()
                changed = True
                if multi.is_new:
                    self._multis.remove(multi)
                else:
                    multi.name = multi.origin.name
                    multi.members[:] = list(multi.base_members)
                    multi.departments = multi.origin.departments
                    multi.shared_timeline = multi.origin.shared_timeline
                    multi.timeline_bookmarks = multi.origin.timeline_bookmarks
                    multi.retire = False
        return changed

    def multi_problems(self) -> dict[int, str]:
        """Why each touched Multi cannot be applied, by Multi id. Untouched
        ones are not checked, so old data never blocks an Apply."""
        if self._multi_problems is None:
            self._multi_problems = self._find_multi_problems()
        return self._multi_problems

    def _find_multi_problems(self) -> dict[int, str]:
        found = {}
        for multi in self._multis:
            if multi.retire or not multi.touched:
                continue
            reason = ''
            if multi.is_new or multi.renamed:
                reason = self.multi_name_problem(multi.name, multi)
                if not reason and multi.renamed and self._multi_rename_blocker is not None:
                    reason = self._multi_rename_blocker(multi.origin.uri) or ''
            reason = reason or self._overlap(multi)
            if reason:
                found[multi.id] = reason
        return found

    def _overlap(self, multi: Multi) -> str:
        """A member whose department another Multi covers too: which Multi's
        workfile it opens would be a guess (``config.groups.find_group``
        takes the first). A listed group is checked row by row."""
        members: list = [row for row in self.multi_covered_rows(multi)]
        members += self.multi_strays(multi)
        for member in members:
            label = member.name if isinstance(member, Row) else member.rsplit('/', 1)[-1]
            for other in self._multis:
                if other is multi or other.retire:
                    continue
                if isinstance(member, Row):
                    if not self.covers(other, member):
                        continue
                elif member not in other.members:
                    continue
                shared = [d for d in multi.departments if d in other.departments]
                if shared:
                    verb = 'is' if len(shared) == 1 else 'are'
                    return (
                        f"{label}'s {', '.join(shared)} {verb} also covered by "
                        f'Multi {other.name}'
                    )
        return ''

    # ── Undo ─────────────────────────────────────────────

    def _snapshot(self) -> tuple:
        return (
            [replace(row, edits=dict(row.edits)) for row in self._rows],
            self._next_id, dict(self._root_edits), dict(self._new_roots),
            [replace(multi, members=list(multi.members)) for multi in self._multis],
            self._next_multi_id, list(self._new_groups),
            {group: dict(edits) for group, edits in self._group_edits.items()},
            dict(self._everyone_edits),
        )

    def _restore(self, state: tuple) -> None:
        (self._rows, self._next_id, self._root_edits, self._new_roots,
         self._multis, self._next_multi_id, self._new_groups,
         self._group_edits, self._everyone_edits) = state
        self._by_id = {row.id: row for row in self._rows}
        self._problems = None
        self._multi_problems = None

    def _changing(self) -> None:
        """Called right before every change: one undo step per edit, or
        per :meth:`batch`."""
        self._problems = None
        self._multi_problems = None
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
        if name not in self.kind.values:
            raise ValueError(f'{self.kind.item.capitalize()}s have no {self.kind.label(name)}')
        value = self._parse(name, value, self.value(row, name))
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
        if name not in self.kind.values:
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
        for multi in self._multis:
            if row.id in multi.members:
                multi.members.remove(row.id)

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
            if (self._root_edits or self._new_roots or self._new_groups
                    or self._group_edits or self._everyone_edits):
                self._changing()
                self._root_edits.clear()
                self._new_roots.clear()
                self._new_groups.clear()
                self._group_edits.clear()
                self._everyone_edits.clear()
                changed = True
            changed |= self.revert_multis([m.id for m in self._multis])
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
        new_spelling: dict[str, str] = {g.casefold(): g for g in self._new_groups}
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
            # A stored group keeps its name even when today's rules refuse it.
            reason = (name_problem(kind.group, row.group) if row.group not in known else '') \
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
        return self._values_problem(lambda name: self.value(row, name)) \
            or self._layers_problem(row)

    def exported_channels(self, uri: Uri) -> frozenset[str]:
        """The channels stored entity ``uri`` has department exports under
        (none without ``channel_exports``); asked once per entity."""
        if self._channel_exports is None:
            return frozenset()
        key = str(uri)
        if key not in self._exported:
            self._exported[key] = frozenset(self._channel_exports(uri) or ())
        return self._exported[key]

    def _layers_problem(self, row: Row) -> str:
        """Layers that drop a channel the shot has exports under: that
        channel would vanish from Farm Submit with its exports still there."""
        if row.origin is None or LAYERS not in row.edits:
            return ''
        value = self.value(row, LAYERS)
        kept = set(value.names) if isinstance(value, Layers) else {DEFAULT_CHANNEL}
        lost = sorted(self.exported_channels(row.origin.uri) - kept)
        if not lost:
            return ''
        names = ', '.join(lost)
        return (
            f'{names} {"has" if len(lost) == 1 else "have"} exports of '
            f'{"its" if len(lost) == 1 else "their"} own: keep '
            f'{"it" if len(lost) == 1 else "them"} in the Layers'
        )

    def _parse(self, name: str, value, current) -> object:
        """``parse_value``; Layers keep what ``current`` already holds."""
        if name == LAYERS:
            if isinstance(value, Layers):
                return Layers.make(value.entries, current if isinstance(current, Layers) else None)
            return Layers.parse(value, current if isinstance(current, Layers) else None)
        if name == DEPARTMENTS_OFF:
            return self._departments_off(parse_value(name, value), current)
        return parse_value(name, value)

    def _departments_off(self, names: list[str], current) -> list[str] | None:
        """Departments off in pool order. A name the pool lacks is refused,
        unless the cell already held it (a department since dropped from
        the pool, kept as stored rather than lost on the next edit).

        None off where nothing is held (no key stored or inherited) is
        None, the same as what the cell holds: OK with every box ticked on
        such a shot changes nothing rather than pinning an empty list."""
        pool = self.base.pool
        held = set(current) if isinstance(current, (list, tuple)) else set()
        unknown = [n for n in names if n not in pool and n not in held]
        if unknown:
            raise ValueError(
                f'Not {"a department" if len(unknown) == 1 else "departments"} '
                f'of the shot pool: {", ".join(unknown)}'
                + (f' (the pool: {", ".join(pool)})' if pool else '')
            )
        if not names and current is None:
            return None
        return [n for n in pool if n in names] + [n for n in names if n not in pool]

    @staticmethod
    def _values_problem(value: Callable[[str], object]) -> str:
        start, end = value(FRAME_START), value(FRAME_END)
        if _is_int(start) and _is_int(end) and end < start:
            return f'Ends ({end}) before it starts ({start})'
        for name in (ROLL_START, ROLL_END):
            number = value(name)
            if _is_int(number) and number < 0:
                return f'{_LABELS[name]} cannot be negative'
        fps = value(FPS)
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
        group_problems = self.group_problems()
        for name in self._new_groups:
            if name not in known and name not in group_problems:
                groups.append(kind.root / name)
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
        group_update = []
        value_problems = self.group_value_problems()
        for name, edits in self._live_group_edits().items():
            if name in value_problems or name in group_problems:
                continue
            uri = kind.root / name
            if name not in known and uri not in groups:
                groups.append(uri)      # a new group whose rows have problems
            group_update.append((uri, dict(edits)))
        everyone_update = (
            ((kind.root, dict(self._everyone_edits)),) if self._everyone_edits else ()
        )
        return Changes(
            tuple(retire), tuple(rename), tuple(groups), tuple(roots),
            tuple(create), tuple(update), *self._multi_changes(problems),
            group_update=tuple(group_update), everyone_update=everyone_update,
        )

    def _member_uris(self, problems: Mapping[int, str]) -> dict[int, str]:
        """Each row's URI once applied: a stored row with a problem keeps
        its name (it is not renamed); a new one with a problem and a retired
        one have none (not created; leaving the project)."""
        uris = {}
        for row in self._rows:
            if row.retire:
                continue
            if row.id in problems:
                if row.origin is not None:
                    uris[row.id] = str(row.origin.uri)
                continue
            uris[row.id] = str(self.kind.root / row.group / row.name)
        return uris

    def _multi_changes(self, problems: Mapping[int, str]) -> tuple:
        multi_problems = self.multi_problems()
        uris = self._member_uris(problems)

        def members(multi: Multi) -> list[str]:
            return [
                uris[m] if isinstance(m, int) else m
                for m in multi.members
                if ((m in uris) if isinstance(m, int) else self._live_member(multi, m))
            ]

        retire, rename, create, update = [], [], [], []
        for multi in self._multis:
            if multi.id in multi_problems:
                continue
            if multi.retire:
                retire.append(multi.origin.uri)
                continue
            uri = GROUPS_URI / self.kind.key / multi.name
            if multi.is_new:
                own = {
                    'members': members(multi), 'departments': list(multi.departments),
                }
                # Only a shared timeline is written: a new Multi on its
                # members' own frames is stored as one always was.
                if multi.shared_timeline:
                    own['shared_timeline'] = True
                    own['timeline_bookmarks'] = multi.timeline_bookmarks
                create.append((uri, own))
                continue
            if multi.renamed:
                rename.append((multi.origin.uri, multi.name))
            edits = {}
            # A member renamed or retired in the sheet changes the stored
            # list too, even with the membership as it was:
            # pipe.entity_files does that on its own, but a bare config
            # rename or removal would not.
            stale = any(
                isinstance(m, int) and m in self._by_id
                and (self._by_id[m].renamed or self._by_id[m].retire)
                for m in multi.members
            )
            if multi.members_changed or stale:
                written = members(multi)
                if written != list(multi.origin.members):
                    edits['members'] = written
            if multi.departments_changed:
                edits['departments'] = list(multi.departments)
            if multi.timeline_changed:
                # The starts stay as stored: switching off keeps every
                # member's place, and switching on places the unplaced
                # ones after Apply (EntityEditorSource.place).
                edits['shared_timeline'] = multi.shared_timeline
                edits['timeline_bookmarks'] = multi.timeline_bookmarks
            if edits:
                update.append((uri, edits))
        return tuple(retire), tuple(rename), tuple(create), tuple(update)

    # ── Rebase ───────────────────────────────────────────

    def rebase(self, base: Base) -> tuple['Sheet', list[str]]:
        """These edits on top of ``base`` (a fresh :func:`load`), and a note
        for each edit that no longer applies.

        An edit already in ``base`` falls away: after an Apply, what was
        written is no longer pending and what was not still is. A new row
        whose entity now exists, and did not when the edits began, becomes
        edits to it where its values differ; so does a new Root that now
        exists. A new row named like an entity that already existed stays
        new (still a problem). Multis are carried by
        :meth:`_carry_multis`. Undo starts over.
        """
        sheet = Sheet(
            base, self._rename_blocker, self._multi_rename_blocker, self._channel_exports,
        )
        sheet._new_groups = [g for g in self._new_groups if g not in base.groups]
        notes = []
        moved: dict[int, int] = {}   # this sheet's row ids -> the new sheet's
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
        # A new row named like an entity stored before the edits was never
        # written (it is a problem, so Apply skips it): it stays a new row,
        # not edits to that entity.
        had = {(row.origin.group, row.origin.name) for row in self._rows if row.origin is not None}
        for old in self._rows:
            if old.origin is None:
                target = None if (old.group, old.name) in had else stored.get((old.group, old.name))
                if target is None:
                    sheet._insert(Row(
                        sheet._next_id, None, old.group, old.name, dict(old.edits),
                    ))
                    moved[old.id] = sheet._next_id
                    sheet._next_id += 1
                else:
                    _carry(target, old.edits)
                    moved[old.id] = target.id
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
            moved[old.id] = target.id
            target.retire = old.retire
            if old.renamed and not renamed_done:
                target.name = old.name
            _carry(target, old.edits)
        sheet._carry_multis(self._multis, moved, notes, self._live_member)
        sheet._carry_group_edits(self._live_group_edits(), notes)
        sheet._carry_everyone_edits(self._everyone_edits)
        return sheet, notes

    def _carry_everyone_edits(self, old: Mapping[str, object]) -> None:
        """:meth:`rebase` for all shots' values: an edit already stored
        falls away."""
        stored = self.base.everyone_own
        for name, value in old.items():
            if value is RESET:
                if name in stored:
                    self._everyone_edits[name] = RESET
            elif stored.get(name, _MISSING) != value:
                self._everyone_edits[name] = value

    def _carry_group_edits(self, old: Mapping[str, Mapping[str, object]], notes: list[str]) -> None:
        """:meth:`rebase` for group values: an edit already stored falls
        away; a group that is gone takes its edits with it."""
        groups = set(self.groups())
        for group, edits in old.items():
            if group not in groups:
                if any(value is not RESET for value in edits.values()):
                    notes.append(
                        f'{self.kind.group.capitalize()} {group} is no longer in '
                        'the project; its changes were dropped'
                    )
                continue
            stored = self.base.group_own.get(group, {})
            carried = {}
            for name, value in edits.items():
                if value is RESET:
                    if name in stored:
                        carried[name] = RESET
                elif stored.get(name, _MISSING) != value:
                    carried[name] = value
            if carried:
                self._group_edits[group] = carried

    def _carry_multis(
        self, old_multis: list[Multi], moved: Mapping[int, int], notes: list[str],
        live: Callable[[Multi, object], bool] = lambda multi, member: True,
    ) -> None:
        """:meth:`rebase` for Multis: what each one's edits added and took
        away, onto the Multi as now stored, in the sheet's member order, so
        a member someone else added meanwhile stays (after the sheet's). A
        rename or a new Multi that is now stored (this Apply wrote it)
        falls away like a row's; a new Multi named like one stored before
        the edits stays new. ``live`` is the old sheet's
        :meth:`_live_member`: a member it would not write (a new sequence
        listed whole, then removed) is not carried either, or it would
        come back as a pending edit nobody can see. A Timeline edit carries
        over like a department's."""
        by_uri = {str(row.origin.uri): row.id for row in self._rows if row.origin is not None}

        def mapped(members) -> list:
            out = []
            for member in members:
                if isinstance(member, int):
                    if member in moved:
                        out.append(moved[member])
                else:
                    out.append(by_uri.get(member, member))
            return out

        stored = {multi.origin.name: multi for multi in self._multis}
        # A new Multi named like one stored before the edits was never
        # written (Apply skips it for the clash); it stays new.
        had = {m.origin.name.casefold() for m in old_multis if m.origin is not None}
        for old in old_multis:
            # Untouched can still be reordered (a member out and back in).
            if not old.touched and list(old.members) == list(old.base_members):
                continue
            base_members = [] if old.is_new else mapped(old.base_members)
            now = mapped([m for m in old.members if live(old, m)])
            added = [m for m in now if m not in base_members]
            removed = [m for m in base_members if m not in now]
            base_departments = () if old.is_new else old.origin.departments
            gained = [d for d in old.departments if d not in base_departments]
            lost = [d for d in base_departments if d not in old.departments]
            key = old.name if old.is_new else old.origin.name
            target = None if old.is_new and key.casefold() in had else stored.get(key)
            if target is None and not old.is_new and old.renamed:
                target = stored.get(old.name)   # the rename was applied
                if target is not None:
                    key = old.name
            if target is None:
                if not old.is_new:
                    if not old.retire:
                        notes.append(
                            f'Multi {old.origin.name} is no longer in the '
                            'project; its changes were dropped'
                        )
                    continue
                self._multis.append(Multi(
                    self._next_multi_id, None, old.name, now, old.departments,
                    shared_timeline=old.shared_timeline,
                    timeline_bookmarks=old.timeline_bookmarks,
                ))
                self._next_multi_id += 1
                continue
            target.retire = old.retire
            if old.timeline_changed:
                target.shared_timeline, target.timeline_bookmarks = old.timeline
            if not old.is_new and old.renamed and key == old.origin.name:
                target.name = old.name
            # The sheet's order, less what someone else took out, then what
            # they added.
            theirs = list(target.members)
            target.members[:] = [
                m for m in now if m in theirs or m in added
            ] + [m for m in theirs if m not in now and m not in removed]
            target.departments = self._ordered(
                [d for d in target.departments if d not in lost]
                + [d for d in gained if d not in target.departments]
            ) if (gained or lost) else (
                old.departments if set(old.departments) == set(target.departments)
                else target.departments
            )


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


def _stored(name: str, value) -> object:
    """A sheet value as the database stores it: Departments off as a list."""
    if name == DEPARTMENTS_OFF and isinstance(value, tuple):
        return list(value)
    return value


def _update(config, uri: Uri, edits: Mapping[str, object]) -> None:
    # The entity's OWN values, read now: another field changed since the
    # sheet was loaded is kept, and nothing inherited gets pinned.
    own = config.get_own_properties(uri)
    if own is None:
        raise ValueError(f'{_label(uri)} is no longer in the project')
    for name, value in edits.items():
        if name == LAYERS:
            if value is RESET:
                own.pop(CHANNEL_PROPERTY, None)
                own.pop(CHANNEL_PRIMS_PROPERTY, None)
            else:
                _pin_layers(own, config.get_inherited_properties(uri) or {}, value)
        elif value is RESET:
            # A render setting is one key of ``render``; the rest stay.
            rc.clear_field(own, name)
        else:
            rc.write_field(own, name, _stored(name, value))
    config.set_own_properties(uri, own)


def _pin_layers(own: dict, inherited: Mapping, layers: Layers) -> None:
    """Write ``layers`` into ``own`` (an entity's own properties) as the
    fewest keys that make them, given what it ``inherited``
    (``get_inherited_properties``, read at Apply time).

    ``channels`` is pinned only when the order differs from the inherited
    one, and a pattern only where it differs from the inherited pattern
    (``""`` drops an inherited one), so a layer the sheet did not change
    keeps following the sequence or project. What was already pinned stays
    pinned, as typed-over values do, and patterns of channels the layers do
    not list are left as they were.
    """
    inherited_layers = _layers_of(_layer_state(_NO_LAYERS, inherited))
    names = list(layers.names)
    if CHANNEL_PROPERTY in own or tuple(names) != inherited_layers.names:
        own[CHANNEL_PROPERTY] = names
    stored = own.get(CHANNEL_PRIMS_PROPERTY)
    stored = dict(stored) if isinstance(stored, dict) else {}
    raw = inherited.get(CHANNEL_PRIMS_PROPERTY)
    raw = raw if isinstance(raw, dict) else {}
    prims = {name: value for name, value in stored.items() if name not in layers.names}
    for name, pattern in layers.entries:
        if name in stored or pattern != _pattern(raw.get(name)):
            prims[name] = pattern
    if prims:
        own[CHANNEL_PRIMS_PROPERTY] = prims
    else:
        own.pop(CHANNEL_PRIMS_PROPERTY, None)


def _create(config, uri: Uri, own: Mapping[str, object]) -> None:
    """A new entity with ``own`` values; its Layers, if set, as
    :func:`_update` writes them (two keys, against what it inherits)."""
    values = {name: _stored(name, value) for name, value in own.items()}
    layers = values.pop(LAYERS, None)
    config.add_entity(uri, rc.nest(values))
    if isinstance(layers, Layers):
        _update(config, uri, {LAYERS: layers})


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
    for uri, edits in changes.everyone_update:
        step(f'Updating {_label(uri) or "all " + uri.segments[0]}', _update, config, uri, edits)
    for uri, edits in changes.group_update:
        step(f'Updating {_label(uri)}', _update, config, uri, edits)
    for uri, own in changes.create:
        step(f'Creating {_label(uri)}', _create, config, uri, own)
    for uri, edits in changes.update:
        step(f'Updating {_label(uri)}', _update, config, uri, edits)
    # Multis last: a new shot can be a member, and a retired Multi frees
    # its name for a new one.
    for uri in changes.multis_retire:
        step(f'Retiring Multi {uri.segments[-1]}', config.remove_entity, uri)
    for uri, name in changes.multis_rename:
        step(f'Renaming Multi {uri.segments[-1]} to {name}', config.rename_entity, uri, name)
    for uri, own in changes.multis_create:
        step(f'Creating Multi {uri.segments[-1]}', config.add_entity, uri, dict(own))
    for uri, edits in changes.multis_update:
        step(f'Updating Multi {uri.segments[-1]}', _update, config, uri, edits)
