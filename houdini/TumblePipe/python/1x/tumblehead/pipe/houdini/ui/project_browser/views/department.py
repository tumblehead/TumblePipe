from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QApplication,
)
from qtpy import QtWidgets

from tumblehead.pipe.paths import Context
from tumblehead.config.department import list_departments
from tumblehead.config.groups import get_group

from ..helpers import (
    get_entity_type,
    get_timestamp_from_context,
    latest_context,
    get_user_from_context,
    format_relative_time,
)
from ..widgets import ButtonSurface, RowHoverTableView, DepartmentItemDelegate
from ..models import DepartmentTableModel



class DepartmentButtonSurface(ButtonSurface):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        assert isinstance(context, Context), f"Invalid context: {context}"

        # Members
        self._context = context
        self._overwrite_context = None

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
            "QLabel[department=true] {"
            "   font-weight: bold;"
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
        if context is None:
            self._overwrite_context = None
        else:
            current_version_name = self._context.version_name
            overwrite_version_name = context.version_name
            version_equal = current_version_name == overwrite_version_name
            self._overwrite_context = None if version_equal else context
        self.refresh()
        return self._overwrite_context is not None

    def refresh(self):
        # Clear the layout
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)

        # Get the context to display
        overwritten = self._overwrite_context is not None
        context = self._context if not overwritten else self._overwrite_context

        # Parameters
        department_name = context.department_name
        version_name = "v0000" if context.version_name is None else context.version_name
        timestamp = get_timestamp_from_context(context)
        user_name = get_user_from_context(context)
        user_name = "" if user_name is None else user_name
        relative_time = format_relative_time(timestamp)

        # Create the version label
        version_label = QtWidgets.QLabel(version_name)
        version_label.setAlignment(Qt.AlignRight)
        self._layout.addWidget(version_label)

        # Create the department label
        department_label = QtWidgets.QLabel(department_name)
        department_label.setAlignment(Qt.AlignCenter)
        department_label.setProperty("department", True)
        self._layout.addWidget(department_label, 1)

        # Create the user label
        user_label = QtWidgets.QLabel(user_name)
        user_label.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(user_label)

        # Create the relative time label
        relative_time_label = QtWidgets.QLabel(relative_time)
        relative_time_label.setAlignment(Qt.AlignRight)
        relative_time_label.setProperty("timestamp", True)
        self._layout.addWidget(relative_time_label)

        # Force layout refresh
        self._content.setLayout(self._layout)


