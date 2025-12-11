"""Dialog for toggling column visibility in job submission tables."""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
)

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import JobTypeSchema


class ColumnVisibilityDialog(QDialog):
    """Dialog for toggling column visibility.

    Shows a checkable list of all columns from the schema.
    Users can toggle individual columns or use Show All / Hide All buttons.
    """

    def __init__(self, schema: JobTypeSchema, hidden_columns: set[str], parent=None):
        """Initialize the dialog.

        Args:
            schema: The job type schema containing column definitions
            hidden_columns: Set of column keys that are currently hidden
            parent: Parent widget
        """
        super().__init__(parent)
        self._schema = schema
        self._hidden_columns = set(hidden_columns)

        self.setWindowTitle(f"Column Visibility - {schema.display_name}")
        self.setMinimumWidth(300)
        self.setMinimumHeight(400)

        self._setup_ui()

    def _setup_ui(self):
        """Create the UI layout."""
        layout = QVBoxLayout(self)

        # Label
        label = QLabel("Select columns to display:")
        layout.addWidget(label)

        # List widget with checkable items
        self._list_widget = QListWidget()
        self._list_widget.setAlternatingRowColors(True)

        for col_def in self._schema.columns:
            item = QListWidgetItem(col_def.label)
            item.setData(Qt.UserRole, col_def.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # Check if column is visible (not in hidden set)
            is_visible = col_def.key not in self._hidden_columns
            item.setCheckState(Qt.Checked if is_visible else Qt.Unchecked)
            item.setToolTip(col_def.tooltip or col_def.key)
            self._list_widget.addItem(item)

        layout.addWidget(self._list_widget, 1)

        # Show All / Hide All buttons
        bulk_layout = QHBoxLayout()

        show_all_btn = QPushButton("Show All")
        show_all_btn.clicked.connect(self._show_all)
        bulk_layout.addWidget(show_all_btn)

        hide_all_btn = QPushButton("Hide All")
        hide_all_btn.clicked.connect(self._hide_all)
        bulk_layout.addWidget(hide_all_btn)

        bulk_layout.addStretch()
        layout.addLayout(bulk_layout)

        # OK / Cancel buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _show_all(self):
        """Check all items (show all columns)."""
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            item.setCheckState(Qt.Checked)

    def _hide_all(self):
        """Uncheck all items (hide all columns)."""
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            item.setCheckState(Qt.Unchecked)

    def get_hidden_columns(self) -> set[str]:
        """Get the set of hidden column keys.

        Returns:
            Set of column keys that should be hidden
        """
        hidden = set()
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            col_key = item.data(Qt.UserRole)
            if item.checkState() != Qt.Checked:
                hidden.add(col_key)
        return hidden

    def get_visible_columns(self) -> list[str]:
        """Get the list of visible column keys.

        Returns:
            List of column keys that should be visible
        """
        visible = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            col_key = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked:
                visible.append(col_key)
        return visible
