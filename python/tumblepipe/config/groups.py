from dataclasses import dataclass, field, replace

from tumblepipe.api import api
from tumblepipe.config.store import JsonConfigStore
from tumblepipe.config.entities import is_terminal_entity
from tumblepipe.config.timeline import FrameRange, get_frame_range_in
from tumblepipe.util.uri import Uri

GROUPS_URI = Uri.parse('groups:/')

# A group's ``members``/``departments`` are read with a ``[]`` default: the
# group schema (``schemas:/groups/<context>/group`` since migration v22; before
# it sat beside the roots and no default reached a group) may not declare a
# key, and groups written by the old diffing store can lack either key. One
# such group must not break every read.
# The timeline keys are read the same way, with their off defaults.

# A stored member can be a sequence (or a category, for an asset Multi):
# its shots are members then, the ones it has now and any made in it later.
# ``members`` is that resolved list, the one every reader wants;
# ``stored_members`` is what the Multi lists, the one edits change.

@dataclass(frozen=True)
class Group:
    uri: Uri
    members: list[Uri]
    departments: list[str]
    # The shared timeline (``config.multi_timeline``): whether the members
    # sit end to end on one timeline in the Multi's workfile, whether that
    # workfile gets a playbar bookmark per member, and the frame each
    # member's first frame sits at (member URI string -> frame).
    shared_timeline: bool = False
    timeline_bookmarks: bool = False
    timeline_starts: dict[str, int] = field(default_factory=dict)
    # What the Multi lists, sequences unexpanded. Empty for a Group built
    # by hand without it: ``members`` then.
    stored_members: list[Uri] = field(default_factory=list)

    def listed(self) -> list[Uri]:
        """The Multi's own list: ``stored_members``, or ``members`` when
        the Group was built without it."""
        return list(self.stored_members or self.members)

    @property
    def name(self) -> str:
        return self.uri.segments[-1] if self.uri.segments else ''

    @property
    def root(self) -> Uri | None:
        if len(self.uri.segments) < 1:
            return None
        context = self.uri.segments[0]
        return Uri.parse(f'entity:/{context}')

def is_parent_member_in(store: JsonConfigStore, uri: Uri) -> bool:
    """True for a stored member that stands for the shots (or assets) under
    it: a sequence or category that exists. A shot, or a member that no
    longer exists, stands for itself."""
    if uri.purpose != 'entity' or len(uri.segments) < 2: return False
    # The schema test first: it is positional and cheap, and most members
    # are shots.
    if is_terminal_entity(store, uri): return False
    return store.get_properties(uri) is not None

def children_of_in(store: JsonConfigStore, uri: Uri) -> list[Uri]:
    """The shots (or assets) a parent member stands for, in config order."""
    return [
        child for child in store.list_entity_uris(uri, closure=True)
        if child != uri and is_terminal_entity(store, child)
    ]

def resolve_members_in(store: JsonConfigStore, stored: list[Uri]) -> list[Uri]:
    """``stored`` with every parent member replaced by its shots, in place,
    each member once."""
    seen = set()
    out = []
    for member in stored:
        for uri in (children_of_in(store, member) if is_parent_member_in(store, member) else [member]):
            key = str(uri)
            if key in seen: continue
            seen.add(key)
            out.append(uri)
    return out

def inherited_from_in(store: JsonConfigStore, group: 'Group', member: Uri) -> Uri | None:
    """The stored parent member ``member`` comes from in ``group``, or None
    when it is listed itself (or not a member)."""
    listed = group.listed()
    if member in listed or member not in group.members: return None
    for parent in listed:
        if parent.contains(member) and is_parent_member_in(store, parent):
            return parent
    return None

def is_group_uri(uri: Uri) -> bool:
    if uri.purpose != 'groups': return False
    if len(uri.segments) != 2: return False
    return True

