"""How many bytes a piece of scene data is, and how many the viewport uploads.

Two answers per item, kept apart on purpose:

* **data bytes** — what the scene holds: a primvar's authored array, a
  texture file's full-resolution pixels. Exact.
* **upload bytes** — what Houdini's Vulkan viewport would put on the GPU for
  it, following the viewport's own rules (texture resolution cap, mipmaps;
  doubles uploaded as floats; triangle and wireframe index buffers) with
  per-point/per-triangle constants measured in the viewport. A model, not a
  measurement: :mod:`.report` uses it to split a measured prim, texture cache
  or Copernicus total across its rows.

The texture rules mirror ``hou.GeometryViewportSettings`` — read live by
:mod:`.collect` into a :class:`TextureRules` — whose H22 defaults are a 2048²
resolution cap, an HDR16 depth setting and mipmaps on. Geometry and texture
upload sizes are calibrated in the viewport (see the tables below).

Pure Python: the property suite imports this without ``hou``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# ── USD value types ──────────────────────────────────────────────────────────

_SCALAR_BYTES = {
    "bool": 1, "uchar": 1, "half": 2, "int": 4, "uint": 4, "float": 4,
    "int64": 8, "uint64": 8, "double": 8,
}
_SUFFIX_SCALAR = {"h": "half", "f": "float", "d": "double", "i": "int"}

# Role-qualified spellings Sdf uses for the same storage: point3f is float3.
_ROLES = ("point", "normal", "vector", "color", "texCoord")
_ROLE_RE = re.compile(r"^(?:%s)(?P<n>[234])(?P<s>[hfd])$" % "|".join(_ROLES))
_VEC_RE = re.compile(r"^(?P<scalar>half|float|double|int)(?P<n>[234])$")
_QUAT_RE = re.compile(r"^quat(?P<s>[hfd])$")
_MATRIX_RE = re.compile(r"^(?:matrix(?P<n>[234])d|frame4d)$")


@dataclass(frozen=True)
class ValueType:
    """Storage of one element of a USD value type: ``count`` × ``scalar``."""

    scalar: str
    count: int

    @property
    def data_bytes(self) -> int:
        """Bytes per element as authored."""
        return _SCALAR_BYTES[self.scalar] * self.count

    @property
    def upload_bytes(self) -> int:
        """Bytes per element on the GPU: doubles go up as floats, and a
        3-vector of halves is padded to 4 (no packed 48-bit vertex format)."""
        scalar = "float" if self.scalar == "double" else self.scalar
        count = 4 if scalar == "half" and self.count == 3 else self.count
        return _SCALAR_BYTES[scalar] * count


def value_type(type_name: str) -> ValueType | None:
    """Parse an Sdf value type name (``"point3f[]"``, ``"float"``,
    ``"texCoord2f[]"``, ``"matrix4d"``) into its element storage.

    Returns ``None`` for types that never reach the GPU as vertex data —
    strings, tokens, asset paths, dictionaries — and for unknown names.
    """
    name = type_name.strip()
    if name.endswith("[]"):
        name = name[:-2]
    if name in _SCALAR_BYTES:
        return ValueType(name, 1)
    m = _VEC_RE.match(name)
    if m:
        return ValueType(m.group("scalar"), int(m.group("n")))
    m = _ROLE_RE.match(name)
    if m:
        return ValueType(_SUFFIX_SCALAR[m.group("s")], int(m.group("n")))
    m = _QUAT_RE.match(name)
    if m:
        return ValueType(_SUFFIX_SCALAR[m.group("s")], 4)
    m = _MATRIX_RE.match(name)
    if m:
        n = int(m.group("n") or 4)
        return ValueType("double", n * n)
    return None


def primvar_bytes(type_name: str, values: int, indices: int = 0) -> tuple[int, int]:
    """``(data_bytes, upload_bytes)`` for one primvar.

    ``values`` is the authored array length (1 for a scalar), ``indices``
    the length of its ``:indices`` array if it is indexed. The viewport
    de-indexes, so an indexed primvar uploads one element per index; the
    data side counts the values plus the 4-byte indices.
    """
    vt = value_type(type_name)
    if vt is None:
        return 0, 0
    data = values * vt.data_bytes + indices * 4
    upload = (indices if indices else values) * vt.upload_bytes
    return data, upload


# ── Calibration (H22.0.368, Vulkan Solaris viewport, RTX 3070) ──────────────
#
# Measured with ``gpumem -U`` on a 1000×1000 grid and variants of it, each
# read after the viewport had redrawn (MiB). Every variant draws as one part,
# where gpumem's geometry figure is right; it overcounts multi-part prims
# (see src/vram/hook/README.md), and the inspector no longer runs it. Re-
# measure with the viewport hook: src/vram/calibration/README.md.
#
#   grid (1,000,000 points, 1,996,002 tris, 3,992,004 face-verts)    99.057
#   triangulated (same points and tris, 5,988,006 face-verts)       114.285
#   unique points (3,992,004 points, same tris and face-verts)      182.752
#   quarter grid (250,000 points, 498,002 tris, 996,004 f-verts)     24.739
#   grid + faceVarying st (texCoord2f, 3,992,004 values)            133.320
#   unique points + faceVarying st                                   217.016
#   grid + authored point normals                                     99.057
#
# Solving the first three: 8.0 B per face-vertex (the wireframe: one 2-index
# segment per face edge), 21.34 B per triangle (a 12 B index + 9.34 B the
# viewport keeps per triangle) and 29.33 B per point (P and N at 12 B each,
# normals generated when not authored, + 5.33 B per point). The quarter grid
# is predicted to 0.1%. A faceVarying texCoord2f costs 9 B per value — its
# 8 B plus one — on either topology.

#: Bytes per face-vertex for the wireframe index buffer.
WIRE_BYTES_PER_FACE_VERTEX = 8
#: Bytes the viewport keeps per point beyond P and N (ids, selection).
POINT_OVERHEAD_BYTES = 5.33
#: Bytes the viewport keeps per triangle beyond its 12-byte index.
TRIANGLE_OVERHEAD_BYTES = 9.34
#: Extra bytes per element of an uploaded faceVarying primvar.
FACEVARYING_EXTRA_BYTES = 1


def triangle_count(face_count: int, face_vertex_count: int) -> int:
    """Triangles a polygon mesh becomes: an *n*-gon is *n* − 2 of them."""
    return max(0, face_vertex_count - 2 * face_count)


def topology_bytes(face_count: int, face_vertex_count: int) -> tuple[int, int]:
    """``(data_bytes, upload_bytes)`` for a mesh's topology arrays.

    Data: ``faceVertexCounts`` + ``faceVertexIndices``, 4 bytes each. Upload:
    the triangle index buffer (three 4-byte indices per triangle) and the
    wireframe buffer (:data:`WIRE_BYTES_PER_FACE_VERTEX`).
    """
    data = (face_count + face_vertex_count) * 4
    triangles = triangle_count(face_count, face_vertex_count)
    return data, triangles * 12 + face_vertex_count * WIRE_BYTES_PER_FACE_VERTEX


def mesh_overhead_bytes(points: int, triangles: int) -> int:
    """What the viewport keeps for a mesh beyond its attributes and indices."""
    return int(round(points * POINT_OVERHEAD_BYTES + triangles * TRIANGLE_OVERHEAD_BYTES))


# ── Textures ─────────────────────────────────────────────────────────────────

#: The viewport's 2D texture bit-depth caps, by ``hou.viewportTextureDepth``
#: member name, as bytes per channel. ``Compressed8`` is block-compressed:
#: BC7 stores a 4×4 RGBA block in 16 bytes, i.e. one byte per *pixel*.
#:
#: Measured (H22.0.368, Solaris, default settings, ``glcache -c`` between
#: reads, MiB): 4K half RGB 42.71 · 4K 8-bit RGB 21.38 · 4K float RGBA 85.38
#: · 2K half RGB 42.71 · 4K 8-bit mono 5.38 · 8K 8-bit RGB 21.38 — every one
#: the 2048² cap × channels (RGB padded to RGBA) × the *file's* depth × 4/3.
#: So the default ``HDR16`` cap leaves 32-bit float at 32 bits: it caps
#: nothing measurable, and is modelled as no cap. ``Fixed8``/``Compressed8``
#: are not yet measured.
DEPTH_LIMITS = {"Compressed8": 1, "Fixed8": 1, "HDR16": 4, "FullHDR": 4}


@dataclass(frozen=True)
class TextureRules:
    """The viewport's texture settings that decide a texture's upload size.

    Defaults are Houdini 22's.
    """

    max_res: tuple[int, int] = (2048, 2048)
    limit_res: bool = True
    scale: float = 1.0
    depth_limit: str = "HDR16"
    mipmaps: bool = True


@dataclass(frozen=True)
class ImageInfo:
    """What a texture file's header says: size, channels, bytes per channel."""

    width: int
    height: int
    channels: int
    channel_bytes: int
    tiles: int = 1


