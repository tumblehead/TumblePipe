"""Houdini-startup registrations for the Radial menu system.

Called by the per-interpreter startup stubs (``python3.11libs/pythonrc.py``
and ``python3.13libs/pythonrc.py``) so Houdini 21 and Houdini 22 register
exactly the same menus — the two stubs must stay thin and identical.

Everything here targets the radial (the Space-key Qt radial), which ships as
its own package — python package ``tumbleradial``. It used to live inside
tumbletrove as ``tumbletrove.radial`` and was split out into its own repo in
tumbletrove v0.14.0; importing it from tumbletrove now fails, and because the
guard below used to swallow that silently, every menu here quietly stopped
registering. The old Houdini-native ``radialmenu/`` system was purged in
favour of the radial; the pipeline-specific menus it carried live on here as:

- ``network.cop`` / ``network.vop`` context menus (registered Python menus,
  same mechanism tumbletrove uses for its built-in sop/lop/network menus).
- ``radial_menus/tumblepipe_pipeline.json`` — static custom menu shipped
  with the package (ASSET / RENDER submenus of pipeline HDAs).
- ``tumblepipe_recipes.json`` + ``tumblepipe_asset_favorites.json`` —
  generated into ``radial_menus/`` at startup from the live Recipes.hda /
  asset-browser favorites, so they cannot drift from what actually exists.
"""

from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


def register_radial(pipeline_path: Path) -> None:
    """Register TumblePipe's actions, context menus and JSON menu directory.

    Order matters: this runs at Houdini startup (synchronous), well before the
    radial's own deferred ``install()`` call, so the autoload picks up the
    menus in ``radial_menus/``.
    """
    try:
        import tumbleradial as radial
    except ImportError:
        # Warn rather than return bare. This guard read "tumbletrove not
        # installed — nothing to register" and swallowed the import error
        # when the radial moved out of tumbletrove (v0.14.0), so every menu
        # below silently stopped registering and nothing said a word.
        logger.warning(
            "tumbleradial is not installed, so TumblePipe's radial menus "
            "(pipeline submenus, recipes, asset favorites, cop/vop network "
            "menus) will not register"
        )
        return

    _register_general_actions(radial)
    _register_cop_menu(radial)
    _register_vop_menu(radial)

    # Generate the dynamic menus + register the menu directory so the
    # autoload picks up both the generated files and the shipped
    # tumblepipe_pipeline.json.
    menu_dir = pipeline_path / 'radial_menus'
    menu_dir.mkdir(exist_ok=True)
    _generate_recipes_radial(radial, menu_dir, key="Alt+R")
    _generate_asset_favorites_radial(radial, menu_dir, key="Alt+F")
    radial.add_custom_menu_dir(menu_dir)


def _defer(fn):
    """Run *fn* on Houdini's GUI thread after the current event finishes.

    The radial controller fires callbacks from inside its release handler.
    Modal dialogs (``hou.ui.displayMessage``) opened synchronously from
    there block the widget's own teardown and the whole UI looks frozen.
    Deferring lets the radial cleanly close before the dialog appears.
    """
    try:
        import hdefereval
        hdefereval.executeDeferred(fn)
    except Exception:
        fn()  # best-effort fallback for environments without hdefereval


# ── General pipeline actions ─────────────────────────────────────────────────

def _register_general_actions(radial) -> None:
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


# ── COP (Copernicus) context menu ────────────────────────────────────────────
#
# Ported from the native radialmenu/definitions/cop.json + cop/composite.json.
# Registered at "network.cop" — the same context ID tumbletrove's detector
# emits for Copernicus networks.

def _cop_nodes_call(fn_name: str, *args):
    """Callback running a tumblepipe.tools.coputils helper on the selection."""
    def _cb():
        import hou
        from tumblepipe.tools import coputils
        getattr(coputils, fn_name)(list(hou.selectedNodes()), *args)
    return _cb