def overlap_in(
    store: JsonConfigStore,
    context: str,
    members: list[Uri],
    departments: list[str],
    group_uri: Uri | None = None
    ) -> str:
    """Why ``members`` cannot be in a Multi covering ``departments``: one
    of them is in another Multi of ``context`` that covers one of those
    departments too, so which Multi's workfile it opens would be a guess
    (``find_group`` takes the first). Empty when nothing clashes.
    ``group_uri`` is the Multi itself, which is left out. A sequence in
    ``members`` is checked shot by shot."""
    members = resolve_members_in(store, list(members))
    for other in list_groups_in(store, context):
        if other.uri == group_uri: continue
        shared = [d for d in departments if d in other.departments]
        if not shared: continue
        for member in members:
            if member not in other.members: continue
            verb = 'is' if len(shared) == 1 else 'are'
            return (
                f"{member.segments[-1]}'s {', '.join(shared)} {verb} already "
                f'covered by Multi {other.name}'
            )
    return ''

def add_group_in(
    store: JsonConfigStore,
    context: str,
    name: str,
    members: list[Uri],
    departments: list[str]
    ) -> Uri:
    group_uri = GROUPS_URI / context / name
    properties = store.get_properties(group_uri)
    if properties is not None: raise ValueError('Group already exists')
    root_uri = Uri.parse(f'entity:/{context}')
    for member in members:
        if not root_uri.contains(member):
            raise ValueError('Invalid group member list')
    clash = overlap_in(store, context, members, departments)
    if clash: raise ValueError(clash)
    store.add_entity(group_uri, dict(
        members = list(map(str, members)),
        departments = departments
    ))
    return group_uri

def remove_group_in(store: JsonConfigStore, group_uri: Uri):
    store.remove_entity(group_uri)

