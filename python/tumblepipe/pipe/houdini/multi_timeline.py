"""A shared-timeline Multi in the session: its playbar and bookmarks.

``config.multi_timeline`` says where each member shot sits on a Multi's
shared timeline. Here the open workfile is read against it: which Multi the
scene is (none, for any other workfile), the time offset a node working on a
member uses, and the session's playbar, set to span every member, with a
bookmark per member when the Multi asks for them.

The bookmarks are Houdini's own playbar bookmarks, so a double-click on one
zooms the playbar to that shot and the next double-click zooms back out.
They are made *temporary* (Houdini does not save them in the hip) and remade
from the configuration whenever the workfile opens or the Multi's timeline
changes: the configuration owns where a shot sits, and a bookmark dragged on
the playbar must not read as a shot that moved.
"""

import hou

from tumblepipe.config import groups as groups_mod
from tumblepipe.config import multi_timeline
from tumblepipe.pipe.houdini import playbar
from tumblepipe.pipe.houdini.scene_context import scene_context
from tumblepipe.util.uri import Uri

#: The bookmark metadata key naming the member a pipeline bookmark is for.
#: Bookmarks without it are the artist's and are never touched.
MEMBER_KEY = 'tumblepipe_member'

# Alternating, so neighbouring shots read apart on the playbar.
_COLORS = (hou.Color((0.85, 0.55, 0.2)), hou.Color((0.3, 0.6, 0.85)))


def scene_group() -> groups_mod.Group | None:
    """The Multi whose workfile is open; None for any other scene."""
    context = scene_context()
    if context is None:
        return None
    if not groups_mod.is_group_uri(context.entity_uri):
        return None
    return groups_mod.get_group(context.entity_uri)


def scene_time_offset(member_uri: Uri) -> int:
    """How far ``member_uri`` is moved on the open Multi's shared timeline;
    0 outside a shared-timeline Multi."""
    return multi_timeline.time_offset(scene_group(), member_uri)


def _pipeline_bookmarks() -> list:
    return [
        bookmark for bookmark in hou.anim.bookmarks()
        if bookmark.metadata(MEMBER_KEY, None)
    ]


def sync_bookmarks(group: groups_mod.Group | None) -> int:
    """Replace the pipeline's bookmarks with one per placed member of
    ``group`` when it asks for them; the number made.

    Without a shared timeline or with bookmarks off this removes the
    pipeline's bookmarks and makes none.
    """
    stale = _pipeline_bookmarks()
    if stale:
        hou.anim.removeBookmarks(tuple(stale))
    if group is None or not group.timeline_bookmarks:
        return 0
    slots = multi_timeline.slots(group)
    for index, slot in enumerate(slots):
        shared = slot.shared_range
        name = '_'.join(slot.member.segments[1:]) or str(slot.member)
        bookmark = hou.anim.newBookmark(
            name, shared.start_frame, shared.end_frame
        )
        bookmark.setColor(_COLORS[index % len(_COLORS)])
        bookmark.setComment(
            f'{slot.member}: frames {slot.frame_range.start_frame}-'
            f'{slot.frame_range.end_frame} at {shared.start_frame}-'
            f'{shared.end_frame} (offset {slot.offset:+d})'
        )
        bookmark.setMetadata(MEMBER_KEY, str(slot.member))
        bookmark.setTemporary(True)
    return len(slots)


def apply(group: groups_mod.Group | None = None) -> bool:
    """Lay the session out for the open Multi's shared timeline: the playbar
    spans every member, and the bookmarks follow the Multi's setting.
    Whether the scene is a shared-timeline Multi.

    ``group`` defaults to the open scene's. Any other scene only loses the
    pipeline's bookmarks (a Multi's, left over from the last workfile).
    """
    if group is None:
        group = scene_group()
    span = (
        multi_timeline.shared_span(group)
        if group is not None and group.shared_timeline else None
    )
    sync_bookmarks(group if span is not None else None)
    if span is None:
        return False
    playbar.set_frame_range(span, preserve_current_frame=True)
    return True


def apply_if_open(group_uri: Uri) -> bool:
    """:func:`apply` when ``group_uri``'s workfile is the open scene, after
    its timeline was edited; whether it was."""
    group = scene_group()
    if group is None or group.uri != group_uri:
        return False
    apply(group)
    return True
