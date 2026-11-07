"""Right-click menus of the Pipeline catalog's cards and deck items.

Every callable a menu hook hands the host is made its own failure boundary
(:func:`reporting_menu`), so an entry that raises reaches the artist rather
than the console. Recipe and node cards have their own menus; the catalog
routes those before anything reaches :class:`PipelineMenus`. An edit card's
menu is :meth:`PipelineMenus.edit_items`.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.types import Asset, MenuItem

from tumblepipe.naming import get_version_code
from tumblepipe.pipe.houdini.ui.report import reporting

from .entity_ops import asset_context
from .types import latest_version

if TYPE_CHECKING:
    from .containers import ContainerManager
    from .discovery import EntityIndex
    from .entity_ops import EntityOps
    from .refresh import BrowserRefresh
    from .resolver import AssetResolver
    from .scene import SceneManager
    from .thumbnails import ThumbnailManager
    from .workfiles import WorkfileManager


def _sync_with_multi() -> None:
    """The open Multi workfile against its Multi
    (``pipe.houdini.multi_sync``); imported on click, it needs ``hou``."""
    from tumblepipe.pipe.houdini import multi_sync
    multi_sync.sync_with_multi()


def note_tooltip_html(version: str, user: str, note: str) -> str:
    """Hover text for a noted version in the Open Version menu.

    Rich text so the header can be bold and the note keeps its line
    breaks; everything the artist typed is escaped first, or a ``<``
    in a note would swallow the rest of it.
    """
    import html

    header = f"<b>{html.escape(version)}</b>"
    if user:
        header += f" · {html.escape(user)}"
    body = html.escape(note).replace("\n", "<br>")
    return f"<qt>{header}<br>{body}</qt>"

# Group accent — matches ``TYPE_COLORS["group"]`` in asset_browser's
# theme.py. Reused as the deck item tint when a shot/asset dept is
# superseded by a group workfile.
_GROUP_ACCENT_COLOR = "#e08c4a"

# Hover text on a dept card that has no workfile yet. The card's
# double-click and its first right-click item both create v0001 from
# the department template; the tooltip says so because a dimmed card
# otherwise reads as inert.
_MISSING_DEPT_TOOLTIP = (
    "{short} — no workfile yet\n"
    "Double-click to create the first version from the template"
)


#: A menu separator, as the browser's menu hooks spell it.
SEPARATOR = ("__separator__", None)


def sections(*groups: list) -> list:
    """The non-empty *groups*, in order, one separator between each two.

    The browser renders ``("__separator__", None)`` as a line in every
    catalog menu from TumbleTrove 0.57; no group means no stray line at
    either end or two in a row.
    """
    out: list = []
    for group in groups:
        if not group:
            continue
        if out:
            out.append(SEPARATOR)
        out.extend(group)
    return out


def with_shown_version(items: list, version, open_version) -> list:
    """A department's menu (*items*) as the detail panel's row shows it:
    led by **Open <version>** and a separator when the row's version
    dropdown shows *version* and the menu does not already lead with
    opening it (**Open Latest (<version>)**). ``open_version()`` opens it.
    Otherwise *items* unchanged."""
    if not version:
        return list(items)
    latest = f"Open Latest ({version})"
    if any(label == latest for label, _action in items):
        return list(items)
    return [(f"Open {version}", open_version), SEPARATOR, *items]


def fill_menu(menu, items: list) -> None:
    """Add catalog menu *items* to the QMenu *menu*, the way the browser
    renders a catalog menu: separators, nested lists as submenus (disabled
    when empty), :class:`MenuItem` icons and tooltips, and a non-callable
    action as an inert entry. For the menus TumblePipe shows itself (the
    detail panel's department rows), so they match the deck's."""
    from PySide6.QtWidgets import QMenu
    from tumbletrove.asset_browser.api.types import menu_entry
    from tumbletrove.asset_browser.core.theme import MENU_STYLE

    for item in items:
        label, action, icon, tooltip = menu_entry(item)
        if label == SEPARATOR[0]:
            menu.addSeparator()
            continue
        if isinstance(action, (list, tuple)):
            sub = QMenu(label, menu)
            sub.setStyleSheet(MENU_STYLE)
            fill_menu(sub, list(action))
            sub.setEnabled(bool(action))
            act = menu.addMenu(sub)
        else:
            act = menu.addAction(label)
            if callable(action):
                act.triggered.connect(lambda _checked=False, f=action: f())
        if icon:
            try:
                from tumbletrove.common.icons import load_qicon
                qicon = load_qicon(icon, size=16)
            except Exception:
                qicon = None
            if qicon is not None:
                act.setIcon(qicon)
        if tooltip:
            act.setToolTip(tooltip)
    if any(menu_entry(i)[3] for i in items):
        menu.setToolTipsVisible(True)


def reporting_menu(items: list) -> list:
    """``items`` (pairs, :class:`MenuItem` s, nested submenus) with every
    callable action made its own failure boundary (:func:`reporting`)
    under its own label.

    A menu entry's callback runs in a Qt slot, where a raise reaches the
    console and nothing else - the artist sees a menu entry that did
    nothing. Wrapping what the menu hooks return is the one place that
    guarantees every action behind a catalog menu is reported.
    """
    out: list = []
    for item in items:
        label, action = item
        if callable(action):
            action = reporting(label, action)
        elif isinstance(action, (list, tuple)):
            action = reporting_menu(list(action))
        if isinstance(item, MenuItem):
            out.append(dataclasses.replace(item, action=action))
        else:
            out.append((label, action))
    return out


class PipelineMenus:
    """Card and deck-item menus for assets, shots and containers."""

    def __init__(
        self,
        *,
        resolver: "AssetResolver",
        index: "EntityIndex",
        ops: "EntityOps",
        workfiles: "WorkfileManager",
        scene: "SceneManager",
        containers: "ContainerManager",
        thumbnails: "ThumbnailManager",
        refresh: "BrowserRefresh",
    ) -> None:
        self._resolver = resolver
        self._index = index
        self._ops = ops
        self._workfiles = workfiles
        self._scene = scene
        self._containers = containers
        self._thumbnails = thumbnails
        self._refresh = refresh

    def card_items(self, asset: Asset, *, selected_assets=None) -> list:
        """An asset, shot or container card's right-click items."""
        asset_id = asset.id

        # Build the submit-jobs target list: the multi-selected cards if
        # the browser provided a selection scoped to this catalog AND
        # filtered to the same context as the right-clicked card,
        # otherwise just this card.
        click_context = asset_context(asset)
        if selected_assets is not None and click_context is not None:
            submit_targets = [
                a for a in selected_assets
                if asset_context(a) == click_context
            ]
            if not submit_targets:
                submit_targets = [asset]
        else:
            submit_targets = [asset]
        submit_label = (
            "Submit Jobs…"
            if len(submit_targets) <= 1
            else f"Submit Jobs for {len(submit_targets)} selected…"
        )

        tags = asset.tags or ()
        is_shot = "type:shot" in tags
        is_asset = "type:asset" in tags

        # The same sections in the same order on every asset and shot
        # card, so an entry is where the artist last saw it whatever they
        # clicked: playing, farm work, the editors, the entity's own
        # details, then leaving a Multi or Root. A section a card has
        # nothing for is left out with its separator. (A Multi's card is
        # a container card: the browser gives it the container menu.)
        play: list = []
        if is_shot:
            # The selected shots' playblasts and renders, back to back in
            # the sequence player.
            shots = [a for a in submit_targets if "type:shot" in (a.tags or ())] or [asset]
            play.append((
                "Play" if len(shots) <= 1 else f"Play {len(shots)} Shots",
                lambda targets=shots: self._play_shots(targets),
            ))
        work: list = [
            (
                submit_label,
                lambda targets=submit_targets:
                    self._ops.submit_jobs(targets),
            ),
            (
                "Generate Master…",
                lambda aid=asset_id: self._ops.generate_master_scene(aid),
            ),
        ]
        if is_asset:
            work.append((
                "Duplicate…",
                lambda aid=asset_id: self._ops.duplicate_asset(aid),
            ))
        editors: list = [
            (
                "Open in Entity Editor…",
                lambda aid=asset_id: self._ops.open_entity_editor_for(aid),
            ),
            (
                "Open in Database Editor…",
                lambda aid=asset_id: self._ops.open_database_editor(aid),
            ),
        ]
        details: list = [
            (
                "Edit Description…",
                lambda aid=asset_id: self._ops.edit_description(aid),
            ),
            (
                "Tasks…",
                lambda a=asset: self._ops.manage_todos(a),
            ),
            (
                "Departments…",
                lambda aid=asset_id: self._ops.edit_departments(aid),
            ),
            (
                "Select Thumbnail…",
                lambda aid=asset_id: self._thumbnails.select(aid),
            ),
            (
                "Capture Thumbnail",
                lambda aid=asset_id: self._thumbnails.capture(aid),
            ),
        ]

        membership: list = []
        # A Root set directly on the shot (not inherited from its
        # sequence) can be cleared here.
        if is_shot and self._ops.shot_has_direct_scene_ref(asset_id):
            membership.append((
                "Clear Root",
                lambda aid=asset_id: self._ops.clear_shot_scene_ref(aid),
            ))
        # "Remove from <Multi/Root>" — one entry per container this
        # asset belongs to. For Multis this is full member-list
        # removal (a Multi's coverage is whole-asset, not per-dept).
        # For Roots this removes the asset from the Root's asset list
        # (shots use ``Clear Root`` above, which clears their
        # ``scene_ref`` instead; Roots don't list shots, so the scene
        # branch only fires for asset cards).
        for cid, label, kind in self._containers.get_asset_membership(asset_id):
            prefix = {"group": "Multi", "scene": "Root"}.get(kind)
            if prefix is None:
                continue
            membership.append((
                f"Remove from {prefix}: {label}",
                lambda aid=asset_id, c=cid:
                    self._containers.remove_member(aid, c),
            ))
        return sections(play, work, editors, details, membership)

    def _play_shots(self, shots: list[Asset]) -> None:
        self._ops.play_shots([a.id for a in shots])

    def edit_items(self, asset: Asset) -> list:
        """An edit card's right-click items. Built from the card (the menu
        opens on the GUI thread, which must not list the file server); each
        action reads what is on disk when picked."""
        meta = asset.metadata or {}
        asset_id = asset.id
        items: list = [
            MenuItem(
                "Open in Editor",
                lambda: self._ops.open_edit(asset_id),
                tooltip=(
                    f"Open working version {meta['working']}" if meta.get("working")
                    else "Never saved: it opens empty, for its first Save"
                ),
            ),
        ]
        if meta.get("published"):
            items.append(MenuItem(
                "Play Published",
                lambda: self._ops.open_edit(asset_id, published=True),
                tooltip=f"Open the latest published version ({meta['published']}), read-only",
            ))
        if meta.get("master"):
            # A label with no action, ticked: picking it does nothing.
            items.append(MenuItem(
                "Master Edit", None, icon="lucide:check",
                tooltip="This is the project's master edit: the cut the "
                        "shots' frame ranges follow",
            ))
            items.append((
                "Clear Master Edit",
                lambda: self._ops.set_master_edit(asset_id, False),
            ))
        else:
            items.append(MenuItem(
                "Set as Master Edit",
                lambda: self._ops.set_master_edit(asset_id, True),
                tooltip="Make this the cut the shots' frame ranges follow",
            ))
        return items

    def deck_item_items(self, asset: Asset, deck_item_key: str) -> list:
        """A department deck item's right-click items (see
        :meth:`PipelineCatalog.get_deck_item_menu_items`)."""
        return self.department_items(asset.id, deck_item_key)

    def department_items(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> list:
        """The right-click items of *asset_id*'s department *dept*: the
        deck item's, the list view's department row's and the detail
        panel's department row's, so all three are one menu.
        *refresh_cb* is what the panel refreshes after an open or a new
        version (the detail panel's own; the browser's otherwise)."""
        refresh = refresh_cb or self._refresh.detail

        # A Multi's department is one workfile shared by its members: the
        # same menu as a shot's, with its exports (written per member)
        # reached through a member submenu.
        is_group = asset_id.startswith("group:")
        if is_group:
            versions = self._containers.group_department_versions(
                asset_id,
            ).get(dept, [])
        else:
            if self._resolver.uri_for(asset_id) is None:
                return []
            versions = self._index.department_versions(asset_id).get(
                dept, [],
            )
        available = bool(versions)
        latest = latest_version(versions)
        scene_dv = self._scene.get_scene_dept_version(asset_id)
        is_active = bool(scene_dv) and scene_dv[0] == dept

        new_tmpl = (
            "New from Template",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_template(
                    aid, dn, refresh,
                ),
        )
        new_cur = (
            "New from Current",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_current(
                    aid, dn, refresh,
                ),
        )
        open_dir = (
            self._workfiles.open_group_dept_work_dir if is_group
            else self._workfiles.open_dept_work_dir
        )
        open_folder = (
            "Open Folder",
            lambda aid=asset_id, dn=dept: open_dir(aid, dn),
        )

        items: list = []
        if available:
            # Versions with an artist's note get a comment icon and show
            # the note on hover, so the ones worth reverting to stand out.
            notes = self._workfiles.get_notes_for_versions(
                asset_id, dept, versions,
            )

            def open_item(label: str, version: str):
                fn = (
                    lambda aid=asset_id, dn=dept, v=version:
                        self._workfiles.open_version_now(aid, dn, v, refresh_cb)
                )
                if version not in notes:
                    return (label, fn)
                user, note = notes[version]
                return MenuItem(
                    label, fn,
                    icon="lucide:message-square",
                    tooltip=note_tooltip_html(version, user, note),
                )

            items.append(open_item(f"Open Latest ({latest})", latest))
            # Every older version, newest first — a submenu, which the
            # browser renders from a nested item list. Omitted when the
            # latest is the only version.
            older = sorted(
                (v for v in versions if v != latest),
                key=get_version_code, reverse=True,
            )
            if older:
                items.append((
                    "Open Version", [open_item(v, v) for v in older],
                ))
            items.append(open_folder)
            if is_group:
                members = self._containers.group_member_ids(asset_id)
                if members:
                    items.append((
                        "View Latest Export",
                        [
                            (
                                name,
                                lambda aid=member_id, dn=dept:
                                    self._workfiles.open_latest_export(aid, dn),
                            )
                            for member_id, name in members
                        ],
                    ))
            else:
                items.append((
                    "View Latest Export",
                    lambda aid=asset_id, dn=dept:
                        self._workfiles.open_latest_export(aid, dn),
                ))
            items.append((
                "Open in New Houdini",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_in_new_instance(aid, dn),
            ))
            if is_active:
                items.append((
                    "Reload Scene",
                    lambda: self._scene.reload_current_scene(refresh_cb),
                ))
                if is_group:
                    # The open Multi workfile against its Multi: exports
                    # for shots not in it, members with no export.
                    items.append(("Sync with Multi", _sync_with_multi))
        else:
            # Empty dept: creating the first version is the whole
            # point of right-clicking here, so it leads.
            items.append(new_tmpl)
            items.append(new_cur)
            items.append(("__separator__", None))
            items.append(open_folder)

        # "Remove from <group>" — visible only when this dept is
        # currently covered by a group's workfile for this member.
        # Removing the member from the group is global (affects every
        # dept the group covers for this member), so the label is
        # phrased as "Remove from <group>" rather than per-dept.
        group_info = None if is_group else (
            self._containers.member_coverage(asset_id).get(dept)
        )
        if group_info:
            group_id, group_label = group_info
            items.append(("__separator__", None))
            items.append((
                f"Remove from {group_label}",
                lambda aid=asset_id, gid=group_id:
                    self._containers.remove_member(aid, gid),
            ))

        if available:
            items.append(("__separator__", None))
            items.append(new_tmpl)
            items.append(new_cur)
        return items