def image_bytes(info: ImageInfo) -> int:
    """Full-resolution, uncompressed bytes of every tile — the data side."""
    return (info.width * info.height * info.channels * info.channel_bytes
            * info.tiles)


def upload_resolution(width: int, height: int, rules: TextureRules) -> tuple[int, int]:
    """The resolution the viewport uploads a ``width`` × ``height`` image at.

    The scale applies first; then, if the cap is on, the image shrinks by
    one factor (aspect kept) until it fits ``max_res``. Never below 1×1.
    """
    w = max(1, int(round(width * rules.scale)))
    h = max(1, int(round(height * rules.scale)))
    if rules.limit_res:
        mw, mh = rules.max_res
        f = min(1.0, mw / w, mh / h)
        if f < 1.0:
            w = max(1, int(math.floor(w * f)))
            h = max(1, int(math.floor(h * f)))
    return w, h


def mip_factor(width: int, height: int) -> float:
    """Total size of a full mip chain relative to its top level (→ 4/3)."""
    total = 0
    w, h = width, height
    while True:
        total += w * h
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    return total / (width * height)


def texture_upload_bytes(info: ImageInfo, rules: TextureRules) -> int:
    """Estimated GPU bytes for a texture under ``rules``.

    Three channels are padded to four (Vulkan has no portable 24/48/96-bit
    sampled formats); depth is the lesser of the file's and the cap's; a
    ``Compressed8`` cap stores one byte per pixel regardless of channels.
    """
    w, h = upload_resolution(info.width, info.height, rules)
    if rules.depth_limit == "Compressed8":
        per_pixel = 1
    else:
        cap = DEPTH_LIMITS.get(rules.depth_limit, 4)
        channels = 4 if info.channels == 3 else max(1, info.channels)
        per_pixel = channels * min(info.channel_bytes, cap)
    level0 = w * h * per_pixel
    mips = mip_factor(w, h) if rules.mipmaps else 1.0
    return int(round(level0 * mips)) * info.tiles


# ── Copernicus layers ────────────────────────────────────────────────────────

#: Bytes per channel by ``hou.imageLayerStorageType`` member name.
STORAGE_BYTES = {
    "Fixed8": 1, "Int8": 1, "Fixed16": 2, "Float16": 2, "Int16": 2,
    "Float32": 4, "Int32": 4,
}


def layer_bytes(width: int, height: int, channels: int, storage: str) -> tuple[int, int]:
    """``(data_bytes, gpu_bytes)`` of one Copernicus layer buffer.

    On the GPU a 3-channel layer occupies four: OpenCL's ``float3`` has the
    size and alignment of ``float4``. Unknown storage counts as 32-bit.
    """
    per = STORAGE_BYTES.get(storage, 4)
    pixels = max(0, width) * max(0, height)
    gpu_channels = 4 if channels == 3 else channels
    return pixels * channels * per, pixels * gpu_channels * per
