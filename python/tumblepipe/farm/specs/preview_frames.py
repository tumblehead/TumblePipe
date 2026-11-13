"""Which frames a preview renders first: the Preview Frames List (#74).

Every place that offers Preview Frames (Farm Submit's Render and Comp
settings, th::export_comp's Jobs) has a text field beside the toggle. Left
empty, the preview is the first, middle and last frame, as it always was.
Otherwise it is a list of frames, separated by spaces (commas are taken
too), rendered first and in the order given: key poses, say. The rest of
the range follows as before.

:func:`parse` is the one reading of that text, for every caller. It takes
whole frame numbers only, collapses repeats (the first one stays where it
is), and drops a frame outside the range or off the step, each drop named
in a warning the caller shows (the Farm Submit Warnings column and submit
log, the node's warning). When nothing usable is left, the frames are empty
and a warning says the preview falls back to first, middle and last.

The farm configs carry the parsed frames, not the text, as the optional
``preview_frame_list`` (``specs.render_pipeline``, ``specs.composite``):
:func:`valid_frame_list` is what a spec checks them against.

Pure: no ``hou``, no Qt, no config reads, no pipeline imports, so the
dialog's settings module and the tests load it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The farm config key that carries the parsed frames (a list of ints).
CONFIG_KEY = 'preview_frame_list'

#: What the field shows while empty.
PLACEHOLDER = 'first / middle / last'

#: What the fallback is called in a warning.
FALLBACK_TEXT = 'first, middle and last'

_SEPARATORS = re.compile(r'[\s,]+')
_INTEGER = re.compile(r'[+-]?\d+')


@dataclass(frozen=True)
class PreviewFrames:
    """What a Preview Frames List asks for.

    ``frames`` in the order to render them; empty means the default first,
    middle and last frame. ``warnings`` name everything that was dropped,
    one line each, for the caller to show.
    """
    frames: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_default(self) -> bool:
        return not self.frames


def _frames_text(frames) -> str:
    shown = ' '.join(str(frame) for frame in frames[:6])
    if len(frames) > 6:
        shown += f' (+{len(frames) - 6} more)'
    return shown


def parse(text: str | None, first_frame: int, last_frame: int, step: int = 1) -> PreviewFrames:
    """The preview frames ``text`` asks for, in a ``first_frame``..
    ``last_frame`` range every ``step`` frames (counted from the first).

    Empty or blank text is the default with no warning. A word that is not a
    whole number, a frame outside the range and a frame off the step are
    dropped, each kind with one warning; repeats collapse silently, the
    first kept. With text but no frame left, the frames are empty (the
    default) and a warning says so.
    """
    if text is None or not text.strip():
        return PreviewFrames()
    step = max(1, step)
    not_numbers: list[str] = []
    outside: list[int] = []
    off_step: list[int] = []
    frames: list[int] = []
    for word in _SEPARATORS.split(text.strip()):
        if not word:
            continue
        if not _INTEGER.fullmatch(word):
            not_numbers.append(word)
            continue
        frame = int(word)
        if frame in frames:
            continue
        if frame < first_frame or frame > last_frame:
            if frame not in outside:
                outside.append(frame)
            continue
        if (frame - first_frame) % step != 0:
            if frame not in off_step:
                off_step.append(frame)
            continue
        frames.append(frame)
    warnings: list[str] = []
    if not_numbers:
        plural = len(not_numbers) > 1
        warnings.append(
            f"preview frames: {', '.join(repr(w) for w in not_numbers[:6])} "
            f"{'are not frame numbers' if plural else 'is not a frame number'}, "
            "dropped"
        )
    if outside:
        warnings.append(
            f"preview frames: {_frames_text(outside)} outside "
            f"{first_frame}-{last_frame}, dropped"
        )
    if off_step:
        warnings.append(
            f"preview frames: {_frames_text(off_step)} not on step {step} "
            f"from {first_frame}, dropped"
        )
    if not frames:
        warnings.append(
            f"preview frames: none usable, previewing {FALLBACK_TEXT}"
        )
    return PreviewFrames(tuple(frames), tuple(warnings))


def format_frames(frames) -> str:
    """``frames`` as Preview Frames List text, in their order: ``1012 1040``.
    :func:`parse` reads it back to the same frames (those in range)."""
    return ' '.join(str(int(frame)) for frame in frames)


def inside_ranges(preview: PreviewFrames, ranges) -> PreviewFrames:
    """``preview`` (parsed against the span of a multi-range render, #84)
    without the frames that fall between its ``ranges``, which are never
    rendered; the drop is named in a warning, as :func:`parse` names its."""
    if preview.is_default:
        return preview
    kept = tuple(
        frame for frame in preview.frames
        if any(first <= frame <= last for first, last in ranges)
    )
    gaps = [frame for frame in preview.frames if frame not in kept]
    if not gaps:
        return preview
    warnings = list(preview.warnings) + [
        f"preview frames: {_frames_text(gaps)} between the frame list's "
        "ranges, dropped"
    ]
    if not kept:
        warnings.append(f"preview frames: none usable, previewing {FALLBACK_TEXT}")
    return PreviewFrames(kept, tuple(warnings))


def valid_frame_list(frames, first_frame: int, last_frame: int, step: int = 1) -> bool:
    """Whether ``frames`` is what :func:`parse` can hand a farm config: a
    non-empty list of distinct ints (not bools), each inside the range and
    on the step."""
    if not isinstance(frames, list) or not frames:
        return False
    step = max(1, step)
    for frame in frames:
        if not isinstance(frame, int) or isinstance(frame, bool):
            return False
        if frame < first_frame or frame > last_frame:
            return False
        if (frame - first_frame) % step != 0:
            return False
    return len(set(frames)) == len(frames)


def config_entry(preview: PreviewFrames) -> dict:
    """``{CONFIG_KEY: [frames]}`` for a farm config, or ``{}`` for the
    default (the key is optional: without it a preview is first, middle and
    last, as every config before it)."""
    return {} if preview.is_default else {CONFIG_KEY: list(preview.frames)}
