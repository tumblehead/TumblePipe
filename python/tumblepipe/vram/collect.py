"""The ``hou`` side of the VRAM Inspector: measure, find, and attribute.

Everything here talks to the running session — the viewport hook's
commands, the Scene Viewers and their stages, viewport settings, node
lookups — and hands plain data to the pure modules (:mod:`.hook`,
:mod:`.report`). Call it on Houdini's main thread.

Houdini's own ``gpumem`` command is never run: its geometry figure is
inflated and it segfaults after a prim is hidden (see :mod:`.hook`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import hou

from . import hook, process
from .clients import Client
from .sizes import TextureRules


def hook_snapshot() -> hook.Snapshot | None:
    """The viewport hook's last snapshot, or ``None`` when it is not loaded.

    Only copies text the hook already recorded during a redraw; it never
    walks a renderer, so it is safe at any moment.
    """
    try:
        out, err = hou.hscript("ttvramhook_snapshot")
    except hou.Error:
        return None
    if err:
        return None
    return hook.parse(out)


def hook_request() -> int:
    """Ask every viewport for a walk on its next redraw; the request number."""
    out, _err = hou.hscript("ttvramhook_request")
    try:
        return int(out.strip())
    except ValueError:
        return 0


def redraw_viewers() -> None:
    """Ask every visible Scene Viewer to redraw (on the next event-loop pass).

    ``hou.ui.triggerUpdate()`` leaves an idle viewport alone; this does not.
    """
    for _owner, tab in _pane_tabs():
        if tab.type() != hou.paneTabType.SceneViewer:
            continue
        try:
            tab.curViewport().draw()
        except (hou.Error, AttributeError):
            pass


def hook_clients(snap: hook.Snapshot) -> tuple[list[Client], dict[str, dict[str, int]]]:
    """The report's clients from a hook snapshot, and exact prim bytes per
    LOP viewer pane (see :func:`.hook.measured_clients`)."""
    shown = []
    for viewer in lop_viewers(list(snap.clients)):
        node = viewer.shown_node()
        shown.append((viewer.pane, node.path() if node is not None else ""))
    return hook.measured_clients(snap, shown)


def free_cached() -> int:
    """Ask every non-geometry client to release its cached memory; bytes freed."""
    out, _err = hou.hscript("ttvramhook_free")
    try:
        return int(out.strip())
    except ValueError:
        return 0


def process_bytes() -> int | None:
    """Dedicated VRAM held by this whole Houdini process (Windows only)."""
    return process.dedicated_bytes(os.getpid())


@dataclass
class Viewer:
    """A Scene Viewer showing a LOP stage, keyed by its geometry client's
    pane name."""

    pane: str
    viewer: hou.SceneViewer

    @property
    def network(self) -> hou.Node:
        return self.viewer.pwd()

    def shown_node(self) -> hou.LopNode | None:
        """The LOP whose stage the viewer draws (its network's viewer node)."""
        net = self.network
        node = net.viewerNode() if hasattr(net, "viewerNode") else None
        return node or (net.displayNode() if hasattr(net, "displayNode") else None)

    def stage(self):
        """The stage on screen. ``SceneViewer.stage()`` raises "not a scene
        graph view" until the viewer has drawn LOPs at least once (seen right
        after ``setPwd``), so fall back to the node it shows."""
        try:
            stage = self.viewer.stage()
            if stage is not None:
                return stage
        except (hou.Error, TypeError):
            pass
        node = self.shown_node()
        return node.stage() if node is not None else None

    def serial(self) -> tuple:
        """Changes whenever the shown stage does: a cheap cache key."""
        node = self.shown_node()
        if node is None:
            return ("", -1)
        return (node.path(), node.cookCount())


def _pane_tabs():
    """Pane tabs that can be on screen: the current desktop's and floating
    panels'. Every other desktop's tabs exist but draw nothing — listing
    them gave one phantom "Geometry" row per saved desktop."""
    seen = set()
    desktop = hou.ui.curDesktop()
    for tab in desktop.paneTabs():
        seen.add(tab.name())
        yield desktop.name(), tab
    for panel in hou.ui.floatingPanels():
        for tab in panel.paneTabs():
            if tab.name() not in seen:
                yield panel.name(), tab


def lop_viewers(clients: list[Client]) -> list[Viewer]:
    """Scene Viewers looking at a LOP network, named the way Houdini's memory
    registry names their geometry client (``<desktop>.<pane tab>.<context>``).

    A viewer is matched to a client by desktop and pane-tab name; one with no
    matching client (its geometry not yet uploaded) gets a synthetic name so
    its stage is still itemised.
    """
    names = {c.pane for c in clients if c.kind == "geometry" and c.pane}
    viewers: list[Viewer] = []
    for owner, tab in _pane_tabs():
        if tab.type() != hou.paneTabType.SceneViewer:
            continue
        try:
            network = tab.pwd()
        except hou.Error:
            continue
        if network is None or network.childTypeCategory() != hou.lopNodeTypeCategory():
            continue
        prefix = f"{owner}.{tab.name()}."
        pane = next((n for n in names if n.startswith(prefix)), prefix + "solaris")
        viewers.append(Viewer(pane, tab))
    return viewers


def texture_rules(viewer: hou.SceneViewer) -> TextureRules:
    """The viewer's 2D texture settings, as the size model takes them."""
    try:
        s = viewer.curViewport().settings()
        mw, mh = s.textureMaxRes2D()
        return TextureRules(
            max_res=(int(mw), int(mh)),
            limit_res=bool(s.textureResLimit2D()),
            scale=float(s.textureScale2D()),
            depth_limit=s.textureBitDepthLimit2D().name(),
            mipmaps=bool(s.textureMipmapping()),
        )
    except (hou.Error, AttributeError):
        return TextureRules()


def owner_resolver(network: hou.Node):
    """A ``prim → node path`` function for prims on ``network``'s stage.

    Houdini records the LOP nodes that edited a prim in its custom data
    (``HoudiniPrimEditorNodes``, session ids, oldest first). The oldest is
    the one that made it. That node may sit deep inside an HDA or a
    ``sopcreate``; the artist wants the node *they* see, so climb to the
    ancestor whose parent is the network the viewer shows (or the top of
    the LOP nesting). Ids that do not resolve to a LOP — stale data from a
    file another session wrote — fall back to the prim's top-level path.
    """
    from .usd_scan import default_owner, editor_node_ids

    cache: dict[int, str] = {}

    def resolve(node_id: int) -> str:
        if node_id in cache:
            return cache[node_id]
        node = hou.nodeBySessionId(node_id)
        path = ""
        if node is not None and isinstance(node, hou.LopNode):
            while True:
                parent = node.parent()
                if (parent is None or parent == network
                        or not isinstance(parent, hou.LopNode)):
                    break
                node = parent
            path = node.path()
        cache[node_id] = path
        return path

    by_prim: dict[str, str] = {}

    def owner_of(prim) -> str:
        # Houdini records editors on the prim a LOP authored — a SOP import's
        # root, not each mesh under it — so take the nearest ancestor that
        # has a record. Cached per path: siblings share their parent's answer.
        key = prim.GetPath().pathString
        if key in by_prim:
            return by_prim[key]
        path = ""
        for node_id in editor_node_ids(prim)[:1]:
            path = resolve(node_id)
        if not path:
            parent = prim.GetParent()
            if parent and not parent.IsPseudoRoot():
                path = owner_of(parent)
        by_prim[key] = path or default_owner(prim)
        return by_prim[key]

    return owner_of
