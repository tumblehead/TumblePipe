"""Read the viewport hook's snapshot: every GPU memory client, and exact
geometry bytes per USD prim.

``tt_vramhook`` (``src/vram/hook``) is a DSO that, during each viewport's own
redraw, walks what the viewport draws and records every primitive's GPU
memory, and reads Houdini's GPU memory registry for everything else (texture
cache, OpenCL pool, Copernicus, Karma XPU). It replaces Houdini's ``gpumem``
command, which the inspector no longer runs (see ``src/vram/hook/README.md``):

* ``gpumem``'s geometry figure is inflated: Houdini counts a prim drawn with
  several renders as a running total (1576 MiB reported, 430 MiB real, on the
  HideAndReek lighting shot);
* ``gpumem`` segfaults after a prim is hidden, reading a null entry Houdini
  leaves in its primitive list. The hook skips those entries, and never asks
  the registry's geometry clients for their use (that is the same code).

A walk is *requested* (``ttvramhook_request`` prints a request number); every
viewport answers on its next redraw, and its snapshot echoes the request it
answered. This module parses ``ttvramhook_snapshot``'s JSON. Pure Python.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .clients import Client


@dataclass(frozen=True)
class HookPrim:
    """One drawn primitive. In a Solaris viewer, ``path`` is its USD prim path."""

    path: str
    used: int
    parts: tuple[int, ...] = ()


@dataclass(frozen=True)
class HookViewport:
    """One viewport's last walk."""

    viewport_id: int
    request: int
    lops: bool
    display_lop: str
    device: str
    used: int
    cached: int
    prims: tuple[HookPrim, ...] = ()
    null_prims: int = 0
    texture_bytes: int = 0
    texture_count: int = 0
    walk_ms: float = 0.0

    def prim_bytes(self) -> dict[str, int]:
        """``prim path → bytes``; a path drawn by several renders is summed."""
        out: dict[str, int] = {}
        for prim in self.prims:
            out[prim.path] = out.get(prim.path, 0) + prim.used
        return out


@dataclass(frozen=True)
class Snapshot:
    """Every viewport's last walk, and the newest request made."""

    request: int = 0
    viewports: tuple[HookViewport, ...] = field(default_factory=tuple)
    #: Houdini's memory registry. Geometry clients carry their pane name and
    #: no figure: the hook measures them through ``viewports`` instead.
    clients: tuple[Client, ...] = field(default_factory=tuple)

    def answered(self, request: int) -> bool:
        """Has every viewport walked for ``request`` (or a later one)?"""
        return bool(self.viewports) and all(v.request >= request for v in self.viewports)

    @property
    def texture_bytes(self) -> int:
        """The texture cache is global: every viewport reports the same one."""
        return max((v.texture_bytes for v in self.viewports), default=0)

    @property
    def texture_count(self) -> int:
        return max((v.texture_count for v in self.viewports), default=0)


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _viewport(raw: dict) -> HookViewport:
    view = raw.get("viewport") or {}
    used = cached = nulls = 0
    prims: list[HookPrim] = []
    for look in view.get("looks") or ():
        for render in look.get("renders") or ():
            used += _int(render.get("used"))
            cached += _int(render.get("cached"))
            nulls += _int(render.get("null_prims"))
            for prim in render.get("prims") or ():
                prims.append(HookPrim(
                    str(prim.get("cache") or ""),
                    _int(prim.get("used")),
                    tuple(_int(p) for p in prim.get("parts") or ())))
    textures = raw.get("textures") or {}
    vulkan = raw.get("vulkan") or {}
    try:
        walk_ms = float(raw.get("walk_ms") or 0.0)
    except (TypeError, ValueError):
        walk_ms = 0.0
    return HookViewport(
        viewport_id=_int(raw.get("viewport_id")),
        request=_int(raw.get("request")),
        lops=bool(view.get("lops")),
        display_lop=str(raw.get("display_lop") or ""),
        device=str(vulkan.get("device") or ""),
        used=used,
        cached=cached,
        prims=tuple(prims),
        null_prims=nulls,
        texture_bytes=_int(textures.get("bytes")),
        texture_count=_int(textures.get("count")),
        walk_ms=walk_ms,
    )


