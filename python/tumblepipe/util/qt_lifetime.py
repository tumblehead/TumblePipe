"""Teardown helper for Qt panels and windows.

``release_kept_references(root)``: drop the references PySide keeps on item
views, proxy models, combo boxes, graphics views and restyled widgets under
*root*, while everything they point at is still alive. Call it when a window
with ``WA_DeleteOnClose`` closes, and on a pane before ``deleteLater``.

Why: PySide keeps a Python reference on the holder for what ``setModel``,
``setSelectionModel``, ``setItemDelegate`` / ``ForColumn``, ``setSourceModel``,
``QComboBox.setModel``, ``setScene`` and ``setStyle`` receive. When Qt deletes
the held object before the holder, PySide frees its Python object but leaves
the pointer in the holder's list; the holder later decrements freed memory,
when its wrapper is destroyed or when the cyclic collector clears it. Two
sessions died exactly there on 2026-11-16 (Shiboken::Object::clearReferences
under SbkObject_tp_clear, H22.0.466 / PySide 6.8.3). Qt deletes children in
creation order, so a model or delegate created before its view, a parentless
proxy whose source model belongs to the pane, or a frozen-column view sharing
the table's selection model is enough. TumbleTrove measures each case in its
``scripts/verify_kept_references.py``.

Making the delegate or model the view's child fixes the order, with one
condition: the child must not refer back to the window. A child that does
closes a cycle window -> view -> child -> window, the cyclic collector may
then free the view first, and the child, deleted from inside the view's
destructor, drops the last reference to the window, which deletes the
half-destroyed view again. The entity picker's card delegate did that in
1.112.0; it is now handed the lookups it paints from, not the picker
(``pipe/houdini/ui/widgets/entity_selector.py``).

TumbleTrove 0.65+ ships the same helper as
``tumbletrove.common.gui.release_kept_references``; this module uses it when
present and its own copy otherwise (only a TumbleTrove older than the 0.66
``hpm.toml`` requires would need the copy).
"""
from __future__ import annotations


def _release_kept_references(root) -> int:
    try:
        import shiboken6
        from PySide6.QtCore import QAbstractProxyModel, QItemSelectionModel, QObject, Qt
        from PySide6.QtGui import QStandardItemModel
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QComboBox,
            QGraphicsView,
            QWidget,
        )
    except ImportError:
        return 0
    if root is None or not shiboken6.isValid(root):
        return 0
    try:
        objects = [root, *root.findChildren(QObject)]
    except RuntimeError:
        return 0
    released = 0
    for obj in objects:
        if not shiboken6.isValid(obj):
            continue
        try:
            if isinstance(obj, QAbstractItemView):
                model = obj.model()
                columns = model.columnCount() if model is not None else 0
                for col in range(columns):
                    if obj.itemDelegateForColumn(col) is not None:
                        obj.setItemDelegateForColumn(col, None)
                obj.setItemDelegate(None)
                obj.setModel(None)
                own = obj.selectionModel()
                obj.setSelectionModel(own if own is not None
                                      else QItemSelectionModel(None, obj))
                released += 1
            elif isinstance(obj, QComboBox):
                obj.setModel(QStandardItemModel(obj))
                released += 1
            elif isinstance(obj, QGraphicsView):
                obj.setScene(None)
                released += 1
            if isinstance(obj, QAbstractProxyModel):
                obj.setSourceModel(None)
                released += 1
            if isinstance(obj, QWidget) and obj.testAttribute(Qt.WA_SetStyle):
                obj.setStyle(None)
                released += 1
        except (RuntimeError, TypeError):
            continue
    return released


try:
    from tumbletrove.common.gui import release_kept_references
except ImportError:
    release_kept_references = _release_kept_references
