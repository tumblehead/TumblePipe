"""The refusals Farm Submit and the farm share: when a submission must stop.

Farm Submit's confirm step (``farm.submit.settings.entity_errors``) lists
these for a row and will not send it while it has one; the farm's
``pipelines.batch_submit.submit_entity_batch`` raises the first of them for
the same submission. One module, so the dialog never lets through what the
farm then refuses, nor blocks what it would take.

``settings`` is one entity's resolved settings, the dict both sides read:
the section booleans (``publish``, ``render``, ``playblast``, ``comp``) and
each section's fields. The rules:

- a step aimed at a department turned off on the entity (#64): the Render
  or Playblast cut, or a ticked publish;
- a Frame List (#84) that cannot be read, or reaches past the render's
  range (rolls included);
- an Into version (#92, ``farm.specs.repair``) that cannot go with the
  rest: no Frame List, a preview, a Step above 1, not a version name;
- Comp beside a render that is not published: a multi-range one (beside
  an Into version the version decides, at the farm, which reads it), or a
  stepped one with a comp frame the render does not make (Soren,
  2026-11-22: on frames it makes, the comp reads that render, a check
  comp);
- Comp beside a render of check frames only (no ``full_render``);
- comp frames that end before they start, or, beside a render, reach
  outside the frames it renders (beside an Into version, outside the
  version: checked at the farm);
- a render with no channel.

What a comp follows (:func:`comp_follows`) is part of the same rule: the
frames the render beside it makes, else what the renders on disk cover
(#43: the shot's range comped black frames over a short render), else the
shot's range.

Pure: no ``hou``, no Qt, no config reads, so the dialog's settings module
and the farm load it alike; the caller reads the entity (its turned-off
departments, the renders on disk, the shot's range) and passes the answers.
"""

from __future__ import annotations

from typing import Any, Collection, Sequence

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import repair

Range = tuple[int, int]

#: The render chains each Range mode submits. With both, pipelines/render.py
#: makes every channel's full_render wait on its partial_render and reuse its
#: version: the first/middle/last check frames render (and go to Discord)
#: while the full range sits queued behind them.
RENDER_TASK_KEYS = {
    'full': ('full_render',),
    'first_middle_last': ('partial_render',),
    'preview_then_full': ('partial_render', 'full_render'),
}

#: The render modes that render the full range, the ones a Comp can follow.
FULL_RENDER_MODES = frozenset(
    mode for mode, keys in RENDER_TASK_KEYS.items() if 'full_render' in keys
)

#: The render modes that render a preview first (a ``partial_render``).
PREVIEW_RENDER_MODES = frozenset(
    mode for mode, keys in RENDER_TASK_KEYS.items() if 'partial_render' in keys
)

#: The settings key of the render's channels.
CHANNELS_KEY = 'channels'


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def render_step(settings: dict) -> int:
    """The render's Step, 1 when unset or not a whole number above 0 (the
    farm refuses those on its own; the dialog's field cannot hold one)."""
    step = _int(settings.get('render_step'))
    return step if step is not None and step > 0 else 1


def rolled_range(settings: dict) -> Range | None:
    """The frames the render covers without a Frame List, rolls included;
    None without a range."""
    first, last = _int(settings.get('first_frame')), _int(settings.get('last_frame'))
    if first is None or last is None:
        return None
    return (
        first - (_int(settings.get('pre_roll')) or 0),
        last + (_int(settings.get('post_roll')) or 0),
    )


def frame_list(settings: dict) -> tuple[tuple[Range, ...], str | None]:
    """``(ranges, error)`` of the render's Frame List, read against its
    rolled range: the normalised ranges, empty when none is typed or the
    render is off; ``error`` why the submission must stop, else None."""
    text = settings.get('frame_list')
    if not settings.get('render') or not isinstance(text, str) or not text.strip():
        return (), None
    span = rolled_range(settings)
    if span is None:
        return (), None
    try:
        return frame_ranges.read(text, *span), None
    except frame_ranges.FrameListError as e:
        return (), str(e)


