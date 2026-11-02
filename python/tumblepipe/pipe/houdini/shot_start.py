"""The frame th::animate holds its shot stage at: the start of its shot.

th::animate reads the shot stage at one frame (its IN_stage_start, a Time
Shift): zero_transforms keeps the layout placement from it, and
th::import_rig's ``place`` starts each rig at its layout placement from it.
That frame was the playbar start, which in a shot's workfile is the shot's
first frame. In a Multi's workfile it is not: the playbar spans every member
(or, without a shared timeline, is whichever range was set), so every member
but the first read its characters' placement at the wrong frame.

In a Multi's workfile the start is the member's own first frame, moved by
its offset on the shared timeline (``time_offset``), the frame its Import
Shot puts that first frame on. Anywhere else it stays the playbar start, as
before.

Nothing here reads the current frame, so an expression calling it does not
turn time dependent.
"""

from __future__ import annotations

ANIMATE_TYPE = 'th::animate'


def animate_node(node):
    """The th::animate ``node`` is, or sits in; None outside one."""
    while node is not None:
        name = node.type().name()
        if name == ANIMATE_TYPE or name.startswith(ANIMATE_TYPE + '::'):
            return node
        node = node.parent()
    return None


def _import_shot_above(node):
    """The first Import Shot up ``node``'s inputs, as an ImportShot; None."""
    from tumblepipe.pipe.houdini.lops.import_shot import ImportShot

    todo, seen = list(node.inputs()), set()
    while todo and len(seen) < 200:
        current = todo.pop(0)
        if current is None or current.path() in seen:
            continue
        seen.add(current.path())
        if 'import_shot' in current.type().name():
            return ImportShot(current)
        todo += [i for i in current.inputs() if i is not None]
    return None


def member_start(import_shot) -> int | None:
    """The first frame of ``import_shot``'s shot where the workfile has it:
    the shot's own start plus its time offset; None when the shot has no
    frame range."""
    from tumblepipe.config.timeline import get_frame_range

    entity_uri = import_shot.get_entity_uri()
    if entity_uri is None:
        return None
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        return None
    return frame_range.start_frame + import_shot.get_time_offset()


def start_frame(node) -> float:
    """The frame the th::animate ``node`` is (or sits in) reads its shot
    stage at: in a Multi's workfile its member's first frame, else the
    playbar start."""
    import hou
    from tumblepipe.pipe.houdini import multi_timeline

    playbar_start = hou.playbar.playbackRange()[0]
    if multi_timeline.scene_group() is None:
        return playbar_start
    animate = animate_node(node)
    if animate is None:
        return playbar_start
    import_shot = _import_shot_above(animate)
    if import_shot is None:
        return playbar_start
    start = member_start(import_shot)
    return playbar_start if start is None else start
