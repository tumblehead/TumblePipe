"""TumblePipe's Radial menus: :func:`register`, called by
``tumblepipe.startup.bootstrap``.

Everything here targets the radial (the Space-key Qt radial), which ships as
its own package, python package ``tumbleradial``. The pipeline's menus are:

- ``network.cop`` / ``network.vop`` context menus, built in Python. The COP
  menu is bound to the chord Radial opens its own built-in menus with; the
  VOP (material) menu to :data:`VOP_MENU_KEY`, because over a VOP network
  that chord (X) is Houdini's Visualize Output (see
  :func:`_bind_context_menus`).
- the ``viewer.lop`` menu on :data:`SELECTION_MENU_KEY`: open a department
  workfile of the asset selected in the Solaris viewport.
- ``radial_menus/tumblepipe_pipeline.json``: a static custom menu shipped
  with the package (ASSET / RENDER submenus of pipeline HDAs).
- ``tumblepipe_recipes.json`` + ``tumblepipe_asset_favorites.json``:
  generated at startup from the live Recipes.hda and asset-browser
  favorites, so they cannot drift from what exists. They are written to
  :func:`user_menu_dir`, never into the package, and only in a graphical
  session.
"""

from pathlib import Path
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

#: The generated menus' file names (without ``.json``).
RECIPES_MENU = "tumblepipe_recipes"
FAVORITES_MENU = "tumblepipe_asset_favorites"

#: Most slots a radial ring holds.
RING_MAX = 9


