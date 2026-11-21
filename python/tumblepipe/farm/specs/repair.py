"""Render frames again into a version that already exists (#92).

gabriel had one frame of a render come out glitched (a GPU error). To fix
it, he rendered that frame as v0002, renamed it to v0001 and dragged it
into the v0001 folder. Farm Submit's **Into version** does that for him:
with a version picked, the render renders its Frame list into that version,
overwriting only those frames, and the frames around them stay as they are.

The rules:

- It needs a Frame list. Without one it would render the whole version
  again, which is a new render, not a repair.
- Its frames must be frames the version holds: inside its range, on its
  step and inside its own ranges (a multi-range version's).
- It renders the full frames: no Preview (whose check frames are a separate
  partial render), no Step above 1 (a check render is a version of its own)
  and no turntable (an asset's studio render).
- Every channel's version must cover the same frames, because the review
  chain stacks them.
- The version keeps its ``context.json``. It still records the version's own
  range, so a repaired version reads exactly as it did before, complete or
  not, published or not.
- The workers skip any frame whose receipt exists (their resume marker), so
  the render and denoise jobs of a repair carry ``overwrite``, which makes
  them render their frames whatever the receipts say.
- A denoised version is repaired too: the denoise goes into the denoise
  version that reads the repaired render version (its context's ``reads``).
  With Denoise ticked and no such denoise version, the repair is refused.
- After that, the review chain (each channel's mp4, the slapcomp, its mp4
  and notify, Copy to edit, a Comp) runs over the version's whole range, as
  it did for the original render, because those outputs are whole movies
  and comps of the version.

The render settings carry it as ``repair``::

    'repair': {
        'versions': {'<channel>': {'render': 'v0001', 'denoise': 'v0001'}},
                                # 'denoise' only when the denoise is repaired
        'first_frame': 1001,    # the version's own range, from its context
        'last_frame': 1100,
        'step_size': 1,
        'frame_ranges': [[1001, 1030], [1050, 1100]],   # optional: a
                                # multi-range version's (farm.specs.frame_ranges)
        'published': True       # False for a version that is never the
                                # shot's render (pipe.paths.render.is_published)
    }

Pure: no ``hou``, no Qt, no config reads and no pipeline imports, so the
dialog, the workers and the tests can load it directly.
"""

from __future__ import annotations

import re

from tumblepipe.farm.specs import frame_ranges

#: The render settings key that carries a repair.
CONFIG_KEY = 'repair'

#: The render and denoise job config (and worker context) key that makes a
#: worker render its frames even when their receipts exist.
OVERWRITE_KEY = 'overwrite'

#: Farm Submit's Into version: a new version, the newest one, or one by name.
TARGET_NEW = ''
TARGET_LATEST = 'latest'

#: The labels Farm Submit shows for the two targets that are not a name.
TARGET_LABELS = {TARGET_NEW: 'New version', TARGET_LATEST: 'Latest version'}

_VERSION = re.compile(r'v\d+')

#: Said wherever Into version is set without a Frame list.
NEEDS_FRAME_LIST = (
    'Into version renders the Frame list into an existing version: list '
    'the frames to render again, or pick New version'
)


class RepairError(ValueError):
    """A repair that cannot be done as asked."""


def is_version_name(text) -> bool:
    """Whether ``text`` names a version: ``v`` and digits, ``v0001``."""
    return isinstance(text, str) and _VERSION.fullmatch(text) is not None


def valid_target(text) -> bool:
    """Whether ``text`` is an Into version value: '' (a new version),
    ``latest``, or a version name."""
    return text in (TARGET_NEW, TARGET_LATEST) or is_version_name(text)


def is_repair(target) -> bool:
    """Whether an Into version value renders into an existing version."""
    return isinstance(target, str) and target != TARGET_NEW


def target_label(target: str) -> str:
    """What Farm Submit shows for ``target``."""
    return TARGET_LABELS.get(target, target)


