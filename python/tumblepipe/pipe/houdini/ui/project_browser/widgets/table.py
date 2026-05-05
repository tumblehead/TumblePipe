from qtpy.QtCore import Qt, QModelIndex, Signal, QItemSelectionModel, QRect
from qtpy.QtGui import QColor, QPen, QBrush, QFont
from qtpy.QtWidgets import QTableView, QStyledItemDelegate, QStyle, QAbstractItemView

from ..models.department import DepartmentTableModel

# Extension badge colors - distinguish license types visually
EXTENSION_COLORS = {
    'hip': '#4a8a4a',    # Green - commercial
    'hiplc': '#8a6a4a',  # Orange - Indie
    'hipnc': '#6a4a8a',  # Purple - Non-commercial
}


class CellSelectionTableView(QTableView):
    """QTableView with cell-level selection and hover tracking.

    Features:
    - Single-click selects, double-click edits
    - Selection restricted to same column only
    - Shift+click for range selection within column
    - Ctrl+click to toggle individual cells within column
    """

    cell_hover_changed = Signal(QModelIndex)
    selection_column_changed = Signal(int)  # Emitted when selection moves to different column

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._hover_index = QModelIndex()  # Invalid by default
        self._selection_column = -1  # Track which column has selection

        # Cell-based selection
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Double-click to edit (not single-click)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)

    def mouseMoveEvent(self, event):
        """Track which cell is being hovered."""
        index = self.indexAt(event.pos())
        if index.isValid():
            if index != self._hover_index:
                old_index = self._hover_index
                self._hover_index = index

                # Update old hover cell
                if old_index.isValid():
                    self.update(old_index)

                # Update new hover cell
                self.update(index)
                self.cell_hover_changed.emit(index)
        else:
            self._clear_hover()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """Clear hover when mouse leaves the view."""
        self._clear_hover()
        super().leaveEvent(event)

    def _clear_hover(self):
        """Clear the hover state."""
        if self._hover_index.isValid():
            old_index = self._hover_index
            self._hover_index = QModelIndex()
            self.update(old_index)
            self.cell_hover_changed.emit(QModelIndex())

    def get_hover_index(self):
        """Get the currently hovered cell index."""
        return self._hover_index

    def get_hover_row(self):
        """Compatibility method - returns hover row or -1."""
        if self._hover_index.isValid():
            return self._hover_index.row()
        return -1

    def mousePressEvent(self, event):
        """Handle mouse press with same-column selection enforcement."""
        index = self.indexAt(event.pos())
        if not index.isValid():
            return super().mousePressEvent(event)

        # Skip entity column (column 0) for selection
        if index.column() == 0:
            return super().mousePressEvent(event)

        current_selection = self.selectionModel().selectedIndexes()
        modifiers = event.modifiers()

        # If we have existing selection and clicking a different column
        if current_selection:
            existing_col = current_selection[0].column()
            if index.column() != existing_col:
                # Without Ctrl, clear selection and start fresh in new column
                if not (modifiers & Qt.ControlModifier):
                    self.clearSelection()
                    self._selection_column = index.column()
                    self.selection_column_changed.emit(self._selection_column)
                else:
                    # With Ctrl on different column, ignore click for selection purposes
                    # but still allow focus to move
                    self.setCurrentIndex(index)
                    return
        else:
            # No existing selection, set the column
            self._selection_column = index.column()
            self.selection_column_changed.emit(self._selection_column)

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Start editing while preserving multi-cell selection."""
        index = self.indexAt(event.pos())
        if not index.isValid() or index.column() == 0:
            return super().mouseDoubleClickEvent(event)

        # Save current selection before edit (Qt clears it by default)
        saved_selection = list(self.selectionModel().selectedIndexes())

        # Start editing
        self.edit(index)

        # Restore selection
        for idx in saved_selection:
            self.selectionModel().select(idx, QItemSelectionModel.Select)

    def keyPressEvent(self, event):
        """Handle keyboard navigation."""
        current = self.currentIndex()

        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Start editing current cell (if not entity column)
            if current.isValid() and current.column() > 0:
                # Save selection before edit
                saved_selection = list(self.selectionModel().selectedIndexes())
                self.edit(current)
                # Restore selection
                for idx in saved_selection:
                    self.selectionModel().select(idx, QItemSelectionModel.Select)
                return

        elif event.key() == Qt.Key_Tab:
            # Move to next editable cell
            if current.isValid():
                next_col = current.column() + 1
                if next_col < self.model().columnCount():
                    next_index = self.model().index(current.row(), next_col)
                    self.setCurrentIndex(next_index)
                    self.clearSelection()
                    self.selectionModel().select(next_index, QItemSelectionModel.Select)
                    self._selection_column = next_col
                    return

        elif event.key() == Qt.Key_Backtab:
            # Move to previous editable cell
            if current.isValid():
                prev_col = current.column() - 1
                if prev_col > 0:  # Skip entity column
                    prev_index = self.model().index(current.row(), prev_col)
                    self.setCurrentIndex(prev_index)
                    self.clearSelection()
                    self.selectionModel().select(prev_index, QItemSelectionModel.Select)
                    self._selection_column = prev_col
                    return

        super().keyPressEvent(event)

    def get_selection_column(self):
        """Get the column index of the current selection."""
        return self._selection_column

    def get_selected_cells_in_column(self):
        """Get list of (row, col) tuples for selected cells in the current selection column."""
        selected = self.selectionModel().selectedIndexes()
        if not selected:
            return []

        # Filter to only cells in the selection column
        col = self._selection_column
        return [(idx.row(), idx.column()) for idx in selected if idx.column() == col]


class RowHoverTableView(QTableView):
    """QTableView that tracks and highlights entire rows on hover"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)  # Track mouse without clicking
        self._hover_row = -1

    def mouseMoveEvent(self, event):
        """Track which row is being hovered"""
        index = self.indexAt(event.pos())
        if index.isValid():
            new_hover_row = index.row()
            if new_hover_row != self._hover_row:
                # Clear old hover row
                if self._hover_row >= 0 and self.model():
                    for col in range(self.model().columnCount()):
                        old_index = self.model().index(self._hover_row, col)
                        if old_index.isValid():
                            self.update(old_index)

                # Set new hover row
                self._hover_row = new_hover_row
                if self.model():
                    for col in range(self.model().columnCount()):
                        new_index = self.model().index(self._hover_row, col)
                        if new_index.isValid():
                            self.update(new_index)
        else:
            self._clear_hover()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """Clear hover when mouse leaves the view"""
        self._clear_hover()
        super().leaveEvent(event)

    def _clear_hover(self):
        """Clear the hover state"""
        if self._hover_row >= 0 and self.model():
            for col in range(self.model().columnCount()):
                old_index = self.model().index(self._hover_row, col)
                if old_index.isValid():
                    self.update(old_index)
            self._hover_row = -1

    def get_hover_row(self):
        """Get the currently hovered row"""
        return self._hover_row