class DepartmentBrowser(QtWidgets.QWidget):
    selection_changed = Signal(object)
    open_location = Signal(object)
    reload_scene = Signal(object)
    new_from_current = Signal(object)
    new_from_template = Signal(object)

    def __init__(self, api, parent=None):
        super().__init__(parent)

        # Members
        self._api = api
        self._entity = None
        self._selection = None
        self._model = DepartmentTableModel(self)
        self._selected_row = -1

        # Settings
        self.setObjectName("DepartmentBrowser")
        self.setMinimumHeight(0)

        # Create the outer layout
        outer_layout = QtWidgets.QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.setLayout(outer_layout)

        # Create outer scroll area
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setLayout(layout)
        scroll_area.setWidget(scroll_widget)
        outer_layout.addWidget(scroll_area)

        # Create the table view for departments
        self._table_view = RowHoverTableView()
        self._table_view.setModel(self._model)
        layout.addWidget(self._table_view, 1)

        # Configure table view appearance to match button layout
        self._table_view.horizontalHeader().hide()
        self._table_view.verticalHeader().hide()
        self._table_view.setShowGrid(False)
        self._table_view.setFrameShape(QFrame.NoFrame)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Disable automatic selection - we handle selection manually after save prompt
        self._table_view.setSelectionMode(QAbstractItemView.NoSelection)
        self._table_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Set column widths to match current layout
        header = self._table_view.horizontalHeader()
        header.setMinimumSectionSize(40)  # Prevent columns from becoming too narrow
        self._table_view.setColumnWidth(DepartmentTableModel.COLUMN_VERSION, 60)
        self._table_view.setColumnWidth(DepartmentTableModel.COLUMN_GROUP, 80)
        self._table_view.setColumnWidth(DepartmentTableModel.COLUMN_DEPARTMENT, 100)  # Set minimum width
        self._table_view.setColumnWidth(DepartmentTableModel.COLUMN_USER, 80)
        self._table_view.setColumnWidth(DepartmentTableModel.COLUMN_RELATIVE_TIME, 70)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(
            DepartmentTableModel.COLUMN_DEPARTMENT, QHeaderView.ResizeMode.Stretch
        )

        # Apply minimal styling - delegate handles row-level backgrounds and borders
        self._table_view.setStyleSheet("""
            QTableView {
                background-color: transparent;
                border: none;
                outline: none;
                gridline-color: transparent;
                selection-background-color: transparent;
            }
        """)

        # Configure row appearance for button-like look
        self._table_view.verticalHeader().setDefaultSectionSize(
            30
        )  # Set row height (reduced for smaller font)
        self._table_view.setAlternatingRowColors(False)  # Ensure no alternating colors

        # Set custom delegate for overwritten state styling
        self._table_view.setItemDelegate(DepartmentItemDelegate(self._table_view))

        # Connect signals
        self._table_view.clicked.connect(self._on_row_clicked)
        self._table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table_view.customContextMenuRequested.connect(self._on_context_menu)


        # Initial update
        self.refresh()

    def set_entity(self, entity):
        """Set the entity and refresh, preserving selection if entity hasn't changed"""
        # Only clear selection if entity actually changed
        if self._entity != entity:
            self._entity = entity
            # Clear selection when entity changes since departments will be different
            self._selection = None
            self._selected_row = -1
        else:
            # Same entity, preserve selection
            self._entity = entity

        self.refresh()

    def select(self, selection):
        """Select a department by name with improved error handling"""
        if selection is None:
            # Clear selection
            old_row = self._selected_row
            self._selected_row = -1
            self._selection = None
            self._update_row_properties(old_row, selected=False)
            return

        try:
            # Find the row with the matching department name
            for row in range(self._model.rowCount()):
                try:
                    context = self._model.getContext(row)
                    if context and context.department_name == selection:
                        # Clear previous selection
                        if self._selected_row >= 0:
                            self._update_row_properties(self._selected_row, selected=False)

                        # Update internal selection state
                        self._selected_row = row
                        self._selection = selection

                        # Set properties for new selection
                        self._update_row_properties(row, selected=True)
                        return
                except Exception as e:
                    raise RuntimeError(f"Error checking row {row} for department selection: {e}")

            # Department not found - this should not happen
            raise AssertionError(f"Department '{selection}' not found in current contexts")

        except Exception as e:
            raise RuntimeError(f"Error during department selection: {e}")

    def overwrite(self, context):
        """Set overwrite context for the currently selected row"""
        if self._selected_row < 0:
            return False
        return self._model.setOverwrite(self._selected_row, context)

    def confirm_selection(self, department_name):
        """Confirm and update visual selection - called after save prompt succeeds"""
        # Find the row with the matching department name
        for row in range(self._model.rowCount()):
            try:
                context = self._model.getContext(row)
                if context and context.department_name == department_name:
                    # Clear previous selection
                    if self._selected_row >= 0:
                        self._update_row_properties(self._selected_row, selected=False)

                    # Update internal selection state
                    self._selected_row = row
                    self._selection = department_name

                    # Set properties for new selection
                    self._update_row_properties(row, selected=True)
                    return
            except Exception:
                continue

    def _update_row_properties(self, row, selected=False, overwritten=False):
        """Update row styling by modifying model data and triggering refresh"""
        if row < 0 or row >= self._model.rowCount():
            return

        # Notify model that this row's styling should update
        self._model.set_row_styling(row, selected=selected, overwritten=overwritten)

        # Force visual update
        self._table_view.viewport().update()

    def _on_row_clicked(self, index):
        """Handle table row click with shift+click detection"""
        if not index.isValid():
            return

        context = self._model.data(index, Qt.UserRole)
        if context and context.department_name != self._selection:
            # Don't update visual selection immediately - let main browser confirm after save prompt
            # Just emit the signal with the clicked context
            modifiers = QApplication.keyboardModifiers()
            auto_save = bool(modifiers & Qt.ShiftModifier)
            self.selection_changed.emit((context, auto_save))

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
        reload_scene_action = menu.addAction("Reload Scene")
        new_from_current_action = menu.addAction("New: Current")
        new_from_template_action = menu.addAction("New: Template")
        selected_action = menu.exec_(self._table_view.mapToGlobal(position))

        if selected_action is None:
            return
        if selected_action == open_location_action:
            return self.open_location.emit(context)
        if selected_action == reload_scene_action:
            return self.reload_scene.emit(context)
        if selected_action == new_from_current_action:
            return self.new_from_current.emit(context)
        if selected_action == new_from_template_action:
            return self.new_from_template.emit(context)

    def refresh(self):
        """Update the model with latest department contexts while preserving selection"""
        # Preserve current selection state before refresh
        preserved_selection = self._selection

        # Check if the entity is valid
        if self._entity is None:
            self._model.setEntityType(None)
            self._model.setContexts([])
            self._selection = None
            self._selected_row = -1
            return

        def _latest_department_contexts():
            try:
                entity_type = get_entity_type(self._entity)
                # Pass entity type to model for group badge display
                self._model.setEntityType(entity_type)

                if entity_type == 'asset':
                    departments = list_departments('assets')
                    return [
                        latest_context(self._entity, dept.name)
                        for dept in departments
                    ]
                elif entity_type == 'shot':
                    departments = list_departments('shots')
                    return [
                        latest_context(self._entity, dept.name)
                        for dept in departments
                    ]
                elif entity_type == 'group':
                    group = get_group(self._entity)
                    if group is None:
                        return []
                    return [
                        latest_context(self._entity, dept_name)
                        for dept_name in group.departments
                    ]
                else:
                    return []
            except Exception as e:
                raise RuntimeError(f"Failed to get department contexts for {self._entity}: {e}")

        # Update the model with new contexts
        try:
            contexts = _latest_department_contexts()
            self._model.setContexts(contexts)
        except Exception as e:
            raise RuntimeError(f"Failed to update department model: {e}")

        # Attempt to restore selection (use preserved values)
        if preserved_selection is not None:
            try:
                self.select(preserved_selection)
                # Verify selection was restored
                assert self._selection == preserved_selection, f"Department selection restoration failed: expected {preserved_selection}, got {self._selection}"
            except Exception as e:
                raise RuntimeError(f"Failed to restore department selection '{preserved_selection}': {e}")