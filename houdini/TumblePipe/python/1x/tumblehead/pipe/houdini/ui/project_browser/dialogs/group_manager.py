from pathlib import Path

from qtpy import QtWidgets
from qtpy.QtCore import Qt
import hou

from tumblehead.config.department import list_departments
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.ui.project_browser.models.group import (
    GroupListModel,
    AvailableEntitiesModel,
    GroupMembersModel
)

class GroupManagerDialog(QtWidgets.QDialog):
    """Main dialog for managing entity groups"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.config_path = Path(hou.getenv('TH_CONFIG_PATH'))

        self.setWindowTitle("Entity Groups Manager")
        self.resize(1000, 700)

        # Models
        self.group_list_model = GroupListModel(api)

        # State
        self.current_group_name = None

        # Create UI
        self._create_ui()

        # Load initial data
        self.refresh_groups()

    def _create_ui(self):
        """Create the main UI layout"""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # Main content area (splitter)
        splitter = QtWidgets.QSplitter(Qt.Horizontal)

        # Left: Groups list
        groups_panel = self._create_groups_panel()
        splitter.addWidget(groups_panel)

        # Right: Group editor
        self.editor_widget = GroupEditorWidget(self.api, parent=self)
        self.editor_widget.setEnabled(False)
        self.editor_widget.group_saved.connect(self.on_group_saved)
        self.editor_widget.group_cancelled.connect(self.on_group_cancelled)
        splitter.addWidget(self.editor_widget)

        # Set splitter sizes (30% left, 70% right)
        splitter.setSizes([300, 700])

        layout.addWidget(splitter)

    def _create_toolbar(self):
        """Create toolbar with action buttons"""
        toolbar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar.setLayout(layout)

        # New Group button
        self.new_button = QtWidgets.QPushButton("New Group")
        self.new_button.clicked.connect(self.create_new_group)
        layout.addWidget(self.new_button)

        # Delete Group button
        self.delete_button = QtWidgets.QPushButton("Delete Group")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_group)
        layout.addWidget(self.delete_button)

        layout.addStretch()

        # Close button
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        layout.addWidget(self.close_button)

        return toolbar

    def _create_groups_panel(self):
        """Create the left panel with groups list"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        # Title
        title = QtWidgets.QLabel("Groups")
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(title)

        # Groups list
        self.groups_list = QtWidgets.QListView()
        self.groups_list.setModel(self.group_list_model)
        self.groups_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.groups_list.selectionModel().currentChanged.connect(self.on_group_selected)
        layout.addWidget(self.groups_list)

        return panel

    def refresh_groups(self):
        """Refresh the groups list"""
        self.group_list_model.load_groups()

        # Clear selection and disable editor
        self.groups_list.clearSelection()
        self.editor_widget.clear()
        self.editor_widget.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.current_group_name = None

    def create_new_group(self):
        """Create a new blank group and open editor"""
        # Prompt for group name
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "New Group",
            "Enter group name:"
        )

        if not accepted or not name:
            return

        # Check if name already exists (check both shot and asset groups)
        shot_groups = self.api.config.list_groups('shots')
        asset_groups = self.api.config.list_groups('assets')
        all_groups = shot_groups + asset_groups
        existing_group = next((g for g in all_groups if g.uri.segments[-1] == name), None)
        if existing_group:
            QtWidgets.QMessageBox.warning(
                self,
                "Group Exists",
                f"Group '{name}' already exists."
            )
            return

        # Clear selection and open editor for new group
        self.groups_list.clearSelection()
        self.current_group_name = name
        self.editor_widget.load_new_group(name)
        self.editor_widget.setEnabled(True)
        self.delete_button.setEnabled(False)

    def on_group_selected(self, current, previous):
        """Handle group selection"""
        if not current.isValid():
            self.editor_widget.clear()
            self.editor_widget.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.current_group_name = None
            return

        group = self.group_list_model.get_group(current)
        if group:
            self.current_group_name = group.uri.segments[-1]
            self.editor_widget.load_existing_group(group)
            self.editor_widget.setEnabled(True)
            self.delete_button.setEnabled(True)

    def delete_group(self):
        """Delete the selected group"""
        if not self.current_group_name:
            return

        # Confirm deletion
        result = QtWidgets.QMessageBox.question(
            self,
            "Delete Group",
            f"Are you sure you want to delete the group '{self.current_group_name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )

        if result != QtWidgets.QMessageBox.Yes:
            return

        try:
            # Find the group to get its full URI
            shot_groups = self.api.config.list_groups('shots')
            asset_groups = self.api.config.list_groups('assets')
            all_groups = shot_groups + asset_groups
            group = next((g for g in all_groups if g.uri.segments[-1] == self.current_group_name), None)

            if not group:
                raise ValueError(f"Group '{self.current_group_name}' not found")

            # Remove from config using API
            self.api.config.remove_group(group.uri)

            self.refresh_groups()

            hou.ui.displayMessage(f"Group '{self.current_group_name}' deleted successfully.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Error",
                f"Failed to delete group: {str(e)}"
            )

    def on_group_saved(self, group_name):
        """Handle group saved event"""
        # Clear cache
        if hasattr(self.api.config, '_cached_groups'):
            delattr(self.api.config, '_cached_groups')

        # Refresh groups list
        self.refresh_groups()

        # Show success message
        hou.ui.displayMessage(f"Group '{group_name}' saved successfully.")

    def on_group_cancelled(self):
        """Handle group edit cancelled"""
        self.refresh_groups()

class GroupEditorWidget(QtWidgets.QWidget):
    """Widget for editing a single group"""

    from qtpy.QtCore import Signal
    group_saved = Signal(str)  # Emits group name when saved
    group_cancelled = Signal()

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.config_path = Path(hou.getenv('TH_CONFIG_PATH'))

        # State
        self.is_new_group = False
        self.original_group = None
        self.current_group_name = None

        # Models
        self.available_model = AvailableEntitiesModel(api)
        self.members_model = GroupMembersModel()

        # Create UI
        self._create_ui()

    def _create_ui(self):
        """Create the editor UI"""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Title
        title = QtWidgets.QLabel("Group Editor")
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(title)

        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        # Basic info section
        basic_info = self._create_basic_info_section()
        layout.addWidget(basic_info)

        # Members section
        members_section = self._create_members_section()
        layout.addWidget(members_section)

        # Status section
        status_section = self._create_status_section()
        layout.addWidget(status_section)

        # Action buttons
        buttons = self._create_action_buttons()
        layout.addWidget(buttons)

    def _create_basic_info_section(self):
        """Create basic info section (name, type, departments)"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout()
        widget.setLayout(layout)

        # Group name
        self.name_field = QtWidgets.QLineEdit()
        self.name_field.textChanged.connect(self.validate)
        layout.addRow("Name:", self.name_field)

        # Root URI selector
        uri_widget = QtWidgets.QWidget()
        uri_layout = QtWidgets.QHBoxLayout()
        uri_layout.setContentsMargins(0, 0, 0, 0)
        uri_widget.setLayout(uri_layout)

        self.root_uri_combo = QtWidgets.QComboBox()
        self.root_uri_combo.setEditable(False)
        self.root_uri_combo.currentTextChanged.connect(self.on_root_uri_changed)
        uri_layout.addWidget(self.root_uri_combo, stretch=1)

        # Refresh button to update URI list
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.setToolTip("Refresh available URIs")
        refresh_btn.clicked.connect(self.refresh_root_uri_list)
        uri_layout.addWidget(refresh_btn)

        layout.addRow("Root URI:", uri_widget)

        # Departments
        dept_label = QtWidgets.QLabel("Departments:")
        layout.addRow(dept_label, QtWidgets.QWidget())  # Placeholder

        self.department_widget = self._create_department_selector()
        layout.addRow("", self.department_widget)

        return widget

    def _create_department_selector(self):
        """Create department checklist widget"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)

        try:
            # Get all departments
            shot_departments = list_departments('shots')
            asset_departments = list_departments('assets')

            # Combine and deduplicate
            all_departments = sorted(set(
                [d.name for d in shot_departments] +
                [d.name for d in asset_departments]
            ))

            self.department_checkboxes = {}

            # Create checkboxes in grid (4 columns)
            for i, dept in enumerate(all_departments):
                checkbox = QtWidgets.QCheckBox(dept)
                checkbox.stateChanged.connect(self.validate)
                self.department_checkboxes[dept] = checkbox
                row = i // 4
                col = i % 4
                layout.addWidget(checkbox, row, col)

        except Exception as e:
            print(f"Error creating department selector: {e}")
            self.department_checkboxes = {}

        return widget

    def _create_members_section(self):
        """Create members section with add/remove UI"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        widget.setLayout(layout)

        # Section title
        title = QtWidgets.QLabel("Members:")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        # Two-column layout
        columns = QtWidgets.QHBoxLayout()

        # Left column: Available entities
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout()
        left_panel.setLayout(left_layout)

        available_label = QtWidgets.QLabel("Available Entities")
        left_layout.addWidget(available_label)

        self.available_list = QtWidgets.QListView()
        self.available_list.setModel(self.available_model)
        self.available_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        left_layout.addWidget(self.available_list)

        columns.addWidget(left_panel)

        # Middle: Add/Remove buttons
        buttons_panel = QtWidgets.QWidget()
        buttons_layout = QtWidgets.QVBoxLayout()
        buttons_panel.setLayout(buttons_layout)

        buttons_layout.addStretch()

        self.add_button = QtWidgets.QPushButton("Add >>")
        self.add_button.clicked.connect(self.on_add_members)
        buttons_layout.addWidget(self.add_button)

        self.remove_button = QtWidgets.QPushButton("<< Remove")
        self.remove_button.clicked.connect(self.on_remove_members)
        buttons_layout.addWidget(self.remove_button)

        buttons_layout.addStretch()

        columns.addWidget(buttons_panel)

        # Right column: Group members
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout()
        right_panel.setLayout(right_layout)

        members_label = QtWidgets.QLabel("Group Members")
        right_layout.addWidget(members_label)

        self.members_list = QtWidgets.QListView()
        self.members_list.setModel(self.members_model)
        self.members_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        right_layout.addWidget(self.members_list)

        columns.addWidget(right_panel)

        layout.addLayout(columns)

        return widget

    def _create_status_section(self):
        """Create validation status section"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)

        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        # Status label
        self.status_label = QtWidgets.QLabel("No changes")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        return widget

    def _create_action_buttons(self):
        """Create save/cancel buttons"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)

        layout.addStretch()

        self.save_button = QtWidgets.QPushButton("Save")
        self.save_button.clicked.connect(self.save_group)
        self.save_button.setEnabled(False)
        layout.addWidget(self.save_button)

        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_edit)
        layout.addWidget(self.cancel_button)

        return widget

    def clear(self):
        """Clear the editor"""
        self.name_field.clear()
        self.root_uri_combo.clear()

        # Uncheck all departments
        for checkbox in self.department_checkboxes.values():
            checkbox.setChecked(False)

        self.available_model.clear()
        self.members_model.clear()

        self.status_label.setText("No changes")
        self.status_label.setStyleSheet("")
        self.save_button.setEnabled(False)

        self.is_new_group = False
        self.original_group = None
        self.current_group_name = None

    def load_new_group(self, name):
        """Load editor for creating a new group"""
        self.clear()
        self.is_new_group = True
        self.current_group_name = name
        self.name_field.setText(name)
        self.name_field.setReadOnly(True)  # Can't change name after creation prompt

        # Populate root URIs and enable selection
        self.refresh_root_uri_list()
        self.root_uri_combo.setEnabled(True)

        # Load available entities
        self.refresh_available_entities()

        self.validate()

    def load_existing_group(self, group):
        """Load editor with existing group data"""
        self.clear()
        self.is_new_group = False
        self.original_group = group
        group_name = group.uri.segments[-1]
        self.current_group_name = group_name

        # Load basic info
        self.name_field.setText(group_name)
        self.name_field.setReadOnly(True)  # Can't rename existing groups

        # Populate and set root URI
        self.refresh_root_uri_list()
        # Determine root from group URI (entity:/groups/{context}/name -> entity:/{context})
        if len(group.uri.segments) > 1:
            context = group.uri.segments[1]  # 'shots' or 'assets'
            root_uri_str = f'entity:/{context}'
            uri_index = self.root_uri_combo.findText(root_uri_str)
            if uri_index >= 0:
                self.root_uri_combo.setCurrentIndex(uri_index)
        # Lock root URI for existing groups (will be properly locked when members exist)
        self.root_uri_combo.setEnabled(len(group.members) == 0)

        # Check departments
        for dept_name, checkbox in self.department_checkboxes.items():
            checkbox.setChecked(dept_name in group.departments)

        # Load members - directly use URIs to create simple entity representations
        # TODO: This needs proper entity model implementation
        member_entities = []
        for member_uri in group.members:
            # Create a simple dict representation for display
            if len(member_uri.segments) >= 3:
                entity = {
                    'uri': member_uri,
                    'type': member_uri.segments[0],
                    'name': member_uri.segments[-1]
                }
                member_entities.append(entity)
        self.members_model.load_members(member_entities)

        # Load available entities
        self.refresh_available_entities()

        self.validate()

    def refresh_root_uri_list(self):
        """Populate root URI dropdown with available options"""
        current_text = self.root_uri_combo.currentText()
        self.root_uri_combo.clear()

        try:
            # Add only top-level entity types
            self.root_uri_combo.addItem('entity:/shots')
            self.root_uri_combo.addItem('entity:/assets')

            # Restore previous selection if it exists
            if current_text:
                index = self.root_uri_combo.findText(current_text)
                if index >= 0:
                    self.root_uri_combo.setCurrentIndex(index)

        except Exception as e:
            print(f"Error loading root URIs: {e}")

    def on_root_uri_changed(self):
        """Handle root URI change"""
        self.refresh_available_entities()
        self.validate()

    def refresh_available_entities(self):
        """Refresh the available entities list"""
        root_uri_str = self.root_uri_combo.currentText()

        if not root_uri_str:
            self.available_model.clear()
            return

        # Get entities already in other groups
        assigned_entities = self.get_entities_in_other_groups()

        # Get current group members
        current_members = set(self.members_model.get_member_entities())

        # Load available entities using root URI
        self.available_model.load_entities_from_uri(root_uri_str, assigned_entities, current_members)

    def get_entities_in_other_groups(self):
        """Get set of entity URI strings already in other groups"""
        assigned = set()

        # Get all groups from both contexts
        shot_groups = self.api.config.list_groups('shots')
        asset_groups = self.api.config.list_groups('assets')
        all_groups = shot_groups + asset_groups

        for group in all_groups:
            # Skip current group being edited
            if group.uri.segments[-1] == self.current_group_name:
                continue

            for member in group.members:
                # member is already a Uri object
                assigned.add(str(member))

        return assigned

    def on_add_members(self):
        """Add selected available entities to members list"""
        selected_indexes = self.available_list.selectedIndexes()

        if not selected_indexes:
            return

        # Get selected items (only enabled ones)
        items_to_add = []
        rows_to_remove = []

        for index in selected_indexes:
            item = self.available_model.itemFromIndex(index)
            if item and item.isEnabled():
                entity_string = item.data(Qt.UserRole)
                items_to_add.append(entity_string)
                rows_to_remove.append(index.row())

        # Remove from available (in reverse order to avoid index shifting)
        for row in sorted(rows_to_remove, reverse=True):
            self.available_model.removeRow(row)

        # Add to members
        for entity_string in items_to_add:
            self.members_model.add_member(entity_string)

        # Lock root URI after first member is added
        if self.members_model.rowCount() > 0:
            self.root_uri_combo.setEnabled(False)

        self.validate()

    def on_remove_members(self):
        """Remove selected members and add back to available list"""
        selected_indexes = self.members_list.selectedIndexes()

        if not selected_indexes:
            return

        # Get items to move
        items_to_remove = []
        for index in selected_indexes:
            item = self.members_model.itemFromIndex(index)
            if item:
                entity_string = item.data(Qt.UserRole)
                items_to_remove.append(entity_string)

        # Remove from members
        self.members_model.remove_members(selected_indexes)

        # Don't add back to available - will be refreshed automatically
        self.refresh_available_entities()

        # Unlock root URI if all members removed
        if self.members_model.rowCount() == 0:
            self.root_uri_combo.setEnabled(True)

        self.validate()

    def get_selected_departments(self):
        """Get list of selected department names"""
        selected = []
        for dept_name, checkbox in self.department_checkboxes.items():
            if checkbox.isChecked():
                selected.append(dept_name)
        return selected

    def validate(self):
        """Validate current group configuration and update UI"""
        errors = []
        warnings = []

        # Check name
        name = self.name_field.text().strip()
        if not name:
            errors.append("Group name is required")

        # Check members
        member_count = self.members_model.rowCount()
        if member_count == 0:
            errors.append("Group must have at least one member")
        elif member_count == 1:
            warnings.append("Group has only one member")

        # Check departments
        selected_depts = self.get_selected_departments()
        if len(selected_depts) == 0:
            errors.append("Select at least one department")

        # Update status display
        if errors:
            self.status_label.setText(f"[ERROR] {'; '.join(errors)}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.save_button.setEnabled(False)
        elif warnings:
            dept_count = len(selected_depts)
            self.status_label.setText(
                f"[WARNING] Valid with warnings: {'; '.join(warnings)}\n"
                f"{member_count} members, {dept_count} departments"
            )
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            self.save_button.setEnabled(True)
        else:
            dept_count = len(selected_depts)
            self.status_label.setText(
                f"[OK] Valid - {member_count} members, {dept_count} departments"
            )
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.save_button.setEnabled(True)

    def save_group(self):
        """Save the current group to configuration"""
        try:
            # Get group data
            name = self.name_field.text().strip()
            root_uri_str = self.root_uri_combo.currentText()
            departments = self.get_selected_departments()

            # Parse member URI strings to Uri objects
            member_uri_strings = self.members_model.get_member_entities()
            member_uris = [Uri.parse_unsafe(uri_str) for uri_str in member_uri_strings]

            # Parse root URI to determine context (shots or assets)
            root_uri = Uri.parse_unsafe(root_uri_str)
            context = root_uri.segments[0] if len(root_uri.segments) > 0 else 'shots'

            # Construct group URI (entity:/groups/{context}/{name})
            Uri.parse_unsafe(f'entity:/groups/{context}/{name}')

            # If editing existing group, remove old version first
            if not self.is_new_group and self.original_group:
                self.api.config.remove_group(self.original_group.uri)

            # Add group to config using API
            self.api.config.add_group(context, name, member_uris, departments)

            # Emit saved signal
            self.group_saved.emit(name)

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Error Saving Group",
                f"Failed to save group: {str(e)}"
            )

    def cancel_edit(self):
        """Cancel editing and close"""
        self.clear()
        self.group_cancelled.emit()