def _register_cop_menu(radial) -> None:
    _node = radial.make_node_callback
    act = radial.register_action
    item = radial.item_from_action

    act("cop.convert",       "Convert",   None, "COP_convertnormal")
    act("cop.convert.mono",  "Mono",  _cop_nodes_call("type_convert", "mono"), "COP2_quantize")
    act("cop.convert.uv",    "UV",    _cop_nodes_call("type_convert", "uv"),   "DATATYPES_uv")
    act("cop.convert.rgb",   "RGB",   _cop_nodes_call("type_convert", "rgb"),  "DATATYPES_rgb")
    act("cop.convert.rgba",  "RGBA",  _cop_nodes_call("type_convert", "rgba"), "DATATYPES_rgba")
    _convert = [item(f"cop.convert.{k}") for k in ("mono", "uv", "rgb", "rgba")]

    act("cop.pattern",           "Pattern",       None,                   "COP_ramp")
    act("cop.pattern.fractal",   "Fractal Noise", _node("fractalnoise"),  "COP_noise")
    act("cop.pattern.worley",    "Worley Noise",  _node("worleynoise"),   "COP_worleynoise")
    act("cop.pattern.tile",      "Tile",          _node("tilepattern"),   "COP_tilepattern")
    act("cop.pattern.rasterize", "Rasterize Geo", _node("rasterizegeo"),  "COP_rasterize")
    act("cop.pattern.sdf",       "SDF Shape",     _node("sdfshape"),      "COP_sdfshape")
    act("cop.pattern.stamp",     "Stamp",         _node("stamppoint"),    "COP_stamppoint")
    act("cop.pattern.ramp",      "Ramp",          _node("ramp"),          "COP_ramp")
    _pattern = [item(f"cop.pattern.{k}") for k in
                ("fractal", "worley", "tile", "rasterize", "sdf", "stamp", "ramp")]

    act("cop.filter",         "Filter",       None,                  "COP_blur")
    act("cop.filter.blur",    "Blur",         _node("blur"),         "COP_blur")
    act("cop.filter.dilate",  "Dilate/Erode", _node("dilateerode"),  "COP_dilateerode")
    act("cop.filter.distort", "Distort",      _node("distort"),      "COP_distort")
    act("cop.filter.remap",   "Remap",        _node("remap"),        "COP_remap")
    act("cop.filter.feather", "Feather",      _node("feather"),      "COP_feather")
    act("cop.filter.hsv",     "HSV Adjust",   _node("hsv"),          "COP_hsv")
    act("cop.filter.invert",  "Invert",       _node("invert"),       "COP_invert")
    _filter = [item(f"cop.filter.{k}") for k in
               ("blur", "dilate", "distort", "remap", "feather", "hsv", "invert")]

    act("cop.comp",          "Composite", None,                                "COP_blend")
    for mode in ("over", "blend", "multiply", "add", "under", "subtract", "divide"):
        act(f"cop.comp.{mode}", mode.capitalize(),
            _cop_nodes_call("composite", mode), f"COP_{mode}")
    _comp = [item(f"cop.comp.{m}") for m in
             ("over", "blend", "multiply", "add", "under", "subtract", "divide")]

    act("cop.file",   "File",   _node("file"),                  "COP_file")
    act("cop.render", "Render", _cop_nodes_call("render_cop"),  "NETWORKS_cop")

    radial.register("network.cop", radial.menu([
        item("cop.convert", children=_convert),
        item("cop.pattern", children=_pattern),
        item("cop.filter",  children=_filter),
        item("cop.comp",    children=_comp),
        item("cop.file"),
        item("cop.render"),
    ], label="COP"))


# ── VOP (MaterialX) context menu ─────────────────────────────────────────────
#
# Ported from the native radialmenu/definitions/vop.json, with the broken
# entries fixed (two malformed node names) and the mislabeled "Math"
# submenu renamed to what its nodes actually are.

