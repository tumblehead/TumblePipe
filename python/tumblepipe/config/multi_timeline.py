"""A Multi's shared timeline: its member shots end to end on one timeline.

A shots Multi normally gives every member its own frames: the Multi's
workfile holds one graph per member, each running over that shot's range
(all of them starting at 1001, say). With **shared timeline** on, the
members sit one after another on a single timeline instead, so one graph
can run through all of them: a simulation that carries on from shot to shot,
a crowd that keeps walking. Each member has a *start*, the shared-timeline
frame its own first frame lands on, and so a *time offset*::

    offset = start - the member's own start frame
    shared frame = own frame + offset

The import nodes move each member's published layers forward by its offset,
so the graph sees every shot where it sits on the shared timeline, and the
export nodes cook the shared frames and write them back on the member's own
frames, so what is published is a normal shot layer. Nothing downstream of
the Multi knows the shared timeline existed.

The starts are **stored** (``timeline_starts`` on the group), not derived
from the member order and lengths: when a shot's length changes, the shots
after it keep their frames, because moving them would move animation the
artists already made against them. A member is placed once, when it joins a
shared-timeline Multi or when shared timeline is switched on, after the last
frame any placed member reaches. :func:`pack` lays everything out afresh
when that is what someone wants.

``timeline_bookmarks`` adds a playbar bookmark per member in the Multi's
workfile (``pipe.houdini.multi_timeline``); Houdini zooms the playbar to a
bookmark on a double-click and back out on the next.
"""

from dataclasses import dataclass

from tumblepipe.config import groups as groups_mod
from tumblepipe.config.timeline import BlockRange, FrameRange, get_frame_range
from tumblepipe.util.uri import Uri

#: A shots Multi's **Timeline** as the Entity Editor offers it, and the
#: (shared timeline, bookmarks) settings each choice stands for. Bookmarks
#: come only with a shared timeline: on separate timelines every shot's
#: bookmark would cover the same frames.
_TIMELINE_SETTINGS = {
    'Per shot': (False, False),
    'Shared': (True, False),
    'Shared, with bookmarks': (True, True),
}
TIMELINE_CHOICES = tuple(_TIMELINE_SETTINGS)


def timeline_choice(shared: bool, bookmarks: bool) -> str:
    """The Timeline choice for a Multi's settings."""
    if not shared:
        return 'Per shot'
    return 'Shared, with bookmarks' if bookmarks else 'Shared'


def timeline_settings(choice: str) -> tuple[bool, bool]:
    """``(shared timeline, bookmarks)`` for a Timeline choice."""
    return _TIMELINE_SETTINGS[choice]


@dataclass(frozen=True)
class Slot:
    """Where one member sits on the shared timeline."""
    member: Uri
    start: int
    frame_range: FrameRange

    @property
    def offset(self) -> int:
        return self.start - self.frame_range.start_frame

    @property
    def shared_range(self) -> FrameRange:
        """The member's range, rolls included, in shared-timeline frames."""
        return shift_range(self.frame_range, self.offset)


def shift_block_range(block_range: BlockRange, offset: int) -> BlockRange:
    """``block_range`` moved by ``offset`` frames: the frames an export of a
    shot on a shared timeline cooks."""
    return BlockRange(
        block_range.first_frame + offset,
        block_range.last_frame + offset,
        block_range.step_size,
    )


def shift_range(frame_range: FrameRange, offset: int) -> FrameRange:
    """``frame_range`` moved by ``offset`` frames, rolls and step kept."""
    return FrameRange(
        frame_range.start_frame + offset,
        frame_range.end_frame + offset,
        frame_range.start_roll,
        frame_range.end_roll,
        frame_range.step_size,
    )


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


def _member_ranges(members: list[Uri]) -> dict[str, FrameRange | None]:
    return {str(member): get_frame_range(member) for member in members}


def slots(group: groups_mod.Group) -> list[Slot]:
    """The placed members of a shared-timeline Multi, earliest first.

    Empty when shared timeline is off: then every member keeps its own
    frames and there is nothing to lay out.
    """
    if not group.shared_timeline:
        return []
    result = []
    for member in group.members:
        start = group.timeline_starts.get(str(member))
        if start is None:
            continue
        frame_range = get_frame_range(member)
        if frame_range is None:
            continue
        result.append(Slot(member, start, frame_range))
    return sorted(result, key=lambda slot: (slot.start, str(slot.member)))


def play_order(members: list[Uri], shared: bool, starts: dict[str, int]) -> list[Uri]:
    """The order a Multi's members play in (#26): on a shared timeline the
    placed members by where they sit (``starts``, member URI string ->
    frame), then any not placed, in member order; without one, member
    order. Every member plays exactly once."""
    if not shared:
        return list(members)
    placed = sorted(
        (m for m in members if str(m) in starts),
        key=lambda m: (starts[str(m)], str(m)),
    )
    return placed + [m for m in members if str(m) not in starts]


def time_offset(group: groups_mod.Group | None, member_uri: Uri) -> int:
    """How far ``member_uri`` is moved on ``group``'s shared timeline.

    0 when there is no group, shared timeline is off, or the member is not
    placed (not a member, or no frame range authored).
    """
    if group is None:
        return 0
    for slot in slots(group):
        if slot.member == member_uri:
            return slot.offset
    return 0


