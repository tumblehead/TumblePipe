"""Read a texture file's size and pixel format from its header.

OpenImageIO (bundled with Houdini) answers for EXR, TX, PNG, JPEG, TIFF and
most else without decoding pixels. Houdini's own ``.rat`` and ``.pic`` are
outside it, so ``hou.imageResolution`` covers those — it reports size only,
so channels and depth are assumed to be the common 8-bit RGBA.

Results are cached by path and modification time: texture paths repeat
across materials, and headers on a network share are not free to read.
"""

from __future__ import annotations

import os

from .sizes import ImageInfo

_FORMAT_BYTES = {
    "uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "half": 2,
    "uint32": 4, "int32": 4, "float": 4, "double": 8,
}

_cache: dict[tuple[str, float], ImageInfo | None] = {}


def read_image_info(path: str) -> ImageInfo | None:
    """The header of ``path``, or ``None`` if it cannot be read."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _cache:
        _cache[key] = _read_oiio(path) or _read_hou(path)
    return _cache[key]


def _read_oiio(path: str) -> ImageInfo | None:
    try:
        import OpenImageIO as oiio
    except ImportError:
        return None
    inp = oiio.ImageInput.open(path)
    if inp is None:
        oiio.geterror()  # clear it, or OIIO prints it at exit
        return None
    try:
        spec = inp.spec()
        return ImageInfo(spec.width, spec.height, spec.nchannels,
                         _FORMAT_BYTES.get(str(spec.format), 4))
    finally:
        inp.close()


def _read_hou(path: str) -> ImageInfo | None:
    try:
        import hou
        w, h = hou.imageResolution(path)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return ImageInfo(int(w), int(h), 4, 1)
