"""Per-asset thumbnail sidecar management.

Each pipeline asset / shot stores its thumbnail as ``thumbnail.png``
next to ``description.txt`` in the entity's project-share folder.
:class:`ThumbnailManager` resolves that path, serves it to the
browser via :meth:`get_thumbnail`, and provides two write-side
actions:

- :meth:`select` — open a file picker and copy a chosen image.
- :meth:`capture` — flipbook one frame from the active Scene Viewer.

Both write actions then invoke :meth:`request_refresh`, which walks
the browser's open ``AssetBrowserWidget`` instances to invalidate the
matching card's thumbnail cache and re-fetch the detail panel.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class ThumbnailManager:
    """Thumbnail sidecar reader / writer for the Pipeline catalog."""

    def __init__(
        self,
        *,
        split_asset_id: Callable[[str], tuple[str, str, str] | None],
        project_root_for: Callable[[str], Path | None],
        categories_for_project: Callable[[str], list[str]],
        request_global_detail_refresh: Callable[[], None],
    ) -> None:
        self._split_asset_id = split_asset_id
        self._project_root_for = project_root_for
        self._categories_for_project = categories_for_project
        self._request_global_detail_refresh = request_global_detail_refresh

    # ── Path resolution ──────────────────────────────────

    def thumbnail_path(self, asset_id: str) -> Path | None:
        """Return the path to ``thumbnail.png`` for an asset/shot.

        Sidecar lives next to ``description.txt`` in the asset/shot
        root directory so it follows the same project share semantics.
        """
        parsed = self._split_asset_id(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._project_root_for(asset_id)
        if root is None:
            return None
        try:
            cats = self._categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            return root / kind / second / third / "thumbnail.png"
        except Exception:
            return None

    def get_thumbnail(self, asset):
        """Return the path to the asset's sidecar thumbnail if present.

        Falls back to an empty string (placeholder icon) when there
        isn't one yet.
        """
        p = self.thumbnail_path(asset.id)
        if p is not None and p.exists():
            return p
        return ""

    # ── Write actions ────────────────────────────────────

    def select(self, asset_id: str) -> None:
        """Pick an image off disk and write it to the asset's
        ``thumbnail.png`` sidecar."""
        try:
            from PySide6.QtWidgets import QFileDialog
            from PySide6.QtGui import QImage
            import hou

            out_path = self.thumbnail_path(asset_id)
            if out_path is None:
                return

            parent = hou.qt.mainWindow()
            src, _ = QFileDialog.getOpenFileName(
                parent,
                "Select Thumbnail",
                "",
                "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)",
            )
            if not src:
                return

            img = QImage(src)
            if img.isNull():
                hou.ui.setStatusMessage(
                    f"Could not load image: {src}",
                    severity=hou.severityType.Warning,
                )
                return

            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not img.save(str(out_path), "PNG"):
                hou.ui.setStatusMessage(
                    f"Failed to write thumbnail to {out_path}",
                    severity=hou.severityType.Warning,
                )
                return

            log.info("Set thumbnail for %s -> %s", asset_id, out_path)
            hou.ui.setStatusMessage(
                f"Thumbnail set for {asset_id}",
                severity=hou.severityType.Message,
            )
            self.request_refresh(asset_id)
        except Exception:
            log.exception("Select thumbnail failed for %s", asset_id)

    def capture(self, asset_id: str) -> None:
        """Capture the current frame from the active Scene Viewer and
        write it to the asset's ``thumbnail.png`` sidecar."""
        try:
            import hou

            out_path = self.thumbnail_path(asset_id)
            if out_path is None:
                return

            sv = self._find_active_scene_viewer()
            if sv is None:
                hou.ui.setStatusMessage(
                    "Capture: no active Scene Viewer.",
                    severity=hou.severityType.Warning,
                )
                return

            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Wipe any stale file so the post-capture mtime check works.
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                log.debug("Could not remove old thumbnail at %s", out_path)

            try:
                settings = sv.flipbookSettings().stash()
                settings.output(str(out_path))
                settings.outputToMPlay(False)
                cur = int(hou.frame())
                settings.frameRange((cur, cur))
                settings.useResolution(True)
                settings.resolution((512, 512))
                sv.flipbook(sv.curViewport(), settings)
            except Exception:
                log.exception("flipbook capture failed for %s", asset_id)
                return

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
                    try:
                        cands[0].replace(out_path)
                    except Exception:
                        log.exception(
                            "Could not rename %s -> %s",
                            cands[0], out_path,
                        )

            if not out_path.exists():
                hou.ui.setStatusMessage(
                    f"Capture: nothing was written to {out_path}",
                    severity=hou.severityType.Warning,
                )
                return

            log.info("Captured thumbnail for %s -> %s", asset_id, out_path)
            hou.ui.setStatusMessage(
                f"Captured thumbnail for {asset_id}",
                severity=hou.severityType.Message,
            )
            self.request_refresh(asset_id)
        except Exception:
            log.exception("Capture thumbnail failed for %s", asset_id)

    # ── Refresh ──────────────────────────────────────────

    def request_refresh(self, asset_id: str) -> None:
        """Drop the asset's thumbnail caches and trigger a re-load.

        Walks ``QApplication.allWidgets()`` for any open
        :class:`AssetBrowserWidget`, finds the matching card in the
        grid, and asks the browser's :class:`ThumbnailLoader` to
        invalidate it. The loader's ``on_ready`` callback will repaint
        the card automatically once the new pixmap arrives.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from asset_browser.ui.browser import AssetBrowserWidget
            for w in QApplication.allWidgets():
                if not isinstance(w, AssetBrowserWidget):
                    continue
                loader = getattr(w, "_thumb_loader", None)
                if loader is None:
                    continue
                grid = getattr(w, "_grid", None)
                if grid is None:
                    continue
                for card in getattr(grid, "_cards", []):
                    a = getattr(card, "asset", None)
                    if a is not None and a.id == asset_id:
                        loader.invalidate(a)
        except Exception:
            log.debug("Thumbnail refresh failed for %s", asset_id)
        # Also re-fetch the detail panel so its preview area picks up
        # the new image immediately.
        self._request_global_detail_refresh()

    # ── Scene-Viewer lookup ──────────────────────────────

    @staticmethod
    def _find_active_scene_viewer():
        """Return the currently active ``hou.SceneViewer``, or any
        Scene Viewer if none is currently focused, or ``None``."""
        try:
            import hou
            scene_viewers = []
            for tab in hou.ui.paneTabs():
                if isinstance(tab, hou.SceneViewer):
                    scene_viewers.append(tab)
            if not scene_viewers:
                return None
            for sv in scene_viewers:
                try:
                    if sv.isCurrentTab():
                        return sv
                except Exception:
                    continue
            return scene_viewers[0]
        except Exception:
            log.debug("Failed to find SceneViewer")
            return None
