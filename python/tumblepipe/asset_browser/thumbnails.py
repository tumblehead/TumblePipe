"""Per-asset thumbnail sidecar management.

Each pipeline asset / shot stores its thumbnail as ``thumbnail.png``
next to ``description.txt`` in the entity's project-share folder.
:class:`ThumbnailManager` resolves that path, serves it to the
browser via :meth:`get_thumbnail`, and provides two write-side
actions:

- :meth:`select` — open a file picker and copy a chosen image.
- :meth:`capture` — flipbook one frame from the active Scene Viewer.

Both write actions then ask the open browsers to drop the card's cached
thumbnail and re-fetch the detail panel (:meth:`BrowserRefresh.thumbnail`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .refresh import BrowserRefresh

log = logging.getLogger(__name__)


class ThumbnailManager:
    """Thumbnail sidecar reader / writer for the Pipeline catalog."""

    def __init__(
        self,
        *,
        entity_dir: Callable[[str], Path | None],
        refresh: "BrowserRefresh",
    ) -> None:
        self._entity_dir = entity_dir
        self._refresh = refresh

    # ── Path resolution ──────────────────────────────────

    def thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the path to ``thumbnail.png`` for an asset/shot, or
        ``None`` for a malformed id.

        Sidecar lives next to ``description.txt`` in the asset/shot
        root directory so it follows the same project share semantics.
        """
        entity_dir = self._entity_dir(asset_id)
        return None if entity_dir is None else entity_dir / "thumbnail.png"

    def get_thumbnail(self, asset):
        """Return the path to the asset's sidecar thumbnail if present.

        An empty string (placeholder icon) when there isn't one yet.
        """
        p = self.thumbnail_path(asset.id)
        if p is not None and p.exists():
            return p
        return ""

    # ── Write actions ────────────────────────────────────
    #
    # Both run behind a card menu entry, which is their failure boundary
    # (menus.reporting_menu): anything that goes wrong raises.

    def select(self, asset_id: str) -> None:
        """Pick an image off disk and write it to the asset's
        ``thumbnail.png`` sidecar."""
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtGui import QImage
        import hou

        out_path = self.thumbnail_path(asset_id)
        if out_path is None:
            return

        src, _ = QFileDialog.getOpenFileName(
            hou.qt.mainWindow(),
            "Select Thumbnail",
            "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)",
        )
        if not src:
            return

        img = QImage(src)
        if img.isNull():
            raise ValueError(f"{src} is not an image Houdini's Qt can read")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not img.save(str(out_path), "PNG"):
            raise OSError(f"could not write the thumbnail to {out_path}")

        log.info("Set thumbnail for %s -> %s", asset_id, out_path)
        hou.ui.setStatusMessage(
            f"Thumbnail set for {asset_id}",
            severity=hou.severityType.Message,
        )
        self._refresh.thumbnail(asset_id)

    def capture(self, asset_id: str) -> None:
        """Capture the current frame from the active Scene Viewer and
        write it to the asset's ``thumbnail.png`` sidecar."""
        import hou

        out_path = self.thumbnail_path(asset_id)
        if out_path is None:
            return

        sv = self._find_active_scene_viewer()
        if sv is None:
            hou.ui.displayMessage(
                "Capture thumbnail: there is no Scene Viewer to capture.",
                severity=hou.severityType.Warning,
            )
            return

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Wipe any stale file so the post-capture existence check means
        # this capture wrote it.
        out_path.unlink(missing_ok=True)

        settings = sv.flipbookSettings().stash()
        settings.output(str(out_path))
        settings.outputToMPlay(False)
        cur = int(hou.frame())
        settings.frameRange((cur, cur))
        settings.useResolution(True)
        settings.resolution((512, 512))
        sv.flipbook(sv.curViewport(), settings)

        # Houdini sometimes inserts a frame-number into the output
        # filename even for a single-frame range
        # (e.g. ``thumbnail.0042.png``). If the literal path is
        # missing, glob the parent for any ``thumbnail*.png`` and
        # rename the newest match to the canonical sidecar path.
        if not out_path.exists():
            cands = sorted(
                out_path.parent.glob("thumbnail*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if cands:
                cands[0].replace(out_path)

        if not out_path.exists():
            raise OSError(f"the flipbook wrote nothing to {out_path}")

        log.info("Captured thumbnail for %s -> %s", asset_id, out_path)
        hou.ui.setStatusMessage(
            f"Captured thumbnail for {asset_id}",
            severity=hou.severityType.Message,
        )
        self._refresh.thumbnail(asset_id)

    # ── Scene-Viewer lookup ──────────────────────────────

    @staticmethod
    def _find_active_scene_viewer():
        """Return the currently active ``hou.SceneViewer``, or any
        Scene Viewer if none is currently focused, or ``None``."""
        import hou
        scene_viewers = [
            tab for tab in hou.ui.paneTabs() if isinstance(tab, hou.SceneViewer)
        ]
        for sv in scene_viewers:
            if sv.isCurrentTab():
                return sv
        return scene_viewers[0] if scene_viewers else None
