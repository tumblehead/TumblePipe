"""The Multi / Root menu: this catalog's entries on tumbletrove's container
menu, and running them.

tumbletrove renders one menu for a container wherever it appears (grid
card, list row, shelf card, sidebar row): its own Open / Edit / Add and
Remove selected / Delete, named with :meth:`ContainerMenu.kind_label`, and
between Open and Edit whatever :meth:`ContainerMenu.actions` returns. A pick
comes back through ``Catalog.execute_container_action`` on the GUI thread,
with the browser widget to parent a dialog to.

The Root flows used to live in the browser, which is why it knew about
shots, scene USD and "Stage Root": the confirmations that put the shot count
in front of the artist before a rebuild (each shot is a local Houdini build
that can take minutes) are about Roots, so they are here now.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tumbletrove.asset_browser.api.types import ContainerAction

from . import containers as containers

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from .containers import ContainerManager

#: What the browser calls each container kind.
KIND_LABELS = {"group": "Multi", "scene": "Root"}

OPEN_LOCATION = "open_location"
STAGE_AND_BUILD = "stage_and_build"
EXPORT_ROOT = "export_root"
REBUILD_SHOTS = "rebuild_shots"


def _plural(n: int) -> str:
    return "s" if n != 1 else ""


def _shot_preview(shot_uris: list) -> str:
    """First 8 shot URIs as a bullet list, with an '…and N more' tail."""
    preview = "\n".join(f"  • {uri}" for uri in shot_uris[:8])
    if len(shot_uris) > 8:
        preview += f"\n  …and {len(shot_uris) - 8} more"
    return preview


def _confirm(parent, title: str, message: str, action_label: str) -> bool:
    from tumbletrove.asset_browser.ui.dialogs import confirm_destructive
    return confirm_destructive(
        parent, title=title, message=message, action_label=action_label,
    )


class ContainerMenu:
    """The catalog's container-menu entries, over the ContainerManager
    that does the work."""

    def __init__(self, manager: "ContainerManager") -> None:
        self._containers = manager

    def kind_label(self, kind: str) -> str:
        return KIND_LABELS[kind]

    def actions(self, collection_id: str, kind: str) -> list[ContainerAction]:
        """Open Location on both kinds; the Root's export and rebuilds on a
        Root. The export is an explicit action because
        ``export_scene_version`` is too heavy to run on every membership
        edit."""
        actions = [ContainerAction(OPEN_LOCATION, "Open Location")]
        if kind == "scene":
            actions += [
                ContainerAction(STAGE_AND_BUILD, "Stage Root && Rebuild Shots…"),
                ContainerAction(EXPORT_ROOT, "Export Root USD"),
                ContainerAction(REBUILD_SHOTS, "Rebuild Assigned Shots…"),
            ]
        return actions

    def execute(
        self, action_id: str, collection_id: str, parent: "QWidget",
    ) -> str:
        """Run *action_id* on *collection_id*; the status-bar message.

        Raises on an id this menu never offered, or on a container id that
        is not a Multi or Root; tumbletrove reports either.
        """
        ref = containers.parse(collection_id)
        if ref is None:
            raise ValueError(f"{collection_id!r} is not a Multi or Root id")
        label = ref.path
        if action_id == OPEN_LOCATION:
            self._containers.open_container_location(collection_id)
            return ""
        if action_id == EXPORT_ROOT:
            if self._containers.export_root_usd(collection_id):
                return f"Exported Root USD: {label}"
            return ""
        if action_id == REBUILD_SHOTS:
            return self._rebuild_shots(collection_id, label, parent)
        if action_id == STAGE_AND_BUILD:
            return self._stage_and_build(collection_id, label, parent)
        raise ValueError(f"unknown container action {action_id!r}")

    def _rebuild_shots(self, collection_id: str, label: str, parent) -> str:
        """Regenerate root layers + rebuild staged USD for every shot
        whose ``scene_ref`` points at this Root, after a confirmation
        that shows the shot count first."""
        shot_uris = self._containers.list_root_assigned_shots(collection_id)
        if not shot_uris:
            return f"No shots assigned to {label}"
        n = len(shot_uris)
        confirmed = _confirm(
            parent,
            title=f"Rebuild {n} shot{_plural(n)}",
            message=(
                f"Regenerate root layer + build staged USD for the "
                f"{n} shot{_plural(n)} assigned to Root '{label}'?\n\n"
                f"{_shot_preview(shot_uris)}\n\n"
                "Each shot triggers a local Houdini build — this can "
                "take a while."
            ),
            action_label=f"Rebuild {n}",
        )
        if not confirmed:
            return ""
        ok_count, failed = self._containers.rebuild_root_assigned_shots(
            collection_id,
        )
        msg = f"Rebuilt {ok_count}/{n} shot{_plural(n)}"
        if failed:
            msg += f" — {len(failed)} failed (see Python Shell)"
        return msg

    def _stage_and_build(self, collection_id: str, label: str, parent) -> str:
        """Export the Root's scene USD, then rebuild every shot assigned
        to it, behind one confirmation. The Root's dirty indicator goes
        once the export completes."""
        shot_uris = self._containers.list_root_assigned_shots(collection_id)
        n = len(shot_uris)
        shots_clause = (
            f"Then regenerate root + build staged USD for {n} "
            f"assigned shot{_plural(n)}.\n\n{_shot_preview(shot_uris)}\n\n"
            "Each shot triggers a local Houdini build — this can "
            "take a while."
        ) if n else (
            "No shots are currently assigned to this Root, so the "
            "build step is a no-op."
        )
        confirmed = _confirm(
            parent,
            title=f"Stage Root & Rebuild {n} shot{_plural(n)}",
            message=f"Export the USD for Root '{label}'.\n{shots_clause}",
            action_label="Stage & Build",
        )
        if not confirmed:
            return ""
        if not self._containers.export_root_usd(collection_id):
            return f"Stage did not export Root '{label}'"
        if n == 0:
            return f"Exported Root USD: {label}"
        try:
            ok_count, failed = self._containers.rebuild_root_assigned_shots(
                collection_id,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Root '{label}' was exported, but rebuilding its shots "
                f"failed: {exc}"
            ) from exc
        msg = f"Staged Root '{label}'; rebuilt {ok_count}/{n} shot{_plural(n)}"
        if failed:
            msg += f" — {len(failed)} failed (see Python Shell)"
        return msg
