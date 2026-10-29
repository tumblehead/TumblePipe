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
    placed = slots(group)
    if not placed:
        return None
    first = min(placed, key=lambda slot: slot.shared_range.start_frame)
    last = max(placed, key=lambda slot: slot.shared_range.end_frame)
    first_range = first.shared_range
    last_range = last.shared_range
    first_frame = min(
        slot.shared_range.full_range().first_frame for slot in placed
    )
    last_frame = max(
        slot.shared_range.full_range().last_frame for slot in placed
    )
    return FrameRange(
        first_range.start_frame,
        last_range.end_frame,
        first_range.start_frame - first_frame,
        last_frame - last_range.end_frame,
    )


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
