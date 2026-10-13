"""Who holds GPU memory: Houdini's memory clients, named as Houdini names them.

Houdini keeps a registry of GPU memory clients per device (``UT_MemoryResource``
/ ``UT_MemoryClient``): the Vulkan texture cache, the OpenCL buffer pool,
Copernicus's cache, Karma XPU, and one geometry client per viewer pane. The
viewport hook (:mod:`.hook`) reads that registry, and measures the geometry
clients itself, prim by prim.

A geometry client is named for its pane: ``Vulkan Geometry
(TumblePipe.panetab2.solaris)`` — ``<desktop>.<pane tab>.<viewer context>``.

Pure Python: the property suite imports this without ``hou``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PANE_RE = re.compile(r"^(?P<kind>.+?)\s*\((?P<pane>[^()]*)\)\s*$")


@dataclass(frozen=True)
class Client:
    """One holder of GPU memory on one device."""

    device: str
    name: str
    used_bytes: int
    cached_bytes: int = 0

    @property
    def kind(self) -> str:
        """Which part of Houdini this is: see :func:`client_kind`."""
        return client_kind(self.name)

    @property
    def pane(self) -> str:
        """The viewer pane a geometry client belongs to, or ``""``."""
        m = _PANE_RE.match(self.name)
        return m.group("pane") if m else ""


def client_kind(name: str) -> str:
    """Classify a client name: ``cop`` (Copernicus's OpenCL cache),
    ``textures``, ``geometry``, ``opencl``, ``render`` (Karma XPU) or
    ``other``.

    Matched on words rather than whole names so a build that renames
    "Vulkan Geometry" to "Viewport Geometry" still lands in the right bucket.
    """
    low = name.casefold()
    # "COP OpenCL Buffer Cache" is Copernicus's, not the generic OpenCL pool.
    if re.match(r"(cop|copernicus)\b", low):
        return "cop"
    if "texture" in low:
        return "textures"
    if "geometry" in low:
        return "geometry"
    if "opencl" in low:
        return "opencl"
    if "xpu" in low or "karma" in low or "optix" in low:
        return "render"
    return "other"