def shared_span(group: groups_mod.Group) -> FrameRange | None:
    """The shared timeline from the first member's first frame to the last
    member's last frame, the outermost rolls included. None when nothing is
    placed."""
    return span_of([slot.shared_range for slot in slots(group)])


def span_of(ranges) -> FrameRange | None:
    """The range from the earliest start to the latest end of ``ranges``
    (None entries skipped), the outermost rolls included. None when there
    is none."""
    ranges = [frame_range for frame_range in ranges if frame_range is not None]
    if not ranges:
        return None
    first_range = min(ranges, key=lambda frame_range: frame_range.start_frame)
    last_range = max(ranges, key=lambda frame_range: frame_range.end_frame)
    first_frame = min(r.full_range().first_frame for r in ranges)
    last_frame = max(r.full_range().last_frame for r in ranges)
    return FrameRange(
        first_range.start_frame,
        last_range.end_frame,
        first_range.start_frame - first_frame,
        last_frame - last_range.end_frame,
    )


def creation_span(group: groups_mod.Group) -> FrameRange | None:
    """The playbar a new workfile of ``group`` starts with (#39): the shared
    timeline when it has one, else the span of the members' own ranges (an
    assets Multi's members all at 1001-1100 open at 1001-1100, not at
    Houdini's 1-240). None when no member has a range."""
    if group.shared_timeline:
        span = shared_span(group)
        if span is not None:
            return span
    return span_of(get_frame_range(member) for member in group.members)


def set_timeline(group_uri: Uri, shared: bool, bookmarks: bool):
    """Switch the Multi's shared timeline and bookmarks.

    Switching shared timeline on places every member not placed yet;
    switching it off keeps the stored starts, so switching it back on puts
    every shot back where it was.
    """
    group = groups_mod.get_group(group_uri)
    if group is None:
        raise ValueError(f'Multi {group_uri} does not exist')
    starts = group.timeline_starts
    if shared:
        starts = place(starts, group.members, _member_ranges(group.members))
    groups_mod.set_timeline(group_uri, shared, bookmarks, starts)


def place_members(group_uri: Uri):
    """Place the Multi's unplaced members, when its timeline is shared.

    Called after a member joins; a Multi without a shared timeline is left
    alone, and placed when shared timeline is switched on.
    """
    group = groups_mod.get_group(group_uri)
    if group is None or not group.shared_timeline:
        return
    starts = place(
        group.timeline_starts, group.members, _member_ranges(group.members)
    )
    if starts == group.timeline_starts:
        return
    groups_mod.set_timeline(
        group_uri, group.shared_timeline, group.timeline_bookmarks, starts
    )


def repack(group_uri: Uri):
    """Lay every member out afresh, end to end in member order."""
    group = groups_mod.get_group(group_uri)
    if group is None:
        raise ValueError(f'Multi {group_uri} does not exist')
    groups_mod.set_timeline(
        group_uri, group.shared_timeline, group.timeline_bookmarks,
        pack(group.members, _member_ranges(group.members)),
    )


def overlaps(
    starts: dict[str, int], ranges: dict[str, FrameRange | None],
) -> list[tuple[str, str]]:
    """The placed members whose own frames run into a later member's.

    Each pair is ``(earlier, later)`` by start, ``later`` starting on or
    before the last frame ``earlier`` reaches (rolls not counted: the rolls
    of neighbouring shots overlap, as continuous action does). A member
    without a start or a frame range is not on the timeline.
    """
    placed = sorted(
        (start, key) for key, start in starts.items()
        if ranges.get(key) is not None
    )
    found = []
    for i, (start, key) in enumerate(placed):
        frame_range = ranges[key]
        last = start + frame_range.end_frame - frame_range.start_frame
        for later_start, later in placed[i + 1:]:
            if later_start > last:
                break
            found.append((key, later))
    return found


def overlapping(group: groups_mod.Group) -> list[tuple[str, str]]:
    """:func:`overlaps` on a Multi's shared timeline; [] when it is off."""
    if not group.shared_timeline:
        return []
    members = [str(member) for member in group.members]
    starts = {k: v for k, v in group.timeline_starts.items() if k in members}
    return overlaps(starts, _member_ranges(group.members))


def overlapping_multis() -> dict[str, list[tuple[str, str]]]:
    """Every shared-timeline shots Multi whose members overlap: its URI
    string -> the overlapping pairs."""
    found = {}
    for group in groups_mod.list_groups('shots'):
        pairs = overlapping(group)
        if pairs:
            found[str(group.uri)] = pairs
    return found


def new_overlaps(
    before: dict[str, list[tuple[str, str]]],
    after: dict[str, list[tuple[str, str]]],
) -> dict[str, list[tuple[str, str]]]:
    """The Multis of ``after`` with an overlap ``before`` did not have, each
    with every pair it has now. An edit warns about what it caused, not
    about overlaps someone already chose to keep."""
    return {
        key: pairs for key, pairs in after.items()
        if set(pairs) - set(before.get(key, ()))
    }
