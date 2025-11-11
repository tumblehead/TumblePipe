from qtpy.QtCore import Qt
from qtpy.QtGui import QColor, QPen, QFont, QBrush
from qtpy.QtWidgets import QTableView, QStyledItemDelegate, QStyle


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
        if text:
            text_rect = rect.adjusted(5, 0, -5, 0)  # Add padding
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

        painter.restore()


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

        painter.restore()