"""Read-only view of what composed into an asset's Layer Stack row.

Opened from the ``...`` button on an ``Asset:`` row in ``th::import_shot``'s
Layer Stack. The row shows one version for a whole composed asset; this shows
the department layers behind it, and the sub-assets it brings with it.

Deliberately read-only: nothing here changes what loads. The data comes from
``tumblepipe.pipe.asset_layers``, which asks the resolver rather than
re-deriving, so what is shown is what composed.
"""

from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QGroupBox,
)

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.asset_layers import AssetLayerReport, LayerStatus


_STATUS_TEXT = {
    LayerStatus.COMPOSED: '',
    LayerStatus.STALE: 'newer export not composing',
    LayerStatus.NOT_IN_BUILD: 'exported after this build',
    LayerStatus.NEVER_EXPORTED: 'never exported',
    LayerStatus.NOT_RENDERABLE: 'not a render layer',
}

# Dim for "nothing to see", amber for "there is work here you are not
# getting". Nothing is red: none of these is an error, and an inspector that
# cries wolf gets ignored.
#
# NOT_RENDERABLE is deliberately dim, not amber — a rig that never composes
# is the pipeline working, not a problem to fix.
_STATUS_COLOUR = {
    LayerStatus.STALE: '#d9a441',
    LayerStatus.NOT_IN_BUILD: '#d9a441',
    LayerStatus.NEVER_EXPORTED: '#777777',
    LayerStatus.NOT_RENDERABLE: '#777777',
}

# Statuses where naming the version on disk answers the artist's next
# question ("which version, then?") instead of making them go look.
_SHOW_LATEST_FOR = (
    LayerStatus.STALE,
    LayerStatus.NOT_IN_BUILD,
    LayerStatus.NOT_RENDERABLE,
)


class AssetLayerDialog(QDialog):
    """What is inside one ``Asset:`` row of the Layer Stack."""

    def __init__(self, report: AssetLayerReport, parent=None) -> None:
        super().__init__(parent)
        self._report = report
        self.setWindowTitle(f"Layers — {report.name}")
        self.resize(560, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        report = self._report
        outer = QVBoxLayout(self)

        staged = report.staged_version or 'unstaged'
        mode = "pinned" if report.pinned else "following latest"
        header = QLabel(f"<b>{report.name}</b> — staged {staged} ({mode})")
        outer.addWidget(header)

        hint = QLabel(
            "The department layers composing this asset."
            if report.pinned else
            "The department layers composing this asset. The import follows "
            "latest, so every layer here is the newest export on disk — the "
            "versions recorded in the build are not what loads."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #999999;")
        outer.addWidget(hint)

        dept_box = QGroupBox("Departments")
        dept_layout = QVBoxLayout(dept_box)
        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels(["Department", "Composed", "", ""])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        for layer in report.departments:
            item = QTreeWidgetItem(tree)
            item.setText(0, layer.department)
            item.setText(1, layer.composed or '—')

            note = _STATUS_TEXT.get(layer.status, '')
            # Name the version, not just its existence: "newer export not
            # composing" prompts "which one?" every single time.
            if layer.status in _SHOW_LATEST_FOR and layer.latest:
                note = f"{note} ({layer.latest})"
            item.setText(2, note)

            if layer.variant != 'default':
                item.setToolTip(0, f"variant: {layer.variant}")

            colour = _STATUS_COLOUR.get(layer.status)
            if colour is not None:
                brush = QBrush(QColor(colour))
                for col in range(3):
                    item.setForeground(col, brush)

            # Only where there is something to open. A department with no
            # workfile gets no button rather than a button that apologises.
            if layer.has_workfile:
                tree.setItemWidget(
                    item, 3, self._open_button(layer.department)
                )
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        tree.resizeColumnToContents(2)
        dept_layout.addWidget(tree)
        outer.addWidget(dept_box)

        if report.nested:
            nested_box = QGroupBox(f"Imports {len(report.nested)} sub-assets")
            nested_layout = QVBoxLayout(nested_box)
            nested_tree = QTreeWidget()
            nested_tree.setColumnCount(2)
            nested_tree.setHeaderLabels(["Asset", "Composed"])
            nested_tree.setRootIsDecorated(False)
            nested_tree.setAlternatingRowColors(True)
            for sub in report.nested:
                sub_item = QTreeWidgetItem(nested_tree)
                sub_item.setText(0, sub.name)
                sub_item.setText(1, sub.composed or '—')
                sub_item.setToolTip(0, sub.uri)
            nested_tree.resizeColumnToContents(0)
            nested_layout.addWidget(nested_tree)
            outer.addWidget(nested_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)

    def _open_button(self, department: str) -> QPushButton:
        """An "Open ▾" for one department, offering this session or a new one.

        Asked every time rather than driven by a preference: it is a rare
        click, the right answer changes with what you have open, and a wrong
        default silently blows away the scene you are standing in.
        """
        button = QPushButton("Open")
        menu = QMenu(button)

        here = menu.addAction("Open in this session")
        here.triggered.connect(
            lambda _checked=False, d=department: self._open(d, new=False)
        )
        new = menu.addAction("Open in new Houdini session")
        new.triggered.connect(
            lambda _checked=False, d=department: self._open(d, new=True)
        )

        button.setMenu(menu)
        return button

    def _open(self, department: str, new: bool) -> None:
        # Imported here, not at module scope: this module is imported to build
        # the dialog and the workfiles module pulls in hou-only machinery.
        from tumblepipe.pipe.houdini import workfiles

        asset_uri = Uri.parse_unsafe(self._report.asset_uri)
        if new:
            workfiles.open_workfile_in_new_instance(asset_uri, department)
            return

        # Loading a scene tears down the node this dialog was opened from —
        # and with it this dialog's parent. Close first: a modeless dialog
        # outliving its owner is how the Browser's teardown crashes started
        # (designs/qt-thread-safety.md).
        self.accept()
        workfiles.open_workfile_in_session(asset_uri, department)
