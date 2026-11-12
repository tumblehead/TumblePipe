"""A task's frame range for one submit (#76).

The publish dialog shows each task's First and Last frame: the range its
node (or, for a Build USD row, the entity's first export node) would export
or build, rolls included. The artist may change them for this submit only,
for a preview or an alternative export. The edit lives on the task
(``ProcessTask.range_override``), never on the node: the node's Frame Range
parms stay as they are, and the next publish window shows the node's range
again.

Rules:

- An edit lives on the leaf rows that run: an export or shared-layer export,
  or a Build USD channel. Editing a group row (``Export (render)``,
  ``Build USD``) edits each of its children; the group shows its children's
  range, or ``VARIES`` when they differ.
- Editing First keeps each row's Last, and the other way round. A row whose
  First would land after its Last refuses the edit (nothing changes).
- A row edited back to its node's range is no longer edited.
- A Build USD row follows its entity's exports (``range_follows``): when one
  of them is edited and the build row is not, it builds the span of every
  export row of the entity (lowest First to highest Last), so a preview
  export of frames 1001-1010 stages 1001-1010. Edit the build row itself to
  break away; reset it to follow again.
- Validation and rig rows carry no range that a run would use, so they are
  not editable.

The module is pure (no Qt, no hou): tasks are read by duck typing, so the
property tests drive it with ``ProcessTask`` directly.
"""

from __future__ import annotations

#: What a group row shows when its children's ranges differ.
VARIES = 'varies'

FIRST = 'first'
LAST = 'last'

# The rows whose range a run uses: the export nodes' and the build's.
_LEAF_TYPES = frozenset({'export', 'export_shared', 'build'})
_GROUP_TYPES = frozenset({'export_group', 'build_group'})
EDITABLE_TYPES = _LEAF_TYPES | _GROUP_TYPES


def node_range(task) -> tuple[int, int] | None:
    """The range the task's node gives (what the dialog opened with)."""
    if task.first_frame is None or task.last_frame is None:
        return None
    return (task.first_frame, task.last_frame)


def is_edited(task) -> bool:
    """Whether the artist changed this row's range for this submit."""
    return getattr(task, 'range_override', None) is not None


def _range_leaves(task) -> list:
    """The rows an edit of ``task`` lands on: its children that carry a
    range, or ``task`` itself."""
    if task.children:
        return [
            child for child in task.children
            if child.task_type in _LEAF_TYPES and node_range(child) is not None
        ]
    if task.task_type in _LEAF_TYPES and node_range(task) is not None:
        return [task]
    return []


def follows_edit(task) -> bool:
    """Whether an unedited build row takes its range from an edited export."""
    if is_edited(task):
        return False
    return any(is_edited(source) for source in getattr(task, 'range_follows', ()))


def effective_range(task) -> tuple[int, int] | None:
    """The range a leaf row runs over this submit: its edit, else the span
    of the exports it follows when one of those is edited, else its node's."""
    if is_edited(task):
        return task.range_override
    if follows_edit(task):
        spans = [
            effective_range(source) for source in task.range_follows
        ]
        spans = [span for span in spans if span is not None]
        if spans:
            return (min(f for f, _ in spans), max(l for _, l in spans))
    return node_range(task)


def display_range(task):
    """What the row's First/Last cells show: a ``(first, last)`` pair, None
    (no range), or ``VARIES`` for a group whose children differ."""
    if not task.children:
        return effective_range(task)
    ranges = {effective_range(child) for child in _range_leaves(task)}
    ranges.discard(None)
    if not ranges:
        return node_range(task)
    if len(ranges) == 1:
        return next(iter(ranges))
    return VARIES


def display_bound(task, bound: str):
    """What one cell shows: the row's First (``bound`` FIRST) or Last, None
    (no range), or ``VARIES`` when a group's children differ on it (a group
    whose children share a First but not a Last shows the First)."""
    index = 0 if bound == FIRST else 1
    if not task.children:
        span = effective_range(task)
        return None if span is None else span[index]
    values = {effective_range(child) for child in _range_leaves(task)}
    values.discard(None)
    if not values:
        span = node_range(task)
        return None if span is None else span[index]
    frames = {span[index] for span in values}
    return next(iter(frames)) if len(frames) == 1 else VARIES


