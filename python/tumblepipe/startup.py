"""TumblePipe's Houdini startup: :func:`bootstrap` and what it registers.

``python3.13libs/pythonrc.py`` calls :func:`bootstrap` and nothing else.
Houdini runs every package's ``pythonrc.py`` in one shared ``__main__``, so a
helper defined there can be replaced by another package's file of the same
name before a deferred call fires — TumbleRig's defines
``_register_tumbletrove``, and once silently took TumblePipe's place. The
logic therefore lives here, in a module namespace no other package touches.

The Radial menus it registers are built in ``pipe.houdini.radial``.
"""

from pathlib import Path
import os
import traceback

#: The desktop layout the package ships (desktop/TumblePipe.desk).
DESKTOP_NAME = "TumblePipe"

#: Seconds a GUI session trusts a config file's last check (config.store's
#: stamp window).
GUI_STAMP_TTL = 1.0

#: Node types kept installed so old scenes still load, but hidden from the
#: TAB menu (and so from the catalog's Nodes section) so nobody builds new
#: work on them. th::create_model is the variant-less predecessor of
#: th::create_asset_model.
RETIRED_NODE_TYPES = (
    ("Lop", "th::create_model::1.0"),
)

#: Node types the pipeline still builds and uses, hidden from the TAB menu
#: (and the catalog's Nodes section) because artists want another one.
#: th::import_rig is the one-rig building block th::import_rigs makes a
#: chain of; artists add rigs with th::import_rigs (Ema, Magnus 2026-11).
#: Code still creates it by name, and placed nodes load and work.
UTILITY_NODE_TYPES = (
    ("Sop", "th::import_rig::1.0"),
)

#: Every node type hidden from the TAB menu at startup.
TAB_HIDDEN_NODE_TYPES = RETIRED_NODE_TYPES + UTILITY_NODE_TYPES


def bootstrap() -> None:
    """Register everything TumblePipe adds to a Houdini session.

    - Radial's actions, menus and menu directory, synchronously: Radial's own
      deferred startup scans the menu directories and must find ours.
    - The TumbleTrove package (identity, documentation, the pipeline catalog),
      its scene-load callback that re-runs import nodes, and the workfile
      context provider — deferred in a graphical session, once the UI exists;
      inline in hython, which has no UI.

    Each registration is isolated: one failing must not stop the others, and
    none may stop Houdini from launching, so a failure prints its traceback to
    the console instead of raising.
    """
    import hou

    from tumblepipe.util.logging import setup_logging
    setup_logging()
    _guarded("radial registration", _register_radial)
    if hou.isUIAvailable():
        # Parameter expressions read the config on every cook and frame
        # change, outside any coherent() scope: each read was a stat() of
        # the db file, ~6 ms on an SMB share. Trust a check for a second;
        # another session's write then shows within one. hython and farm
        # jobs keep reading the disk every time.
        from tumblepipe.config.store import set_stamp_ttl
        set_stamp_ttl(GUI_STAMP_TTL)
        # The Jobs popover lists the farm jobs submitted since now.
        _guarded("farm session start", _mark_farm_session_start)
        import hdefereval
        hdefereval.executeDeferred(_register_tumbletrove)
    else:
        _register_tumbletrove()


def _register_radial() -> None:
    from tumblepipe.pipe.houdini import radial
    radial.register(Path(os.environ["TH_PIPELINE_PATH"]))


def _mark_farm_session_start() -> None:
    from tumblepipe.farm.manager.finished import mark_session_start
    mark_session_start()


def select_desktop() -> None:
    """Make the TumblePipe desktop current once the UI is ready, if it exists
    and isn't current already (a desktop switched mid-session is respected
    until the next launch). Called from ``python3.13libs/uiready.py``."""
    import hou

    desktop = {d.name(): d for d in hou.ui.desktops()}.get(DESKTOP_NAME)
    if desktop is not None and hou.ui.curDesktop().name() != DESKTOP_NAME:
        desktop.setAsCurrent()


def _register_tumbletrove() -> None:
    from tumblepipe import workfile_context
    from tumblepipe.asset_browser import load_hook
    from tumblepipe.pipe.houdini import viewer_camera_guard

    # Before the package: its catalog lists only the TAB-visible types.
    _guarded("hidden node types", hide_retired_node_types)
    _guarded("package registration", register_package)
    _guarded("scene-load import refresh", load_hook.register)
    _guarded("viewer camera guard", viewer_camera_guard.register)
    _guarded("workfile context provider", workfile_context.register)
    _guarded("shot slate", _start_shot_slate)
    _guarded("live playblast hotkey", _bind_live_playblast_hotkey)
    _guarded("editor link", _register_editor_link)


