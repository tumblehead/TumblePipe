"""What a frame timeline selects, as the text field beside it holds it.

Farm Submit draws a timeline (``asset_browser.frame_timeline``) under the
Preview frames fields (#74) and under the Render's Frame list (#84), so a
frame or a stretch can be picked with the mouse as well as typed. The text
field stays the one source of truth: the timeline reads the field's text
(:func:`read`), and a gesture on it writes new text (:func:`write`) through
the same parse and format code the submit reads, so the two can never
disagree.

Two kinds of selection, after the two fields:

* :data:`FRAMES` (Preview frames): single frames *in the order given*, read
  by ``farm.specs.preview_frames.parse``. A gesture appends new frames at the
  end and leaves the order of the others alone. Empty is the default
  preview: the first, middle and last frame (:func:`default_frames`).
* :data:`RANGES` (Frame list): ranges, sorted and merged, read by
  ``farm.specs.frame_ranges.parse`` + ``normalise``. Empty is every frame.

A gesture works inside the frames the timeline shows (``lo``..``hi``): what
it adds or removes is clamped to them, and frames the text names outside
them are kept as typed. Text the parser cannot read (a word that is not a
frame) is never rewritten by a gesture: :attr:`Selection.error` says why,
and the timeline ignores the mouse until the text is fixed.

Pure: no ``hou``, no Qt, no config reads, so the tests load it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import preview_frames

#: Single frames in order (Preview frames).
FRAMES = 'frames'
#: Sorted, merged ranges (Frame list).
RANGES = 'ranges'

# Wide enough that preview_frames.parse drops nothing for being outside:
# the text keeps frames beyond the shown frames as typed.
_UNBOUNDED = 10 ** 12

Range = tuple[int, int]


@dataclass(frozen=True)
class Selection:
    """What a field's text selects.

    ``frames`` for :data:`FRAMES` (in order), ``ranges`` for :data:`RANGES`
    (normalised); ``error`` names why the text cannot be read, in which
    case the selection is empty and gestures leave the text alone.
    """
    mode: str
    frames: tuple[int, ...] = ()
    ranges: tuple[Range, ...] = ()
    error: str | None = field(default=None, compare=True)

    @property
    def is_empty(self) -> bool:
        return not self.frames and not self.ranges


@dataclass(frozen=True)
class View:
    """The frames a timeline shows, and what to call them.

    ``caption`` says what the frames are when that is not plain: the one
    range every ticked entity shares, or the union of differing ones.
    """
    lo: int
    hi: int
    caption: str = ''
    mixed: bool = False


def read(text: str | None, mode: str) -> Selection:
    """The selection ``text`` names in ``mode``."""
    if mode == FRAMES:
        parsed = preview_frames.parse(text, -_UNBOUNDED, _UNBOUNDED)
        # The only drops left with an unbounded range are words that are
        # not frame numbers; "none usable" alone (text of only commas) is
        # an empty list, not an error.
        errors = [w for w in parsed.warnings if 'none usable' not in w]
        error = errors[0] if errors else None
        if error is not None:
            return Selection(FRAMES, error=error)
        return Selection(FRAMES, frames=parsed.frames)
    if mode == RANGES:
        try:
            ranges = frame_ranges.normalise(frame_ranges.parse(text))
        except frame_ranges.FrameListError as e:
            return Selection(RANGES, error=str(e))
        return Selection(RANGES, ranges=ranges)
    raise ValueError(f"unknown selection mode {mode!r}")


def write(selection: Selection) -> str:
    """``selection`` as its field's text (the field's own format)."""
    if selection.mode == FRAMES:
        return preview_frames.format_frames(selection.frames)
    return frame_ranges.format_ranges(selection.ranges)


def contains(selection: Selection, frame: int) -> bool:
    if selection.mode == FRAMES:
        return frame in selection.frames
    return any(first <= frame <= last for first, last in selection.ranges)


def _clamp(a: int, b: int, lo: int, hi: int) -> Range | None:
    first, last = min(a, b), max(a, b)
    first, last = max(first, lo), min(last, hi)
    return (first, last) if first <= last else None


def subtract(ranges, first: int, last: int) -> tuple[Range, ...]:
    """Normalised ``ranges`` without ``first``..``last``."""
    out: list[Range] = []
    for a, b in frame_ranges.normalise(ranges):
        if b < first or a > last:
            out.append((a, b))
            continue
        if a < first:
            out.append((a, first - 1))
        if b > last:
            out.append((last + 1, b))
    return tuple(out)


def on_step(frame: int, lo: int, step: int) -> bool:
    return (frame - lo) % max(1, step) == 0


def snap(frame: int, lo: int, hi: int, step: int = 1) -> int:
    """``frame`` clamped to ``lo``..``hi`` and moved to the nearest frame
    on ``step`` counted from ``lo`` (never past ``hi``)."""
    step = max(1, step)
    frame = max(lo, min(hi, frame))
    below = lo + (frame - lo) // step * step
    above = below + step
    if above <= hi and above - frame < frame - below:
        return above
    return below


def add(selection: Selection, a: int, b: int, lo: int, hi: int, step: int = 1) -> Selection:
    """``selection`` with the frames ``a``..``b`` (clamped to ``lo``..``hi``)
    added: for :data:`FRAMES` the ones on ``step`` from ``lo`` not yet
    there, appended in ascending order. Unreadable text is left as it is."""
    if selection.mode == RANGES:
        lo = max(lo, 0)     # a Frame List names frames from 0 only
    span = _clamp(a, b, lo, hi)
    if selection.error is not None or span is None:
        return selection
    if selection.mode == FRAMES:
        have = set(selection.frames)
        new = [
            frame for frame in range(span[0], span[1] + 1)
            if frame not in have and on_step(frame, lo, step)
        ]
        return Selection(FRAMES, frames=selection.frames + tuple(new))
    return Selection(RANGES, ranges=frame_ranges.normalise(selection.ranges + (span,)))


def remove(selection: Selection, a: int, b: int, lo: int, hi: int) -> Selection:
    """``selection`` without the frames ``a``..``b`` (clamped to ``lo``..
    ``hi``). An empty :data:`RANGES` selection is every frame, so removing
    from it leaves the shown frames minus ``a``..``b``."""
    if selection.mode == RANGES:
        lo = max(lo, 0)
    span = _clamp(a, b, lo, hi)
    if selection.error is not None or span is None:
        return selection
    if selection.mode == FRAMES:
        return Selection(FRAMES, frames=tuple(
            frame for frame in selection.frames if not span[0] <= frame <= span[1]
        ))
    ranges = selection.ranges or ((lo, hi),)
    return Selection(RANGES, ranges=subtract(ranges, *span))


def toggle(selection: Selection, frame: int, lo: int, hi: int, step: int = 1) -> Selection:
    """``selection`` with ``frame`` added if it was not selected, removed if
    it was. A frame outside ``lo``..``hi`` (or, for :data:`FRAMES`, off
    ``step``) changes nothing."""
    if contains(selection, frame):
        return remove(selection, frame, frame, lo, hi)
    return add(selection, frame, frame, lo, hi, step)


def default_frames(lo: int, hi: int, step: int = 1) -> tuple[int, ...]:
    """The first, middle and last frame an empty Preview frames list
    previews (``post_render.Shot.check_frames``), distinct, in order."""
    frames = list(range(lo, hi + 1, max(1, step)))
    if not frames:
        return ()
    picks = (frames[0], frames[(len(frames) - 1) // 2], frames[-1])
    return tuple(dict.fromkeys(picks))


def view(spans, noun: str = 'shots') -> View | None:
    """The frames a timeline shows for the ticked entities' ``spans``
    (``(first, last)`` each, None for one with no range): their union, and
    a caption saying so when they differ. None when no entity has a range."""
    known = [(int(a), int(b)) for a, b in (s for s in spans if s is not None) if a <= b]
    if not known:
        return None
    lo = min(a for a, _b in known)
    hi = max(b for _a, b in known)
    distinct = set(known)
    count = len(known)
    if len(distinct) == 1:
        caption = '' if count == 1 else f"all {count} {noun}"
        return View(lo, hi, caption, mixed=False)
    return View(lo, hi, f"union of {count} {noun}' frames", mixed=True)