def is_range_editable(task) -> bool:
    """Whether the row's First/Last cells can be edited."""
    return task.task_type in EDITABLE_TYPES and bool(_range_leaves(task))


def row_is_edited(task) -> bool:
    """Whether the row reads as changed: it, or one of its children, was
    edited, or it follows an edit."""
    return any(
        is_edited(leaf) or follows_edit(leaf) for leaf in _range_leaves(task)
    )


def parse_frame(value) -> int:
    """A frame number from a cell's value; ValueError for anything else
    (text, a fraction, a bool)."""
    if isinstance(value, bool):
        raise ValueError(f'not a frame number: {value!r}')
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f'not a whole frame: {value!r}')
        return int(value)
    text = str(value).strip()
    if text.startswith(('+', '-')):
        sign, digits = text[0], text[1:]
    else:
        sign, digits = '', text
    if not digits.isdigit() or not digits.isascii():
        raise ValueError(f'not a frame number: {value!r}')
    return int(sign + digits)


def _set_leaf(leaf, first: int, last: int) -> None:
    """Edit one leaf; an edit to what it runs unedited (its node's range,
    or the exports' span it follows) is no edit."""
    leaf.range_override = None
    if (first, last) != effective_range(leaf):
        leaf.range_override = (first, last)


def set_bound(task, bound: str, value) -> None:
    """Set the First (``bound`` FIRST) or Last frame of ``task`` for this
    submit. Each leaf keeps its other bound. Raises ValueError, changing
    nothing, when the value is not a frame or a leaf would end before it
    starts; or when the row has no range to edit."""
    if bound not in (FIRST, LAST):
        raise ValueError(f'unknown bound: {bound!r}')
    frame = parse_frame(value)
    leaves = _range_leaves(task)
    if task.task_type not in EDITABLE_TYPES or not leaves:
        raise ValueError(f'{task.description} has no frame range to edit')
    planned = []
    for leaf in leaves:
        first, last = effective_range(leaf)
        if bound == FIRST:
            first = frame
        else:
            last = frame
        if first > last:
            raise ValueError(
                f'First frame {first} is after the Last frame {last}'
            )
        planned.append((leaf, first, last))
    for leaf, first, last in planned:
        _set_leaf(leaf, first, last)


def set_range(task, first, last) -> None:
    """Set both bounds of ``task`` (each leaf) for this submit; ValueError,
    changing nothing, for a bad value or First after Last."""
    first, last = parse_frame(first), parse_frame(last)
    if first > last:
        raise ValueError(f'First frame {first} is after the Last frame {last}')
    leaves = _range_leaves(task)
    if task.task_type not in EDITABLE_TYPES or not leaves:
        raise ValueError(f'{task.description} has no frame range to edit')
    for leaf in leaves:
        _set_leaf(leaf, first, last)


def reset_range(task) -> None:
    """Back to the node's range: ``task`` and its children lose their edit
    (a build row follows its exports again)."""
    for leaf in _range_leaves(task):
        leaf.range_override = None


def has_edits(tasks) -> bool:
    """Whether any task (or child) of ``tasks`` has an edited range."""
    return any(
        is_edited(leaf)
        for task in tasks
        for leaf in (task.children or [task])
    )


def run_range(task) -> tuple[int, int] | None:
    """The range to hand an export node's run: the edited (or followed)
    range, or None to let the node read its own Frame Range parms, as it
    always has."""
    if is_edited(task) or follows_edit(task):
        return effective_range(task)
    return None


def block_range(span):
    """``span`` as the ``BlockRange`` the export and build code take, or
    None for None."""
    if span is None:
        return None
    from tumblepipe.config.timeline import BlockRange
    return BlockRange(span[0], span[1])
