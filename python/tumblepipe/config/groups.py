from dataclasses import dataclass, field

from tumblepipe.api import api
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

def is_parent_member(uri: Uri) -> bool:
    """True for a stored member that stands for the shots (or assets) under
    it: a sequence or category that exists. A shot, or a member that no
    longer exists, stands for itself."""
    from tumblepipe.config.entities import is_terminal_entity
    if uri.purpose != 'entity' or len(uri.segments) < 2: return False
    # The schema test first: it is positional and cheap, and most members
    # are shots.
    if is_terminal_entity(api.config, uri): return False
    return api.config.get_properties(uri) is not None

def children_of(uri: Uri) -> list[Uri]:
    """The shots (or assets) a parent member stands for, in config order."""
    from tumblepipe.config.entities import is_terminal_entity
    return [
        child for child in api.config.list_entity_uris(uri, closure=True)
        if child != uri and is_terminal_entity(api.config, child)
    ]

def resolve_members(stored: list[Uri]) -> list[Uri]:
    """``stored`` with every parent member replaced by its shots, in place,
    each member once."""
    seen = set()
    out = []
    for member in stored:
        for uri in (children_of(member) if is_parent_member(member) else [member]):
            key = str(uri)
            if key in seen: continue
            seen.add(key)
            out.append(uri)
    return out

def inherited_from(group: 'Group', member: Uri) -> Uri | None:
    """The stored parent member ``member`` comes from in ``group``, or None
    when it is listed itself (or not a member)."""
    listed = group.listed()
    if member in listed or member not in group.members: return None
    for parent in listed:
        if parent.contains(member) and is_parent_member(parent):
            return parent
    return None

def is_group_uri(uri: Uri) -> bool:
    if uri.purpose != 'groups': return False
    if len(uri.segments) != 2: return False
    return True

