"""Holding the nearest rendered frame: what th::import_render's **Missing
Frames** does.

Remy, 2026-11-18: a check render (Farm Submit's **Step**, every Nth frame)
read into a comp was black on the frames between the rendered ones. A
version records the frames it has (``context.json``: first, last and step),
so which frame is nearest is arithmetic, done by the File COP's own frame
expression: nothing lists a directory when a frame cooks.

* :func:`holds`: whether a version's missing frames hold, from the parm
  and whether the version is a check render;
* :func:`held_frame`: the rendered frame a scene frame shows, the nearest
  one, the earlier one when two are as near;
* :func:`hold_expression`: the same as an HScript expression for a File
  COP's file name (``pipe.houdini.cops.import_render``);
* :func:`missing_label`: what the node says it does.

Pure: no Houdini, no disk.
"""

from typing import Iterable, Optional
from tumblepipe.util.text import ordinal

AUTO = 'auto'
EMPTY = 'empty'
HOLD = 'hold'
MODES = (AUTO, EMPTY, HOLD)


def holds(setting: str, check_step: Optional[int]) -> bool:
    """Whether a version's missing frames hold the nearest rendered one.

    ``setting`` is the **Missing Frames** parm; ``check_step`` the version's
    step when it is a check render (``pipe.paths.render.check_step``), None
    for a published one. Automatic (and anything unknown, as an old scene
    may hold) holds a check render only: a published version has every
    frame of its range, and a frame outside it stays empty, as before.
    """
    if setting == HOLD: return True
    if setting == EMPTY: return False
    return check_step is not None


def frames(first: int, last: int, step: int = 1) -> list[int]:
    """The frames a version of ``first`` .. ``last`` on ``step`` has."""
    return list(range(first, last + 1, max(1, step)))


def held_frame(frame: int, first: int, last: int, step: int = 1) -> int:
    """The rendered frame scene frame ``frame`` shows: the nearest of
    :func:`frames`, the earlier one when two are as near; before the first
    the first, after the last the last."""
    step = max(1, step)
    count = (last - first) // step + 1
    # The nearest index, halves down: floor((d + (step - 1) / 2) / step).
    index = (2 * (frame - first) + step - 1) // (2 * step)
    return first + step * min(max(index, 0), count - 1)


def hold_expression(first: int, last: int, step: int = 1, frame: str = '$F') -> str:
    """:func:`held_frame` as an HScript expression of ``frame`` (an HScript
    expression itself, bracketed here)."""
    step = max(1, step)
    count = (last - first) // step + 1
    return (
        f'{first}+{step}*clamp(floor((2*(({frame})-{first})+{step - 1})/{2 * step}),'
        f'0,{count - 1})'
    )


def _check_note(steps: list[int]) -> str:
    if not steps: return ''
    if len(steps) > 1: return 'check renders'
    if steps[0] > 1: return f'a check render, every {ordinal(steps[0])} frame'
    return 'a check render'


HELD_TEXT = 'holding the nearest rendered frame'
EMPTY_TEXT = 'empty (black) where the render has no frame'


def missing_label(setting: str, check_steps: Iterable[Optional[int]]) -> str:
    """What the node says under **Missing Frames**: what happens to a frame
    the render does not have, and why when Automatic chose.

    ``check_steps`` has one entry per channel read (:func:`holds`' second
    argument). Nothing read yet: empty.
    """
    read = list(check_steps)
    if not read: return ''
    checks = sorted({step for step in read if step is not None})
    held = [holds(setting, step) for step in read]
    note = _check_note(checks)
    if setting == HOLD:
        return HELD_TEXT[0].upper() + HELD_TEXT[1:] + (f' ({note})' if note else '')
    if setting == EMPTY:
        return EMPTY_TEXT[0].upper() + EMPTY_TEXT[1:] + (f' ({note})' if note else '')
    if all(held):
        return f'Automatic: {HELD_TEXT} ({note})'
    if any(held):
        return f'Automatic: {HELD_TEXT} on the check renders, empty (black) elsewhere'
    return f'Automatic: {EMPTY_TEXT} (not a check render)'
