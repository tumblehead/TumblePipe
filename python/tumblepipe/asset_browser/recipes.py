"""Recipes — node clusters saved into the project, shared by everyone on it.

A recipe is a selection of nodes saved with Houdini's ``saveItemsToFile``
(a ``.cpio``), plus a thumbnail of the network editor and the node layout
for the drag ghost. They used to live in TumbleTrove's Network catalog,
in a personal folder per machine; now they are a third entity type of
the pipeline catalog beside assets and shots, stored **per project** so
a recipe one artist saves is on every teammate's grid after a Refresh::

    <project>/recipes/
      <context>/            # sop, lop, obj, ... (short names from
        <slug>/             #   tumbletrove's CONTEXT_MAP)
          entry.json        # {"kind": "recipe", "meta": {...}}
          recipe.cpio       # payload — loaded via loadItemsFromFile
          thumbnail.png     # optional

``entry.json`` keeps the shape the Network catalog wrote, so an old
personal ``<ctx>/recipe/<slug>/`` directory copies in unchanged (the
``recipe`` kind level goes; there is only one kind here).

Recipe ids are ``recipe:<project>:<context>/<slug>``, the same
``<kind>:<project>:<path>`` shape the Multi / Root container ids use, so
the catalog can route them before any of its entity resolvers see them
(``PROJECT/second/third`` parsing would read one as a shot).

Threading, as for the rest of the catalog: :meth:`RecipeManager.scan_all`
walks the project share and runs on the worker (``warm_up_worker_thread``
/ ``get_assets``) or synchronously after a local write; sidebar counts
read the cache only. ``hou`` and Qt are imported inside the methods that
need them so this module imports headlessly for the property tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from tumbletrove.asset_browser.api.errors import (
    AssetDiscoveryError, CatalogError, DetailBuildError,
)
from tumblepipe.api import get_user_name
from tumblepipe.util import desktop
from tumbletrove.asset_browser.api.types import (
    Asset,
    AssetAction,
    AssetDetail,
    CreationField,
)

if TYPE_CHECKING:
    from .projects import PipelineProjectRegistry
    from .refresh import BrowserRefresh

log = logging.getLogger(__name__)

#: Directory under the project root that holds every recipe.
RECIPES_DIR = "recipes"
ID_PREFIX = "recipe:"
TYPE_TAG = "type:recipe"
CREATE_OPTION = "new_recipe"

_ENTRY_FILE = "entry.json"
_CPIO_FILE = "recipe.cpio"
_THUMB_FILE = "thumbnail.png"
_KIND = "recipe"


# ── Ids ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecipeRef:
    """The three parts of a recipe id, ``recipe:<project>:<context>/<slug>``."""

    project: str
    context: str
    slug: str

    @property
    def asset_id(self) -> str:
        return f"{ID_PREFIX}{self.project}:{self.context}/{self.slug}"


def parse_id(asset_id: str) -> RecipeRef | None:
    """``recipe:<project>:<context>/<slug>`` → :class:`RecipeRef`, else None.

    Strict on shape: every segment must be non-empty and the path exactly
    ``context/slug``, so a mangled id never resolves to a neighbouring
    directory.
    """
    if not asset_id or not asset_id.startswith(ID_PREFIX):
        return None
    rest = asset_id[len(ID_PREFIX):]
    project, sep, path = rest.partition(":")
    if not sep or not project:
        return None
    context, sep, slug = path.partition("/")
    if not sep or not context or not slug or "/" in slug:
        return None
    return RecipeRef(project, context, slug)


def is_recipe_id(asset_id: str) -> bool:
    return parse_id(asset_id) is not None


# ── On-disk entries ──────────────────────────────────────────


# Windows reserves these as device names. Which ones actually refuse to
# hold a file varies by Windows version, filesystem and — since the root
# is a studio share — by whatever serves the mount, so the whole
# documented set is avoided rather than just the ones that fail locally.
_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(10)),
    *(f"lpt{i}" for i in range(10)),
})


def slugify(name: str) -> str:
    """Convert a human name to a filesystem-safe directory name."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    if not s:
        return "entry"
    if s in _RESERVED_NAMES:
        # A recipe called "NUL" or "COM1" otherwise produced a directory
        # Windows will not write into: mkdir appears to succeed, then
        # every write inside it fails with FileNotFoundError, so the save
        # died with nothing on screen but a logged traceback. Suffixing
        # keeps the slug readable and still round-trips through slugify.
        return f"{s}_entry"
    return s