def overlap(
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
    members = resolve_members(list(members))
    for other in list_groups(context):
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

def add_group(
    context: str,
    name: str,
    members: list[Uri],
    departments: list[str]
    ) -> Uri:
    group_uri = GROUPS_URI / context / name
    properties = api.config.get_properties(group_uri)
    if properties is not None: raise ValueError('Group already exists')
    root_uri = Uri.parse(f'entity:/{context}')
    for member in members:
        if not root_uri.contains(member):
            raise ValueError('Invalid group member list')
    clash = overlap(context, members, departments)
    if clash: raise ValueError(clash)
    api.config.add_entity(group_uri, dict(
        members = list(map(str, members)),
        departments = departments
    ))
    return group_uri

def remove_group(group_uri: Uri):
    api.config.remove_entity(group_uri)

def add_member(group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties.get('members', [])
    member_uri_raw = str(member_uri)
    if member_uri_raw in members: raise ValueError('Already a member of group')
    parent = inherited_from(_group(group_uri, properties), member_uri)
    if parent is not None:
        raise ValueError(
            f'{member_uri.segments[-1]} is already a member: it comes from '
            f'{parent.segments[-1]}'
        )
    clash = overlap(context, [member_uri], properties.get('departments', []), group_uri)
    if clash: raise ValueError(clash)
    members.append(member_uri_raw)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['members'] = members
    api.config.set_own_properties(group_uri, own)
    # A member joining a shared timeline gets its place on it.
    from tumblepipe.config.multi_timeline import place_members
    place_members(group_uri)

def remove_member(group_uri: Uri, member_uri: Uri):
    context = group_uri.segments[0]
    root_uri = Uri.parse(f'entity:/{context}')
    if not root_uri.contains(member_uri):
        raise ValueError('Invalid group member')
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    members = properties.get('members', [])
    member_uri_raw = str(member_uri)
    if member_uri_raw not in members:
        parent = inherited_from(_group(group_uri, properties), member_uri)
        if parent is not None:
            # A shot of a listed sequence: taking it out alone would have
            # to list the sequence's other shots instead, and stop new ones
            # joining. Refused; the sequence is what to remove.
            raise ValueError(
                f'{member_uri.segments[-1]} comes from {parent.segments[-1]}; '
                f'remove {parent.segments[-1]} from the Multi instead'
            )
        raise ValueError('Not a member of group')
    before = resolve_members(list(map(Uri.parse, members)))
    members.pop(members.index(member_uri_raw))
    after = {str(m) for m in resolve_members(list(map(Uri.parse, members)))}
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a member layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['members'] = members
    # The member (every shot of a sequence) leaves the shared timeline too.
    # The others keep their frames: moving them would move animation
    # already made against them.
    starts = dict(properties.get('timeline_starts', {}))
    left = [str(m) for m in before if str(m) not in after]
    popped = [starts.pop(m, None) for m in [*left, member_uri_raw]]
    if any(frame is not None for frame in popped):
        own['timeline_starts'] = starts
    api.config.set_own_properties(group_uri, own)

def add_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties.get('departments', [])
    if department in departments: raise ValueError('Already a department of group')
    clash = overlap(
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
    own = api.config.get_own_properties(group_uri) or {}
    own['departments'] = departments
    api.config.set_own_properties(group_uri, own)

def remove_department(group_uri: Uri, department: str):
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    departments = properties.get('departments', [])
    if department not in departments: raise ValueError('Not a department of group')
    departments.pop(departments.index(department))
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a department layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(group_uri) or {}
    own['departments'] = departments
    api.config.set_own_properties(group_uri, own)

def _group(group_uri: Uri, properties: dict) -> Group:
    stored = list(map(Uri.parse, properties.get('members', [])))
    return Group(
        uri = group_uri,
        members = resolve_members(stored),
        stored_members = stored,
        departments = properties.get('departments', []),
        shared_timeline = bool(properties.get('shared_timeline', False)),
        timeline_bookmarks = bool(properties.get('timeline_bookmarks', False)),
        timeline_starts = {
            str(uri): int(frame)
            for uri, frame in properties.get('timeline_starts', {}).items()
        },
    )

def get_group(group_uri: Uri) -> Group | None:
    properties = api.config.get_properties(group_uri)
    if properties is None: return None
    return _group(group_uri, properties)

def list_groups(context: str) -> list[Group]:
    context_uri = GROUPS_URI / context
    entities = api.config.list_entities(context_uri)
    return [
        _group(entity.uri, entity.properties)
        for entity in entities
    ]

def set_timeline(
    group_uri: Uri,
    shared: bool,
    bookmarks: bool,
    starts: dict[str, int]
    ):
    """Pin the Multi's timeline settings onto its own overrides, the way a
    member or department edit pins its key."""
    properties = api.config.get_properties(group_uri)
    if properties is None: raise ValueError('Group does not exist')
    own = api.config.get_own_properties(group_uri) or {}
    own['shared_timeline'] = bool(shared)
    own['timeline_bookmarks'] = bool(bookmarks)
    own['timeline_starts'] = {
        str(uri): int(frame) for uri, frame in starts.items()
    }
    api.config.set_own_properties(group_uri, own)

def place_new_members(uris: list[Uri]):
    """Give new members a place on their Multis' shared timelines: every
    Multi in ``uris`` (``groups:/…``, whose member list changed) and every
    Multi a new shot in ``uris`` joins by being made in a listed sequence.
    Called after an edit that does not go through :func:`add_member`; a
    Multi without a shared timeline is left alone."""
    from tumblepipe.config.multi_timeline import place_members
    by_context: dict[str, set[str]] = {}
    touched: list[Uri] = []
    for uri in uris:
        if is_group_uri(uri):
            touched.append(uri)
        elif uri.purpose == 'entity' and uri.segments:
            by_context.setdefault(uri.segments[0], set()).add(str(uri))
    for context, new in by_context.items():
        for group in list_groups(context):
            if group.shared_timeline and any(str(m) in new for m in group.members):
                touched.append(group.uri)
    for group_uri in dict.fromkeys(touched):
        place_members(group_uri)

def find_group(context: str, member: Uri, department: str) -> Group | None:
    for group in list_groups(context):
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

def _members_of(entity_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """(group, its own member list, its timeline starts) for each Multi
    listing ``entity_uri`` or an entity under it, or with one of them on
    its shared timeline (a shot of a listed sequence is placed there
    without being listed). The list is the stored one: a listed sequence
    stays a sequence."""
    if entity_uri.purpose != 'entity' or not entity_uri.segments:
        return []
    found = []
    for group in list_groups(entity_uri.segments[0]):
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

def _set_members(group_uri: Uri, members: list[str], starts: dict[str, int]):
    own = api.config.get_own_properties(group_uri) or {}
    own['members'] = members
    # A Multi that never had a shared timeline is not given an empty one.
    if starts or 'timeline_starts' in own:
        own['timeline_starts'] = starts
    api.config.set_own_properties(group_uri, own)

def _moved(entity_uri: Uri, new_uri: Uri, raw: str) -> str:
    member = Uri.parse(raw)
    if not _is_or_under(entity_uri, member):
        return raw
    depth = len(entity_uri.segments)
    return str(Uri(member.purpose, [*new_uri.segments, *member.segments[depth:]]))

def drop_member_everywhere(entity_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """Take ``entity_uri`` (and anything under it) out of every Multi, and
    off its shared timeline. Returns what each changed Multi held before,
    for :func:`restore_members`."""
    before = _members_of(entity_uri)
    for group_uri, members, starts in before:
        _set_members(
            group_uri,
            [m for m in members if not _is_or_under(entity_uri, Uri.parse(m))],
            {m: s for m, s in starts.items() if not _is_or_under(entity_uri, Uri.parse(m))},
        )
    return before

def rename_member_everywhere(entity_uri: Uri, new_uri: Uri) -> list[tuple[Uri, list[str], dict[str, int]]]:
    """Rewrite ``entity_uri`` (and anything under it) to ``new_uri`` in
    every Multi's member list, keeping its place, and its place on the
    shared timeline. Returns what each changed Multi held before, for
    :func:`restore_members`."""
    before = _members_of(entity_uri)
    for group_uri, members, starts in before:
        _set_members(
            group_uri,
            [_moved(entity_uri, new_uri, m) for m in members],
            {_moved(entity_uri, new_uri, m): s for m, s in starts.items()},
        )
    return before

def restore_members(saved: list[tuple[Uri, list[str], dict[str, int]]]):
    """Put back the member lists and timeline starts
    :func:`drop_member_everywhere` or :func:`rename_member_everywhere`
    changed."""
    for group_uri, members, starts in saved:
        _set_members(group_uri, members, starts)
