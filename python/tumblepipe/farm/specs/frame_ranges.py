"""Which frames a render renders: Farm Submit's Frame List (#84).

Remy asked to render only some stretches of a shot: a space separated list
of ranges and single frames, ``1001-1030 1040-1055 1100-1200``. Left empty,
a render renders its First..Last as it always did.

:func:`parse` is the one reading of that text. A word is a frame (``1100``)
or a range (``1001-1030``); anything else, or a range that runs backwards,
is refused (:class:`FrameListError`), never guessed at: a wrong guess renders
the wrong frames. :func:`normalise` sorts the ranges and merges the ones that
overlap or touch, so ``1001-1030 1031-1040`` is the one range 1001-1040.

One range after normalising is an ordinary render: the batch's First and
Last become that range, and it renders, encodes and publishes exactly as
before. Two or more are a *multi-range* render (:func:`is_multi`):

- the render and denoise jobs run in blocks (:func:`blocks`) that never
  span a gap, so no frame between two ranges is rendered;
- the slapcomp, and the mp4s, take the listed frames only; the mp4 plays
  them back to back with each frame's number burnt in, so the jumps show;
- it is *not published*, like a preview: the frames stay in their render
  version on disk, to be viewed, but the version is never the shot's
  render (it is not complete over its span, ``pipe.paths.render``), no
  daily is written, nothing is copied to the edit and no comp follows it.

The farm configs carry the normalised ranges, not the text, as the optional
``frame_ranges`` (``[[first, last], ...]``, at least two); :func:`valid` is
what a spec checks them against.

Pure: no ``hou``, no Qt, no config reads, no pipeline imports, so the
dialog's settings module, the workers and the tests load it directly.
"""

from __future__ import annotations

import re

#: The farm config key that carries the normalised ranges.
CONFIG_KEY = 'frame_ranges'

#: The render job context key that carries its blocks (``worker.stepped``).
BLOCKS_KEY = 'blocks'

#: What the field shows while empty.
PLACEHOLDER = 'First → Last'

#: Said wherever a multi-range render is set up (the Farm Submit field, its
#: Warnings column, the submit log).
NOT_PUBLISHED = 'multi-range renders are not published'

_SEPARATORS = re.compile(r'[\s,]+')
_WORD = re.compile(r'(\d+)(?:-(\d+))?')

Range = tuple[int, int]


class FrameListError(ValueError):
    """A Frame List that cannot be read, or that does not fit its shot."""


def parse(text: str | None) -> tuple[Range, ...]:
    """The ranges ``text`` names, in the order typed (not normalised).

    Words are separated by spaces (commas are taken too). A word is a
    frame ``N`` or a range ``A-B`` with ``A <= B``, frames being whole
    numbers from 0. Blank text names nothing. Raises :class:`FrameListError`
    naming every word that is neither.
    """
    if text is None or not text.strip():
        return ()
    ranges: list[Range] = []
    bad: list[str] = []
    backwards: list[str] = []
    for word in _SEPARATORS.split(text.strip()):
        if not word:
            continue
        match = _WORD.fullmatch(word)
        if match is None or not word.isascii():
            bad.append(word)
            continue
        first = int(match.group(1))
        last = first if match.group(2) is None else int(match.group(2))
        if first > last:
            backwards.append(word)
            continue
        ranges.append((first, last))
    problems = []
    if bad:
        shown = ', '.join(repr(word) for word in bad[:6])
        problems.append(
            f"{shown} {'are' if len(bad) > 1 else 'is'} not a frame or a "
            "range (write 1001-1030 or 1100)"
        )
    if backwards:
        shown = ', '.join(backwards[:6])
        problems.append(f"{shown} {'run' if len(backwards) > 1 else 'runs'} backwards")
    if problems:
        raise FrameListError('frame list: ' + '; '.join(problems))
    return tuple(ranges)


def normalise(ranges) -> tuple[Range, ...]:
    """``ranges`` sorted, with the ones that overlap or touch merged."""
    merged: list[list[int]] = []
    for first, last in sorted((int(f), int(l)) for f, l in ranges):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return tuple((first, last) for first, last in merged)


def format_ranges(ranges) -> str:
    """``ranges`` as Frame List text: ``1001-1030 1100``."""
    return ' '.join(
        str(first) if first == last else f'{first}-{last}'
        for first, last in ranges
    )


def is_multi(ranges) -> bool:
    """Whether normalised ``ranges`` leave a gap: a multi-range render."""
    return len(ranges) > 1


def span(ranges) -> Range:
    """The first and last frame of non-empty normalised ``ranges``."""
    return ranges[0][0], ranges[-1][1]


def frames(ranges, step: int = 1) -> list[int]:
    """Every frame ``ranges`` render, in order: each range by ``step`` from
    its own first frame."""
    step = max(1, step)
    return [
        frame
        for first, last in ranges
        for frame in range(first, last + 1, step)
    ]


def of_frames(frames) -> tuple[Range, ...]:
    """The normalised ranges exactly ``frames`` make: each run of
    consecutive frames one range (a stepped render's frames are each their
    own). Inverse of :func:`frames` with step 1."""
    return normalise((frame, frame) for frame in set(frames))