def user_menu_dir(environ=None, platform: str | None = None) -> Path:
    """Where this user's generated menus are written:
    ``%LOCALAPPDATA%/tumblepipe/radial_menus`` on Windows,
    ``$XDG_DATA_HOME`` (or ``~/.local/share``) on Linux,
    ``~/Library/Application Support`` on macOS."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path(environ.get('HOME') or environ.get('USERPROFILE') or Path.home())
    if platform.startswith('win'):
        base = Path(environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    elif platform == 'darwin':
        base = home / 'Library' / 'Application Support'
    else:
        base = Path(environ.get('XDG_DATA_HOME') or home / '.local' / 'share')
    return base / 'tumblepipe' / 'radial_menus'


def register(pipeline_path: Path) -> None:
    """Register TumblePipe's actions, context menus and menu directories.

    This runs at Houdini startup (synchronous), before Radial's own deferred
    startup scans the custom-menu directories, so that scan picks up the
    menus registered here.

    The context menus are bound before anything is written, so a menu file
    that cannot be written takes nothing else with it. The generated menus
    (and their actions) exist only in a graphical session: nothing opens a
    radial without a UI, and every hython, farm tasks included, runs this.
    """
    import hou
    import tumbleradial as radial

    _register_general_actions(radial)
    context_menus = {
        "network.cop": _build_cop_menu(radial),
    }
    keyed_menus = {
        (VOP_MENU_KEY, "network.vop"): _build_vop_menu(radial),
    }
    selection_menu = _build_selection_menu(radial)
    if selection_menu is not None:
        keyed_menus[(SELECTION_MENU_KEY, "viewer.lop")] = selection_menu
    _bind_context_menus(radial, context_menus, keyed_menus)

    if hou.isUIAvailable():
        menu_dir = user_menu_dir()
        menu_dir.mkdir(parents=True, exist_ok=True)
        _write_ring_menu(menu_dir, name=RECIPES_MENU, label="Recipes", key="Alt+R",
                         ring=_register_recipe_actions(radial)[:RING_MAX])
        _write_ring_menu(menu_dir, name=FAVORITES_MENU, label="Asset Favorites", key="Alt+F",
                         ring=_register_favorite_actions(radial))
        # Before the package's folder: of two menus with one name Radial
        # takes the first, so a copy an older TumblePipe wrote into the
        # package is shadowed.
        radial.add_custom_menu_dir(menu_dir)
    radial.add_custom_menu_dir(pipeline_path / 'radial_menus')


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
                f"Project path : {_safe(api.get_project_path)}"
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

    # Action: live playblast of the selected (or the scene's only)
    # th::animate / th::playblast; the animation radial lists it.
    def _live_playblast():
        def _do():
            from tumblepipe.pipe.houdini import live_playblast
            live_playblast.start_selected()
        _defer(_do)

    radial.register_action(
        key="tumblepipe.live_playblast",
        label="Live playblast",
        callback=_live_playblast,
        icon="lucide:play",
        contexts=(),  # the viewport too, where the animator works
    )


# ── COP (Copernicus) context menu ────────────────────────────────────────────
#
# Ported from the native radialmenu/definitions/cop.json + cop/composite.json.
# Bound at "network.cop", the context ID Radial's detector reports for a
# Copernicus network.

def _cop_nodes_call(fn_name: str, *args):
    """Callback running a tumblepipe.tools.coputils helper on the selection."""
    def _cb():
        import hou
        from tumblepipe.tools import coputils
        getattr(coputils, fn_name)(list(hou.selectedNodes()), *args)
    return _cb


def _build_cop_menu(radial):
    _node = radial.make_node_callback
    act = radial.register_action
    item = radial.item_from_action

    act("cop.convert",       "Convert",   None, "COP2_convert")
    act("cop.convert.mono",  "Mono",  _cop_nodes_call("type_convert", "mono"), "COP2_quantize")
    act("cop.convert.uv",    "UV",    _cop_nodes_call("type_convert", "uv"),   "DATATYPES_uv")
    act("cop.convert.rgb",   "RGB",   _cop_nodes_call("type_convert", "rgb"),  "DATATYPES_rgb")
    act("cop.convert.rgba",  "RGBA",  _cop_nodes_call("type_convert", "rgba"), "DATATYPES_rgba")
    _convert = [item(f"cop.convert.{k}") for k in ("mono", "uv", "rgb", "rgba")]

    act("cop.pattern",           "Pattern",       None,                   "COP2_ramp")
    act("cop.pattern.fractal",   "Fractal Noise", _node("fractalnoise"),  "COP2_noise")
    act("cop.pattern.worley",    "Worley Noise",  _node("worleynoise"),   "VOP_worleynoise")
    act("cop.pattern.tile",      "Tile",          _node("tilepattern"),   "COP_tilepattern")
    act("cop.pattern.rasterize", "Rasterize Geo", _node("rasterizegeo"),  "COP_rasterize")
    act("cop.pattern.sdf",       "SDF Shape",     _node("sdfshape"),      "COP_monotosdf")
    act("cop.pattern.stamp",     "Stamp",         _node("stamppoint"),    "COP_stamppoint")
    act("cop.pattern.ramp",      "Ramp",          _node("ramp"),          "COP2_ramp")
    _pattern = [item(f"cop.pattern.{k}") for k in
                ("fractal", "worley", "tile", "rasterize", "sdf", "stamp", "ramp")]

    act("cop.filter",         "Filter",       None,                  "COP2_blur")
    act("cop.filter.blur",    "Blur",         _node("blur"),         "COP2_blur")
    act("cop.filter.dilate",  "Dilate/Erode", _node("dilateerode"),  "COP2_dilateerode")
    act("cop.filter.distort", "Distort",      _node("distort"),      "COP_distort")
    act("cop.filter.remap",   "Remap",        _node("remap"),        "COP_remap")
    act("cop.filter.feather", "Feather",      _node("feather"),      "COP_feather")
    act("cop.filter.hsv",     "HSV Adjust",   _node("hsv"),          "COP2_hsv")
    act("cop.filter.invert",  "Invert",       _node("invert"),       "COP2_invert")
    _filter = [item(f"cop.filter.{k}") for k in
               ("blur", "dilate", "distort", "remap", "feather", "hsv", "invert")]

    act("cop.comp",          "Composite", None,                                "COP2_blend")
    # Houdini 22 has COP icons for over, multiply, add, under and subtract
    # only; blend and divide borrow the legacy compositor's and VOPs'.
    comp_icons = {"blend": "COP2_blend", "divide": "VOP_divide"}
    for mode in ("over", "blend", "multiply", "add", "under", "subtract", "divide"):
        act(f"cop.comp.{mode}", mode.capitalize(),
            _cop_nodes_call("composite", mode), comp_icons.get(mode, f"COP_{mode}"))
    _comp = [item(f"cop.comp.{m}") for m in
             ("over", "blend", "multiply", "add", "under", "subtract", "divide")]

    act("cop.file",   "File",   _node("file"),                  "COP2_file")
    act("cop.render", "Render", _cop_nodes_call("render_cop"),  "NETWORKS_cop")

    return radial.menu([
        item("cop.convert", children=_convert),
        item("cop.pattern", children=_pattern),
        item("cop.filter",  children=_filter),
        item("cop.comp",    children=_comp),
        item("cop.file"),
        item("cop.render"),
    ], label="COP")


# ── VOP (MaterialX) context menu ─────────────────────────────────────────────
#
# First ported from the native radialmenu/definitions/vop.json. Brought up
# to Radial's own network menus (Discord, Remy 2026-11-11: "looks to be an
# old version"): it behaves as they do (hold, release over a slice to
# place it, tap to latch), and its centre places a surface (release:
# Standard Surface; open it for OpenPBR, Surface Material, Displacement).
# The old "Image Sequence" slice placed mtlximagesequence, a node Houdini
# 22 does not have; an image sequence is an Image with <frame> in its file.

#: Every node type the VOP menu places, for tests/test_radial_startup.py.
VOP_MENU_NODES = (
    "mtlxstandard_surface", "mtlxopen_pbr_surface", "mtlxsurfacematerial",
    "mtlxdisplacement",
    "mtlximage", "mtlxtiledimage", "mtlxtriplanarprojection",
    "mtlxremap", "mtlxcolorcorrect",
    "mtlxconstant", "mtlxfractal3d", "mtlxambientocclusion",
    "kma_voronoinoise3d", "mtlxgeomcolor", "mtlxgeompropvalue", "kma_curvature",
    "mtlxnormalmap", "mtlxbump", "mtlxmultiply", "mtlxadd", "mtlxsubtract",
    "mtlxdivide",
)


def _build_vop_menu(radial):
    _node = radial.make_node_callback
    act = radial.register_action
    item = radial.item_from_action

    act("vop.surface",              "Surface",          None,                            "VOP_surfacemodel")
    act("vop.surface.standard",     "Standard Surface", _node("mtlxstandard_surface"),   "VOP_surfacemodel")
    act("vop.surface.openpbr",      "OpenPBR",          _node("mtlxopen_pbr_surface"),   "VOP_principled")
    act("vop.surface.material",     "Surface Material", _node("mtlxsurfacematerial"),    "VOP_kma_mtlx")
    act("vop.surface.displacement", "Displacement",     _node("mtlxdisplacement"),       "VOP_displace")
    # Centre slots: top, right, bottom, left.
    _surface = [item(f"vop.surface.{k}") for k in
                ("standard", "openpbr", "material", "displacement")]

    act("vop.image",           "Image",          None,                              "PARTS_image")
    act("vop.image.image",     "Image",          _node("mtlximage"),                "PARTS_image")
    act("vop.image.tiled",     "Tiled Image",    _node("mtlxtiledimage"),           "CHOP_image")
    act("vop.image.triplanar", "Triplanar",      _node("mtlxtriplanarprojection"),  "VOP_uvtriplanarproject")
    _image = [item(f"vop.image.{k}") for k in ("image", "tiled", "triplanar")]

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
    act("vop.util.normalmap", "Normal Map", _node("mtlxnormalmap"),  "VOP_displace")
    act("vop.util.bump",      "Bump",       _node("mtlxbump"),       "COP2_bump")
    act("vop.util.multiply",  "Multiply",   _node("mtlxmultiply"),   "VOP_multiply")
    act("vop.util.add",       "Add",        _node("mtlxadd"),        "VOP_add")
    act("vop.util.subtract",  "Subtract",   _node("mtlxsubtract"),   "VOP_subtract")
    act("vop.util.divide",    "Divide",     _node("mtlxdivide"),     "VOP_divide")
    _util = [item(f"vop.util.{k}") for k in
             ("normalmap", "bump", "multiply", "add", "subtract", "divide")]

    return radial.menu(
        [
            item("vop.image",  children=_image),
            item("vop.adjust", children=_adjust),
            item("vop.gen",    children=_gen),
            item("vop.util",   children=_util),
        ],
        label="Material",
        center=item("vop.surface", children=_surface,
                    release_callback=_node("mtlxstandard_surface")),
        # As Radial's own network menus: hold and release over a slice to
        # place it, or tap to keep the menu open and click.
        latch=False,
        tap_release=True,
        activate_on_release=True,
    )


# ── Selected asset: open a department workfile ──────────────────────────────
#
# Discord, Remy 2026-11-19: select an asset in the Solaris viewport and jump
# to its model / blendshape / lookdev / rig workfile from a radial around it.
# The asset is read off the selected prims (pipe.usd.entity_stamp), never
# from their names.

#: The key the selected asset's department menu opens on, over a Solaris
#: viewport. A key of its own: Radial's *Open menus with* key opens Radial's
#: own viewport menu there.
SELECTION_MENU_KEY = "Alt+D"

#: Action key prefix; the department name follows.
OPEN_SELECTED_PREFIX = "tumblepipe.open_selected."

#: Seconds one reading of the selection serves the menu's slices. A radial
#: asks every slice whether it is enabled each time it paints.
_SELECTION_TTL = 0.5

_selection_read: tuple[float, str, frozenset] | None = None


def _selected_asset() -> tuple[str, frozenset]:
    """``(name, departments)`` of the one entity the LOP selection belongs
    to; ``("", frozenset())`` when it names none or several."""
    global _selection_read
    import time

    now = time.monotonic()
    if _selection_read is not None and now - _selection_read[0] < _SELECTION_TTL:
        return _selection_read[1], _selection_read[2]
    name, departments = "", frozenset()
    try:
        from tumblepipe.pipe.houdini import selection_entities
        entity_uri = selection_entities.selected_entity()
        if entity_uri is not None:
            name = entity_uri.segments[-1]
            departments = frozenset(
                selection_entities.selected_entity_departments())
    except Exception:
        logger.debug("reading the LOP selection failed", exc_info=True)
    _selection_read = (now, name, departments)
    return name, departments


def _open_selected_callback(department: str, new_instance: bool):
    def _cb():
        def _do():
            from tumblepipe.pipe.houdini import selection_entities
            selection_entities.open_selected_workfile(department, new_instance)
        _defer(_do)
    return _cb


def department_slot_label(department) -> str:
    """What a department's slice reads: its short label in capitals (MDL),
    or its name when it has none."""
    return department.short.upper() if department.short else department.name


def register_open_selected_actions(radial, departments) -> list[str]:
    """Register one "open the selected asset's <department> workfile" action
    per department; returns their keys, in the order given.

    Shift opens the workfile in a new Houdini. A slice is greyed while the
    selection does not belong to exactly one entity that has the
    department, and its second line names the entity it would open.
    """
    keys = []
    for department in departments:
        name = department.name
        key = _safe_action_key(OPEN_SELECTED_PREFIX, name)
        radial.register_action(
            key=key,
            label=department_slot_label(department),
            callback=_open_selected_callback(name, False),
            icon="lucide:folder",
            sublabel_fn=lambda: _selected_asset()[0],
            enabled=lambda name=name: name in _selected_asset()[1],
            shift_callback=_open_selected_callback(name, True),
            contexts=("viewer.lop", "network.lop"),
        )
        keys.append(key)
    return keys


def _build_selection_menu(radial):
    """The menu of the project's asset departments, each slice opening that
    department's workfile of the selected asset; None when the project has
    fewer than two (a ring needs two) or its departments cannot be read.

    The pool is read once, here: a department added mid-session appears
    after the next launch. Generated departments have no workfile and get
    no slice.
    """
    try:
        from tumblepipe.config.department import list_departments
        departments = list_departments('assets', include_generated=False)
    except Exception:
        logger.warning(
            "could not read the asset departments, so the selected-asset "
            "workfile menu (%s) is not registered", SELECTION_MENU_KEY,
            exc_info=True,
        )
        return None
    keys = register_open_selected_actions(radial, departments)
    if len(keys) < 2:
        return None
    return radial.menu(
        [radial.item_from_action(key, close_on_select=True) for key in keys[:9]],
        label="Open Workfile",
    )


# ── Binding the context menus ────────────────────────────────────────────────

#: Owner name for the COP / VOP bindings, so a chord change can withdraw
#: exactly these and nothing a user or another package bound.
_CONTEXT_MENU_SOURCE = "tumblepipe.context_menus"

#: The key the VOP (material) menu opens on. Not Radial's *Open menus with*
#: key: that is X by default, and over a VOP network holding X and clicking
#: a node is Houdini's Visualize Output, which the menu took over (Discord,
#: Remy 2026-11-11). Alt+E is free in Houdini's network editor (Alt+X is
#: Cut, Alt+V Paste, Alt+M Merge).
VOP_MENU_KEY = "Alt+E"


def _bind_context_menus(radial, menus: dict, keyed: dict | None = None) -> None:
    """Bind the network context menus.

    *menus* (context -> menu) bind on the chord Radial's own menus open with:
    its *Open menus with* setting (``trigger_key``, X by default), so they
    open where the shipped network menu would. When that setting changes
    they move with it, and an empty setting unbinds them, as it does
    Radial's own menus. *keyed* ((chord, context) -> menu) bind on a chord
    of their own and stay there whatever the setting says.

    Binding installs the chord's controller on Houdini's UI event loop. A
    headless session therefore binds nothing, and a GUI session binds once
    the UI is up.
    """
    import hou
    if not hou.isUIAvailable():
        return

    from tumbleradial import settings

    keyed = dict(keyed or {})

    def _rebind():
        chord = str(settings.get("trigger_key") or "").strip()
        with radial.batch():
            radial.unbind_source(_CONTEXT_MENU_SOURCE)
            for (own_chord, context), menu in keyed.items():
                radial.bind(own_chord, context, menu, source=_CONTEXT_MENU_SOURCE)
            if not chord:
                return
            for context, menu in menus.items():
                radial.bind(chord, context, menu, source=_CONTEXT_MENU_SOURCE)

    def _on_setting_changed(key, _value):
        if key == "trigger_key":
            _rebind()

    settings.subscribe(_on_setting_changed)
    _defer(_rebind)


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


def _register_recipe_actions(radial) -> list[dict]:
    """Register one action per recipe in Recipes.hda; returns their ring
    slots. Generated from the live HDA so the menu cannot drift from the
    recipes that actually exist.

    Every recipe gets an action, not just the nine the ring has room for:
    the stock department menus (``stock/radial_menus``) name recipes by
    action key too.
    """
    import hou
    recipe_file = hou.text.expandString('$TH_PIPELINE_PATH/otls/Recipes.hda')
    definitions = hou.hda.definitionsInFile(recipe_file)

    ring_items: list[dict] = []
    for definition in definitions:
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
        ring_items.append(_ring_slot(action_key, close_on_select=True))
    return ring_items


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
        logger.warning(
            "could not read the asset browser's favorites, so the Asset "
            "Favorites menu (Alt+F) lists none", exc_info=True,
        )
        return []
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
    return favorites[:RING_MAX]


def _register_favorite_actions(radial) -> list[dict]:
    """Register one catalog action per asset-browser favorite; returns their
    ring slots. Read once per session: favorites added or removed later show
    after the next launch.

    Each slot drops the asset into whatever network the cursor is over when
    it fires.
    """
    favorites = _collect_asset_favorites()
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
        ring_items.append(_ring_slot(action_key, close_on_select=False))
    return ring_items


# ── Writing the generated menus ──────────────────────────────────────────────────

def _ring_slot(action_key: str, *, close_on_select: bool) -> dict:
    return {
        "action": action_key,
        "label_override": None,
        "icon_override":  None,
        "hidden":         False,
        "close_on_select": close_on_select,
    }


def _write_ring_menu(menu_dir: Path, *, name: str, label: str,
                     key: str, ring: list[dict]) -> None:
    """Write the network menu ``name`` with ``ring``'s slots, or remove it
    when there are fewer than two: a radial ring needs two filled slots to
    validate (empty placeholders do not count), and the loader would reject
    the menu with an error the artist sees.

    Written to a temporary file and moved into place, so another session
    of the same user scanning the folder never reads half a menu.
    """
    target = menu_dir / f"{name}.json"
    if len(ring) < 2:
        target.unlink(missing_ok=True)
        return
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
    partial = menu_dir / f".{name}.{os.getpid()}.json.partial"
    partial.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    os.replace(partial, target)