def _register_vop_menu(radial) -> None:
    _node = radial.make_node_callback
    act = radial.register_action
    item = radial.item_from_action

    act("vop.image",           "Image",          None,                              "PARTS_image")
    act("vop.image.image",     "Image",          _node("mtlximage"),                "PARTS_image")
    act("vop.image.tiled",     "Tiled Image",    _node("mtlxtiledimage"),           "CHOP_image")
    act("vop.image.sequence",  "Image Sequence", _node("mtlximagesequence"),        "NETWORKS_cop2")
    act("vop.image.triplanar", "Triplanar",      _node("mtlxtriplanarprojection"),  "VOP_uvtriplanarproject")
    _image = [item(f"vop.image.{k}") for k in ("image", "tiled", "sequence", "triplanar")]

    act("vop.adjust",         "Adjust",        None,                       "COP_remap")
    act("vop.adjust.remap",   "Remap",         _node("mtlxremap"),         "COP_remap")
    act("vop.adjust.correct", "Color Correct", _node("mtlxcolorcorrect"),  "BUTTONS_secondary_colors")
    _adjust = [item(f"vop.adjust.{k}") for k in ("remap", "correct")]

    act("vop.gen",           "Generators",        None,                          "VOP_unifiednoise")
    act("vop.gen.constant",  "Constant",          _node("mtlxconstant"),         "VOP_constant")
    act("vop.gen.noise",     "Noise",             _node("mtlxfractal3d"),        "VOP_unifiednoise")
    act("vop.gen.occlusion", "Occlusion",         _node("mtlxambientocclusion"), "COP_heighttoambientocclusion")
    act("vop.gen.voronoi",   "Voronoi",           _node("kma_voronoinoise3d"),   "VOP_voronoise")
    act("vop.gen.geocolor",  "Geometry Color",    _node("mtlxgeomcolor"),        "VOP_surfacecolor")
    act("vop.gen.geoprop",   "Geometry Property", _node("mtlxgeompropvalue"),    "BUTTONS_attribute")
    act("vop.gen.curvature", "Curvature",         _node("kma_curvature"),        "VOP_curvature")
    _gen = [item(f"vop.gen.{k}") for k in
            ("constant", "noise", "occlusion", "voronoi", "geocolor", "geoprop", "curvature")]

    act("vop.util",           "Utility",    None,                    "VOP_multiply")
    act("vop.util.normalmap", "Normal Map", _node("mtlxnormalmap"),  "VOP_displacetexture")
    act("vop.util.bump",      "Bump",       _node("mtlxbump"),       "VOP_bump")
    act("vop.util.multiply",  "Multiply",   _node("mtlxmultiply"),   "VOP_multiply")
    act("vop.util.add",       "Add",        _node("mtlxadd"),        "VOP_add")
    act("vop.util.subtract",  "Subtract",   _node("mtlxsubtract"),   "VOP_subtract")
    act("vop.util.divide",    "Divide",     _node("mtlxdivide"),     "VOP_divide")
    _util = [item(f"vop.util.{k}") for k in
             ("normalmap", "bump", "multiply", "add", "subtract", "divide")]

    radial.register("network.vop", radial.menu([
        item("vop.image",  children=_image),
        item("vop.adjust", children=_adjust),
        item("vop.gen",    children=_gen),
        item("vop.util",   children=_util),
    ], label="VOP"))


# ── Recipes radial (generated from Recipes.hda) ──────────────────────────────

def _safe_action_key(prefix: str, raw: str) -> str:
    """Build a stable catalog action key from a free-form id."""
    return prefix + "".join(
        c if (c.isalnum() or c in ".-_") else "_" for c in raw)


def _recipe_callback(recipe_name: str):
    def _cb():
        def _do():
            import hou
            from tumblepipe.tools.utils import create_recipe
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            create_recipe(recipe_name, pane=pane, autoplace=False)
        _defer(_do)
    return _cb