@dataclass
class RecipeEntry:
    """A recipe found on disk: its id parts, directory and ``meta``."""

    ref: RecipeRef
    entry_dir: Path
    meta: dict = field(default_factory=dict)

    @property
    def asset_id(self) -> str:
        return self.ref.asset_id

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.ref.slug)

    @property
    def cpio_path(self) -> Path:
        return self.entry_dir / _CPIO_FILE

    @property
    def thumbnail_path(self) -> Path | None:
        thumb_name = self.meta.get("thumbnail", _THUMB_FILE)
        if not thumb_name:
            return None
        p = self.entry_dir / str(thumb_name)
        return p if p.exists() else None

    @property
    def user_tags(self) -> list[str]:
        tags = self.meta.get("tags", [])
        return [str(t) for t in tags] if isinstance(tags, list) else []


def read_entry(entry_dir: Path) -> dict | None:
    """The ``meta`` dict from ``entry.json``; None when there is none.

    No ``entry.json`` is a folder that is not a recipe (yet: a save writes
    it last). One that is there but is not a recipe's raises ValueError —
    a torn or hand-edited file is a fault to show, not a recipe to hide.
    """
    path = entry_dir / _ENTRY_FILE
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if (
        not isinstance(doc, dict)
        or doc.get("kind") != _KIND
        or not isinstance(doc.get("meta"), dict)
    ):
        raise ValueError(f"{path} is not a recipe entry")
    return dict(doc["meta"])


def write_entry(entry_dir: Path, meta: dict) -> None:
    """Write ``entry.json`` atomically (tmp + replace)."""
    entry_dir.mkdir(parents=True, exist_ok=True)
    target = entry_dir / _ENTRY_FILE
    tmp = target.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"kind": _KIND, "meta": meta}, fh, indent=2)
    os.replace(tmp, target)


def recipes_root(project_path: str | os.PathLike) -> Path:
    return Path(project_path) / RECIPES_DIR


