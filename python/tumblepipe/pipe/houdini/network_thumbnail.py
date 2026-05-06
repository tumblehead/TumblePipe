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

import logging
from pathlib import Path
from typing import Union

import hou
import nodegraphutils

log = logging.getLogger(__name__)

# 2x2 unit square anchored to the upper-left corner of the node.
# Network coordinates: positive Y is up, default node origin sits at
# (0, 0). Placing the image just above the node keeps it from
# overlapping the connectors and lets multiple stacked imports each
# carry their own preview without colliding.
_DEFAULT_RECT = hou.BoundingRect(-2.0, 1.2, 0.0, 3.2)


def _resolve_editor(editor=None):
    """Return the network editor to write background images to.

    Prefers an explicit *editor*, falls back to the desktop's first
    NetworkEditor pane tab. Returns ``None`` if neither is available
    (e.g. drop landed on a scene viewer or Houdini was launched
    headless).
    """
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
        images = [
            img for img in ed.backgroundImages()
            if img.relativeToPath() != node_path
        ]
        image = hou.NetworkImage(str(path_obj), rect or _DEFAULT_RECT)
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
        ed.setBackgroundImages(after)
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
        ed.setBackgroundImages(images)
        _persist(ed)
    except Exception:
        log.exception(
            "Failed to rename network thumbnail %s -> %s",
            old_path, new_path,
        )
