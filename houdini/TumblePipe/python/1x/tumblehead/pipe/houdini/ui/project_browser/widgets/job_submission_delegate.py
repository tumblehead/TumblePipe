"""Custom delegate for job submission table with multiple editor types."""

from qtpy.QtCore import Qt, Signal, QObject
from qtpy.QtGui import QColor, QPen
from qtpy.QtWidgets import (
    QStyledItemDelegate,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QStyle,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QDialog,
)

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import ColumnType
from tumblehead.pipe.houdini.ui.project_browser.models.job_submission_table import JobSubmissionTableModel


class MultiSelectDialog(QDialog):
    """Dialog for selecting multiple items from a list."""

    def __init__(self, title: str, choices: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(200)
        self.setMinimumHeight(150)

        self._selected = list(selected)

        layout = QVBoxLayout()
        self.setLayout(layout)

        # List widget with checkable items
        self._list_widget = QListWidget()
        for choice in choices:
            item = QListWidgetItem(choice)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if choice in selected else Qt.Unchecked)
            self._list_widget.addItem(item)
        layout.addWidget(self._list_widget)

        # Buttons
        button_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_all)
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(clear_btn)
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def _select_all(self):
        """Select all items."""
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.Checked)

    def _clear_all(self):
        """Clear all selections."""
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.Unchecked)

    def get_selected(self) -> list[str]:
        """Get list of selected items."""
        selected = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.text())
        return selected


class MultiSelectEditor(QWidget):
    """Editor widget for multi-select columns that opens a dialog."""

    editingFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._choices = []
        self._selected = []

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.setLayout(layout)

        # Display label
        self._label = QLineEdit()
        self._label.setReadOnly(True)
        layout.addWidget(self._label)

        # Edit button
        self._button = QPushButton("...")
        self._button.setFixedWidth(24)
        self._button.clicked.connect(self._open_dialog)
        layout.addWidget(self._button)

    def set_choices(self, choices: list[str]):
        """Set available choices."""
        self._choices = choices

    def set_selected(self, selected: list[str]):
        """Set currently selected items."""
        self._selected = list(selected) if selected else []
        self._update_label()

    def get_selected(self) -> list[str]:
        """Get currently selected items."""
        return self._selected

    def _update_label(self):
        """Update display label."""
        if self._selected:
            self._label.setText(", ".join(self._selected))
        else:
            self._label.setText("(none)")

    def _open_dialog(self):
        """Open multi-select dialog."""
        dialog = MultiSelectDialog(
            "Select Variants",
            self._choices,
            self._selected,
            self
        )
        if dialog.exec_() == QDialog.Accepted:
            self._selected = dialog.get_selected()
            self._update_label()
        self.editingFinished.emit()