def add_member_in(store: JsonConfigStore, group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = store.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties.get('members', [])
    member_uri_raw = str(member_uri)
    if member_uri_raw in members: raise ValueError('Already a member of group')
    parent = inherited_from_in(store, _group(store, group_uri, properties), member_uri)
    if parent is not None:
        raise ValueError(
            f'{member_uri.segments[-1]} is already a member: it comes from '
            f'{parent.segments[-1]}'
        )
    clash = overlap_in(store, context, [member_uri], properties.get('departments', []), group_uri)
    if clash: raise ValueError(clash)
    members.append(member_uri_raw)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = store.get_own_properties(group_uri) or {}
    own['members'] = members
    store.set_own_properties(group_uri, own)
    # A member joining a shared timeline gets its place on it.
    place_members_in(store, group_uri)

def remove_member_in(store: JsonConfigStore, group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = store.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties.get('members', [])
    member_uri_raw = str(member_uri)
    if member_uri_raw not in members:
        parent = inherited_from_in(store, _group(store, group_uri, properties), member_uri)
        if parent is not None:
            # A shot of a listed sequence: taking it out alone would have
            # to list the sequence's other shots instead, and stop new ones
            # joining. Refused; the sequence is what to remove.
            raise ValueError(
                f'{member_uri.segments[-1]} comes from {parent.segments[-1]}; '
                f'remove {parent.segments[-1]} from the Multi instead'
            )
        raise ValueError('Not a member of group')
    before = resolve_members_in(store, list(map(Uri.parse, members)))
    members.pop(members.index(member_uri_raw))
    after = {str(m) for m in resolve_members_in(store, list(map(Uri.parse, members)))}
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = store.get_own_properties(group_uri) or {}
    own['members'] = members
    # The member (every shot of a sequence) leaves the shared timeline too.
    # The others keep their frames: moving them would move animation
    # already made against them.
    starts = dict(properties.get('timeline_starts', {}))
    left = [str(m) for m in before if str(m) not in after]
    popped = [starts.pop(m, None) for m in [*left, member_uri_raw]]
    if any(frame is not None for frame in popped):
        own['timeline_starts'] = starts
    store.set_own_properties(group_uri, own)

def add_department_in(store: JsonConfigStore, group_uri: Uri, department: str):
    properties = store.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties.get('departments', [])
    if department in departments: raise ValueError('Already a department of group')
    clash = overlap_in(store, 
        group_uri.segments[0],
        list(map(Uri.parse, properties.get('members', []))),
        [department],
        group_uri
    )
    if clash: raise ValueError(clash)
    departments.append(department)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a department layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = store.get_own_properties(group_uri) or {}
    own['departments'] = departments
    store.set_own_properties(group_uri, own)

def remove_department_in(store: JsonConfigStore, group_uri: Uri, department: str):
    properties = store.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties.get('departments', [])
    if department not in departments: raise ValueError('Not a department of group')
    departments.pop(departments.index(department))
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a department layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = store.get_own_properties(group_uri) or {}
    own['departments'] = departments
    store.set_own_properties(group_uri, own)

def _group(store: JsonConfigStore, group_uri: Uri, properties: dict) -> Group:
    stored = list(map(Uri.parse, properties.get('members', [])))
    return Group(
        uri = group_uri,
        members = resolve_members_in(store, stored),
        stored_members = stored,
        departments = properties.get('departments', []),
        shared_timeline = bool(properties.get('shared_timeline', False)),
        timeline_bookmarks = bool(properties.get('timeline_bookmarks', False)),
        timeline_starts = {
            str(uri): int(frame)
            for uri, frame in properties.get('timeline_starts', {}).items()
        },
    )

# The db files a Group is built from: the Multi itself, and the entity tree
# (and the schemas that place it) its sequence members resolve through.
_GROUP_PURPOSES = ('groups', 'entity', 'schemas')

def _copy(group: Group) -> Group:
    """A Group whose lists the caller may change without touching the memo."""
    return replace(
        group,
        members = list(group.members),
        departments = list(group.departments),
        timeline_starts = dict(group.timeline_starts),
        stored_members = list(group.stored_members),
    )

def get_group_in(store: JsonConfigStore, group_uri: Uri) -> Group | None:
    # One coherent scope: resolving the members reads the entity tree once
    # per member, and each read outside a scope stats the db files again.
    with store.coherent():
        properties = store.get_properties(group_uri)
        if properties is None: return None
        return _group(store, group_uri, properties)

def _list_groups(store: JsonConfigStore, context: str) -> list[Group]:
    context_uri = GROUPS_URI / context
    entities = store.list_entities(context_uri)
    return [
        _group(store, entity.uri, entity.properties)
        for entity in entities
    ]

def list_groups_in(store: JsonConfigStore, context: str) -> list[Group]:
    """Every Multi of ``context``. Memoized until the groups, entity or
    schemas file changes: the browser asks once per card per department
    (``find_group`` behind every workspace), and resolving every Multi's
    members again each time was the bulk of a browser load on an SMB
    share."""
    groups = store.memoized(
        f'groups.list_groups:{context}', _GROUP_PURPOSES,
        lambda: _list_groups(store, context),
    )
    return [_copy(group) for group in groups]

def set_timeline_in(
    store: JsonConfigStore,
    group_uri: Uri,
    shared: bool,
    bookmarks: bool,
    starts: dict[str, int]
    ):
    """Pin the Multi's timeline settings onto its own overrides, the way a
    member or department edit pins its key."""
    properties = store.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    own = store.get_own_properties(group_uri) or {}
    own['shared_timeline'] = bool(shared)
    own['timeline_bookmarks'] = bool(bookmarks)
    own['timeline_starts'] = {
        str(uri): int(frame) for uri, frame in starts.items()
    }
    store.set_own_properties(group_uri, own)

def place_new_members_in(store: JsonConfigStore, uris: list[Uri]):
    """Give new members a place on their Multis' shared timelines: every
    Multi in ``uris`` (``groups:/…``, whose member list changed) and every
    Multi a new shot in ``uris`` joins by being made in a listed sequence.
    Called after an edit that does not go through :func:`add_member`; a
    Multi without a shared timeline is left alone."""
    by_context: dict[str, set[str]] = {}
    touched: list[Uri] = []
    for uri in uris:
        if is_group_uri(uri):
            touched.append(uri)
        elif uri.purpose == 'entity' and uri.segments:
            by_context.setdefault(uri.segments[0], set()).add(str(uri))
    for context, new in by_context.items():
        for group in list_groups_in(store, context):
            if group.shared_timeline and any(str(m) in new for m in group.members):
                touched.append(group.uri)
    for group_uri in dict.fromkeys(touched):
        place_members_in(store, group_uri)

# ── the shared timeline's placement ──────────────────────────────
# Here rather than in ``config.multi_timeline`` because adding a member
# places it, so ``add_member`` needs it, and multi_timeline builds on this
# module (th_pipe's groups.rs keeps them together the same way).

def place(
    starts: dict[str, int],
    members: list[Uri],
    ranges: dict[str, FrameRange | None],
) -> dict[str, int]:
    """``starts`` with every unplaced member placed, in member order.

    A placed member keeps its start; a start for a URI no longer a member is
    dropped. An unplaced member goes on the frame after the last frame any
    placed member reaches (its own last frame, rolls not counted: the rolls
    of neighbouring shots overlap, as continuous action does), or, on an
    empty timeline, on its own start frame. A member with no frame range
    authored cannot be placed and is left out.
    """
    member_keys = [str(member) for member in members]
    placed = {
        key: frame for key, frame in starts.items() if key in member_keys
    }

    def last_frame(key):
        frame_range = ranges.get(key)
        if frame_range is None:
            return None
        return placed[key] + frame_range.end_frame - frame_range.start_frame

    ends = [
        end for end in map(last_frame, placed) if end is not None
    ]
    end = max(ends) if ends else None
    for key in member_keys:
        if key in placed:
            continue
        frame_range = ranges.get(key)
        if frame_range is None:
            continue
        start = frame_range.start_frame if end is None else end + 1
        placed[key] = start
        end = max(
            end if end is not None else start,
            start + frame_range.end_frame - frame_range.start_frame,
        )
    return placed


def pack(members: list[Uri], ranges: dict[str, FrameRange | None]) -> dict[str, int]:
    """Every member laid out afresh, end to end in member order."""
    return place({}, members, ranges)


def member_ranges_in(store: JsonConfigStore, members: list[Uri]) -> dict[str, FrameRange | None]:
    return {str(member): get_frame_range_in(store, member) for member in members}


def place_members_in(store: JsonConfigStore, group_uri: Uri):
    """Place the Multi's unplaced members, when its timeline is shared.

    Called after a member joins; a Multi without a shared timeline is left
    alone, and placed when shared timeline is switched on.
    """
    group = get_group_in(store, group_uri)
    if group is None or not group.shared_timeline:
        return
    starts = place(
        group.timeline_starts, group.members, member_ranges_in(store, group.members)
    )
    if starts == group.timeline_starts:
        return
    set_timeline_in(store, 
        group_uri, group.shared_timeline, group.timeline_bookmarks, starts
    )


def find_group_in(store: JsonConfigStore, context: str, member: Uri, department: str) -> Group | None:
    for group in list_groups_in(store, context):
        if member not in group.members: continue
        if department not in group.departments: continue
        return group
    return None


# A Multi lists its members by URI, so an entity that is deleted or renamed
# must be taken out of, or renamed in, every Multi's list: left alone, a
# deleted shot stays a member (the Multi's template lays out a graph for a
# shot that is gone) and a renamed one silently drops out.

def _is_or_under(entity_uri: Uri, member: Uri) -> bool:
    return member == entity_uri or entity_uri.contains(member)

def _members_of(store: JsonConfigStore, entity_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """(group, its own member list, its timeline starts) for each Multi
    listing ``entity_uri`` or an entity under it, or with one of them on
    its shared timeline (a shot of a listed sequence is placed there
    without being listed). The list is the stored one: a listed sequence
    stays a sequence."""
    if entity_uri.purpose != 'entity' or not entity_uri.segments:
        return []
    found = []
    for group in list_groups_in(store, entity_uri.segments[0]):
        listed = group.listed()
        if any(_is_or_under(entity_uri, m) for m in listed) or any(
            _is_or_under(entity_uri, Uri.parse(m)) for m in group.timeline_starts
        ):
            found.append((
                group.uri,
                [str(m) for m in listed],
                dict(group.timeline_starts),
            ))
    return found

def _set_members(store: JsonConfigStore, group_uri: Uri, members: list[str], starts: dict[str, int]):
    own = store.get_own_properties(group_uri) or {}
    own['members'] = members
    # A Multi that never had a shared timeline is not given an empty one.
    if starts or 'timeline_starts' in own:
        own['timeline_starts'] = starts
    store.set_own_properties(group_uri, own)

def _moved(entity_uri: Uri, new_uri: Uri, raw: str) -> str:
    member = Uri.parse(raw)
    if not _is_or_under(entity_uri, member):
        return raw
    depth = len(entity_uri.segments)
    return str(Uri(member.purpose, [*new_uri.segments, *member.segments[depth:]]))

def drop_member_everywhere_in(store: JsonConfigStore, entity_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """Take ``entity_uri`` (and anything under it) out of every Multi, and
    off its shared timeline. Returns what each changed Multi held before,
    for :func:`restore_members`."""
    before = _members_of(store, entity_uri)
    for group_uri, members, starts in before:
        _set_members(store, 
            group_uri,
            [m for m in members if not _is_or_under(entity_uri, Uri.parse(m))],
            {m: s for m, s in starts.items() if not _is_or_under(entity_uri, Uri.parse(m))},
        )
    return before

def rename_member_everywhere_in(store: JsonConfigStore, entity_uri: Uri, new_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """Rewrite ``entity_uri`` (and anything under it) to ``new_uri`` in
    every Multi's member list, keeping its place, and its place on the
    shared timeline. Returns what each changed Multi held before, for
    :func:`restore_members`."""
    before = _members_of(store, entity_uri)
    for group_uri, members, starts in before:
        _set_members(store, 
            group_uri,
            [_moved(entity_uri, new_uri, m) for m in members],
            {_moved(entity_uri, new_uri, m): s for m, s in starts.items()},
        )
    return before

def restore_members_in(store: JsonConfigStore, saved: list[tuple[Uri, list[str], dict[str, int]]]):
    """Put back the member lists and timeline starts
    :func:`drop_member_everywhere` or :func:`rename_member_everywhere`
    changed."""
    for group_uri, members, starts in saved:
        _set_members(store, group_uri, members, starts)


# --------------------------------------------------------------------------- #
# The active project
# --------------------------------------------------------------------------- #
# The pipeline, the HDAs and the project templates work in the active project
# and call these; the Asset Browser, which works on any project, calls the
# ``_in`` cores with that project's store.


def is_parent_member(uri: Uri) -> bool:
    return is_parent_member_in(api.config, uri)


def children_of(uri: Uri) -> list[Uri]:
    return children_of_in(api.config, uri)


def resolve_members(stored: list[Uri]) -> list[Uri]:
    return resolve_members_in(api.config, stored)


def inherited_from(group: 'Group', member: Uri) -> Uri | None:
    return inherited_from_in(api.config, group, member)


def overlap(context: str, members: list[Uri], departments: list[str], group_uri: Uri | None = None) -> str:
    return overlap_in(api.config, context, members, departments, group_uri)


def add_group(context: str, name: str, members: list[Uri], departments: list[str]) -> Uri:
    return add_group_in(api.config, context, name, members, departments)


def remove_group(group_uri: Uri):
    remove_group_in(api.config, group_uri)


def add_member(group_uri: Uri, member_uri: Uri):
    add_member_in(api.config, group_uri, member_uri)


def remove_member(group_uri: Uri, member_uri: Uri):
    remove_member_in(api.config, group_uri, member_uri)


def add_department(group_uri: Uri, department: str):
    add_department_in(api.config, group_uri, department)


def remove_department(group_uri: Uri, department: str):
    remove_department_in(api.config, group_uri, department)


def get_group(group_uri: Uri) -> Group | None:
    return get_group_in(api.config, group_uri)


def list_groups(context: str) -> list[Group]:
    return list_groups_in(api.config, context)


def set_timeline(group_uri: Uri, shared: bool, bookmarks: bool, starts: dict[str, int]):
    set_timeline_in(api.config, group_uri, shared, bookmarks, starts)


def place_new_members(uris: list[Uri]):
    place_new_members_in(api.config, uris)


def member_ranges(members: list[Uri]) -> dict[str, FrameRange | None]:
    return member_ranges_in(api.config, members)


def place_members(group_uri: Uri):
    place_members_in(api.config, group_uri)


def find_group(context: str, member: Uri, department: str) -> Group | None:
    return find_group_in(api.config, context, member, department)


def drop_member_everywhere(entity_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    return drop_member_everywhere_in(api.config, entity_uri)


def rename_member_everywhere(entity_uri: Uri, new_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    return rename_member_everywhere_in(api.config, entity_uri, new_uri)


def restore_members(saved: list[tuple[Uri, list[str], dict[str, int]]]):
    restore_members_in(api.config, saved)