def setup_problems(
    target,
    frame_list_ranges,
    *,
    preview: bool,
    step: int,
    turntable: bool = False,
) -> list[str]:
    """Why an Into version of ``target`` cannot go with the rest of the
    submission, read without touching the disk: empty when it can, or when
    ``target`` is a new version.

    Args:
        target: The Into version value.
        frame_list_ranges: The row's normalised Frame list ranges.
        preview: The render also renders preview frames.
        step: The render's Step.
        turntable: The render is an asset's turntable.
    """
    if not is_repair(target):
        return []
    problems = []
    if not valid_target(target):
        problems.append(f'Into version {target!r} is not a version (write v0001)')
    if turntable:
        problems.append('a turntable renders whole versions: pick New version')
        return problems
    if not frame_list_ranges:
        problems.append(NEEDS_FRAME_LIST)
    if preview:
        problems.append(
            'Into version renders the listed frames in full: untick Preview'
        )
    if isinstance(step, int) and step > 1:
        problems.append(
            'Into version renders every listed frame: set the Step to 1'
        )
    return problems


def version_frames(context: dict) -> set[int]:
    """Every frame a version holds according to its ``context.json``: its
    ``frame_ranges`` (a multi-range version's), else its first..last, each
    on its step."""
    step = context.get('step_size', 1)
    ranges = frame_ranges.from_config(context)
    if ranges is None:
        ranges = ((context['first_frame'], context['last_frame']),)
    return set(frame_ranges.frames(ranges, step))


def frames_outside(listed, context: dict) -> list[int]:
    """The frames of the normalised ranges ``listed`` that the version
    whose context is ``context`` does not hold."""
    held = version_frames(context)
    return [frame for frame in frame_ranges.frames(listed) if frame not in held]


def valid_context(context) -> bool:
    """Whether a version's ``context.json`` records the frames a repair
    must check against."""
    if not isinstance(context, dict): return False
    for key in ('first_frame', 'last_frame'):
        value = context.get(key)
        if isinstance(value, bool) or not isinstance(value, int): return False
    step = context.get('step_size', 1)
    if isinstance(step, bool) or not isinstance(step, int) or step < 1: return False
    if context['first_frame'] > context['last_frame']: return False
    if frame_ranges.CONFIG_KEY in context and not frame_ranges.valid(
        context[frame_ranges.CONFIG_KEY], context['first_frame'], context['last_frame']
    ):
        return False
    return True


def review_entry(context: dict) -> dict:
    """The range part of a ``repair`` entry: the version's own range, step,
    ranges and published state, which its review chain runs over."""
    return dict(
        first_frame = context['first_frame'],
        last_frame = context['last_frame'],
        step_size = context.get('step_size', 1),
        **({} if frame_ranges.from_config(context) is None
           else {frame_ranges.CONFIG_KEY: [list(pair) for pair in context[frame_ranges.CONFIG_KEY]]}),
        published = context.get('published', True) is not False,
    )


def _same_range(entries) -> bool:
    return len({repr(sorted(entry.items())) for entry in entries}) <= 1


def denoise_reading(
    contexts: dict[str, dict],
    channel: str,
    render_department: str,
    render_version: str,
) -> str | None:
    """The newest denoise version whose context reads ``render_version`` of
    ``render_department``'s ``channel``: ``contexts`` is
    ``{denoise version name: its context.json}``. None when none does."""
    want = dict(department = render_department, version = render_version)
    reading = [
        name for name, context in contexts.items()
        if isinstance(context, dict)
        and isinstance(context.get('reads'), dict)
        and context['reads'].get(channel) == want
    ]
    if not reading:
        return None
    return max(reading, key = _version_number)


def _version_number(name: str) -> int:
    digits = name[1:] if is_version_name(name) else ''
    return int(digits) if digits else -1


def latest(names) -> str | None:
    """The newest of the version names ``names``, None for none."""
    names = [name for name in names if is_version_name(name)]
    return max(names, key = _version_number) if names else None


