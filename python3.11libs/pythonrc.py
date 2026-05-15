"""Houdini Python startup hook for TumblePipe.

Adds `$TH_PIPELINE_PATH/python` to sys.path so `tumblepipe` imports.

The tumbleResolver USD plugin is registered by Houdini itself via the
`PXR_PLUGINPATH_NAME` (and platform DLL search path) entries in
`hpm.toml [env]` — those are applied during Houdini's package-loading
phase, before USD's Plug.Registry first-touches. No Python-side plugin
registration is needed (or possible — Plug.Registry is a one-shot
scanner read once at USD init, well before this hook runs).
"""

import os
import sys
from pathlib import Path


def load():
    pipeline_path = Path(os.environ['TH_PIPELINE_PATH'])
    packages_path = pipeline_path / 'python'
    sys.path.insert(0, str(packages_path))

    # Register TumblePipe-shipped radial menus + their actions with the
    # tumbletrove radial system. Wrapped in a try/except so a missing
    # `radial` module doesn't block the rest of TumblePipe loading.
    _register_radial(pipeline_path)


def _register_radial(pipeline_path: Path) -> None:
    """Register TumblePipe's custom actions and JSON menu directory.

    Order matters: this runs at Houdini startup (synchronous), well before
    tumbletrove's deferred ``radial.install()`` call, so the autoload picks
    up the menu we ship in ``radial_menus/``.
    """
    try:
        import radial
    except ImportError:
        return  # tumbletrove not installed — nothing to register

    def _defer(fn):
        """Run *fn* on Houdini's GUI thread after the current event finishes.

        The radial controller fires callbacks from inside its release
        handler. Modal dialogs (``hou.ui.displayMessage``) opened
        synchronously from there block the widget's own teardown and the
        whole UI looks frozen. Deferring lets the radial cleanly close
        before the dialog appears.
        """
        try:
            import hdefereval
            hdefereval.executeDeferred(fn)
        except Exception:
            fn()  # best-effort fallback for environments without hdefereval

    # Action: show project info (path / user / project name). Each field
    # is read independently so a single missing env var doesn't blank the
    # whole message — the dev session typically lacks one or two.
    def _show_project_info():
        def _safe(fn):
            try:
                return fn() or "(unset)"
            except Exception as exc:
                return f"(error: {exc})"

        def _do():
            import hou
            try:
                from tumblepipe import api
            except Exception as exc:
                hou.ui.displayMessage(
                    f"tumblepipe.api unavailable: {exc}", title="TumblePipe")
                return
            msg = (
                f"TumblePipe project info\n\n"
                f"Project name : {_safe(api.get_project_name)}\n"
                f"User         : {_safe(api.get_user_name)}\n"
                f"Pipeline     : {_safe(api.get_pipeline_path)}\n"
                f"Project path : {_safe(api.get_project_path)}\n"
                f"Edit path    : {_safe(api.get_edit_path)}"
            )
            hou.ui.displayMessage(msg, title="TumblePipe")
        _defer(_do)

    radial.register_action(
        key="tumblepipe.show_project_info",
        label="Project info",
        callback=_show_project_info,
        icon="lucide:info",
        contexts=(),  # always available
    )

    # Action: refresh the global cache (cheap; useful after env changes).
    def _refresh_cache():
        def _do():
            try:
                from tumblepipe import api
                api.refresh_global_cache(purpose="radial")
            except Exception as exc:
                import hou
                hou.ui.displayMessage(
                    f"refresh_global_cache failed: {exc}",
                    severity=hou.severityType.Warning, title="TumblePipe")
        _defer(_do)

    radial.register_action(
        key="tumblepipe.refresh_global_cache",
        label="Refresh cache",
        callback=_refresh_cache,
        icon="lucide:refresh-cw",
        contexts=("network",),
    )

    # Generate per-favorite actions + a matching menu, then register the
    # menu directory so the autoload picks it up.
    menu_dir = pipeline_path / 'radial_menus'
    menu_dir.mkdir(exist_ok=True)
    _generate_asset_favorites_radial(radial, menu_dir, key="Alt+F")
    radial.add_custom_menu_dir(menu_dir)


def _safe_action_key(prefix: str, raw: str) -> str:
    """Build a stable catalog action key from a free-form id."""
    return prefix + "".join(
        c if (c.isalnum() or c in ".-_") else "_" for c in raw)