def _generate_recipes_radial(radial, menu_dir: Path, *, key: str = "Alt+R") -> None:
    """Register one action per recipe in Recipes.hda and write a JSON radial
    menu listing them. Generated from the live HDA so the menu cannot drift
    from the recipes that actually exist.
    """
    import hou
    recipe_file = hou.text.expandString('$TH_PIPELINE_PATH/otls/Recipes.hda')
    definitions = hou.hda.definitionsInFile(recipe_file)

    ring_items: list[dict] = []
    for definition in definitions[:9]:  # radial ring max
        type_name = definition.nodeTypeName()
        # applyTabToolRecipe wants the unversioned recipe name.
        recipe_name = type_name.rsplit('::', 1)[0] if type_name.count('::') > 1 else type_name
        label = definition.description() or recipe_name
        action_key = _safe_action_key("tumblepipe.recipe.", recipe_name)
        radial.register_action(
            key=action_key,
            label=label,
            callback=_recipe_callback(recipe_name),
            icon=definition.icon() or "lucide:package",
            contexts=("network",),
        )
        ring_items.append({
            "action": action_key,
            "label_override": None,
            "icon_override":  None,
            "hidden":         False,
            "close_on_select": True,
        })

    # A radial ring needs at least 2 filled slots to validate — empty
    # placeholder padding does not count. Fewer recipes than that: no menu.
    if len(ring_items) < 2:
        stale = menu_dir / 'tumblepipe_recipes.json'
        if stale.exists():
            stale.unlink()
        return

    _write_menu_spec(menu_dir, name="tumblepipe_recipes",
                     label="Recipes", key=key, ring=ring_items)


# ── Asset-favorites radial (generated from asset-browser favorites) ─────────

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
                from tumbletrove.asset_browser.core.drop import build_drop_context
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
        _defer(_do)
    return _cb


def _collect_asset_favorites() -> list[tuple[object, str, str]]:
    """Favorites across every asset-browser catalog: (catalog, catalog_id,
    asset_id) entries, capped at the radial ring maximum of 9. Empty when
    the asset browser is unavailable or has no favorites."""
    try:
        from tumbletrove import asset_browser as ab
        ab._ensure_initialized()
    except Exception:
        return []  # asset_browser not installed/initialised — skip silently
    if ab._user_collections is None or ab._registry is None:
        return []

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
    return favorites[:9]


def _generate_asset_favorites_radial(radial, menu_dir: Path, *,
                                     key: str = "Alt+F") -> None:
    """Register one catalog action per asset-browser favorite, then write
    a JSON radial menu listing them. Static across the session — call
    again to pick up favorites added/removed mid-session.

    Surfaces a useful cross-cutting demo: asset-browser favorites become
    radial slots that drop the asset into whatever network the cursor is
    over when the slot fires.
    """
    favorites = _collect_asset_favorites()

    # A radial ring needs at least 2 filled slots to validate — empty
    # placeholder padding does not count, so with fewer favorites the
    # loader would reject the spec with a user-facing error. No menu,
    # and any menu written by an earlier session state is removed.
    if len(favorites) < 2:
        stale = menu_dir / 'tumblepipe_asset_favorites.json'
        if stale.exists():
            stale.unlink()
        return

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

    _write_menu_spec(menu_dir, name="tumblepipe_asset_favorites",
                     label="Asset Favorites", key=key, ring=ring_items)


# ── Shared JSON-spec writer ──────────────────────────────────────────────────

def _write_menu_spec(menu_dir: Path, *, name: str, label: str,
                     key: str, ring: list[dict]) -> None:
    spec = {
        "schema_version": 1,
        "name":    name,
        "label":   label,
        "key":     key,
        "context": "network",
        "flags":   {"latch": True, "tap_release": False,
                    "keep_sub_open": False, "activate_on_release": False},
        "ring":    ring,
        "center":  None,
        "center_right": None,
        "zones":   {z: None for z in
                    ("top", "bottom", "left", "right", "left2", "right2")},
        "drawer":  [], "menubar": [], "submenus": {},
        "release_action": "", "press_action": "", "mclick_action": "",
    }
    target = menu_dir / f"{name}.json"
    target.write_text(json.dumps(spec, indent=2), encoding="utf-8")
