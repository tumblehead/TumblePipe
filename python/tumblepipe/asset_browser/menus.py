"""Right-click menus of the Pipeline catalog's cards and deck items.

Every callable a menu hook hands the host is made its own failure boundary
(:func:`reporting_menu`), so an entry that raises reaches the artist rather
than the console. Recipe and node cards have their own menus; the catalog
routes those before anything reaches :class:`PipelineMenus`.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable

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
        edit_group_departments: Callable[[str], None],
    ) -> None:
        self._resolver = resolver
        self._index = index
        self._ops = ops
        self._workfiles = workfiles
        self._scene = scene
        self._containers = containers
        self._thumbnails = thumbnails
        self._refresh = refresh
        self._edit_group_departments = edit_group_departments

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

        items = [
            (
                submit_label,
                lambda targets=submit_targets:
                    self._ops.submit_jobs(targets),
            ),
            (
                "Generate Master…",
                lambda aid=asset_id: self._ops.generate_master_scene(aid),
            ),
            (
                "Edit description…",
                lambda aid=asset_id: self._ops.edit_description(aid),
            ),
            (
                "Tasks…",
                lambda a=asset: self._ops.manage_todos(a),
            ),
            (
                "Select thumbnail…",
                lambda aid=asset_id: self._thumbnails.select(aid),
            ),
            (
                "Capture thumbnail",
                lambda aid=asset_id: self._thumbnails.capture(aid),
            ),
            (
                "Departments…",
                # A Multi's departments are a coverage decision, not an
                # assignment, and EntityDepartmentsDialog rejects it
                # outright (uri_for has no answer for a container id).
                # Route it to the coverage editor instead.
                (
                    lambda aid=asset_id: self._edit_group_departments(aid)
                ) if "type:group" in (asset.tags or ()) else (
                    lambda aid=asset_id: self._ops.edit_departments(aid)
                ),
            ),
            (
                "Open in Database Editor…",
                lambda aid=asset_id: self._ops.open_database_editor(aid),
            ),
        ]
        if (
            ("type:shot" in asset.tags or "type:asset" in asset.tags)
            and "type:group" not in (asset.tags or ())
        ):
            items.append((
                "Open in Entity Editor…",
                lambda aid=asset_id: self._ops.open_entity_editor_for(aid),
            ))
        if "type:asset" in asset.tags and "type:group" not in (asset.tags or ()):
            items.insert(2, (
                "Duplicate…",
                lambda aid=asset_id: self._ops.duplicate_asset(aid),
            ))
        # For shots, offer a "Clear scene" item when one is set directly
        # on the shot (not inherited from sequence).
        if "type:shot" in asset.tags and self._ops.shot_has_direct_scene_ref(asset_id):
            items.append(
                (
                    "Clear Root",
                    lambda aid=asset_id: self._ops.clear_shot_scene_ref(aid),
                )
            )

        # "Remove from <Multi/Root>" — one entry per container this
        # asset belongs to. For Multis this is full member-list
        # removal (a Multi's coverage is whole-asset, not per-dept).
        # For Roots this removes the asset from the Root's asset list
        # (shots use ``Clear Root`` above, which clears their
        # ``scene_ref`` instead).
        for cid, label, kind in self._containers.get_asset_membership(asset_id):
            if kind == "group":
                items.append((
                    f"Remove from Multi: {label}",
                    lambda aid=asset_id, c=cid:
                        self._containers.remove_member(aid, c),
                ))
            elif kind == "scene":
                # Roots don't store shot membership in their
                # ``assets`` list (shots point at a Root via
                # ``scene_ref``), so this branch only fires for
                # asset cards. The unified removal path covers it.
                items.append((
                    f"Remove from Root: {label}",
                    lambda aid=asset_id, c=cid:
                        self._containers.remove_member(aid, c),
                ))
        return items

    def deck_item_items(self, asset: Asset, deck_item_key: str) -> list:
        """A department deck item's right-click items (see
        :meth:`PipelineCatalog.get_deck_item_menu_items`)."""
        dept = deck_item_key
        asset_id = asset.id

        # Group container deck items: simpler menu — New from Template
        # / Open Latest / Open Folder. "New from Current" is
        # intentionally omitted for v1 since the active-scene-context
        # resolver doesn't track group workfiles yet.
        if asset_id.startswith("group:"):
            new_tmpl = (
                "New from Template",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.new_group_from_template(
                        aid, dn, self._refresh.detail,
                    ),
            )
            open_folder = (
                "Open Folder",
                lambda aid=asset_id, dn=dept:
                    self._workfiles.open_group_dept_work_dir(aid, dn),
            )
            group_depts = asset.metadata.get("departments") or {}
            if not group_depts.get(dept):
                return [new_tmpl, ("__separator__", None), open_folder]
            return [
                (
                    "Open Latest",
                    lambda aid=asset_id, dn=dept:
                        self._workfiles.open_group_workfile(aid, dn),
                ),
                open_folder,
                ("__separator__", None),
                new_tmpl,
            ]

        if self._resolver.uri_for(asset_id) is None:
            return []
        versions = self._index.department_versions(asset_id).get(dept, [])
        available = bool(versions)
        latest = latest_version(versions)
        scene_dv = self._scene.get_scene_dept_version(asset_id)
        is_active = bool(scene_dv) and scene_dv[0] == dept

        new_tmpl = (
            "New from Template",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_template(
                    aid, dn, self._refresh.detail,
                ),
        )
        new_cur = (
            "New from Current",
            lambda aid=asset_id, dn=dept:
                self._workfiles.new_from_current(
                    aid, dn, self._refresh.detail,
                ),
        )
        open_folder = (
            "Open Folder",
            lambda aid=asset_id, dn=dept:
                self._workfiles.open_dept_work_dir(aid, dn),
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
                        self._workfiles.open_version_now(aid, dn, v, None)
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
                    lambda: self._scene.reload_current_scene(None),
                ))
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
        dept_groups = self._containers.member_coverage(asset_id)
        group_info = dept_groups.get(dept)
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