def scan_project(project: str, project_path: str | os.PathLike) -> list[RecipeEntry]:
    """Every recipe under ``<project_path>/recipes/<context>/<slug>/``.

    ``iterdir`` for a fixed two-level layout — no ``os.walk`` over a
    share. A directory without a readable ``entry.json`` is skipped.
    """
    root = recipes_root(project_path)
    out: list[RecipeEntry] = []
    if not root.is_dir():
        return out
    for ctx_dir in sorted(root.iterdir()):
        if not ctx_dir.is_dir():
            continue
        for slug_dir in sorted(ctx_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            meta = read_entry(slug_dir)
            if meta is None:
                continue
            out.append(RecipeEntry(
                ref=RecipeRef(project, ctx_dir.name, slug_dir.name),
                entry_dir=slug_dir,
                meta=meta,
            ))
    return out


# ── Cards ────────────────────────────────────────────────────


def _created_epoch(meta: dict) -> float:
    """``created_date`` (ISO 8601) as epoch seconds, 0.0 when absent.

    Cards carry it as ``latest_update`` so the pipeline's "Latest Update"
    sort and Edited column treat recipes like any other row — a sort key
    must not mix ISO strings with the entities' floats.
    """
    raw = meta.get("created_date", "")
    if not raw:
        return 0.0
    return datetime.fromisoformat(raw).timestamp()


def active_context() -> str:
    """The network editor's current short context, or ``""``.

    Wraps tumbletrove's ``detect_active_context``.
    """
    from tumbletrove.asset_browser.core.context_map import detect_active_context
    return detect_active_context() or ""


def context_label(context: str) -> str:
    from tumbletrove.asset_browser.core.context_map import CONTEXT_LABELS
    return CONTEXT_LABELS.get(context, context.upper())


def card_fields(entry: RecipeEntry, active_ctx: str, catalog_id: str) -> dict:
    """Constructor kwargs shared by the card and the detail."""
    m = entry.meta
    ref = entry.ref
    tags = {
        f"source:{catalog_id}",
        TYPE_TAG,
        f"project:{ref.project}",
        f"context:{ref.context}",
        f"kind:{_KIND}",
    }
    for t in entry.user_tags:
        tags.add(f"tag:{t}")
    thumb = entry.thumbnail_path
    return dict(
        id=entry.asset_id,
        name=entry.name,
        thumbnail_url=str(thumb) if thumb else "",
        tags=frozenset(tags),
        catalog_id=catalog_id,
        kind=_KIND,
        context=ref.context,
        disabled=bool(active_ctx and ref.context != active_ctx),
        icon=str(m.get("icon", "") or ""),
        has_deck_items=False,
        metadata={
            "project": ref.project,
            "context": ref.context,
            "node_count": str(m.get("node_count", 0)),
            "created_date": str(m.get("created_date", "")),
            "houdini_version": str(m.get("houdini_version", "")),
            "latest_update": _created_epoch(m),
            "last_user": str(m.get("user", "")),
        },
    )


def build_card(entry: RecipeEntry, active_ctx: str, catalog_id: str) -> Asset:
    return Asset(**card_fields(entry, active_ctx, catalog_id))


def build_detail(entry: RecipeEntry, active_ctx: str, catalog_id: str) -> AssetDetail:
    return AssetDetail(
        description=str(entry.meta.get("description") or "(no description)"),
        **card_fields(entry, active_ctx, catalog_id),
    )


def ghost_data(entry: RecipeEntry):
    """The saved node layout as a drag ghost, or None when it is missing.

    A save always writes ``nodes`` and ``connections``.
    """
    from tumbletrove.asset_browser.core.ghost_overlay import (
        GhostConnection, GhostData, GhostNode,
    )
    m = entry.meta
    nodes_data = m["nodes"]
    if not nodes_data:
        return GhostData(nodes=[GhostNode("null", 0.0, 0.0)])
    nodes = [
        GhostNode(n["type"], n["rel_x"], n["rel_y"]) for n in nodes_data
    ]
    conns = [
        GhostConnection(c["src"], c["src_port"], c["dst"], c["dst_port"])
        for c in m["connections"]
    ]
    return GhostData(nodes=nodes, connections=conns)


# ── Houdini capture / materialize ────────────────────────────


def capture_node_layout(nodes: list) -> tuple[list[dict], list[dict]]:
    """Positions relative to the first node, and the wires between them."""
    if not nodes:
        return [], []
    anchor = nodes[0].position()
    path_to_idx = {n.path(): i for i, n in enumerate(nodes)}
    nodes_data = []
    for n in nodes:
        pos = n.position()
        nodes_data.append({
            "type": n.type().name(),
            "rel_x": round(pos[0] - anchor[0], 3),
            "rel_y": round(pos[1] - anchor[1], 3),
        })
    conns_data = []
    for n in nodes:
        dst_idx = path_to_idx[n.path()]
        for in_port in range(len(n.inputs())):
            src = n.input(in_port)
            if src is None:
                continue
            src_path = src.path()
            if src_path not in path_to_idx:
                continue
            src_idx = path_to_idx[src_path]
            out_port = 0
            for oc in src.outputConnections():
                if (
                    oc.inputNode().path() == n.path()
                    and oc.inputIndex() == in_port
                ):
                    out_port = oc.outputIndex()
                    break
            conns_data.append({
                "src": src_idx,
                "src_port": out_port,
                "dst": dst_idx,
                "dst_port": in_port,
            })
    return nodes_data, conns_data


def capture_thumbnail() -> bytes | None:
    """A 400×300 PNG of the network editor framed on the selection.

    GUI thread only (grabs a widget). None when there is no network
    editor to grab — the card then shows the placeholder.
    """
    import hou
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    editor = None
    for pane_tab in hou.ui.paneTabs():
        if isinstance(pane_tab, hou.NetworkEditor):
            editor = pane_tab
            break
    if editor is None:
        return None
    editor.homeToSelection()
    pixmap = editor.qtWindow().grab()
    scaled = pixmap.scaled(400, 300, mode=1)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    scaled.save(buf, "PNG")
    return bytes(ba)


def _place_loaded_nodes(new_nodes: list, drop) -> None:
    """Offset the loaded nodes to the drop position, select them all,
    and display/render-flag the last one."""
    import hou

    if not new_nodes:
        return
    if drop.position is not None:
        anchor = new_nodes[0].position()
        offset = drop.position - anchor - hou.Vector2(0.5, 0.0)
        for n in new_nodes:
            n.setPosition(n.position() + offset)
    for i, n in enumerate(new_nodes):
        n.setSelected(True, clear_all_selected=(i == 0))
    # Not every node type carries the flags (a LOP has no render flag, a
    # ROP no display flag), so each is set where the node has it.
    last = new_nodes[-1]
    for flag in ("setDisplayFlag", "setRenderFlag"):
        setter = getattr(last, flag, None)
        if setter is not None:
            setter(True)


def materialize(entry: RecipeEntry, drop) -> bool:
    """Load the recipe's nodes into the dropped-on network editor.

    Returns True whenever the drop was ours to handle (including a
    cancelled context-mismatch prompt or a failed load, which is
    reported), False only for a pane that is not a network editor so the
    browser can show its fallback menu.
    """
    import hou

    if drop.pane_type != "network_editor":
        return False

    cpio_path = entry.cpio_path
    name = entry.name
    if not cpio_path.exists():
        hou.ui.setStatusMessage(
            f"Recipe file not found for: {name}",
            severity=hou.severityType.Error,
        )
        return True

    recipe_ctx = entry.ref.context
    if recipe_ctx and recipe_ctx != drop.context:
        choice = hou.ui.displayMessage(
            f"This recipe was created in {context_label(recipe_ctx)} "
            f"but you're dropping into {context_label(drop.context)}.\n"
            f"Continue?",
            buttons=("Load Anyway", "Cancel"),
            title="Context Mismatch",
        )
        if choice != 0:
            return True

    network = drop.network
    existing_paths = {n.path() for n in network.children()}
    # A failure raises into the browser, which reports it and does not
    # retry the drop its own way (TumbleTrove >= 0.48).
    with hou.undos.group(f"Load recipe: {name}"):
        network.loadItemsFromFile(str(cpio_path))
        new_nodes = [
            n for n in network.children()
            if n.path() not in existing_paths
        ]
        _place_loaded_nodes(new_nodes, drop)

    hou.ui.setStatusMessage(
        f"Loaded recipe: {name} ({len(new_nodes)} nodes)",
        severity=hou.severityType.Message,
    )
    return True


# ── Manager ──────────────────────────────────────────────────


class RecipeManager:
    """Recipe scanning, cards and mutations for :class:`PipelineCatalog`.

    Handed the project registry and the browser refresh, not the catalog.
    """

    def __init__(
        self,
        catalog_id: str,
        *,
        registry: "PipelineProjectRegistry",
        refresh: "BrowserRefresh",
    ) -> None:
        self._catalog_id = catalog_id
        self._registry = registry
        self._refresh = refresh
        # project name → entries. None until the first scan, so the
        # sidebar can tell "not scanned yet" from "no recipes".
        self._cache: dict[str, list[RecipeEntry]] | None = None
        # Set by a Refresh. The entries stay readable until the grid
        # query rescans: Refresh rebuilds the sidebar on the GUI thread
        # *before* that query runs, and dropping the entries here left
        # the Recipes section with no rows and no count after every
        # Refresh (the auto-refresh timer included).
        self._stale = False
        self._errors: list[CatalogError] = []

    # ── Cache ────────────────────────────────────────────

    def invalidate(self) -> None:
        self._stale = True

    def scan_all(self) -> list[RecipeEntry]:
        """Walk every registered project's ``recipes/`` and cache the result.

        Worker thread (``warm_up_worker_thread``, ``get_assets``), or the
        GUI thread right after a local write — a small, local scan.
        """
        before = self._all_counts() if self._cache is not None else None
        cache: dict[str, list[RecipeEntry]] = {}
        errors: list[CatalogError] = []
        for proj in self._registry.all():
            # One project's unreadable share or torn recipe must not hide
            # every other project's recipes: its recipes are left out and
            # the failure rides on the next page's errors.
            try:
                cache[proj.name] = scan_project(proj.name, proj.project_path)
            except Exception as exc:
                log.exception("recipe scan failed for %s", proj.name)
                errors.append(AssetDiscoveryError(
                    self._catalog_id,
                    f"recipes of {proj.name} could not be read: {exc}",
                    cause=exc,
                ))
                cache[proj.name] = []
        self._cache = cache
        self._errors = errors
        self._stale = False
        if before is not None and self._all_counts() != before:
            # The sidebar was built from the previous scan's counts.
            self._refresh.sidebar()
        return self.cached_entries()

    @property
    def errors(self) -> list[CatalogError]:
        """What the last scan could not read, for the page's errors."""
        return list(self._errors)

    def _all_counts(self) -> dict[str, dict[str, int]]:
        return {p: self.context_counts(p) for p in (self._cache or {})}

    def cached_entries(self) -> list[RecipeEntry]:
        """Entries from the last scan; empty before any scan."""
        if not self._cache:
            return []
        return [e for entries in self._cache.values() for e in entries]

    def entries(self) -> list[RecipeEntry]:
        """Entries, rescanning first if nothing is cached or a Refresh
        marked the cache stale. The sidebar reads :meth:`context_counts`
        instead, which never scans."""
        if self._cache is None or self._stale:
            return self.scan_all()
        return self.cached_entries()

    def find(self, asset_id: str) -> RecipeEntry | None:
        ref = parse_id(asset_id)
        if ref is None:
            return None
        for e in self.entries():
            if e.ref == ref:
                return e
        return None

    def _require(self, asset_id: str) -> RecipeEntry:
        entry = self.find(asset_id)
        if entry is None:
            raise DetailBuildError(
                self._catalog_id, asset_id, "recipe not found",
            )
        return entry

    def contexts(self) -> list[str]:
        return sorted({e.ref.context for e in self.cached_entries()})

    def context_counts(self, project: str) -> dict[str, int]:
        """``{context: count}`` for one project, from the cache only."""
        counts: dict[str, int] = {}
        for e in (self._cache or {}).get(project, []):
            counts[e.ref.context] = counts.get(e.ref.context, 0) + 1
        return counts

    # ── Cards ────────────────────────────────────────────

    def cards(self) -> list[Asset]:
        """A card per recipe, for the catalog's merged item list."""
        active = active_context()
        cid = self._catalog_id
        return [build_card(e, active, cid) for e in self.entries()]

    def restamp_disabled(self, items: list[Asset]) -> list[Asset]:
        """Re-evaluate every recipe card's ``disabled`` against the
        network editor's context *now* — the cards are cached, the
        context is not."""
        import dataclasses
        active = active_context()
        out: list[Asset] = []
        for a in items:
            if a.kind == _KIND:
                disabled = bool(active and a.context != active)
                if disabled != a.disabled:
                    a = dataclasses.replace(a, disabled=disabled)
            out.append(a)
        return out

    def get_asset(self, asset_id: str) -> Asset | None:
        entry = self.find(asset_id)
        if entry is None:
            return None
        return build_card(entry, active_context(), self._catalog_id)

    def get_detail(self, asset_id: str) -> AssetDetail:
        return build_detail(
            self._require(asset_id), active_context(), self._catalog_id,
        )

    def get_thumbnail(self, asset: Asset):
        entry = self.find(asset.id)
        if entry is None:
            return ""
        if asset.icon:
            # A marker, not a pixmap: get_thumbnail runs on the thumbnail
            # worker, where building a QPixmap is undefined behaviour.
            from tumbletrove.asset_browser.core.thumbnail import IconThumbnail
            return IconThumbnail(asset.icon, 128)
        return entry.thumbnail_path or ""

    def get_ghost_data(self, asset_id: str):
        entry = self.find(asset_id)
        return ghost_data(entry) if entry is not None else None

    def on_drop(self, detail, drop) -> bool:
        entry = self.find(detail.id)
        if entry is None:
            return False
        return materialize(entry, drop)

    # ── Actions / edit / delete ───────────────────────────

    def get_actions(self, detail) -> list[AssetAction]:
        return [
            AssetAction(
                id="open_recipe_folder", label="Open Recipe Folder",
                icon="folder-open",
            ),
            AssetAction(id="edit_entity", label="Edit…", icon="settings"),
            AssetAction(
                id="delete_entity", label="Delete", icon="x", destructive=True,
            ),
        ]

    def execute_action(self, action_id: str, detail) -> bool:
        """Run a recipe action; True when *action_id* was one of ours."""
        if action_id == "open_recipe_folder":
            entry = self.find(detail.id)
            if entry is not None:
                desktop.open_in_file_browser(entry.entry_dir)
            return True
        return False

    def get_edit_fields(self, asset_id: str) -> list[CreationField]:
        entry = self.find(asset_id)
        if entry is None:
            return []
        return [
            CreationField(
                key="name", label="Name", field_type="text",
                readonly=True, initial=entry.name, required=False,
            ),
            CreationField(
                key="context", label="Context", field_type="text",
                readonly=True, initial=context_label(entry.ref.context),
                required=False,
            ),
            CreationField(
                key="description", label="Description", field_type="text",
                initial=str(entry.meta.get("description", "")), required=False,
            ),
            CreationField(
                key="tags", label="Tags", field_type="text",
                initial=", ".join(entry.user_tags), required=False,
            ),
        ]

    def edit(self, asset_id: str, fields: dict) -> bool:
        entry = self.find(asset_id)
        if entry is None:
            return False
        updates: dict[str, Any] = {}
        if "description" in fields:
            updates["description"] = str(fields["description"]).strip()
        if "tags" in fields:
            updates["tags"] = _split_tags(str(fields["tags"]))
        if not updates:
            return False
        self.mutate_meta(entry, updates)
        return True

    def mutate_meta(self, entry: RecipeEntry, updates: dict) -> None:
        """Read-modify-write ``entry.json`` and the cached entry alike."""
        fresh = read_entry(entry.entry_dir)
        meta = dict(fresh if fresh is not None else entry.meta)
        meta.update(updates)
        write_entry(entry.entry_dir, meta)
        entry.meta = meta

    def delete(self, asset_id: str) -> bool:
        entry = self.find(asset_id)
        if entry is None:
            return False
        shutil.rmtree(entry.entry_dir)
        if self._cache is not None:
            entries = self._cache.get(entry.ref.project, [])
            self._cache[entry.ref.project] = [
                e for e in entries if e.ref != entry.ref
            ]
        return True

    # ── Card menu ────────────────────────────────────────

    def card_menu_items(
        self, asset: Asset,
    ) -> list[tuple[str, Callable[[], None]]]:
        entry = self.find(asset.id)
        if entry is None:
            return []
        asset_id = asset.id
        refresh_card = self._refresh.card

        def _edit_description():
            import hou
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(
                hou.qt.mainWindow(), "Edit Description",
                f"Description for {entry.name}:",
                str(entry.meta.get("description", "")),
            )
            if ok:
                self.mutate_meta(entry, {"description": text})
                refresh_card(asset_id)

        def _set_icon():
            import hou
            from tumbletrove.common.icon_picker import IconPicker
            picker = IconPicker(hou.qt.mainWindow())
            if not picker.exec():
                return
            icon_id = picker.selected_icon()
            if not icon_id:
                hou.ui.setStatusMessage(
                    "No icon selected", severity=hou.severityType.Warning,
                )
                return
            self.mutate_meta(entry, {"icon": icon_id})
            refresh_card(asset_id)
            hou.ui.setStatusMessage(
                f"Set icon: {icon_id} on {entry.name}",
                severity=hou.severityType.Message,
            )

        def _open_directory():
            desktop.open_in_file_browser(entry.entry_dir)

        def _delete():
            import hou
            choice = hou.ui.displayMessage(
                f"Delete recipe \"{entry.name}\"?\n"
                "This removes it from the project for everyone and cannot "
                "be undone.",
                buttons=("Delete", "Cancel"),
                title="Delete Recipe",
            )
            if choice == 0 and self.delete(asset_id):
                self._refresh.grid()

        return [
            ("Edit Description…", _edit_description),
            ("Set Icon…", _set_icon),
            ("Open Recipe Folder", _open_directory),
            ("Delete Recipe", _delete),
        ]

    # ── Creation ─────────────────────────────────────────

    def creation_fields(
        self, default_project: str, projects: list[str],
    ) -> list[CreationField]:
        fields = [
            CreationField("name", "Name", required=True),
            CreationField(
                "description", "Description", field_type="text",
                required=False,
            ),
            CreationField(
                "tags", "Tags", field_type="text", required=False,
                default="",
            ),
        ]
        if len(projects) > 1:
            fields.append(CreationField(
                "project", "Project",
                field_type="dropdown",
                choices=tuple(projects),
                default=default_project,
            ))
        return fields

    def create(self, project, fields: dict) -> str | None:
        """Save the selected nodes as a recipe of *project*.

        GUI thread, straight after the creation dialog. Returns the new
        recipe's id (the browser selects its card) or None when the
        user cancelled or nothing was selected (both said through
        ``hou.ui``). A save that fails raises; ``create_entity`` reports it.
        """
        import hou

        selected = hou.selectedNodes()
        if not selected:
            hou.ui.displayMessage(
                "Select one or more nodes in the network editor "
                "before saving a recipe.",
                title="No Selection",
                severity=hou.severityType.Warning,
            )
            return None

        name = str(fields.get("name", "")).strip()
        if not name:
            hou.ui.displayMessage(
                "Recipe name is required.",
                title="Missing Name",
                severity=hou.severityType.Warning,
            )
            return None
        description = str(fields.get("description", "")).strip()
        tags = _split_tags(str(fields.get("tags", "")))

        # hou.selectedNodes() spans every network, and a selection left
        # behind in another one (/obj while working in /stage) made
        # saveItemsToFile refuse the lot: "Failed to save selected
        # nodes." Keep the network of the most recently selected node.
        parent = selected[-1].parent()
        selected = [n for n in selected if n.parent() == parent]
        cat = parent.childTypeCategory()
        context = _short_context(cat.name()) if cat else "sop"

        root = recipes_root(project.project_path)
        slug = slugify(name)
        entry_dir = root / context / slug
        if entry_dir.exists():
            choice = hou.ui.displayMessage(
                f"A recipe named \"{slug}\" already exists in "
                f"{context_label(context)} for {project.name}.\nOverwrite?",
                buttons=("Overwrite", "Cancel"),
                title="Recipe Exists",
            )
            if choice != 0:
                return None

        with tempfile.NamedTemporaryFile(suffix=".cpio", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            parent.saveItemsToFile(selected, str(tmp_path))
            cpio_bytes = tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

        thumb_bytes = capture_thumbnail()
        nodes_data, conns_data = capture_node_layout(list(selected))
        hou_ver = ".".join(str(x) for x in hou.applicationVersion())

        meta = {
            "name": name,
            "description": description,
            "tags": tags,
            "node_count": len(selected),
            "created_date": datetime.now(timezone.utc).isoformat(),
            "houdini_version": hou_ver,
            "user": get_user_name(),
            "nodes": nodes_data,
            "connections": conns_data,
            "thumbnail": _THUMB_FILE if thumb_bytes else "",
        }
        entry_dir.mkdir(parents=True, exist_ok=True)
        (entry_dir / _CPIO_FILE).write_bytes(cpio_bytes)
        if thumb_bytes:
            (entry_dir / _THUMB_FILE).write_bytes(thumb_bytes)
        # Last: entry.json is what makes the folder a recipe.
        write_entry(entry_dir, meta)

        # Local write, so a synchronous rescan is cheap and makes the
        # new card and its sidebar count visible at once.
        self.scan_all()
        ref = RecipeRef(project.name, context, slug)
        hou.ui.setStatusMessage(
            f"Saved recipe: {name} ({len(selected)} nodes)",
            severity=hou.severityType.Message,
        )
        return ref.asset_id


def _split_tags(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def _short_context(category_name: str) -> str:
    from tumbletrove.asset_browser.core.context_map import CONTEXT_MAP
    return CONTEXT_MAP.get(category_name, category_name.lower())