class JobSubmissionDelegate(QStyledItemDelegate):
    """
    Delegate that creates appropriate editors based on column type.
    Handles visual styling for override state and validation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def createEditor(self, parent, option, index):
        """Create appropriate editor widget based on column type."""
        col_type = index.data(JobSubmissionTableModel.ROLE_COLUMN_TYPE)
        col_def = index.data(JobSubmissionTableModel.ROLE_COLUMN_DEF)

        if col_type is None:
            return super().createEditor(parent, option, index)

        if col_type == ColumnType.INTEGER:
            editor = QSpinBox(parent)
            # Set sensible default max (QSpinBox defaults to 99 which is too low for frame numbers)
            editor.setMaximum(999999)
            if col_def:
                if col_def.min_value is not None:
                    editor.setMinimum(int(col_def.min_value))
                if col_def.max_value is not None:
                    editor.setMaximum(int(col_def.max_value))
                if col_def.step is not None:
                    editor.setSingleStep(int(col_def.step))
            return editor

        elif col_type == ColumnType.FLOAT:
            editor = QDoubleSpinBox(parent)
            editor.setDecimals(2)
            # Set sensible default max (QDoubleSpinBox defaults to 99.99 which is too low)
            editor.setMaximum(999999.0)
            if col_def:
                if col_def.min_value is not None:
                    editor.setMinimum(col_def.min_value)
                if col_def.max_value is not None:
                    editor.setMaximum(col_def.max_value)
                if col_def.step is not None:
                    editor.setSingleStep(col_def.step)
            return editor

        elif col_type == ColumnType.COMBO:
            editor = QComboBox(parent)
            if col_def:
                choices = col_def.get_choices()
                if choices:
                    editor.addItems(choices)
            return editor

        elif col_type == ColumnType.BOOLEAN:
            # Use a checkbox centered in the cell
            editor = QCheckBox(parent)
            return editor

        elif col_type == ColumnType.STRING:
            editor = QLineEdit(parent)
            return editor

        elif col_type == ColumnType.MULTI_SELECT:
            editor = MultiSelectEditor(parent)
            # Get choices from the model (per-entity)
            choices = index.data(JobSubmissionTableModel.ROLE_ENTITY_CHOICES)
            if choices:
                editor.set_choices(choices)
            editor.editingFinished.connect(lambda: self.commitData.emit(editor))
            editor.editingFinished.connect(lambda: self.closeEditor.emit(editor))
            return editor

        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        """Populate editor with current value."""
        value = index.data(JobSubmissionTableModel.ROLE_RAW_VALUE)
        col_type = index.data(JobSubmissionTableModel.ROLE_COLUMN_TYPE)

        if col_type == ColumnType.INTEGER:
            editor.setValue(int(value) if value is not None else 0)
        elif col_type == ColumnType.FLOAT:
            editor.setValue(float(value) if value is not None else 0.0)
        elif col_type == ColumnType.COMBO:
            idx = editor.findText(str(value))
            if idx >= 0:
                editor.setCurrentIndex(idx)
        elif col_type == ColumnType.BOOLEAN:
            editor.setChecked(bool(value))
        elif col_type == ColumnType.STRING:
            editor.setText(str(value) if value is not None else '')
        elif col_type == ColumnType.MULTI_SELECT:
            selected = value if isinstance(value, list) else []
            editor.set_selected(selected)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        """Transfer editor value back to model.

        If multiple cells are selected in the same column, applies the
        value to all selected cells.
        """
        col_type = index.data(JobSubmissionTableModel.ROLE_COLUMN_TYPE)

        # Get the value from the editor
        if col_type == ColumnType.INTEGER:
            value = editor.value()
        elif col_type == ColumnType.FLOAT:
            value = editor.value()
        elif col_type == ColumnType.COMBO:
            value = editor.currentText()
        elif col_type == ColumnType.BOOLEAN:
            value = editor.isChecked()
        elif col_type == ColumnType.STRING:
            value = editor.text()
        elif col_type == ColumnType.MULTI_SELECT:
            value = editor.get_selected()
        else:
            super().setModelData(editor, model, index)
            return

        # Check if multiple cells are selected in the same column
        table_view = self.parent()
        if hasattr(table_view, 'get_selected_cells_in_column'):
            selected_cells = table_view.get_selected_cells_in_column()

            # If multiple cells selected in same column, apply to all
            if len(selected_cells) > 1:
                # Get column key from the edited index
                col_def = index.data(JobSubmissionTableModel.ROLE_COLUMN_DEF)
                if col_def:
                    col_key = col_def.key
                    row_indices = [row for row, col in selected_cells]
                    model.apply_to_selected(row_indices, col_key, value)
                    return

        # Single cell edit - use standard setData
        model.setData(index, value)

    def paint(self, painter, option, index):
        """Custom paint with override and validation styling."""
        painter.save()

        # Get cell state
        is_overridden = index.data(JobSubmissionTableModel.ROLE_IS_OVERRIDDEN)
        validation_error = index.data(JobSubmissionTableModel.ROLE_VALIDATION_ERROR)
        is_selected = option.state & QStyle.State_Selected

        # Get hover state from table view (cell-level preferred, row-level fallback)
        table_view = self.parent()
        is_hover_cell = False
        if hasattr(table_view, "get_hover_index"):
            # Cell-level hover (CellSelectionTableView)
            hover_index = table_view.get_hover_index()
            is_hover_cell = (hover_index.isValid() and
                            hover_index.row() == index.row() and
                            hover_index.column() == index.column())
        elif hasattr(table_view, "get_hover_row"):
            # Row-level hover fallback (RowHoverTableView)
            hover_row = table_view.get_hover_row()
            is_hover_cell = hover_row == index.row()

        # Determine background color
        if validation_error:
            bg_color = QColor("#5c1a1a")  # Dark red for validation error
        elif is_selected:
            bg_color = QColor("#5e4a8a")  # Purple for selected
        elif is_hover_cell:
            bg_color = QColor("#4a4a4a")  # Slightly brighter for hover
        elif is_overridden:
            bg_color = QColor("#1a3d1a")  # Dark green tint for overridden
        else:
            bg_color = QColor("#3a3a3a")  # Default gray

        # Fill background
        painter.fillRect(option.rect, bg_color)

        # Draw borders
        rect = option.rect
        painter.setPen(QPen(QColor("#2a2a2a"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Draw selection border for selected cells
        if is_selected:
            painter.setPen(QPen(QColor("#7e6aaa"), 2))  # Lighter purple border
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

        # Determine text color
        if validation_error:
            text_color = QColor("#ff6b6b")  # Light red for errors
        elif is_selected or is_hover_cell:
            text_color = QColor("#ffffff")  # White for selected/hovered
        elif is_overridden:
            text_color = QColor("#ffffff")  # White for overridden
        else:
            text_color = QColor("#888888")  # Dimmed for defaults

        # Draw text
        painter.setPen(text_color)
        text = index.data(Qt.DisplayRole)
        if text:
            text_rect = rect.adjusted(5, 0, -5, 0)

            # Bold for overridden values
            font = painter.font()
            if is_overridden:
                font.setBold(True)
            painter.setFont(font)

            # Handle boolean column specially (center the text)
            col_type = index.data(JobSubmissionTableModel.ROLE_COLUMN_TYPE)
            if col_type == ColumnType.BOOLEAN:
                painter.drawText(text_rect, Qt.AlignCenter, str(text))
            else:
                painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, str(text))

        painter.restore()

    def updateEditorGeometry(self, editor, option, index):
        """Position editor within cell."""
        editor.setGeometry(option.rect)

    def sizeHint(self, option, index):
        """Return size hint for the cell."""
        hint = super().sizeHint(option, index)
        # Ensure minimum height for comfortable editing
        if hint.height() < 24:
            hint.setHeight(24)
        return hint
