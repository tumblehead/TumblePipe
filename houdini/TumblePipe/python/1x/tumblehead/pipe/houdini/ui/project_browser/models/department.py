from qtpy.QtCore import Qt, QAbstractTableModel, QModelIndex
from qtpy.QtGui import QFont, QBrush, QColor

from ..helpers import get_timestamp_from_context, get_user_from_context, format_relative_time


class DepartmentTableModel(QAbstractTableModel):
    """Model for department list with columns: Version, Department, User, Relative Time"""

    COLUMN_VERSION = 0
    COLUMN_DEPARTMENT = 1
    COLUMN_USER = 2
    COLUMN_RELATIVE_TIME = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._contexts = []
        self._overwrite_context = None
        self._overwrite_index = -1
        self._selected_row = -1

    def columnCount(self, parent=QModelIndex()):
        return 4

    def rowCount(self, parent=QModelIndex()):
        return len(self._contexts)

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._contexts):
            return None

        context = self._contexts[index.row()]

        # Handle overwrite context for specific row
        if self._overwrite_context is not None and self._overwrite_index == index.row():
            context = self._overwrite_context

        if role == Qt.DisplayRole:
            if index.column() == self.COLUMN_VERSION:
                return "v0000" if context.version_name is None else context.version_name
            elif index.column() == self.COLUMN_DEPARTMENT:
                return context.department_name
            elif index.column() == self.COLUMN_USER:
                user = get_user_from_context(context)
                return "" if user is None else user
            elif index.column() == self.COLUMN_RELATIVE_TIME:
                timestamp = get_timestamp_from_context(context)
                return format_relative_time(timestamp)

        elif role == Qt.TextAlignmentRole:
            if index.column() == self.COLUMN_VERSION:
                return Qt.AlignRight | Qt.AlignVCenter
            elif index.column() == self.COLUMN_DEPARTMENT:
                return Qt.AlignCenter | Qt.AlignVCenter
            elif index.column() == self.COLUMN_USER:
                return Qt.AlignCenter | Qt.AlignVCenter
            elif index.column() == self.COLUMN_RELATIVE_TIME:
                return Qt.AlignRight | Qt.AlignVCenter

        elif role == Qt.FontRole:
            font = QFont()
            font.setPointSize(8)  # Reduce font size for all columns
            if index.column() == self.COLUMN_DEPARTMENT:
                font.setBold(True)
            return font

        elif role == Qt.ForegroundRole:
            if index.column() == self.COLUMN_RELATIVE_TIME:
                return QBrush("#919191")  # Gray color for timestamps
            elif index.column() == self.COLUMN_VERSION and (
                context.version_name is None or context.version_name == "v0000"
            ):
                return QBrush("#616161")  # Darker gray for v0000

        elif role == Qt.UserRole:
            # Return the full context for click handling
            return context

        elif role == Qt.UserRole + 1:
            # Return whether this row is overwritten
            return (
                self._overwrite_context is not None
                and self._overwrite_index == index.row()
            )

        elif role == Qt.BackgroundRole:
            # Return appropriate background color based on row state
            is_selected = (self._selected_row == index.row())
            is_overwritten = (self._overwrite_context is not None and self._overwrite_index == index.row())

            if is_selected:
                if is_overwritten:
                    return QBrush(QColor("#b01c3c"))  # Red for overwritten
                else:
                    return QBrush(QColor("#5e4a8a"))  # Purple for confirmed selection
            else:
                return QBrush(QColor("#3a3a3a"))  # Default gray background

        return None

    def set_row_styling(self, row, selected=False, overwritten=None):
        """Update row styling state and notify views"""
        # Update selection state
        old_selected_row = self._selected_row
        if selected:
            self._selected_row = row
        elif self._selected_row == row:
            self._selected_row = -1

        # Update overwritten state if specified
        if overwritten is not None:
            if overwritten:
                self._overwrite_index = row
            elif self._overwrite_index == row:
                self._overwrite_index = -1

        # Emit data changed for affected rows
        if old_selected_row >= 0 and old_selected_row != row:
            # Refresh old selected row
            old_top_left = self.index(old_selected_row, 0)
            old_bottom_right = self.index(old_selected_row, self.columnCount() - 1)
            self.dataChanged.emit(old_top_left, old_bottom_right)

        if row >= 0:
            # Refresh current row
            top_left = self.index(row, 0)
            bottom_right = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def setContexts(self, contexts):
        """Update the model with new contexts"""
        self.beginResetModel()
        self._contexts = contexts[:]
        self._overwrite_context = None
        self._overwrite_index = -1
        self.endResetModel()

    def getContext(self, row):
        """Get context for a specific row"""
        if 0 <= row < len(self._contexts):
            return self._contexts[row]
        return None

    def setOverwrite(self, row, context):
        """Set overwrite context for a specific row"""
        if 0 <= row < len(self._contexts):
            self._overwrite_index = row
            if context is None:
                self._overwrite_context = None
            else:
                current_version = self._contexts[row].version_name
                overwrite_version = context.version_name
                if current_version == overwrite_version:
                    self._overwrite_context = None
                else:
                    self._overwrite_context = context

            # Emit data changed for the entire row
            top_left = self.index(row, 0)
            bottom_right = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right)

            return self._overwrite_context is not None
        return False