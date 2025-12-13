from pathlib import Path

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
import hou

from tumblehead.config.department import list_departments
from tumblehead.config.groups import list_groups, add_group, remove_group
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.ui.project_browser.models.group import (
    AvailableEntitiesModel,
    GroupMembersModel
)


class GroupEditorWidget(QtWidgets.QWidget):
    """Reusable widget for editing a group (name, context, members, departments)"""

    group_saved = Signal(str)
    group_cancelled = Signal()
    validation_changed = Signal(bool, str)

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.config_path = Path(hou.getenv('TH_CONFIG_PATH'))

        self.is_new_group = False
        self.original_group = None
        self.current_group_name = None

        self.available_model = AvailableEntitiesModel(api)
        self.members_model = GroupMembersModel()

        self.department_checkboxes = {}
        self._validation_errors = []

        self._create_ui()

    def _create_ui(self):
        """Create the widget UI"""
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        basic_info = self._create_basic_info_section()
        layout.addWidget(basic_info)

        members_section = self._create_members_section()
        layout.addWidget(members_section, stretch=1)

    def _create_basic_info_section(self):
        """Create basic info section (context, name)"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout()
        widget.setLayout(layout)

        self.context_combo = QtWidgets.QComboBox()
        self.context_combo.addItem('shots')
        self.context_combo.addItem('assets')
        self.context_combo.currentTextChanged.connect(self._on_context_changed)
        layout.addRow("Context:", self.context_combo)

        self.name_field = QtWidgets.QLineEdit()
        self.name_field.textChanged.connect(self._validate)
        layout.addRow("Name:", self.name_field)

        return widget

    def _create_members_section(self):
        """Create members section with add/remove UI and departments panel"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        widget.setLayout(layout)

        title = QtWidgets.QLabel("Members:")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        columns = QtWidgets.QHBoxLayout()

        left_panel = self._create_available_panel()
        columns.addWidget(left_panel, stretch=1)

        buttons_panel = self._create_transfer_buttons()
        columns.addWidget(buttons_panel)

        right_panel = self._create_members_panel()
        columns.addWidget(right_panel, stretch=1)

        dept_panel = self._create_department_panel()
        columns.addWidget(dept_panel)

        layout.addLayout(columns)

        return widget

    def _create_available_panel(self):
        """Create available entities panel with tree view"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Available Entities")
        layout.addWidget(label)

        self.available_tree = QtWidgets.QTreeView()
        self.available_tree.setModel(self.available_model)
        self.available_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.available_tree.setExpandsOnDoubleClick(True)

        # Configure header and columns for Scene column visibility
        header = self.available_tree.header()
        header.setStretchLastSection(True)
        if header.count() > 0:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)

        layout.addWidget(self.available_tree)

        return panel

    def _create_transfer_buttons(self):
        """Create add/remove buttons panel"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        layout.addStretch()

        self.add_button = QtWidgets.QPushButton("→")
        self.add_button.clicked.connect(self._on_add_members)
        layout.addWidget(self.add_button)

        self.remove_button = QtWidgets.QPushButton("←")
        self.remove_button.clicked.connect(self._on_remove_members)
        layout.addWidget(self.remove_button)

        layout.addStretch()

        return panel

    def _create_members_panel(self):
        """Create group members panel with tree view"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Group Members")
        layout.addWidget(label)

        self.members_tree = QtWidgets.QTreeView()
        self.members_tree.setModel(self.members_model)
        self.members_tree.setHeaderHidden(True)
        self.members_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.members_tree.setExpandsOnDoubleClick(True)
        layout.addWidget(self.members_tree)

        return panel

    def _create_department_panel(self):
        """Create departments panel with vertical scrollable list"""
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(150)
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Departments")
        layout.addWidget(label)

        self.dept_scroll = QtWidgets.QScrollArea()
        self.dept_scroll.setWidgetResizable(True)
        self.dept_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.dept_scroll.setFrameShape(QtWidgets.QFrame.StyledPanel)

        self.dept_container = QtWidgets.QWidget()
        self.dept_layout = QtWidgets.QVBoxLayout()
        self.dept_layout.setContentsMargins(4, 4, 4, 4)
        self.dept_layout.setSpacing(2)
        self.dept_container.setLayout(self.dept_layout)
        self.dept_scroll.setWidget(self.dept_container)

        layout.addWidget(self.dept_scroll)

        return panel

    def _refresh_departments(self):
        """Refresh department checkboxes based on current context"""
        context = self.context_combo.currentText()
        if not context:
            return

        previously_checked = set(self._get_selected_departments())

        for checkbox in self.department_checkboxes.values():
            self.dept_layout.removeWidget(checkbox)
            checkbox.deleteLater()
        self.department_checkboxes.clear()

        while self.dept_layout.count():
            item = self.dept_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            departments = list_departments(context)

            for dept in departments:
                checkbox = QtWidgets.QCheckBox(dept.name)
                checkbox.stateChanged.connect(self._validate)
                checkbox.setChecked(dept.name in previously_checked)
                self.department_checkboxes[dept.name] = checkbox
                self.dept_layout.addWidget(checkbox)

            self.dept_layout.addStretch()

        except Exception as e:
            print(f"Error refreshing departments: {e}")

    def _set_widget_incomplete(self, widget, is_incomplete):
        """Apply blue border for incomplete/needs-input fields"""
        if is_incomplete:
            class_name = widget.__class__.__name__
            widget.setStyleSheet(f"{class_name} {{ border: 2px solid #3399ff; }}")
        else:
            widget.setStyleSheet("")

    def _set_widget_error(self, widget, has_error):
        """Apply red border for malformed/invalid input"""
        if has_error:
            class_name = widget.__class__.__name__
            widget.setStyleSheet(f"{class_name} {{ border: 2px solid #cc0000; }}")
        else:
            widget.setStyleSheet("")

    def clear(self):
        """Clear the editor"""
        self.name_field.clear()
        self.name_field.setReadOnly(False)
        self.context_combo.setCurrentIndex(0)
        self.context_combo.setEnabled(True)

        for checkbox in self.department_checkboxes.values():
            checkbox.setChecked(False)

        self.available_model.clear()
        self.members_model.clear()

        self._set_widget_incomplete(self.name_field, False)
        self._set_widget_incomplete(self.members_tree, False)
        self._set_widget_incomplete(self.dept_scroll, False)
        self._validation_errors = []

        self.is_new_group = False
        self.original_group = None
        self.current_group_name = None

    def load_new_group(self, name, context=None):
        """Load editor for creating a new group"""
        self.clear()
        self.is_new_group = True
        self.current_group_name = name
        self.name_field.setText(name)
        self.name_field.setReadOnly(False)

        if context:
            index = self.context_combo.findText(context)
            if index >= 0:
                self.context_combo.setCurrentIndex(index)

        self.context_combo.setEnabled(True)

        self._refresh_departments()
        self._refresh_available_entities()
        self._validate()

    def load_existing_group(self, group):
        """Load editor with existing group data"""
        self.clear()
        self.is_new_group = False
        self.original_group = group
        group_name = group.uri.segments[-1]
        self.current_group_name = group_name

        self.name_field.setText(group_name)
        self.name_field.setReadOnly(True)

        context = None
        if len(group.uri.segments) > 1:
            context = group.uri.segments[1]
            index = self.context_combo.findText(context)
            if index >= 0:
                self.context_combo.setCurrentIndex(index)

        self.context_combo.setEnabled(False)

        self._refresh_departments()

        for dept_name, checkbox in self.department_checkboxes.items():
            checkbox.setChecked(dept_name in group.departments)

        member_entities = []
        for member_uri in group.members:
            if len(member_uri.segments) >= 3:
                member_entities.append(str(member_uri))
        self.members_model.load_members(member_entities)

        self._refresh_available_entities()
        self._validate()

    def _on_context_changed(self):
        """Handle context change"""
        self._refresh_departments()
        self._refresh_available_entities()
        self._validate()

    def _refresh_available_entities(self):
        """Refresh the available entities tree based on selected context"""
        context = self.context_combo.currentText()
        if not context:
            self.available_model.clear()
            return

        root_uri_str = f'entity:/{context}'

        assigned_entities = self._get_entities_in_other_groups()
        current_members = set(self.members_model.get_member_entities())

        self.available_model.load_entities_from_uri(root_uri_str, assigned_entities, current_members)
        # Reconfigure header after model reload (model.clear() invalidates header column refs)
        header = self.available_tree.header()
        if header is not None and header.count() > 0:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self._expand_trees()

    def _expand_trees(self):
        """Expand all nodes in both tree views"""
        self.available_tree.expandAll()
        self.members_tree.expandAll()

    def _get_expanded_items(self, tree_view, model):
        """Get set of expanded item names"""
        expanded = set()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            item = model.itemFromIndex(index)
            if item and tree_view.isExpanded(index):
                expanded.add(item.text())
        return expanded

    def _restore_expanded_items(self, tree_view, model, expanded_names):
        """Restore expansion state for items by name"""
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            item = model.itemFromIndex(index)
            if item and item.text() in expanded_names:
                tree_view.expand(index)

    def _get_entities_in_other_groups(self):
        """Get set of entity URI strings already in other groups"""
        assigned = set()
        current_name = self.name_field.text().strip()

        shot_groups = list_groups('shots')
        asset_groups = list_groups('assets')
        all_groups = shot_groups + asset_groups

        for group in all_groups:
            if group.name == current_name:
                continue

            for member in group.members:
                assigned.add(str(member))

        return assigned

    def _on_add_members(self):
        """Add selected available entities to members tree"""
        selected_indexes = self.available_tree.selectedIndexes()

        if not selected_indexes:
            return

        items_to_add = []

        for index in selected_indexes:
            item = self.available_model.itemFromIndex(index)
            if item and item.isEnabled():
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    items_to_add.append(uri_str)
                else:
                    for row in range(item.rowCount()):
                        child = item.child(row)
                        if child:
                            child_uri = child.data(Qt.UserRole)
                            if child_uri and child_uri not in items_to_add:
                                items_to_add.append(child_uri)

        for uri_str in items_to_add:
            self.members_model.add_member(uri_str)

        # Preserve tree state before refresh
        expanded_available = self._get_expanded_items(self.available_tree, self.available_model)
        scroll_pos = self.available_tree.verticalScrollBar().value()

        self._refresh_available_entities()

        # Restore tree state after refresh
        self._restore_expanded_items(self.available_tree, self.available_model, expanded_available)
        self.available_tree.verticalScrollBar().setValue(scroll_pos)
        self.members_tree.expandAll()

        if len(self.members_model.get_member_entities()) > 0:
            self.context_combo.setEnabled(False)

        self._validate()

    def _on_remove_members(self):
        """Remove selected members from tree and add back to available"""
        selected_indexes = self.members_tree.selectedIndexes()

        if not selected_indexes:
            return

        indexes_to_remove = []
        for index in selected_indexes:
            item = self.members_model.itemFromIndex(index)
            if item:
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    indexes_to_remove.append(index)
                else:
                    for row in range(item.rowCount()):
                        child_index = self.members_model.index(row, 0, index)
                        if child_index.isValid():
                            indexes_to_remove.append(child_index)

        self.members_model.remove_members(indexes_to_remove)

        # Preserve tree state before refresh
        expanded_available = self._get_expanded_items(self.available_tree, self.available_model)
        scroll_pos = self.available_tree.verticalScrollBar().value()

        self._refresh_available_entities()

        # Restore tree state after refresh
        self._restore_expanded_items(self.available_tree, self.available_model, expanded_available)
        self.available_tree.verticalScrollBar().setValue(scroll_pos)
        self.members_tree.expandAll()

        if len(self.members_model.get_member_entities()) == 0 and self.is_new_group:
            self.context_combo.setEnabled(True)

        self._validate()

    def _get_selected_departments(self):
        """Get list of selected department names"""
        selected = []
        for dept_name, checkbox in self.department_checkboxes.items():
            if checkbox.isChecked():
                selected.append(dept_name)
        return selected

    def _validate(self):
        """Validate current group configuration and update UI styling"""
        errors = []

        name = self.name_field.text().strip()
        name_empty = not name
        name_duplicate = False
        if name_empty:
            errors.append("Group name is required")
        elif self.is_new_group:
            shot_groups = list_groups('shots')
            asset_groups = list_groups('assets')
            all_groups = shot_groups + asset_groups
            if any(g.name == name for g in all_groups):
                errors.append(f"Group '{name}' already exists")
                name_duplicate = True
        if name_duplicate:
            self._set_widget_error(self.name_field, True)
        elif name_empty:
            self._set_widget_incomplete(self.name_field, True)
        else:
            self._set_widget_incomplete(self.name_field, False)

        member_count = len(self.members_model.get_member_entities())
        members_empty = member_count == 0
        if members_empty:
            errors.append("Group must have at least one member")
        self._set_widget_incomplete(self.members_tree, members_empty)

        selected_depts = self._get_selected_departments()
        depts_empty = len(selected_depts) == 0
        if depts_empty:
            errors.append("Select at least one department")
        self._set_widget_incomplete(self.dept_scroll, depts_empty)

        is_valid = len(errors) == 0
        self._validation_errors = errors

        self.validation_changed.emit(is_valid, "")

    def is_valid(self):
        """Check if current configuration is valid"""
        name = self.name_field.text().strip()
        if not name:
            return False

        if len(self.members_model.get_member_entities()) == 0:
            return False

        if len(self._get_selected_departments()) == 0:
            return False

        return True

    def save_group(self):
        """Save the current group to configuration"""
        if not self.is_valid():
            error_list = "\n".join(f"• {e}" for e in self._validation_errors)
            QtWidgets.QMessageBox.warning(
                self,
                "Cannot Save Group",
                f"Please fix the following issues:\n\n{error_list}"
            )
            return False

        try:
            name = self.name_field.text().strip()
            context = self.context_combo.currentText()
            departments = self._get_selected_departments()

            member_uri_strings = self.members_model.get_member_entities()
            member_uris = [Uri.parse_unsafe(uri_str) for uri_str in member_uri_strings]

            if not self.is_new_group and self.original_group:
                remove_group(self.original_group.uri)

            add_group(context, name, member_uris, departments)

            self.group_saved.emit(name)
            return True

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Error Saving Group",
                f"Failed to save group: {str(e)}"
            )
            return False

    def cancel_edit(self):
        """Cancel editing"""
        self.clear()
        self.group_cancelled.emit()


class GroupEditorDialog(QtWidgets.QDialog):
    """Dialog wrapper for GroupEditorWidget"""

    group_saved = Signal(str)

    def __init__(self, api, group=None, context=None, parent=None):
        super().__init__(parent)
        self.api = api

        is_new = group is None
        title = "New Group" if is_new else f"Edit Group: {group.name}"
        self.setWindowTitle(title)
        self.resize(900, 600)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        self.editor = GroupEditorWidget(api, parent=self)
        self.editor.validation_changed.connect(self._on_validation_changed)
        layout.addWidget(self.editor, stretch=1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        self.save_button = button_box.button(QtWidgets.QDialogButtonBox.Save)
        self.save_button.setEnabled(False)
        button_box.accepted.connect(self._save_group)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        if is_new:
            name = ""
            if context:
                self.editor.load_new_group(name, context)
            else:
                self.editor.load_new_group(name, 'shots')
        else:
            self.editor.load_existing_group(group)

    def _on_validation_changed(self, is_valid, message):
        """Handle validation state changes"""
        self.save_button.setEnabled(is_valid)

    def _save_group(self):
        """Save and close dialog"""
        if self.editor.save_group():
            self.group_saved.emit(self.editor.current_group_name or "")
            self.accept()
