"""Pipeline nodes: every TumblePipe HDA as a card in the TumblePipe catalog.

The catalog's **Nodes** section lists the operators this package ships, one
card per node, so an artist can find ``import_shot`` or ``export_layer``
without knowing its TAB-menu path, and drag the card onto a network to
create it. The model is TumbleRig's node catalogue; here it lives inside the
pipeline catalog, beside Assets, Shots and Recipes, rather than as a catalog
of its own.

What is listed
──────────────
``hpm.toml`` ``[[operators]]`` names *which* nodes ship, with their Houdini
context and TAB submenu. The submenu (``_TumblePipe/pipeline``) becomes the
sidebar row a node is filed under. ``Data`` operators (the
``th_configure_*`` config HDAs) cannot be placed in a network and are left
out, as is any type that is not registered in this session or is hidden
from the TAB menu.

One card per node *family*: the same name in two contexts (``cache`` in LOPs
and SOPs) is two cards, and several versions of one type are one card whose
detail panel offers the older versions.

Nodes belong to the package, not to a project, so the section sits beside
the project sections and a ``project:`` filter never hides it (see
:meth:`PipelineNodes.get_assets`).

Threading
─────────
``get_assets`` / ``get_detail`` / ``get_thumbnail`` / ``execute_action`` run
on browser worker threads. The index only reads ``hou`` node types, which
the HOM lock serializes. Creating a node touches the network editor, so it
happens on the main thread: drops and card menus are already there, and the
detail panel's action hops over with :func:`run_on_main_thread`.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tumbletrove.asset_browser.api.tags import match_tags, parse_tag
from tumbletrove.asset_browser.api.types import (
    Asset, AssetAction, AssetDetail, AssetPage, AssetVersion, Collection,
)

from .houdini import run_on_main_thread

log = logging.getLogger(__name__)

ID_PREFIX = "node:"
TYPE_TAG = "type:node"
KIND = "node"
# The sidebar row a node is filed under: ``menu:pipeline``.
MENU_KEY = "menu"

_SUBMENU_PREFIX = "_TumblePipe/"
# Sidebar rows, in order: (id, label, icon). The id is the TAB submenu's
# last segment; a submenu not listed here files under Other.
SECTIONS = (
    ("pipeline", "Pipeline", "package"),
    ("model", "Model", "box"),
    ("lookdev", "Lookdev", "palette"),
    ("lighting", "Lighting", "lamp"),
    ("rendering", "Rendering", "camera"),
    ("comp", "Comp", "layers"),
    ("utils", "Utils", "settings"),
    ("debug", "Debug", "search"),
)
OTHER = ("other", "Other", "shapes")
_SECTION_IDS = tuple(s[0] for s in SECTIONS)
_SECTION_LABELS = {menu: label for menu, label, _icon in (*SECTIONS, OTHER)}

# Contexts a node can be created in from a drop. ``Data`` is the
# th_configure_* config HDAs, which have no network to live in.
PLACEABLE = frozenset({"Sop", "Lop", "Cop", "Vop"})

FALLBACK_ICON = "houdini:SOP_subnet"

# (hou category name, type name) -> the type's icon name, or None when the
# type is not registered or is hidden from the TAB menu.
Lookup = Callable[[str, str], "str | None"]


# ── Ids ──────────────────────────────────────────────────────────────────────


def make_id(context: str, name: str) -> str:
    return f"{ID_PREFIX}{context}/{name}"


def is_node_id(asset_id: str) -> bool:
    return asset_id.startswith(ID_PREFIX)


# ── Index (pure) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeVersion:
    type_name: str      # 'th::import_shot::1.0'
    version: str        # '1.0' ('' when the type carries no version)
    label: str          # TAB-menu label
    icon: str           # icon identifier ('houdini:<icon>' or a file path)
    image: str = ""     # a picture icon's file, shown as the card itself


@dataclass(frozen=True)
class NodeFamily:
    asset_id: str       # 'node:lop/import_shot'
    name: str           # 'import_shot'
    hou_category: str   # 'Lop'
    menu: str           # sidebar row id ('pipeline')
    tab_submenu: str    # '_TumblePipe/pipeline'
    versions: tuple     # NodeVersion, newest first
    # The card title. Names shipped in two contexts get the context
    # ('Import Asset (LOP)') so two cards never read the same.
    title: str = ""

    @property
    def latest(self) -> NodeVersion:
        return self.versions[0]

    @property
    def context(self) -> str:
        return self.hou_category.lower()

    @property
    def tags(self) -> frozenset:
        return frozenset({
            "source:pipeline",
            TYPE_TAG,
            f"context:{self.context}",
            f"{MENU_KEY}:{self.menu}",
        })

    def version(self, type_name: str | None) -> NodeVersion:
        for v in self.versions:
            if v.type_name == type_name:
                return v
        return self.latest

    def matches(self, query: str) -> bool:
        haystack = " ".join((
            self.name, self.menu, self.context, self.title,
            *(v.type_name for v in self.versions),
        )).lower()
        return all(word in haystack for word in query.lower().split())


def display_name(label: str) -> str:
    """The card title for a TAB label: 'th import shot' -> 'Import Shot'.

    The 'th ' prefix every TumblePipe label wears is dropped; lowercase
    words are capitalised, words with their own capitals ('LPE') are kept.

    The prefix match is case-sensitive: the prefix is always lowercase, and a
    title whose first word is 'TH' or 'Th' must survive a second pass.
    """
    name = re.sub(r"^th\s+", "", label.strip())
    words = [w[:1].upper() + w[1:] if w.islower() else w for w in name.split()]
    return " ".join(words) or label


def split_type_name(type_name: str) -> tuple[str, str]:
    """'th::import_shot::1.0' -> ('import_shot', '1.0'); no version -> ''."""
    parts = type_name.split("::")
    if len(parts) >= 3:
        return parts[1], parts[2]
    if len(parts) == 2:
        return parts[1], ""
    return type_name, ""


def _version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in version.split("."))


def menu_for(tab_submenu: str) -> str:
    """The sidebar row for a TAB submenu: '_TumblePipe/lookdev' -> 'lookdev'."""
    if tab_submenu.startswith(_SUBMENU_PREFIX):
        leaf = tab_submenu[len(_SUBMENU_PREFIX):].split("/")[0].lower()
        if leaf in _SECTION_IDS:
            return leaf
    return OTHER[0]


# Houdini's own icon schemes, which hou.qt.Icon resolves; anything else with a
# path separator or a variable is a file.
_HOUDINI_SCHEMES = ("opdef:", "hicon:", "oplib:")
_PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _is_file_icon(icon: str) -> bool:
    return not icon.startswith(_HOUDINI_SCHEMES) and any(c in icon for c in "/\\$")


def icon_identifier(icon: str) -> str:
    """The browser's icon identifier for a node type's icon.

    Most TumblePipe HDAs name a file (``$TH_PIPELINE_PATH/resources/X.png``)
    rather than a Houdini icon. That is expanded and handed over as a path,
    which the icon loader reads directly; a file that is not there falls back
    rather than painting a blank card.
    """
    if not icon:
        return FALLBACK_ICON
    if _is_file_icon(icon):
        path = Path(os.path.expandvars(icon))
        return str(path) if "$" not in str(path) and path.is_file() else FALLBACK_ICON
    return f"houdini:{icon}"


def icon_image(identifier: str) -> str:
    """The icon file when it is a picture, else ''.

    TumblePipe's picture icons are full illustrations, so, as in TumbleRig's
    catalogue, the card shows the picture itself instead of a node drawing
    with the picture shrunk onto it.
    """
    if identifier.lower().endswith(_PICTURE_SUFFIXES) and not identifier.startswith("houdini:"):
        return identifier
    return ""


def load_operators(package_root: Path) -> list[dict]:
    with (Path(package_root) / "hpm.toml").open("rb") as fh:
        return list(tomllib.load(fh).get("operators", []))


def build_index(operators: list[dict], lookup: Lookup) -> list[NodeFamily]:
    """Every placeable, registered, TAB-visible operator, one per family.

    *lookup* answers whether a type is live in this session (and its icon),
    so this stays pure: the catalog passes :func:`hou_lookup`, tests a fake.
    """
    grouped: dict[str, dict] = {}
    for op in operators:
        category = op.get("category", "")
        type_name = op.get("type_name", "")
        if category not in PLACEABLE or not type_name:
            continue
        icon = lookup(category, type_name)
        if icon is None:
            continue
        name, version = split_type_name(type_name)
        submenu = op.get("tab_submenu") or ""
        asset_id = make_id(category.lower(), name)
        family = grouped.setdefault(asset_id, {
            "name": name, "category": category, "submenu": submenu,
            "versions": [],
        })
        identifier = icon_identifier(icon)
        family["versions"].append(NodeVersion(
            type_name=type_name,
            version=version,
            label=op.get("label") or name,
            icon=identifier,
            image=icon_image(identifier),
        ))

    families = []
    for asset_id, f in grouped.items():
        versions = sorted(
            f["versions"], key=lambda v: _version_key(v.version), reverse=True,
        )
        families.append(NodeFamily(
            asset_id=asset_id,
            name=f["name"],
            hou_category=f["category"],
            menu=menu_for(f["submenu"]),
            tab_submenu=f["submenu"],
            versions=tuple(versions),
        ))

    names: dict[str, int] = {}
    for fam in families:
        name = display_name(fam.latest.label)
        names[name] = names.get(name, 0) + 1
    families = [
        dataclasses.replace(fam, title=(
            f"{display_name(fam.latest.label)} ({fam.hou_category.upper()})"
            if names[display_name(fam.latest.label)] > 1
            else display_name(fam.latest.label)
        ))
        for fam in families
    ]
    families.sort(key=lambda fam: fam.title.lower())
    return families


def hou_lookup(category: str, type_name: str) -> str | None:
    """The type's icon name, or None when it is not live or is TAB-hidden."""
    import hou
    hou_category = hou.nodeTypeCategories().get(category)
    node_type = hou.nodeType(hou_category, type_name) if hou_category else None
    if node_type is None or node_type.hidden():
        return None
    return node_type.icon() or ""