def _register_editor_link() -> None:
    """Let Cinedex, started from this session, open workfiles here
    (graphical sessions only; ``pipe.houdini_link``)."""
    import hou

    if hou.isUIAvailable():
        from tumblepipe.asset_browser import editor_requests
        editor_requests.register()


def _bind_live_playblast_hotkey() -> None:
    """The Live Playblast shelf tool's hotkey (graphical sessions only)."""
    import hou

    if hou.isUIAvailable():
        from tumblepipe.pipe.houdini import live_playblast
        live_playblast.bind_hotkey()


def _start_shot_slate() -> None:
    """The shot slate under look-through cameras (graphical sessions only)."""
    import hou

    if hou.isUIAvailable():
        from tumblepipe.pipe.houdini.ui import shot_slate
        shot_slate.start()


def hide_retired_node_types() -> None:
    """Hide every :data:`TAB_HIDDEN_NODE_TYPES` entry (the retired types
    and the utility ones) from the TAB menu. A type that is not installed
    is skipped."""
    import hou

    categories = hou.nodeTypeCategories()
    for category_name, type_name in TAB_HIDDEN_NODE_TYPES:
        category = categories.get(category_name)
        node_type = category.nodeType(type_name) if category else None
        if node_type is not None:
            node_type.setHidden(True)


def _guarded(label: str, fn) -> None:
    try:
        fn()
    except Exception:
        print(f"[tumblepipe] {label} failed:")
        traceback.print_exc()


# ── TumbleTrove package registration ──────────────────────────────────────────


def _make_pipeline_catalog():
    """Build the pipeline catalog, or ``None`` when nothing is configured.

    The factory TumbleTrove calls when the asset-browser panel first opens —
    not at Houdini startup, so registry construction and client setup stay off
    the launch path.

    Imported here rather than at module scope so the catalog's dependencies
    stay off the Houdini launch path.
    """
    from tumblepipe.asset_browser.factory import create_catalog
    return create_catalog()


def register_package():
    """Register TumblePipe with TumbleTrove: identity, docs, menu, catalog.

    TumbleTrove is a declared dependency (hpm.toml). Order does not matter: TumbleTrove's
    menu is declared in XML and reads the package registry each time it opens,
    and the browser follows the registry for the life of the session, so this
    is picked up whether it runs before or after either of them.
    """
    from tumbletrove import package

    # ``docs`` is the shipped ``docs/`` folder (Markdown, rendered offline
    # under **TumbleTrove ▸ Documentation**). The online build of the same
    # pages stays reachable as an extra entry.
    package.register(
        "TumblePipe",
        version=_own_version(),
        description="Tumblehead's USD production pipeline.",
        website="https://tumbletrove.com",
        docs=_package_root() / "docs",
        extra_docs=[("Online documentation", "https://tumblepipe.readthedocs.io")],
        icon="lucide:database",
        menu=[
            package.item("Farm Manager", _open_farm_manager),
            package.item("VRAM Inspector", _open_vram_inspector),
            package.item("Sync with Multi", _sync_with_multi),
        ],
        catalogs=[package.catalog("pipeline", _make_pipeline_catalog)],
    )


def _open_farm_manager() -> None:
    """Open the Farm Manager without submitting anything (imported on click: Qt)."""
    import hou

    from tumblepipe.farm.manager.window import show_manager
    show_manager(parent=hou.qt.mainWindow())


def _open_vram_inspector() -> None:
    """Open the VRAM Inspector (imported on click: it pulls in Qt and pxr)."""
    from tumblepipe.vram import ui
    ui.show()


def _sync_with_multi() -> None:
    """Compare the open Multi workfile with its Multi and offer to bring
    them in step (``pipe.houdini.multi_sync``)."""
    from tumblepipe.pipe.houdini import multi_sync
    multi_sync.sync_with_multi()


def _package_root() -> Path:
    """The package root (the directory holding ``hpm.toml``)."""
    return Path(__file__).resolve().parents[2]


def _own_version() -> str:
    """Our version from the shipped hpm.toml, or empty if unreadable."""
    try:
        import tomllib
        manifest = _package_root() / "hpm.toml"
        with manifest.open("rb") as fh:
            return str(tomllib.load(fh)["package"].get("version", ""))
    except Exception:
        return ""
