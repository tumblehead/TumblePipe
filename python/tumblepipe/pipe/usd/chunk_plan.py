"""How a layer export with no Batch set is cut into chunks (#108).

A shot's animation layer is one crate file with every frame's samples:
Sigurd 010/010 (776 frames) was 3.0 GB on disk and about 13 GB of samples
held in memory at once, by the export, the copy to the server and every
render task loading it. th::export_layer with Batch 0 now sizes itself:

* :func:`frame_bytes` reads the stage being exported: the raw size of one
  frame of every attribute with time samples (points, normals, topology
  that changes, ...); static data does not grow with the range.
* :func:`auto_chunks` keeps the export one file when the whole range is
  under the budget (:data:`RAW_BUDGET_BYTES`), byte for byte what it wrote
  before; above it, the range is cut into chunks (written as value clips)
  of a length that is a multiple of the render batch, so a render task,
  which renders a batch of frames from the range's first, reads one clip.

``TH_EXPORT_CLIP_BUDGET_MB`` overrides the budget (a test forces a tiny
one to clip a small export).
"""

import os

from tumblepipe.pipe.usd.stitch import calculate_chunks

# Raw samples of the whole range one file may hold: 1.5 GiB
RAW_BUDGET_BYTES = 1536 * 2 ** 20
BUDGET_ENV = 'TH_EXPORT_CLIP_BUDGET_MB'
# Farm render batch when the project names none (the template's default)
DEFAULT_RENDER_BATCH = 10


def budget_bytes(environ=None) -> int:
    """The raw-size budget: ``TH_EXPORT_CLIP_BUDGET_MB`` (megabytes, may be
    a fraction) when set to a positive number, else :data:`RAW_BUDGET_BYTES`."""
    value = (os.environ if environ is None else environ).get(BUDGET_ENV, '')
    try:
        megabytes = float(value)
    except ValueError:
        return RAW_BUDGET_BYTES
    if not megabytes > 0:
        return RAW_BUDGET_BYTES
    return max(1, int(megabytes * 2 ** 20))


def auto_chunks(
    first_frame: int,
    last_frame: int,
    frame_bytes: int,
    render_batch: int,
    budget: int = RAW_BUDGET_BYTES,
) -> list[tuple[int, int]]:
    """The chunks to export ``first_frame``..``last_frame`` in, each frame
    ``frame_bytes`` of samples.

    One chunk (the whole range: one file, as before) when the range's
    samples fit ``budget``. Otherwise chunks from the first frame of a
    length that is a multiple of ``render_batch`` (so each block of a render
    starting at the first frame falls in one chunk), as many batches as fit
    the budget and at least one: a single batch over the budget is still
    one chunk, not split under a render task.
    """
    frames = last_frame - first_frame + 1
    if frame_bytes <= 0 or frames * frame_bytes <= budget:
        return [(first_frame, last_frame)]
    batch = max(1, render_batch)
    fit = max(1, budget // frame_bytes)
    length = max(batch, fit // batch * batch)
    return calculate_chunks(first_frame, last_frame, length)


# Bytes per component, by the last letter of a Gf type (GfVec3f, GfQuath)
_COMPONENT = {'f': 4, 'd': 8, 'h': 2, 'i': 4}


def value_type_bytes(cpp_type_name: str) -> int:
    """Bytes one value of a USD scalar type takes in memory (an estimate:
    strings, tokens and asset paths count 8, as their handle)."""
    name = cpp_type_name.replace('pxr_half::half', 'GfHalf')
    for kind, count in (
        ('Matrix2', 4), ('Matrix3', 9), ('Matrix4', 16), ('Quat', 4),
        ('Vec2', 2), ('Vec3', 3), ('Vec4', 4),
    ):
        if kind in name:
            return count * _COMPONENT.get(name[-1], 4)
    lowered = name.lower()
    if 'half' in lowered:
        return 2
    if 'double' in lowered or '64' in lowered or 'long' in lowered:
        return 8
    if 'float' in lowered or 'int' in lowered:
        return 4
    if 'bool' in lowered or 'char' in lowered:
        return 1
    return 8


def frame_bytes(stage, time) -> int:
    """The raw size of one frame of ``stage``'s animated data: every
    attribute with time samples, its value at ``time`` (length × element
    size for an array)."""
    from pxr import Usd

    total = 0
    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        for attr in prim.GetAttributes():
            if attr.GetNumTimeSamples() == 0:
                continue
            type_name = attr.GetTypeName()
            value = attr.Get(time)
            if value is None:
                continue
            size = value_type_bytes(type_name.scalarType.type.typeName)
            total += size * len(value) if type_name.isArray else size
    return total
