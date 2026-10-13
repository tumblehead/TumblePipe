"""Parse the output of Houdini's ``gpumem`` hscript command.

``gpumem -U`` is the only place Houdini reports GPU memory by who holds it.
Captured from a lighting shot on an RTX 3070 (H22.0.368)::

    NVIDIA GeForce RTX 3070 (0):
    Vulkan Texture Cache: 407.35MB (cached 4.03125MB)
    Vulkan Geometry (TumblePipe.panetab2.solaris): 2420.6MB (cached 0MB)
    OpenCL Buffer Pool: 0MB (cached 0MB)
    Vulkan Geometry (Build.panetab1.world): 0MB (cached 0MB)

A device line ends in a bare colon; every other line is a client. With
``-r <device>`` the device line is absent, so a client before any device
belongs to the device the caller asked about (``default_device``). Lines
that fit neither shape are skipped, not fatal — this is text meant for
people, and a later build may add some.

Pure Python: the property suite imports this without ``hou``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Bytes per unit. Houdini's "MB" is a mebibyte, like the rest of its memory
#: reporting (``memory -v`` prints the same 1024-based figures).
_UNITS = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30, "TB": 1 << 40}

_AMOUNT = r"(?P<{0}>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?P<{0}_unit>[KMGT]?B)"

_CLIENT_RE = re.compile(
    r"^(?P<name>.+?):\s+" + _AMOUNT.format("used")
    + r"(?:\s*\(cached\s+" + _AMOUNT.format("cached") + r"\))?\s*$")

_DEVICE_RE = re.compile(r"^(?P<name>.+?):\s*$")

# "Vulkan Geometry (TumblePipe.panetab2.solaris)": the geometry of one viewer
# pane, named <desktop>.<pane tab>.<viewer context>.
_PANE_RE = re.compile(r"^(?P<kind>.+?)\s*\((?P<pane>[^()]*)\)\s*$")


@dataclass(frozen=True)
class Client:
    """One holder of GPU memory on one device, as ``gpumem -U`` names it."""

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


def to_bytes(amount: str, unit: str) -> int:
    """``("407.35", "MB")`` → bytes, rounded to the nearest byte."""
    return int(round(float(amount) * _UNITS[unit.upper()]))


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


def parse_usage(text: str, default_device: str = "") -> list[Client]:
    """Parse ``gpumem -U`` (or ``-r <device> -U``) output into clients.

    Order is preserved. A device with no clients contributes nothing.
    """
    clients: list[Client] = []
    device = default_device
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _CLIENT_RE.match(line)
        if m:
            cached = (to_bytes(m.group("cached"), m.group("cached_unit"))
                      if m.group("cached") else 0)
            clients.append(Client(
                device=device,
                name=m.group("name").strip(),
                used_bytes=to_bytes(m.group("used"), m.group("used_unit")),
                cached_bytes=cached,
            ))
            continue
        m = _DEVICE_RE.match(line)
        if m:
            device = m.group("name").strip()
    return clients


def parse_totals(text: str) -> dict[str, int]:
    """Parse ``gpumem -u`` — one ``<device>: <amount>`` line per device."""
    totals: dict[str, int] = {}
    for raw in text.splitlines():
        m = _CLIENT_RE.match(raw.strip())
        if m:
            totals[m.group("name").strip()] = to_bytes(
                m.group("used"), m.group("used_unit"))
    return totals


def parse_devices(text: str) -> list[str]:
    """Parse ``gpumem -l`` — one device name per line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def format_client_line(client: Client) -> str:
    """Render a client the way ``gpumem -U`` prints it (in MB).

    The inverse of :func:`parse_usage` for one line; the property suite
    round-trips through it.
    """
    used = client.used_bytes / _UNITS["MB"]
    cached = client.cached_bytes / _UNITS["MB"]
    return f"{client.name}: {used!r}MB (cached {cached!r}MB)"
