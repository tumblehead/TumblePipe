"""Attach background thumbnails to network editor nodes.

Adapted from ``lop_th.image_plane_painter.1.1``'s ``PythonModule``
``thumbnail()`` / ``OnDeleted`` / ``OnNameChanged`` scripts. Wraps
``hou.NetworkImage`` + ``setRelativeToPath`` + ``nodegraphutils``
persistence so the catalog drop hook and the import_* HDA event
scripts can call into a single place.

All entry points are defensive — they swallow exceptions through
``log.exception`` so a thumbnail failure never breaks an asset drop or
a node delete/rename.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from pathlib import Path
from typing import Union

import hou

# nodegraphutils is imported where it is used (_persist), not here: it reads
# hou.ui at import time, and the import_* HDAs' OnDeleted/OnNameChanged
# scripts import this module in hython too — a farm job tearing its scene
# down printed one AttributeError traceback per import node.

log = logging.getLogger(__name__)

# Thumbnail rect ratios — expressed as multiples of the node's width.
# Hand-tuned against the asymmetric ``import_asset`` shape so the image
# reads as visually centered above the node (math-centering on the bbox
# looks off-center because the shape isn't symmetric within its bbox).
_RECT_X_MIN_RATIO = -0.319
_RECT_X_MAX_RATIO = 1.184
_RECT_Y_MIN = 0.005

# The network editor reloads every background image in the network, on the
# GUI thread, whenever the image list changes. A pipeline thumbnail is a
# 2048x2048 PNG on the project share, and each one took 300-400 ms to reload
# (Houdini 22.0.368, measured 2026-10): with five assets imported, every
# drop, delete or rename froze Houdini for 1-1.5 s, and the freeze grew
# with each asset. The image is drawn about a node's width across, so the
# editor gets a small copy instead.
NETWORK_IMAGE_MAX_SIDE = 256

# The sidecar every pipeline asset/shot keeps (asset_browser.thumbnails).
_PIPELINE_THUMBNAIL = "thumbnail.png"


def network_image_path(image_path: Path) -> Path:
    """Where the network editor's small copy of *image_path* lives.

    Beside the image, on the same share, so a hip that saves the path opens
    with its thumbnails on any machine. Named ``network_<stem>.png`` rather
    than ``<stem>.network.png``: the thumbnail capture globs
    ``thumbnail*.png`` for the flipbook's output and would take the copy
    for it.
    """
    return image_path.with_name(f"network_{image_path.stem}.png")


def _copy_is_current(image_path: Path, copy_path: Path) -> bool:
    """The copy exists and is no older than the image it was made from.

    A thumbnail replaced from the browser (select/capture) is newer than
    its copy, so the next attach makes the copy again.
    """
    try:
        return copy_path.stat().st_mtime >= image_path.stat().st_mtime
    except OSError:
        return False


def network_image(image_path: Path) -> Path:
    """The file the network editor should show for *image_path*.

    The image itself when it is already small or is a copy; otherwise its
    copy, written first when it is missing or stale. Falls back to the
    image when the copy cannot be written, so a read-only share costs
    speed, never the thumbnail.
    """
    if image_path.name.startswith("network_"):
        return image_path
    copy_path = network_image_path(image_path)
    if _copy_is_current(image_path, copy_path):
        return copy_path

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage(str(image_path))
    if image.isNull() or max(image.width(), image.height()) <= NETWORK_IMAGE_MAX_SIDE:
        return image_path
    small = image.scaled(
        NETWORK_IMAGE_MAX_SIDE, NETWORK_IMAGE_MAX_SIDE,
        Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    # Written under a unique name and swapped in, so two artists dropping
    # the same asset at once never read a half-written PNG.
    tmp_path = copy_path.with_name(f".{copy_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        if not small.save(str(tmp_path), "PNG"):
            raise OSError(f"could not write {tmp_path}")
        os.replace(tmp_path, copy_path)
    except OSError:
        log.warning(
            "network_thumbnail: no small copy of %s; showing it full size",
            image_path, exc_info=True,
        )
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        return copy_path if copy_path.exists() else image_path
    return copy_path


def _shrink_pipeline_thumbnails(images: list) -> list:
    """Point each node's full-size pipeline thumbnail at its small copy.

    Hips saved before the copies existed still name ``thumbnail.png``, and
    one of those in the network is enough to make every later change to the
    list slow. Only pipeline thumbnails tied to a node are touched; any
    other background image is the artist's own.
    """
    for image in images:
        path = Path(image.path())
        if path.name != _PIPELINE_THUMBNAIL or not image.relativeToPath():
            continue
        small = network_image(path)
        if small != path:
            image.setPath(str(small))
    return images


def _default_rect_for_node(node) -> "hou.BoundingRect":
    """Square thumbnail sitting just above the node, sized and offset
    by hand-tuned ratios of the node's width.

    ``hou.NetworkImage.setRelativeToPath`` offsets coords by the node's
    position; on the ``import_asset`` shape that anchors the relative
    rect such that y=0 corresponds to the node's top edge.
    """
    w, _h = node.size()
    img_w = (_RECT_X_MAX_RATIO - _RECT_X_MIN_RATIO) * w
    return hou.BoundingRect(
        _RECT_X_MIN_RATIO * w,
        _RECT_Y_MIN,
        _RECT_X_MAX_RATIO * w,
        _RECT_Y_MIN + img_w,
    )


def _resolve_editor(editor=None):
    """Return the network editor to write background images to.

    Prefers an explicit *editor*, falls back to the desktop's first
    NetworkEditor pane tab. Returns ``None`` if neither is available
    (e.g. drop landed on a scene viewer or Houdini was launched
    headless).
    """
    if not hou.isUIAvailable():
        return None  # hython / farm: there is no network editor to draw in
    if editor is not None and isinstance(editor, hou.NetworkEditor):
        return editor
    desktop = hou.ui.curDesktop()
    if desktop is None:
        return None
    pane = desktop.paneTabOfType(hou.paneTabType.NetworkEditor)
    if pane is None or not isinstance(pane, hou.NetworkEditor):
        return None
    return pane


def _persist(editor) -> None:
    """Save + reload the editor's current parent's background images
    so the change survives hip save/reopen."""
    import nodegraphutils

    parent = editor.pwd()
    if parent is None:
        return
    nodegraphutils.saveBackgroundImages(parent, editor.backgroundImages())
    nodegraphutils.loadBackgroundImages(parent)


def attach(
    node: "hou.Node",
    image_path: Union[str, Path],
    *,
    editor=None,
    rect=None,
) -> None:
    """Attach *image_path* as a NetworkImage tied to *node*.

    Replaces any image already relative to ``node.path()`` so repeated
    drops of the same node don't stack copies. No-op if there's no
    network editor available or the image file doesn't exist.
    """
    try:
        path_obj = Path(image_path)
        if not path_obj.exists():
            log.debug("network_thumbnail.attach: missing file %s", path_obj)
            return
        ed = _resolve_editor(editor)
        if ed is None:
            log.debug(
                "network_thumbnail.attach: no NetworkEditor available "
                "(editor=%s)", editor,
            )
            return

        # When the editor's pwd is not the node's parent (e.g. user
        # dropped from a different pane than the active one), the
        # NetworkImage would render in the wrong network. Re-target the
        # editor to the node's parent so the image actually shows up.
        try:
            node_parent = node.parent()
            if node_parent is not None and ed.pwd() != node_parent:
                log.debug(
                    "network_thumbnail.attach: editor.pwd()=%s != "
                    "node.parent()=%s — repointing editor",
                    ed.pwd(), node_parent,
                )
                ed.setPwd(node_parent)
        except Exception:
            log.debug(
                "network_thumbnail.attach: failed to verify editor pwd",
                exc_info=True,
            )

        node_path = node.path()
        images = _shrink_pipeline_thumbnails([
            img for img in ed.backgroundImages()
            if img.relativeToPath() != node_path
        ])
        resolved_rect = rect if rect is not None else _default_rect_for_node(node)
        image = hou.NetworkImage(str(network_image(path_obj)), resolved_rect)
        image.setRelativeToPath(node_path)
        images.append(image)

        ed.setBackgroundImages(images)
        _persist(ed)
        log.debug(
            "network_thumbnail.attach: attached %s to %s "
            "(image count now %d)",
            path_obj, node_path, len(images),
        )
    except Exception:
        log.exception(
            "Failed to attach network thumbnail %s to %s",
            image_path, getattr(node, "path", lambda: "?")(),
        )


def detach(node_path: str, *, editor=None) -> None:
    """Remove any background image attached to *node_path*.

    Use from an HDA ``OnDeleted`` callback:
    ``network_thumbnail.detach(kwargs['node'].path())``.
    """
    try:
        ed = _resolve_editor(editor)
        if ed is None:
            return
        before = ed.backgroundImages()
        after = [img for img in before if img.relativeToPath() != node_path]
        if len(after) == len(before):
            return
        ed.setBackgroundImages(_shrink_pipeline_thumbnails(after))
        _persist(ed)
    except Exception:
        log.exception("Failed to detach network thumbnail for %s", node_path)


def rename(old_path: str, new_path: str, *, editor=None) -> None:
    """Re-bind any image attached to *old_path* so it follows *new_path*.

    Use from an HDA ``OnNameChanged`` callback. Houdini's
    ``hou.NetworkImage`` is keyed by string path, so a node rename
    leaves orphaned images unless we rewrite the relative-to path.
    """
    try:
        if old_path == new_path:
            return
        ed = _resolve_editor(editor)
        if ed is None:
            return
        images = list(ed.backgroundImages())
        changed = False
        for img in images:
            if img.relativeToPath() == old_path:
                img.setRelativeToPath(new_path)
                changed = True
        if not changed:
            return
        ed.setBackgroundImages(_shrink_pipeline_thumbnails(images))
        _persist(ed)
    except Exception:
        log.exception(
            "Failed to rename network thumbnail %s -> %s",
            old_path, new_path,
        )