def _make_asset_drop_callback(catalog, asset_id: str):
    """Build a callback that drops the favorite asset into the network
    under the cursor when the radial slot fires.

    Uses the asset_browser drop pipeline: build a DropContext from the
    pane under the cursor, try the catalog's ``on_drop`` first, fall
    back to executing the first non-download action.
    """
    def _cb():
        def _do():
            import hou
            try:
                detail = catalog.get_detail(asset_id)
            except Exception as exc:
                hou.ui.setStatusMessage(
                    f"asset {asset_id!r} unavailable: {exc}",
                    severity=hou.severityType.Warning)
                return
            # Prefer the catalog's on_drop with a real DropContext if the
            # cursor is over a network editor — that produces the best
            # placement (auto-connect, position from cursor, etc.).
            try:
                from asset_browser.core.drop import build_drop_context
                pane = hou.ui.paneTabUnderCursor()
                drop = build_drop_context(pane) if pane is not None else None
            except Exception:
                drop = None
            if drop is not None:
                try:
                    if catalog.on_drop(detail, drop):
                        return
                except Exception:
                    pass  # fall through to the action route
            # Fallback: execute the first non-download (file_id is None) action
            try:
                actions = catalog.get_actions(detail)
            except Exception as exc:
                hou.ui.setStatusMessage(
                    f"no actions for {detail.name}: {exc}",
                    severity=hou.severityType.Warning)
                return
            for a in actions:
                if getattr(a, "file_id", None) is None:
                    catalog.execute_action(a.id, detail)
                    return
            hou.ui.setStatusMessage(
                f"no drop action for {detail.name}",
                severity=hou.severityType.Warning)

        # Defer like the other tumblepipe.* actions so the radial widget
        # finishes its teardown before the catalog handler runs.
        try:
            import hdefereval
            hdefereval.executeDeferred(_do)
        except Exception:
            _do()
    return _cb


def _generate_asset_favorites_radial(radial, menu_dir: "Path", *,
                                     key: str = "Alt+F") -> None:
    """Register one catalog action per asset-browser favorite, then write
    a JSON radial menu listing them. Static across the session — call
    again to pick up favorites added/removed mid-session.

    Surfaces a useful cross-cutting demo: asset-browser favorites become
    radial slots that drop the asset into whatever network the cursor is
    over when the slot fires.
    """
    import json
    try:
        import asset_browser as ab
        ab._ensure_initialized()
    except Exception:
        return  # asset_browser not installed/initialised — skip silently
    if ab._user_collections is None or ab._registry is None:
        return

    # Collect favorites cross-catalog. Each entry: (catalog, catalog_id, asset_id).
    favorites: list[tuple[object, str, str]] = []
    for cat in ab._registry.catalogs:
        try:
            cat_id = cat.id
        except Exception:
            continue
        col = ab._user_collections.get(cat_id, "__favorites__")
        if col is None:
            continue
        for asset_id in col.asset_refs:
            favorites.append((cat, cat_id, asset_id))

    # Cap at 9 (radial ring max).
    favorites = favorites[:9]

    ring_items: list[dict] = []
    for cat, cat_id, asset_id in favorites:
        # Resolve display label cheaply; fall back to asset_id if the
        # catalog's pipeline client isn't ready yet.
        label = asset_id
        try:
            detail = cat.get_detail(asset_id)
            label = detail.name or asset_id
        except Exception:
            pass
        action_key = _safe_action_key(
            prefix="tumblepipe.asset.",
            raw=f"{cat_id}.{asset_id}",
        )
        radial.register_action(
            key=action_key,
            label=label,
            callback=_make_asset_drop_callback(cat, asset_id),
            icon="lucide:star",
            contexts=("network",),
        )
        ring_items.append({
            "action": action_key,
            "label_override": None,
            "icon_override":  None,
            "hidden":         False,
            "close_on_select": False,
        })

    # Pad to >= 2 ring items so the spec validates.
    while len(ring_items) < 2:
        ring_items.append({
            "action": "", "label_override": None, "icon_override": None,
            "hidden": False, "close_on_select": False,
        })

    spec = {
        "schema_version": 1,
        "name":    "tumblepipe_asset_favorites",
        "label":   "Asset Favorites",
        "key":     key,
        "context": "network",
        "flags":   {"latch": True, "tap_release": False,
                    "keep_sub_open": False, "activate_on_release": False},
        "ring":    ring_items,
        "center":  None,
        "center_right": None,
        "zones":   {z: None for z in
                    ("top", "bottom", "left", "right", "left2", "right2")},
        "drawer":  [], "menubar": [], "submenus": {},
        "release_action": "", "press_action": "", "mclick_action": "",
    }
    target = menu_dir / f"{spec['name']}.json"
    target.write_text(json.dumps(spec, indent=2), encoding="utf-8")


load()