def parse(text: str) -> Snapshot | None:
    """``ttvramhook_snapshot``'s output, or ``None`` if it is not one."""
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict) or "viewports" not in raw:
        return None
    viewports = tuple(_viewport(v) for v in raw.get("viewports") or ()
                      if isinstance(v, dict))
    clients = tuple(
        Client(str(c.get("device") or ""), str(c.get("name") or ""),
               _int(c.get("used")), _int(c.get("cached")))
        for c in raw.get("clients") or () if isinstance(c, dict) and c.get("name"))
    return Snapshot(_int(raw.get("request")), viewports, clients)


def match_viewers(viewports: tuple[HookViewport, ...],
                  shown: list[tuple[str, str]]) -> dict[str, HookViewport]:
    """Pair LOP viewers with the hook viewports drawing them.

    ``shown`` is ``(pane, shown LOP path)`` per LOP viewer, in screen order.
    A viewport is matched by the LOP it displays; two viewers showing the
    same LOP take that LOP's viewports in order. Viewers left over (their
    viewer node is not the display LOP the hook sees) take the remaining LOP
    viewports in order. Each viewport is used once.
    """
    free = [v for v in viewports if v.lops]
    out: dict[str, HookViewport] = {}
    for pane, lop in shown:
        for v in free:
            if v.display_lop == lop:
                out[pane] = v
                free.remove(v)
                break
    for pane, _lop in shown:
        if pane not in out and free:
            out[pane] = free.pop(0)
    return out


def measured_clients(snap: Snapshot, shown: list[tuple[str, str]]
                     ) -> tuple[list[Client], dict[str, dict[str, int]]]:
    """The report's clients, and the exact prim bytes per LOP viewer pane.

    Every registry client but the geometry ones keeps its own figure. Each
    geometry client gets the hook's measurement of its viewport: LOP viewers
    by the LOP they show (``shown`` is ``(pane, LOP path)`` per viewer, see
    :func:`match_viewers`), other viewports in order. A measured viewport no
    registry client names (a viewer that has not registered yet) still gets a
    row, so nothing measured is dropped.
    """
    matched = match_viewers(snap.viewports, shown)
    device = next((c.device for c in snap.clients if c.device),
                  next((v.device for v in snap.viewports if v.device), ""))
    out = [c for c in snap.clients if c.kind != "geometry"]
    exact: dict[str, dict[str, int]] = {}
    taken: set[int] = set()
    unmatched: list[Client] = []
    for c in snap.clients:
        if c.kind != "geometry":
            continue
        vp = matched.get(c.pane)
        if vp is None:
            unmatched.append(c)
            continue
        out.append(Client(c.device, c.name, vp.used, vp.cached))
        exact[c.pane] = vp.prim_bytes()
        taken.add(id(vp))
    named = {c.pane for c in snap.clients if c.kind == "geometry"}
    for pane, vp in matched.items():
        if pane not in named:
            out.append(Client(device, f"Vulkan Geometry ({pane})", vp.used, vp.cached))
            exact[pane] = vp.prim_bytes()
            taken.add(id(vp))
    rest = [v for v in snap.viewports if id(v) not in taken and not v.lops]
    for c in unmatched:
        vp = rest.pop(0) if rest else None
        out.append(Client(c.device, c.name, vp.used if vp else 0, vp.cached if vp else 0))
        if vp is not None:
            taken.add(id(vp))
    for vp in snap.viewports:
        if id(vp) not in taken and vp.used:
            out.append(Client(device, f"Vulkan Geometry (viewport {vp.viewport_id})",
                              vp.used, vp.cached))
    return out, exact