# ── Node creation (main thread) ──────────────────────────────────────────────


def current_network_editor():
    import hou
    editors = [
        t for t in hou.ui.paneTabs()
        if t.type() == hou.paneTabType.NetworkEditor
    ]
    for editor in editors:
        if editor.isCurrentTab():
            return editor
    return editors[0] if editors else None


def can_place(hou_category: str, network) -> bool:
    """Whether a *hou_category* node can be created in *network*.

    A SOP node also places at object level, inside a new Geometry
    container, the way the TAB menu offers SOP tools there.
    """
    import hou
    if network is None:
        return False
    wanted = hou.nodeTypeCategories().get(hou_category)
    child = network.childTypeCategory()
    return child == wanted or (
        wanted == hou.sopNodeTypeCategory()
        and child == hou.objNodeTypeCategory()
    )


def create_node(version: NodeVersion, hou_category: str, network=None,
                position=None, upstream=None):
    """Create *version* in *network* (default: the current network editor's).

    Dropped into an object network, a SOP node gets a Geometry container.
    With *upstream*, the new node is wired below it and takes its display
    flag when *upstream* had it, as TAB does. Raises ``hou.OperationFailed``
    with an artist-readable message when the network cannot hold the node.
    """
    import hou

    if network is None:
        editor = current_network_editor()
        if editor is None:
            raise hou.OperationFailed("Open a network editor to create the node in.")
        network = editor.pwd()
        if upstream is None:
            selected = [n for n in hou.selectedNodes() if n.parent() == network]
            upstream = selected[0] if len(selected) == 1 else None

    if not can_place(hou_category, network):
        raise hou.OperationFailed(
            f"{version.type_name} is a {hou_category} node and cannot be "
            f"created in {network.path()}."
        )

    with hou.undos.group(f"Create {version.type_name}"):
        parent = network
        if network.childTypeCategory() != hou.nodeTypeCategories()[hou_category]:
            name, _ = split_type_name(version.type_name)
            parent = network.createNode("geo", name)
            if position is not None:
                parent.setPosition(hou.Vector2(position[0], position[1]))
            else:
                parent.moveToGoodPosition()
            position = None
            upstream = None

        node = parent.createNode(version.type_name)
        if upstream is not None and upstream.parent() == parent:
            node.setFirstInput(upstream)
            node.moveToGoodPosition()
            if getattr(upstream, "isDisplayFlagSet", None) and upstream.isDisplayFlagSet():
                node.setDisplayFlag(True)
                # A LopNode has no render flag.
                if hasattr(node, "setRenderFlag"):
                    node.setRenderFlag(True)
        elif position is not None:
            size = node.size()
            node.setPosition(hou.Vector2(
                position[0] - size[0] / 2.0, position[1] - size[1] / 2.0,
            ))
        else:
            node.moveToGoodPosition()
        node.setSelected(True, clear_all_selected=True)
        node.setCurrent(True)
    return node