def into_version(settings: dict) -> str:
    """The render's Into version (#92): ``repair.TARGET_NEW`` for new
    versions, ``latest`` or a version name for a repair; new when the
    render is off."""
    target = settings.get('into_version')
    if not settings.get('render') or not isinstance(target, str):
        return repair.TARGET_NEW
    return target


def repair_problems(settings: dict) -> list[str]:
    """Why the render's Into version cannot go with its other settings,
    read without the disk (``repair.setup_problems``); none while the Frame
    List cannot be read (that is its own problem). A version that is not
    there is the farm's to refuse: it reads the disk."""
    target = into_version(settings)
    if not repair.is_repair(target):
        return []
    ranges, error = frame_list(settings)
    if error is not None:
        return []
    return repair.setup_problems(
        target, ranges,
        preview=settings.get('render_mode') in PREVIEW_RENDER_MODES,
        step=render_step(settings),
    )


def rendered_span(settings: dict) -> Range | None:
    """The frames the render makes, first to last: its Frame List's span,
    else its rolled range; None without a range."""
    ranges, _error = frame_list(settings)
    if ranges:
        return frame_ranges.span(ranges)
    return rolled_range(settings)


def comp_follows(
    settings: dict,
    rendered: Range | None,
    shot: Range | None,
) -> Range | None:
    """The range a comp follows: the frames the render beside it makes, else
    ``rendered`` (what the renders on disk cover), else ``shot`` (the shot's
    range with its rolls); None when there is none."""
    if settings.get('render'):
        return rendered_span(settings)
    return rendered if rendered is not None else shot


def comp_frames(own_first: Any, own_last: Any, follows: Range) -> Range:
    """The comp's first and last frame: ``follows``, with each end replaced
    by the frame the artist set (Farm Submit's ``comp_first_frame`` /
    ``comp_last_frame``, a re-comp block's ``first_frame``/``last_frame``)."""
    own_first, own_last = _int(own_first), _int(own_last)
    return (
        follows[0] if own_first is None else own_first,
        follows[1] if own_last is None else own_last,
    )


def comp_frame_problems(frames: Range, follows: Range, *, inside: bool) -> list[str]:
    """Why the comp's ``frames`` make no comp: they end before they start,
    or, ``inside`` a render it reads, reach outside the ``follows`` it makes
    (frames past a published render would comp over empty input; past a
    stepped render they hold its first and last frame, so :func:`problems`
    passes ``inside`` False beside one, :func:`stepped_comp_frames`)."""
    first, last = frames
    if first > last:
        return [f"the comp frames {first}-{last} end before they start"]
    if inside and (first < follows[0] or last > follows[1]):
        return [
            f"the comp frames {first}-{last} reach outside the render's "
            f"{follows[0]}-{follows[1]}: comp inside them, or untick Render to "
            f"comp the renders on disk"
        ]
    return []


def comp_step(settings: dict) -> int:
    """The Comp's Step, 1 when unset or not a whole number above 0."""
    step = _int(settings.get('comp_step'))
    return step if step is not None and step > 0 else 1


def stepped_comp_frames(settings: dict) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """``(comp, render)``, each ``(first, last, step)``, of a Comp that
    reads the stepped render beside it (:func:`comp_reads_check_render`);
    None for any other Comp.

    Such a comp goes on any frames (Soren, 2026-11-23: a Step 1 comp of a
    Step 2 render; 2026-11-24: past the render too): a frame the render
    does not make is held from the nearest one it does, its first before
    its range and its last after it, and the Warnings column says how many
    (``pipe.comp.held_text``). It is a check comp
    (``pipe.comp.is_check_comp``). Frames that end before they start are
    :func:`comp_frame_problems`' to refuse."""
    if not comp_reads_check_render(settings):
        return None
    ranges, _error = frame_list(settings)
    if frame_ranges.is_multi(ranges):
        return None     # refused on its own: Comp follows one range
    span = rendered_span(settings)
    if span is None:
        return None
    first, last = comp_frames(
        settings.get('comp_first_frame'), settings.get('comp_last_frame'), span,
    )
    if first > last:
        return None
    return (first, last, comp_step(settings)), (span[0], span[1], render_step(settings))