class DepartmentItemDelegate(QStyledItemDelegate):
    """Custom delegate for department table to handle row-level hover and overwritten state styling"""

    def paint(self, painter, option, index):
        """Custom paint for unified row-level styling"""
        painter.save()

        # Get the table view to check hover state
        table_view = self.parent()
        is_hover_row = False
        if hasattr(table_view, "get_hover_row"):
            hover_row = table_view.get_hover_row()
            is_hover_row = hover_row == index.row()

        # Check if this row is overwritten
        is_overwritten = index.model().data(index, Qt.UserRole + 1)

        # Find DepartmentBrowser to check selection state
        is_selected = False
        department_browser = table_view
        while department_browser:
            if hasattr(department_browser, '_selected_row') and hasattr(department_browser, '_selection'):
                selected_row = getattr(department_browser, "_selected_row", -1)
                is_selected = selected_row >= 0 and selected_row == index.row()
                break
            department_browser = department_browser.parent()

        # Determine background color
        background_color = QColor("#3a3a3a")  # Default light gray background
        text_color = Qt.white  # White text for better contrast on gray background

        if is_selected:
            if is_overwritten:
                background_color = QColor("#b01c3c")  # Red for overwritten
            else:
                background_color = QColor("#5e4a8a")  # Purple for confirmed selection
            text_color = Qt.white
        elif is_hover_row:
            background_color = QColor("#404040")  # Subtle hover - slightly brighter gray
            text_color = Qt.white

        # Fill background
        painter.fillRect(option.rect, background_color)

        # Draw row borders (only on edges, not between cells)
        painter.setPen(QPen(QColor("black"), 1))
        rect = option.rect

        # Always draw bottom border to separate rows
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Draw left border for first column only
        if index.column() == 0:
            painter.drawLine(rect.topLeft(), rect.bottomLeft())

        # Draw right border for last column only
        if index.column() == index.model().columnCount() - 1:
            painter.drawLine(rect.topRight(), rect.bottomRight())

        # Draw the text content
        painter.setPen(text_color)
        text = index.data(Qt.DisplayRole)

        # Check if this is VERSION column with extension badge
        extension = index.data(Qt.UserRole + 2)
        has_badge = extension and index.column() == DepartmentTableModel.COLUMN_VERSION

        # Special rendering for group column - draw pill badge
        if index.column() == DepartmentTableModel.COLUMN_GROUP and text:
            self._draw_group_badge(painter, rect, str(text))
        elif text:
            # Adjust text_rect to leave space for badge on the left if present
            if has_badge:
                text_rect = rect.adjusted(35, 0, -5, 0)  # Leave space for badge on left
            else:
                text_rect = rect.adjusted(5, 0, -5, 0)  # Normal padding
            alignment = index.data(Qt.TextAlignmentRole)
            if alignment is None:
                alignment = Qt.AlignLeft | Qt.AlignVCenter

            # Handle font for department column
            font = index.data(Qt.FontRole)
            if font:
                painter.setFont(font)

            # Handle text color for timestamps and v0000
            text_color_role = index.data(Qt.ForegroundRole)
            if text_color_role and not (is_selected or is_hover_row):
                painter.setPen(text_color_role.color())

            painter.drawText(text_rect, alignment, str(text))

        # Draw extension badge on version column
        if has_badge:
            self._draw_extension_badge(painter, rect, extension)

        painter.restore()

    def _draw_group_badge(self, painter, rect, text):
        """Draw a pill-shaped badge with group name"""
        # Calculate badge size based on text
        font_metrics = painter.fontMetrics()
        text_width = font_metrics.horizontalAdvance(text)
        badge_width = text_width + 12  # padding
        badge_height = 18

        # Center badge in cell
        badge_x = rect.x() + (rect.width() - badge_width) // 2
        badge_y = rect.y() + (rect.height() - badge_height) // 2
        badge_rect = QRect(badge_x, badge_y, badge_width, badge_height)

        # Draw pill background
        painter.setBrush(QBrush(QColor("#4a6fa5")))  # Blue-ish color
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_rect, badge_height // 2, badge_height // 2)

        # Draw text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge_rect, Qt.AlignCenter, text)

    def _draw_extension_badge(self, painter, rect, extension):
        """Draw a small pill-shaped badge showing file extension."""
        if not extension:
            return

        ext = extension.lstrip('.').lower()
        color = EXTENSION_COLORS.get(ext, '#5a5a5a')

        # Use smaller font for compact badge
        original_font = painter.font()
        small_font = QFont(original_font)
        small_font.setPointSize(7)
        painter.setFont(small_font)

        text = ext.upper()  # "HIP", "HIPLC", "HIPNC"
        font_metrics = painter.fontMetrics()
        text_width = font_metrics.horizontalAdvance(text)
        badge_width = text_width + 8
        badge_height = 14

        # Position badge at left side of cell to avoid overlapping version text
        badge_x = rect.x() + 5
        badge_y = rect.y() + (rect.height() - badge_height) // 2
        badge_rect = QRect(badge_x, badge_y, badge_width, badge_height)

        # Draw pill background
        painter.setBrush(QBrush(QColor(color)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_rect, badge_height // 2, badge_height // 2)

        # Draw text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge_rect, Qt.AlignCenter, text)

        # Restore original font
        painter.setFont(original_font)


class VersionItemDelegate(QStyledItemDelegate):
    """Custom delegate for version table to handle row-level hover styling"""

    def paint(self, painter, option, index):
        """Custom paint for row-level hover and selection states"""
        painter.save()

        # Get the table view to check hover state
        table_view = self.parent()
        is_hover_row = False
        if hasattr(table_view, "get_hover_row"):
            hover_row = table_view.get_hover_row()
            is_hover_row = hover_row == index.row()

        is_selected = option.state & QStyle.State_Selected

        # Determine background color
        background_color = QColor("#3a3a3a")  # Default light gray background
        text_color = Qt.white  # White text for better contrast on gray background

        if is_selected:
            background_color = QColor("#5e4a8a")  # Purple for selected
            text_color = Qt.white
        elif is_hover_row:
            background_color = QColor("#5a5a5a")  # Brighter gray for hover
            text_color = Qt.white

        # Fill background
        painter.fillRect(option.rect, background_color)

        # Draw row borders (only on edges)
        painter.setPen(QPen(QColor("black"), 1))
        rect = option.rect

        # Always draw bottom border
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Draw left border for first column
        if index.column() == 0:
            painter.drawLine(rect.topLeft(), rect.bottomLeft())

        # Draw right border for last column
        if index.column() == index.model().columnCount() - 1:
            painter.drawLine(rect.topRight(), rect.bottomRight())

        # Draw the text content
        painter.setPen(text_color)
        text = index.data(Qt.DisplayRole)
        if text:
            text_rect = rect.adjusted(5, 0, -5, 0)  # Add padding
            alignment = index.data(Qt.TextAlignmentRole)
            if alignment is None:
                alignment = Qt.AlignLeft | Qt.AlignVCenter

            # Handle font
            font = index.data(Qt.FontRole)
            if font:
                painter.setFont(font)

            # Handle text color for timestamps and v0000 (only when not selected/hovered)
            text_color_role = index.data(Qt.ForegroundRole)
            if text_color_role and not (is_selected or is_hover_row):
                painter.setPen(text_color_role.color())

            painter.drawText(text_rect, alignment, str(text))

        # Draw extension badge if available (on version column only)
        extension = index.data(Qt.UserRole + 2)
        if extension and index.column() == 0:
            self._draw_extension_badge(painter, rect, extension)

        painter.restore()

    def _draw_extension_badge(self, painter, rect, extension):
        """Draw a small pill-shaped badge showing file extension."""
        if not extension:
            return

        ext = extension.lstrip('.').lower()
        color = EXTENSION_COLORS.get(ext, '#5a5a5a')

        # Use smaller font for compact badge
        original_font = painter.font()
        small_font = QFont(original_font)
        small_font.setPointSize(7)
        painter.setFont(small_font)

        text = ext.upper()  # "HIP", "HIPLC", "HIPNC"
        font_metrics = painter.fontMetrics()
        text_width = font_metrics.horizontalAdvance(text)
        badge_width = text_width + 8
        badge_height = 14

        # Position badge at right side of cell
        badge_x = rect.right() - badge_width - 5
        badge_y = rect.y() + (rect.height() - badge_height) // 2
        badge_rect = QRect(badge_x, badge_y, badge_width, badge_height)

        # Draw pill background
        painter.setBrush(QBrush(QColor(color)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(badge_rect, badge_height // 2, badge_height // 2)

        # Draw text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge_rect, Qt.AlignCenter, text)

        # Restore original font
        painter.setFont(original_font)