def plan(
    target: str,
    listed,
    channels,
    *,
    render_department: str,
    denoise: bool,
    render_versions: dict[str, dict[str, dict]],
    denoise_versions: dict[str, dict[str, dict]],
) -> dict:
    """The ``repair`` entry of an Into version of ``target``, from what is
    on disk; raises :class:`RepairError` saying why it cannot be done.

    Args:
        target: ``latest`` or a version name.
        listed: The normalised Frame list ranges (not empty).
        channels: The channels the render renders.
        render_department: The render's department.
        denoise: The submission denoises (and so repairs the denoise).
        render_versions: ``{channel: {version name: its context.json}}`` of
            the render department's versions on disk (a context of None for
            a version folder with none).
        denoise_versions: The same for the denoise department.
    """
    if not is_repair(target) or not valid_target(target):
        raise RepairError(f'Into version {target!r} is not a version')
    if not listed:
        raise RepairError(NEEDS_FRAME_LIST)
    versions = {}
    entries = []
    for channel in channels:
        on_disk = render_versions.get(channel) or {}
        name = latest(on_disk) if target == TARGET_LATEST else target
        if name is None or name not in on_disk:
            have = ', '.join(sorted(on_disk, key = _version_number)) or 'none'
            raise RepairError(
                f'channel {channel} of {render_department} has no render '
                f'version {target if name is None else name} to render into '
                f'(it has: {have})'
            )
        context = on_disk[name]
        if not valid_context(context):
            raise RepairError(
                f'{render_department}/{channel}/{name} records no frame range '
                '(no context.json): it was not made by a farm render'
            )
        outside = frames_outside(listed, context)
        if outside:
            shown = frame_ranges.format_ranges(frame_ranges.of_frames(outside))
            raise RepairError(
                f'{render_department}/{channel}/{name} holds no frame '
                f'{shown}: list frames inside its '
                f'{context["first_frame"]}-{context["last_frame"]}'
                + ('' if context.get('step_size', 1) == 1
                   else f' on its step {context["step_size"]}')
            )
        entry = {'render': name}
        if denoise:
            denoised = denoise_reading(
                denoise_versions.get(channel) or {}, channel, render_department, name
            )
            if denoised is None:
                raise RepairError(
                    f'{render_department}/{channel}/{name} has no denoise version '
                    'to repair: untick Denoise to render the frames only'
                )
            entry['denoise'] = denoised
        versions[channel] = entry
        entries.append(review_entry(context))
    if not _same_range(entries):
        raise RepairError(
            'the channels\' versions cover different frames: render them '
            'into their versions one channel at a time'
        )
    return dict(versions = versions, **entries[0])


def unrepaired_denoise(
    channels,
    *,
    render_department: str,
    repair: dict,
    denoise_versions: dict[str, dict[str, dict]],
) -> list[str]:
    """The ``channel/version`` of each denoise version that reads a
    repaired render version but is left as it was (Denoise unticked): it
    keeps the old frames, and a comp's best pass reads it."""
    left = []
    for channel in channels:
        entry = repair['versions'].get(channel) or {}
        if 'denoise' in entry:
            continue
        denoised = denoise_reading(
            denoise_versions.get(channel) or {}, channel, render_department,
            entry.get('render', ''),
        )
        if denoised is not None:
            left.append(f'{channel}/{denoised}')
    return left


def valid_entry(settings: dict, task_keys=None) -> bool:
    """An optional ``repair`` of render ``settings`` (the render pipeline's,
    the stage's, the collapse's render block): one version per channel, a
    review range that holds the settings' frames, and no preview."""
    if CONFIG_KEY not in settings: return True
    repair = settings[CONFIG_KEY]
    if not isinstance(repair, dict): return False
    versions = repair.get('versions')
    if not isinstance(versions, dict) or not versions: return False
    channels = settings.get('channel_names')
    if isinstance(channels, list) and set(versions) != set(channels): return False
    for entry in versions.values():
        if not isinstance(entry, dict): return False
        if not is_version_name(entry.get('render')): return False
        if 'denoise' in entry and not is_version_name(entry['denoise']): return False
        if set(entry) - {'render', 'denoise'}: return False
    if not valid_context(repair): return False
    if not isinstance(repair.get('published'), bool): return False
    if task_keys is not None and 'partial_render' in task_keys: return False
    if task_keys is not None and 'full_render' not in task_keys: return False
    if settings.get('step_size', 1) != 1: return False
    listed = frame_ranges.from_config(settings) or (
        (settings['first_frame'], settings['last_frame']),
    )
    return not frames_outside(listed, repair)


def check_overwrite(config: dict) -> bool:
    """An optional ``overwrite`` of a render or denoise config: a bool."""
    return OVERWRITE_KEY not in config or isinstance(config[OVERWRITE_KEY], bool)