def comp_reads_check_render(settings: dict) -> bool:
    """Whether the Comp reads a check render it is submitted beside: the
    render is ticked, renders the full range (the render a comp waits
    for), on a Step above 1, into new versions. Such a comp is a check comp
    whatever its own Step (``pipe.comp.is_check_comp``; the farm marks it
    so, ``pipelines.render.build``'s ``follows_check_render``), a one-frame
    Comp on Step 1 too."""
    return (
        bool(settings.get('comp')) and bool(settings.get('render'))
        and settings.get('render_mode') in FULL_RENDER_MODES
        and render_step(settings) > 1
        and not repair.is_repair(into_version(settings))
    )


def turned_off_problems(settings: dict, off: Collection[str]) -> list[str]:
    """A step aimed at a department turned off on the entity (#64): the
    Render or Playblast cut, or a ticked publish (``pub_departments``)."""
    off = set(off)
    if not off:
        return []
    problems = []
    for section, key, what in (
        ('render', 'render_department', 'Render'),
        ('playblast', 'pb_department', 'Playblast'),
    ):
        name = settings.get(key)
        if settings.get(section) and name in off:
            problems.append(
                f"the {what} cut, {name}, is turned off on this shot: pick "
                f"another department, or turn it back on"
            )
    if settings.get('publish'):
        for name in settings.get('pub_departments') or ():
            if name in off:
                problems.append(f"{name} is turned off on this shot: not published")
    return problems


def channel_list_text(names: Sequence[str]) -> str:
    """``names`` for a message, elided past four: a pick across a wide batch
    can name twenty, and the message lands in a table cell."""
    shown = ', '.join(names[:4])
    if len(names) > 4:
        shown += f", +{len(names) - 4} more"
    return shown


def problems(
    settings: dict,
    *,
    off: Collection[str],
    rendered: Range | None,
    shot: Range | None,
    requested: Sequence[str] | None = None,
) -> list[str]:
    """Every reason the submission of ``settings`` must stop.

    Args:
        settings: The entity's resolved settings.
        off: The departments turned off on the entity.
        rendered: The frames its renders on disk cover, None when none.
        shot: The shot's range with its rolls, None when it has none.
        requested: The channels checked for the batch, for the message of a
            render left with none.
    """
    found = list(turned_off_problems(settings, off))
    render, comp = settings.get('render'), settings.get('comp')
    ranges, frame_list_error = frame_list(settings)
    if frame_list_error is not None:
        found.append(frame_list_error)
    found.extend(repair_problems(settings))
    repairing = repair.is_repair(into_version(settings))
    if comp and frame_ranges.is_multi(ranges) and not repairing:
        found.append(
            f"Comp follows a published render, and "
            f"{frame_ranges.NOT_PUBLISHED}: untick Comp, or render one range"
        )
    if comp and render and settings.get('render_mode') not in FULL_RENDER_MODES:
        found.append(
            "Comp waits for the full render: tick Full range in the Render "
            "settings, or untick Render to comp the renders on disk"
        )
    if comp:
        follows = comp_follows(settings, rendered, shot)
        if follows is not None:
            found.extend(comp_frame_problems(
                comp_frames(
                    settings.get('comp_first_frame'), settings.get('comp_last_frame'),
                    follows,
                ),
                # Beside a stepped render, frames past it hold its first
                # and last frame (Soren, 2026-11-24).
                follows, inside=(bool(render) and not repairing
                                 and not comp_reads_check_render(settings)),
            ))
    if render and not settings.get(CHANNELS_KEY):
        checked = list(requested or ())
        if checked:
            found.append(
                f"defines none of the checked channels "
                f"({channel_list_text(checked)}), so it has nothing to "
                f"render; check one of its channels or untick Render"
            )
        else:
            found.append("no channel checked to render")
    return found