def frame_count(ranges, step: int = 1) -> int:
    step = max(1, step)
    return sum((last - first) // step + 1 for first, last in ranges)


def summary(ranges, step: int = 1) -> str:
    """``75 frames in 3 ranges`` (``1 frame`` for one)."""
    count = frame_count(ranges, step)
    text = f"{count} frame{'s' if count != 1 else ''}"
    if len(ranges) > 1:
        text += f' in {len(ranges)} ranges'
    return text


def outside(ranges, first_frame: int, last_frame: int) -> tuple[Range, ...]:
    """The ranges that reach past ``first_frame``..``last_frame``."""
    return tuple(
        (first, last) for first, last in ranges
        if first < first_frame or last > last_frame
    )


def read(text: str | None, first_frame: int, last_frame: int) -> tuple[Range, ...]:
    """The normalised ranges of ``text`` for a render of ``first_frame``..
    ``last_frame`` (its rolls included); empty for blank text. Raises
    :class:`FrameListError` when the text cannot be read or a range reaches
    past the shot."""
    ranges = normalise(parse(text))
    beyond = outside(ranges, first_frame, last_frame)
    if beyond:
        raise FrameListError(
            f"frame list: {format_ranges(beyond)} "
            f"{'are' if len(beyond) > 1 else 'is'} outside the shot's "
            f"{first_frame}-{last_frame}"
        )
    return ranges


def blocks(ranges, batch_size: int, step: int = 1) -> list[Range]:
    """The farm tasks of a render of ``ranges``: each range cut into blocks
    of ``batch_size`` frames on ``step``, so that no block spans a gap.

    For one range these are the tasks the farm makes of ``first-last x
    step`` in chunks of ``batch_size``: each block's first and last frame.
    """
    batch_size = max(1, batch_size)
    result: list[Range] = []
    for first, last in ranges:
        stepped = list(range(first, last + 1, max(1, step)))
        for index in range(0, len(stepped), batch_size):
            chunk = stepped[index:index + batch_size]
            result.append((chunk[0], chunk[-1]))
    return result


def block_at(block_list, first_frame: int) -> Range | None:
    """The block of ``block_list`` that starts at ``first_frame``, None
    when none does."""
    for first, last in block_list:
        if first == first_frame:
            return first, last
    return None


def _is_pair(value) -> bool:
    return (
        isinstance(value, list) and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and value[0] <= value[1]
    )


def valid_blocks(value) -> bool:
    """A render context's ``blocks``: a non-empty list of ``[first, last]``
    with distinct first frames."""
    if not isinstance(value, list) or not value: return False
    if not all(_is_pair(block) for block in value): return False
    starts = [block[0] for block in value]
    return len(set(starts)) == len(starts)


def valid(value, first_frame: int, last_frame: int) -> bool:
    """Whether ``value`` is what a farm config may carry as ``frame_ranges``
    for ``first_frame``..``last_frame``: at least two ``[first, last]``
    pairs, normalised (sorted, a gap between each two), spanning exactly
    the config's range."""
    if not isinstance(value, list) or len(value) < 2: return False
    if not all(_is_pair(pair) for pair in value): return False
    for before, after in zip(value, value[1:]):
        if after[0] <= before[1] + 1: return False
    return value[0][0] == first_frame and value[-1][1] == last_frame


def valid_entry(config: dict, explicit_frames: bool = False) -> bool:
    """An optional ``frame_ranges`` of ``config``, inside its first_frame..
    last_frame (:func:`valid`). With ``explicit_frames`` the config also
    has a ``frames`` list (render, denoise), which a multi-range one leaves
    empty: its frames are the ranges'."""
    if CONFIG_KEY not in config: return True
    if explicit_frames and config.get('frames'): return False
    return valid(config[CONFIG_KEY], config['first_frame'], config['last_frame'])


def valid_context_entry(context: dict) -> bool:
    """An optional ``blocks`` of a worker context (:func:`valid_blocks`)."""
    if BLOCKS_KEY not in context: return True
    return valid_blocks(context[BLOCKS_KEY])


def config_entry(ranges) -> dict:
    """``{CONFIG_KEY: [[first, last], ...]}`` for a multi-range render, or
    ``{}`` for one range or none (the key is optional: without it a render
    renders its First..Last, as every config before it)."""
    if not is_multi(ranges):
        return {}
    return {CONFIG_KEY: [[first, last] for first, last in ranges]}


def context_entry(config: dict) -> dict:
    """The ``blocks`` a batched job's worker context carries for a
    multi-range ``config`` (render, denoise), ``{}`` for an ordinary one."""
    ranges = from_config(config)
    if ranges is None:
        return {}
    return {BLOCKS_KEY: [
        [first, last]
        for first, last in blocks(ranges, config['batch_size'], config['step_size'])
    ]}


def from_config(block: dict) -> tuple[Range, ...] | None:
    """The ranges a config carries, None for an ordinary render."""
    value = block.get(CONFIG_KEY)
    if not value:
        return None
    return tuple((first, last) for first, last in value)
