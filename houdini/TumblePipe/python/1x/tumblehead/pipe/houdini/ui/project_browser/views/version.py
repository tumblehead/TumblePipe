from qtpy.QtCore import Qt, Signal, QItemSelectionModel
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
)
from qtpy import QtWidgets

from tumblehead.pipe.paths import get_workfile_context

from ..helpers import get_timestamp_from_context, list_file_paths, get_user_from_context
from ..widgets import ButtonSurface, RowHoverTableView, VersionItemDelegate
from ..models import VersionTableModel


class VersionButtonSurface(ButtonSurface):
    def __init__(self, context, parent=None):
        super().__init__(parent)

        # Members
        self._context = context

        # Settings
        self.setMinimumHeight(0)

        # Create the main layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the content widget
        self._content = QtWidgets.QWidget()
        self._content.setStyleSheet(
            ".QWidget {"
            "   border: 1px solid black;"
            "}"
            "QLabel[timestamp=true] {"
            "   color: #919191;"
            "}"
            "QLabel[text=v0000] {"
            "   color: #616161;"
            "}"
        )
        layout.addWidget(self._content)

        # Create the content layout
        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._content.setLayout(self._layout)

        # Set style
        self.setStyleSheet("padding: 5px;")

        # Update the content layout
        self.refresh()

    def payload(self):
        return self._context

    def overwrite(self, context):
        return False

    def refresh(self):
        # Clear the layout
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)

        # Parameters
        version_name = (
            "v0000"
            if self._context.version_name is None
            else self._context.version_name
        )
        timestamp = get_timestamp_from_context(self._context)
        user_name = get_user_from_context(self._context)
        user_name = "" if user_name is None else user_name
        date = "" if timestamp is None else timestamp.strftime("%d-%m-%Y")
        time = "" if timestamp is None else timestamp.strftime("%H:%M")

        # Create the version label
        version_label = QtWidgets.QLabel(version_name)
        version_label.setAlignment(Qt.AlignLeft)
        self._layout.addWidget(version_label, 1)

        # Create the user label
        user_label = QtWidgets.QLabel(user_name)
        user_label.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(user_label)

        # Create the date label
        date_label = QtWidgets.QLabel(date)
        date_label.setAlignment(Qt.AlignRight)
        date_label.setProperty("timestamp", True)
        self._layout.addWidget(date_label)

        # Create the time label
        time_label = QtWidgets.QLabel(time)
        time_label.setAlignment(Qt.AlignRight)
        time_label.setProperty("timestamp", True)
        self._layout.addWidget(time_label)

        # Force layout refresh
        self._content.setLayout(self._layout)


class VersionView(QtWidgets.QWidget):
    open_location = Signal(object)
    open_version = Signal(object)
    revive_version = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Members
        self._context = None
        self._selection = None
        self._model = VersionTableModel(self)
        self._selected_row = -1

        # Settings
        self.setMinimumHeight(0)

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the table view for versions
        self._table_view = RowHoverTableView()
        self._table_view.setModel(self._model)
        layout.addWidget(self._table_view)

        # Configure table view appearance to match button layout
        self._table_view.horizontalHeader().hide()
        self._table_view.verticalHeader().hide()
        self._table_view.setShowGrid(False)
        self._table_view.setFrameShape(QFrame.NoFrame)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SingleSelection)

        # Set column widths to match current layout - version stretches, user/date/time fixed
        header = self._table_view.horizontalHeader()
        self._table_view.setColumnWidth(VersionTableModel.COLUMN_USER, 80)
        self._table_view.setColumnWidth(VersionTableModel.COLUMN_DATE, 80)
        self._table_view.setColumnWidth(VersionTableModel.COLUMN_TIME, 50)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(
            VersionTableModel.COLUMN_VERSION, QHeaderView.ResizeMode.Stretch
        )

        # Apply minimal styling (delegate handles backgrounds and borders)
        self._table_view.setStyleSheet("""
            QTableView {
                background-color: transparent;
                border: none;
                outline: none;
                gridline-color: transparent;
            }
        """)

        # Configure row appearance for button-like look
        self._table_view.verticalHeader().setDefaultSectionSize(
            30
        )  # Set row height (reduced for smaller font)
        self._table_view.setAlternatingRowColors(False)  # Ensure no alternating colors

        # Set custom delegate for consistent styling with department table
        self._table_view.setItemDelegate(VersionItemDelegate(self._table_view))

        # Connect signals
        self._table_view.clicked.connect(self._on_row_clicked)
        self._table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table_view.customContextMenuRequested.connect(self._on_context_menu)

        # Initial update
        self.refresh()

    def _on_row_clicked(self, index):
        """Handle table row click"""
        if not index.isValid():
            return

        context = self._model.data(index, Qt.UserRole)
        if context and context.version_name != self._selection:
            self._selected_row = index.row()
            self._selection = context.version_name
            self.open_version.emit(context)

    def _on_context_menu(self, position):
        """Handle right-click context menu"""
        index = self._table_view.indexAt(position)
        if not index.isValid():
            return

        context = self._model.data(index, Qt.UserRole)
        if not context:
            return

        # Build and display the menu (same as before)
        menu = QtWidgets.QMenu()
        open_location_action = menu.addAction("Open Location")
        revive_version_action = menu.addAction("Revive")
        selected_action = menu.exec_(self._table_view.mapToGlobal(position))

        if selected_action is None:
            return
        if selected_action == open_location_action:
            return self.open_location.emit(context)
        if selected_action == revive_version_action:
            return self.revive_version.emit(context)

    def set_context(self, context):
        self._context = context
        self._selection = None
        self.refresh()

    def select(self, selection):
        """Select a version by name"""
        if selection is None:
            self._table_view.clearSelection()
            self._selected_row = -1
            self._selection = None
            return

        # Find the row with the matching version name
        for row in range(self._model.rowCount()):
            context = self._model.getContext(row)
            if context and context.version_name == selection:
                # Select the row
                selection_model = self._table_view.selectionModel()
                index = self._model.index(row, 0)
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.SelectCurrent
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                self._selected_row = row
                self._selection = selection
                return

        # Version not found, clear selection
        self._table_view.clearSelection()
        self._selected_row = -1
        self._selection = None

    def refresh(self):
        """Update the model with latest version contexts"""
        # Get the version contexts
        version_contexts = (
            []
            if self._context is None
            else list(map(get_workfile_context, list_file_paths(self._context)))
        )

        # Update the model with new contexts (reversed to show latest first)
        self._model.setContexts(list(reversed(version_contexts)))

        # Restore selection if it still exists
        self.select(self._selection)