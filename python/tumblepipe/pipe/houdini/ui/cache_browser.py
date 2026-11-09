"""Pick an existing cache for a ``th::cache`` node.

Opened from the browse button next to the node's Name. Lists every cache the
node's entity has on disk, in every department and both locations, so an
artist loading the model department's cache into a rig workfile picks it
from a list instead of having to know (and type) where it was written.

The rows come from ``CacheBase.browse_caches``; picking one sets the node's
Department, Location, Name and its latest finished Version.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

_COLUMNS = ('Name', 'Department', 'Location', 'Latest', 'Versions', 'By', 'When')


def _when(timestamp: str | None) -> str:
    if not timestamp: return ''
    # ISO 'YYYY-MM-DDTHH:MM:SS.ffffff' -> 'YYYY-MM-DD HH:MM'
    return timestamp.replace('T', ' ')[:16]


class CacheBrowserDialog(QDialog):
    def __init__(self, listings, entity_label, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Browse Caches')
        self.resize(760, 420)
        self._listings = list(listings)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Caches of {entity_label}'))

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('Filter by name or department')
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(list(_COLUMNS))
        self._tree.setRootIsDecorated(False)
        self._tree.setSortingEnabled(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(self._tree)

        for index, listing in enumerate(self._listings):
            item = QTreeWidgetItem([
                listing.name,
                listing.department,
                listing.location.capitalize(),
                listing.latest or '',
                str(len(listing.versions)),
                listing.user or '',
                _when(listing.timestamp),
            ])
            item.setData(0, Qt.UserRole, index)
            self._tree.addTopLevelItem(item)
            if current == (listing.department, listing.location, listing.name):
                self._tree.setCurrentItem(item)
        self._tree.sortByColumn(0, Qt.AscendingOrder)
        for column in range(len(_COLUMNS)):
            self._tree.resizeColumnToContents(column)

        self._empty = QLabel(
            'No caches on disk for this entity yet, in any department.'
        )
        self._empty.setVisible(not self._listings)
        layout.addWidget(self._empty)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText('Use Cache')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._tree.currentItemChanged.connect(lambda *_: self._update_ok())
        self._update_ok()
        self._filter.setFocus()

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for row in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(row)
            haystack = f'{item.text(0)} {item.text(1)}'.lower()
            item.setHidden(bool(needle) and needle not in haystack)
        # Keep a visible row selected, so typing a filter then Enter picks it
        current = self._tree.currentItem()
        if current is None or current.isHidden():
            visible = [
                self._tree.topLevelItem(row)
                for row in range(self._tree.topLevelItemCount())
                if not self._tree.topLevelItem(row).isHidden()
            ]
            self._tree.setCurrentItem(visible[0] if visible else None)
        self._update_ok()

    def _update_ok(self):
        item = self._tree.currentItem()
        self._ok.setEnabled(item is not None and not item.isHidden())

    def selected_listing(self):
        item = self._tree.currentItem()
        if item is None or item.isHidden(): return None
        return self._listings[item.data(0, Qt.UserRole)]
