from qtpy.QtCore import Qt, QAbstractTableModel, QModelIndex
from qtpy.QtGui import QFont, QBrush

from ..helpers import get_timestamp_from_context, get_user_from_context


class VersionTableModel(QAbstractTableModel):
    """Model for version list with columns: Version, User, Date, Time"""

    COLUMN_VERSION = 0
    COLUMN_USER = 1
    COLUMN_DATE = 2
    COLUMN_TIME = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._contexts = []

    def columnCount(self, parent=QModelIndex()):
        return 4

    def rowCount(self, parent=QModelIndex()):
        return len(self._contexts)

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._contexts):
            return None

        context = self._contexts[index.row()]

        if role == Qt.DisplayRole:
            if index.column() == self.COLUMN_VERSION:
                return "v0000" if context.version_name is None else context.version_name
            elif index.column() == self.COLUMN_USER:
                user = get_user_from_context(context)
                return "" if user is None else user
            elif index.column() == self.COLUMN_DATE:
                timestamp = get_timestamp_from_context(context)
                return "" if timestamp is None else timestamp.strftime("%d-%m-%Y")
            elif index.column() == self.COLUMN_TIME:
                timestamp = get_timestamp_from_context(context)
                return "" if timestamp is None else timestamp.strftime("%H:%M")

        elif role == Qt.TextAlignmentRole:
            if index.column() == self.COLUMN_VERSION:
                return Qt.AlignLeft | Qt.AlignVCenter
            elif index.column() == self.COLUMN_USER:
                return Qt.AlignCenter | Qt.AlignVCenter
            elif index.column() in [self.COLUMN_DATE, self.COLUMN_TIME]:
                return Qt.AlignRight | Qt.AlignVCenter

        elif role == Qt.FontRole:
            font = QFont()
            font.setPointSize(8)  # Reduce font size for all columns
            return font

        elif role == Qt.ForegroundRole:
            if index.column() in [self.COLUMN_DATE, self.COLUMN_TIME]:
                return QBrush("#919191")  # Gray color for timestamps
            elif index.column() == self.COLUMN_VERSION and (
                context.version_name is None or context.version_name == "v0000"
            ):
                return QBrush("#616161")  # Darker gray for v0000

        elif role == Qt.UserRole:
            # Return the full context for click handling
            return context

        return None

    def setContexts(self, contexts):
        """Update the model with new contexts"""
        self.beginResetModel()
        self._contexts = contexts[:]
        self.endResetModel()

    def getContext(self, row):
        """Get context for a specific row"""
        if 0 <= row < len(self._contexts):
            return self._contexts[row]
        return None