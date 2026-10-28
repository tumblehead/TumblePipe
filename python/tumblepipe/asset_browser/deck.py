"""The department deck of asset, shot and Multi cards.

One :class:`DeckItem` per department: its latest workfile version (the
list view's Version / User / Edited / Note cells), "active" for the
department of the loaded workfile, "missing" with create-from-template for
a department with no workfile yet, and a Multi's tint where a Multi's
workfile covers a member's department.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.types import Asset, DeckItem

from . import containers as containers_mod
from .types import DEPT_ICONS, DEPT_SHORT_NAMES, SHOT_DEPT_ICONS

if TYPE_CHECKING:
    from .containers import ContainerManager
    from .dept_scope import DepartmentScope
    from .scene import SceneManager
    from .workfiles import WorkfileManager

# Hover text on a dept card that has no workfile yet. The card's
# double-click and its first right-click item both create v0001 from
# the department template; the tooltip says so because a dimmed card
# otherwise reads as inert.
_MISSING_DEPT_TOOLTIP = (
    "{short} — no workfile yet\n"
    "Double-click to create the first version from the template"
)

# TumbleTrove 0.50 added ``DeckItem.draggable``; an older build rejects
# the keyword, and there the drop router's refusal is all there is.
_HAS_DRAGGABLE = "draggable" in {f.name for f in dataclasses.fields(DeckItem)}

# Group accent — matches ``TYPE_COLORS["group"]`` in the asset browser's
# theme. The deck item tint where a Multi's workfile covers a member's
# department.
_GROUP_ACCENT_COLOR = "#e08c4a"


class DeckBuilder:
    """Deck items for the Pipeline catalog's cards."""

    def __init__(
        self,
        *,
        containers: "ContainerManager",
        scene: "SceneManager",
        workfiles: "WorkfileManager",
        dept_scope: "DepartmentScope",
    ) -> None:
        self._containers = containers
        self._scene = scene
        self._workfiles = workfiles
        self._dept_scope = dept_scope

    @staticmethod
    def _group_context(group_id: str) -> str | None:
        ref = containers_mod.parse(group_id)
        return ref.context if isinstance(ref, containers_mod.GroupContainer) else None

    def items(self, asset: Asset) -> list[DeckItem]:
        """The deck of an asset, shot or Multi card: one item per department."""
        # Group container cards: one deck item per dept the group
        # covers. "missing" status for covered depts that don't have a
        # workfile yet — right-click "New from Template" creates one,
        # and so does a double-click (``new_from_template:<dept>``).
        if "type:group" in asset.tags:
            # ``departments`` on a group card is dict[str, str]: dept
            # name → latest version (empty string when uncovered).
            depts_dict = asset.metadata.get("departments") or {}
            covered = list(depts_dict.keys())
            ctx = (
                asset.context
                or self._group_context(asset.id)
                or "shots"
            )
            canonical = self._dept_scope.pool(ctx)
            order = {name: i for i, name in enumerate(canonical)}
            sorted_depts = sorted(
                covered,
                key=lambda n: (order.get(n, len(order)), n),
            )
            # Per-dept author, save time and note for the list view's
            # User/Edited/Note columns (one stat + sidecar read per dept,
            # deck rows populate lazily on expand).
            attribution = self._containers.group_attribution(
                asset.id)
            # A Multi's open workfile marks its row "active", the same
            # as an entity's below: get_scene_dept_version reads a
            # container id now, so the two branches no longer disagree
            # about which department the user is in.
            group_dv = self._scene.get_scene_dept_version(asset.id)
            group_active = group_dv[0] if group_dv else None
            cards: list[DeckItem] = []
            for dept_name in sorted_depts:
                short = DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
                latest = depts_dict.get(dept_name) or ""
                if latest:
                    user, mtime, note = attribution.get(
                        dept_name, ("", 0.0, ""),
                    )
                    cards.append(DeckItem(
                        key=dept_name,
                        label=short,
                        status=(
                            "active" if dept_name == group_active
                            else "available"
                        ),
                        detail=latest,
                        icon=DEPT_ICONS.get(dept_name, "package"),
                        action_id=f"open_workfile:{dept_name}",
                        dismiss_on_click=True,
                        user=user,
                        edited=mtime,
                        note=note,
                    ))
                else:
                    cards.append(DeckItem(
                        key=dept_name,
                        label=short,
                        status="missing",
                        icon=DEPT_ICONS.get(dept_name, "package"),
                        action_id=f"new_from_template:{dept_name}",
                        dismiss_on_click=True,
                        tooltip=_MISSING_DEPT_TOOLTIP.format(short=short),
                    ))
            return cards

        depts = asset.metadata.get("departments", {})

        cards = []
        # The departments this entity is scoped to, plus any that already
        # have a workfile — an unassigned department with work still shows,
        # flagged, rather than vanishing from the deck.
        is_shot = "type:shot" in asset.tags
        all_depts, assigned_depts = self._dept_scope.for_entity(
            asset.id,
            "shots" if is_shot else "assets",
            with_work=depts.keys(),
        )

        # Detect the loaded scene's dept so we can mark it "active".
        scene_dv = self._scene.get_scene_dept_version(asset.id)
        active_dept = scene_dv[0] if scene_dv else None

        # Group coverage: depts where this member's workfile is
        # superseded by a group's multi-shot workfile. The deck item
        # detail line shows a "ⓜ" badge instead of a version (the
        # tooltip names the Multi), and
        # the click action route through ``open_workfile:<dept>`` —
        # which now resolves via ``latest_hip_file_path_with_context``
        # and lands on the group's hip automatically.
        dept_groups = self._containers.member_coverage(asset.id)

        def _icon_for(dept: str) -> str:
            if is_shot and dept in SHOT_DEPT_ICONS:
                return SHOT_DEPT_ICONS[dept]
            return DEPT_ICONS.get(dept, "package")

        def _drag(dept: str) -> dict:
            # A dept published from SOPs (rig: a .bgeo.sc) has no layer
            # for a LOP drop to import, so its item does not drag (Magnus).
            if _HAS_DRAGGABLE and not self._workfiles.exports_usd(asset.id, dept):
                return {"draggable": False}
            return {}

        for dept_name in all_depts:
            short = DEPT_SHORT_NAMES.get(dept_name, dept_name.title())
            version = depts.get(dept_name)
            group_info = dept_groups.get(dept_name)
            if group_info:
                _group_id, group_label = group_info
                status = (
                    "active" if dept_name == active_dept else "available"
                )
                # Detail text stays short — deck items are narrow, so a
                # long group name overflows. The tint (border + icon)
                # signals "covered by a group"; the tooltip carries
                # the full group name for hover discovery.
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail="ⓜ",
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
                    dismiss_on_click=True,
                    tint=_GROUP_ACCENT_COLOR,
                    tooltip=(
                        f"{short} — covered by Multi: {group_label}\n"
                        "Double-click to open the Multi's workfile"
                    ),
                    **_drag(dept_name),
                ))
            elif version:
                status = (
                    "active" if dept_name == active_dept else "available"
                )
                # Per-dept attribution for the list view's User/Edited/Note
                # columns (deck_user/deck_edited/deck_note): who last saved
                # this dept's latest workfile, when, and what they said
                # changed. One resolve + glob + stat + sidecar read per
                # dept — see get_dept_row_meta, which exists so this isn't
                # three of each. Group-covered and missing depts stay blank:
                # the member has no workfile of its own to attribute.
                user, mtime, note = self._workfiles.get_dept_row_meta(
                    asset.id, dept_name, version,
                )
                tooltip = None
                if dept_name not in assigned_depts:
                    tooltip = (
                        f"{short} — has work but is not one of this entity's "
                        "departments.\nRe-assign it, or leave it: the "
                        "workfile and its exports are untouched either way."
                    )
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status=status,
                    detail=version,
                    icon=_icon_for(dept_name),
                    action_id=f"open_workfile:{dept_name}",
                    dismiss_on_click=True,
                    tooltip=tooltip,
                    user=user,
                    edited=mtime,
                    note=note,
                    **_drag(dept_name),
                ))
            else:
                # No workfile yet. Double-click creates v0001 from the
                # template — the action nearly every click on an empty
                # card is after. ``dismiss_on_click`` because it loads
                # a new scene: the list view then waits for a double-
                # click too (a stray single click must not create a
                # workfile), and the grid shows the loading overlay.
                cards.append(DeckItem(
                    key=dept_name,
                    label=short,
                    status="missing",
                    icon=_icon_for(dept_name),
                    action_id=f"new_from_template:{dept_name}",
                    dismiss_on_click=True,
                    tooltip=_MISSING_DEPT_TOOLTIP.format(short=short),
                ))

        return cards