def _create_reporting(family: NodeFamily, version: NodeVersion, **kwargs) -> None:
    """Create on the main thread; a network that refuses it is a status warning."""
    import hou
    try:
        create_node(version, family.hou_category, **kwargs)
    except hou.OperationFailed as exc:
        message = exc.instanceMessage() if hasattr(exc, "instanceMessage") else str(exc)
        hou.ui.setStatusMessage(message, severity=hou.severityType.Warning)


# ── Catalog section ──────────────────────────────────────────────────────────


class PipelineNodes:
    """The Nodes section of the pipeline catalog.

    ``PipelineCatalog`` routes every ``node:`` id here, the way it routes
    ``recipe:`` ids to :class:`~tumblepipe.asset_browser.recipes.RecipeManager`.
    """

    def __init__(
        self, catalog_id: str, package_root: Path | None = None,
        lookup: Lookup | None = None,
    ) -> None:
        self._catalog_id = catalog_id
        # python/tumblepipe/asset_browser/nodes.py -> the package root, where
        # hpm.toml lives both installed and in a dev checkout.
        self._root = Path(package_root or Path(__file__).resolve().parents[3])
        self._lookup = lookup or hou_lookup
        self._index: list[NodeFamily] | None = None
        self._by_id: dict[str, NodeFamily] = {}

    # ── Index ───────────────────────────────────────────

    def families(self) -> list[NodeFamily]:
        index = self._index
        if index is None:
            # The sidebar asks for this on the GUI thread, so a Nodes section
            # that cannot be built is an empty section and a log line, never
            # an exception out of get_collections.
            try:
                index = build_index(load_operators(self._root), self._lookup)
            except Exception:
                log.warning("pipeline node index could not be built from %s",
                            self._root, exc_info=True)
                index = []
            self._by_id = {f.asset_id: f for f in index}
            self._index = index
        return index

    def find(self, asset_id: str) -> NodeFamily | None:
        self.families()
        return self._by_id.get(asset_id)

    def invalidate(self) -> None:
        self._index = None

    # ── Listing ─────────────────────────────────────────

    def _asset(self, family: NodeFamily, cls=Asset, version: NodeVersion | None = None, **extra):
        chosen = version or family.latest
        return cls(
            id=family.asset_id,
            name=family.title or display_name(chosen.label),
            # A picture icon's file: get_thumbnail serves it as the card, and
            # the loader refreshes its cached copy when the file changes.
            thumbnail_url=chosen.image,
            tags=family.tags,
            kind=KIND,
            context=family.context,
            icon=chosen.icon,
            metadata={
                "category": _SECTION_LABELS[family.menu],
                "version": chosen.version,
                "type_name": chosen.type_name,
            },
            catalog_id=self._catalog_id,
            **extra,
        )

    def get_asset(self, asset_id: str) -> Asset | None:
        family = self.find(asset_id)
        return self._asset(family) if family is not None else None

    def get_assets(
        self, query: str, tags: frozenset, cursor: str | None, page_size: int,
    ) -> AssetPage:
        """The Nodes grid. ``project:`` / ``source:`` atoms are ignored:
        nodes belong to the package, and the browser keeps the launch
        project's filter on top of whichever section is picked."""
        filters = frozenset(
            t for t in tags
            if parse_tag(t)[0] not in ("project", "source") and t != TYPE_TAG
        )
        hits = [
            f for f in self.families()
            if (not query or f.matches(query))
            and (not filters or match_tags(f.tags, filters))
        ]
        start = int(cursor or 0)
        end = start + page_size
        return AssetPage(
            assets=[self._asset(f) for f in hits[start:end]],
            cursor=str(end) if end < len(hits) else None,
            total=len(hits),
        )

    def collection(self) -> Collection | None:
        """The sidebar's Nodes section, one row per non-empty TAB submenu."""
        families = self.families()
        if not families:
            return None
        counts: dict[str, int] = {}
        for f in families:
            counts[f.menu] = counts.get(f.menu, 0) + 1
        children = tuple(
            Collection(
                id=f"nodes_menu:{menu}",
                label=label,
                count=counts[menu],
                tag=f"{TYPE_TAG}+{MENU_KEY}:{menu}",
                icon=icon,
            )
            for menu, label, icon in (*SECTIONS, OTHER)
            if counts.get(menu)
        )
        return Collection(
            id="nodes_section",
            label="Nodes",
            count=len(families),
            tag=TYPE_TAG,
            icon="network",
            children=children,
        )

    def available_tags(self) -> dict[str, list[str]]:
        menus = {f.menu for f in self.families()}
        return {MENU_KEY: [m for m, _, _ in (*SECTIONS, OTHER) if m in menus]}

    # ── Detail ──────────────────────────────────────────

    def get_detail(self, asset_id: str, version: str | None = None) -> AssetDetail:
        family = self.find(asset_id)
        if family is None:
            from tumbletrove.asset_browser.api.errors import DetailBuildError
            raise DetailBuildError(f"No pipeline node {asset_id!r} in this session.")
        chosen = family.version(version)
        # The browser's version combo opens on its first entry, so the
        # chosen version leads and the rest follow newest-first.
        ordered = (chosen,) + tuple(v for v in family.versions if v is not chosen)
        lines = [
            f"Node type: {chosen.type_name}",
            f"Context: {family.hou_category}",
        ]
        if family.tab_submenu:
            lines.append(f"TAB menu: {family.tab_submenu}")
        if len(family.versions) > 1:
            lines.append(
                "Installed versions: " + ", ".join(v.version for v in family.versions)
            )
        lines.append("Drag onto a network editor to create it, or use Create Node.")
        return self._asset(
            family, AssetDetail, chosen,
            description="\n".join(lines),
            versions=tuple(
                AssetVersion(id=v.type_name, version=v.version or v.type_name)
                for v in ordered
            ),
        )

    def get_thumbnail(self, asset: Asset):
        """The picture icon itself, else the node drawn as in the network editor.

        Falls back to the icon on a TumbleTrove without node thumbnails, or
        when the type is not loaded. Worker thread: HOM calls only.
        """
        if asset.thumbnail_url:
            return Path(asset.thumbnail_url)
        from tumbletrove.asset_browser.core.thumbnail import IconThumbnail
        fallback = IconThumbnail(asset.icon or FALLBACK_ICON, 128)
        try:
            from tumbletrove.asset_browser.api.node_thumbnail import NodeThumbnail
        except ImportError:
            return fallback
        family = self.find(asset.id)
        if family is None:
            return fallback
        import hou
        category = hou.nodeTypeCategories().get(family.hou_category)
        node_type = hou.nodeType(category, family.latest.type_name) if category else None
        if node_type is None:
            return fallback
        thumb = NodeThumbnail.from_node_type(node_type, size=256)
        # The checked identifier: an icon path with $TH_PIPELINE_PATH in it
        # is expanded here, and one naming a missing file falls back.
        return dataclasses.replace(thumb, icon=asset.icon or FALLBACK_ICON)

    # ── Actions ─────────────────────────────────────────

    def _selected(self, detail) -> tuple[NodeFamily, NodeVersion] | None:
        family = self.find(detail.id)
        if family is None:
            return None
        return family, family.version((detail.metadata or {}).get("type_name"))

    def get_actions(self, detail) -> list[AssetAction]:
        selected = self._selected(detail)
        if selected is None:
            return []
        _family, version = selected
        return [
            AssetAction(
                id="create_node", label="Create Node", icon="plus",
                tooltip="Create the node in the current network editor, "
                        "wired below the selected node.",
            ),
            AssetAction(
                id="copy_type_name", label="Copy Node Type", icon="copy",
                tooltip=version.type_name,
            ),
        ]

    def execute_action(self, action_id: str, detail) -> bool:
        """Run a node action; True when *action_id* was one of ours."""
        selected = self._selected(detail)
        if selected is None:
            return False
        family, version = selected
        if action_id == "create_node":
            run_on_main_thread(_create_reporting, family, version)
            return True
        if action_id == "copy_type_name":
            run_on_main_thread(_copy_type_name, version.type_name)
            return True
        return False

    def card_menu_items(self, asset: Asset) -> list:
        family = self.find(asset.id)
        if family is None:
            return []

        def _create(f=family):
            _create_reporting(f, f.latest)

        def _copy(t=family.latest.type_name):
            _copy_type_name(t)

        return [("Create Node", _create), ("Copy Node Type", _copy)]

    # ── Drag and drop (main thread) ─────────────────────
    #
    # A drop the network cannot hold returns False, so the browser's
    # fallback menu names the context that refused it.

    def get_ghost_data(self, asset_id: str):
        family = self.find(asset_id)
        if family is None:
            return None
        from tumbletrove.asset_browser.core.ghost_overlay import GhostData, GhostNode
        return GhostData(nodes=[GhostNode(family.latest.type_name, 0.0, 0.0)])

    def on_drop(self, detail, drop) -> bool:
        family = self.find(detail.id)
        if family is None or not can_place(family.hou_category, drop.network):
            return False
        version = family.version((detail.metadata or {}).get("type_name"))
        self._drop(family, version, drop, offset=0.0)
        return True

    def on_multi_drop(self, assets, drop) -> bool:
        families = [self.find(a.id) for a in assets]
        if not all(
            f is not None and can_place(f.hou_category, drop.network)
            for f in families
        ):
            return False
        import hou
        with hou.undos.group("Create pipeline nodes"):
            for i, family in enumerate(families):
                self._drop(family, family.latest, drop, offset=3.0 * i)
        return True

    @staticmethod
    def _drop(family: NodeFamily, version: NodeVersion, drop, offset: float) -> None:
        """Create at the drop point; wired below the node it landed on, if any."""
        kwargs = {"network": drop.network}
        if drop.pane_type == "network_editor" and drop.position is not None:
            if drop.target_node is not None and not offset:
                kwargs["upstream"] = drop.target_node
            else:
                kwargs["position"] = (drop.position[0] + offset, drop.position[1])
        _create_reporting(family, version, **kwargs)


def _copy_type_name(type_name: str) -> None:
    import hou
    hou.ui.copyTextToClipboard(type_name)
    hou.ui.setStatusMessage(f"Copied {type_name}")
