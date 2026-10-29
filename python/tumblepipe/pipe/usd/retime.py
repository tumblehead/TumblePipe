"""Moving an exported layer's animation to other frames.

A Multi with a shared timeline (``config.multi_timeline``) cooks a member
shot on shared-timeline frames (1051-1100, say) that are not the shot's own
(1001-1050). The export writes those shared frames, then every layer file it
wrote is moved back by the member's offset here, so the published layer is
an ordinary shot layer on the shot's own frames.

Houdini's Time Shift LOP cannot do this: it cooks its input at another frame
but stamps the samples at that other frame, and a USD ROP writing through it
collapses the range to one sample.
"""

from pathlib import Path

from pxr import Sdf

LAYER_SUFFIXES = ('.usd', '.usda', '.usdc')


def shift_layer(layer: Sdf.Layer, offset: float) -> int:
    """Move every time sample in ``layer`` by ``offset`` frames; the number
    of attributes and clip sets moved.

    Also moves what names frames beside the samples: the layer's start and
    end time codes and value clip ``active``/``times`` (their stage times).
    A ``0`` offset changes nothing.
    """
    if offset == 0:
        return 0
    sampled = []
    clipped = []

    # Collected first, edited after: the traversal must not see its own edits.
    def visit(path):
        if path.IsPropertyPath():
            if layer.GetNumTimeSamplesForPath(path) > 0:
                sampled.append(path)
        elif path.IsPrimPath():
            spec = layer.GetPrimAtPath(path)
            if spec is not None and spec.HasInfo('clips'):
                clipped.append(spec)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    for path in sampled:
        # All read before any is written: old and new frames can overlap.
        samples = [
            (time, layer.QueryTimeSample(path, time))
            for time in layer.ListTimeSamplesForPath(path)
        ]
        for time, _value in samples:
            layer.EraseTimeSample(path, time)
        for time, value in samples:
            layer.SetTimeSample(path, time + offset, value)
    for spec in clipped:
        spec.SetInfo('clips', _shift_clips(spec.GetInfo('clips'), offset))
    if layer.HasStartTimeCode():
        layer.startTimeCode = layer.startTimeCode + offset
    if layer.HasEndTimeCode():
        layer.endTimeCode = layer.endTimeCode + offset
    return len(sampled) + len(clipped)


def _shift_clips(clips: dict, offset: float) -> dict:
    """Clip sets with their stage times moved; clip times stay."""
    shifted = {}
    for name, clip_set in clips.items():
        clip_set = dict(clip_set)
        if 'active' in clip_set:
            clip_set['active'] = [
                (stage_time + offset, index)
                for stage_time, index in clip_set['active']
            ]
        if 'times' in clip_set:
            clip_set['times'] = [
                (stage_time + offset, clip_time)
                for stage_time, clip_time in clip_set['times']
            ]
        shifted[name] = clip_set
    return shifted


def shift_directory(directory: Path, offset: float) -> list[Path]:
    """:func:`shift_layer` on every layer file under ``directory``, saved in
    place; the files changed. A ``0`` offset touches nothing."""
    if offset == 0:
        return []
    changed = []
    for path in sorted(Path(directory).rglob('*')):
        if path.suffix.lower() not in LAYER_SUFFIXES or not path.is_file():
            continue
        # Anonymous, so a layer the session (the ROP that just wrote it)
        # still holds open is neither edited in place nor read stale.
        layer = Sdf.Layer.OpenAsAnonymous(str(path))
        if layer is None:
            raise ValueError(f'could not open {path} to move its frames')
        had_frames = layer.HasStartTimeCode() or layer.HasEndTimeCode()
        if shift_layer(layer, offset) == 0 and not had_frames:
            continue
        if not layer.Export(str(path)):
            raise ValueError(f'could not save {path} after moving its frames')
        changed.append(path)
    return changed
