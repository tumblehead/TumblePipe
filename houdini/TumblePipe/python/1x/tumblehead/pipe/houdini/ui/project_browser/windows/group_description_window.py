"""
Group Editor window.

Manages group definitions with hierarchical organization.
- Left panel: Groups tree (shots/assets as roots, groups as children)
- Right panel: Group editor panel (when group selected)

Changes are tracked until Save is clicked.
"""

from dataclasses import dataclass, field

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QFrame,
)

from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.groups import (
    Group,
    add_group,
    remove_group,
    get_group,
    list_groups,
)

from ..models.group import AvailableEntitiesModel, GroupMembersModel


@dataclass
class PendingGroupChanges:
    """Tracks all uncommitted changes in the group editor."""

    # Modified group data: group_uri_str -> (members_list, departments_list)
    modified_data: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)

    def has_changes(self) -> bool:
        """Check if there are any pending changes."""
        return bool(self.modified_data)

    def clear(self):
        """Clear all pending changes."""
        self.modified_data.clear()


class GroupsTreeModel(QStandardItemModel):
    """Model for hierarchical groups tree (shots/assets as roots)."""

    # Custom roles
    GroupUriRole = Qt.UserRole + 1
    ContextRole = Qt.UserRole + 2
    IsGroupRole = Qt.UserRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uri_to_item = {}

    def load_groups(self):
        """Load groups into hierarchical tree."""
        self.clear()
        self._uri_to_item = {}

        # Create context root items
        for context in ['shots', 'assets']:
            context_item = QStandardItem(context.upper())
            context_item.setEditable(False)
            context_item.setSelectable(False)
            context_item.setData(None, self.GroupUriRole)
            context_item.setData(context, self.ContextRole)
            context_item.setData(False, self.IsGroupRole)

            # Add groups under this context
            groups = list_groups(context)
            for group in sorted(groups, key=lambda g: g.name):
                group_item = self._build_group_item(group, context)
                context_item.appendRow(group_item)

            self.appendRow(context_item)

    def _build_group_item(self, group: Group, context: str) -> QStandardItem:
        """Build a tree item from a group."""
        item = QStandardItem(group.name)
        item.setEditable(False)
        item.setData(str(group.uri), self.GroupUriRole)
        item.setData(context, self.ContextRole)
        item.setData(True, self.IsGroupRole)

        # Tooltip with details
        member_count = len(group.members)
        dept_count = len(group.departments)
        item.setToolTip(
            f"Members: {member_count}\n"
            f"Departments: {', '.join(group.departments) if group.departments else 'None'}"
        )

        self._uri_to_item[str(group.uri)] = item
        return item

    def get_item_for_uri(self, uri: Uri) -> QStandardItem | None:
        """Get item for a URI."""
        return self._uri_to_item.get(str(uri))


class GroupEditorPanel(QWidget):
    """Panel for editing a group's members and departments."""

    group_changed = Signal()  # Emitted when changes are saved
    changes_made = Signal()   # Emitted when unsaved changes occur

    def __init__(self, api, parent=None):
        super().__init__(parent)

        self._api = api
        self._current_group: Group | None = None
        self._original_members: list[str] = []
        self._original_departments: list[str] = []
        self._has_changes = False
        self._department_checkboxes: dict[str, QCheckBox] = {}

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with group name
        self._header_label = QLabel("No group selected")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._header_label)

        # Main content area (horizontal)
        content_layout = QHBoxLayout()
        layout.addLayout(content_layout, stretch=1)

        # Left side: Available entities + transfer buttons + group members
        entities_layout = QHBoxLayout()
        content_layout.addLayout(entities_layout, stretch=3)

        # Available entities panel
        available_panel = QWidget()
        available_layout = QVBoxLayout(available_panel)
        available_layout.setContentsMargins(0, 0, 0, 0)
        available_label = QLabel("Available Entities")
        available_label.setStyleSheet("font-weight: bold;")
        available_layout.addWidget(available_label)

        self._available_model = AvailableEntitiesModel(self._api)
        self._available_tree = QTreeView()
        self._available_tree.setModel(self._available_model)
        self._available_tree.setHeaderHidden(True)
        self._available_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._available_tree.doubleClicked.connect(self._on_available_double_click)
        available_layout.addWidget(self._available_tree)
        entities_layout.addWidget(available_panel, stretch=1)

        # Transfer buttons
        button_panel = QWidget()
        button_layout = QVBoxLayout(button_panel)
        button_layout.addStretch()
        self._add_button = QPushButton("->")
        self._add_button.clicked.connect(self._add_to_group)
        self._add_button.setEnabled(False)
        button_layout.addWidget(self._add_button)
        self._remove_button = QPushButton("<-")
        self._remove_button.clicked.connect(self._remove_from_group)
        self._remove_button.setEnabled(False)
        button_layout.addWidget(self._remove_button)
        button_layout.addStretch()
        entities_layout.addWidget(button_panel)

        # Group members panel
        members_panel = QWidget()
        members_layout = QVBoxLayout(members_panel)
        members_layout.setContentsMargins(0, 0, 0, 0)
        members_label = QLabel("Group Members")
        members_label.setStyleSheet("font-weight: bold;")
        members_layout.addWidget(members_label)

        self._members_model = GroupMembersModel()
        self._members_tree = QTreeView()
        self._members_tree.setModel(self._members_model)
        self._members_tree.setHeaderHidden(True)
        self._members_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._members_tree.doubleClicked.connect(self._on_members_double_click)
        self._members_tree.selectionModel().selectionChanged.connect(
            self._on_members_selection_changed
        )
        members_layout.addWidget(self._members_tree)
        entities_layout.addWidget(members_panel, stretch=1)

        # Right side: Departments panel
        dept_panel = QWidget()
        dept_panel.setFixedWidth(150)
        dept_layout = QVBoxLayout(dept_panel)
        dept_layout.setContentsMargins(0, 0, 0, 0)

        dept_label = QLabel("Departments")
        dept_label.setStyleSheet("font-weight: bold;")
        dept_layout.addWidget(dept_label)

        self._dept_scroll = QScrollArea()
        self._dept_scroll.setWidgetResizable(True)
        self._dept_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_scroll.setFrameShape(QFrame.StyledPanel)

        self._dept_container = QWidget()
        self._dept_layout = QVBoxLayout()
        self._dept_layout.setContentsMargins(4, 4, 4, 4)
        self._dept_layout.setSpacing(2)
        self._dept_container.setLayout(self._dept_layout)
        self._dept_scroll.setWidget(self._dept_container)

        dept_layout.addWidget(self._dept_scroll)
        content_layout.addWidget(dept_panel)

    def set_group(self, group: Group | None):
        """Set the group to edit."""
        self._current_group = group

        if group is None:
            self._header_label.setText("No group selected")
            self._available_model.clear()
            self._members_model.clear()
            self._clear_departments()
            self._add_button.setEnabled(False)
            self._remove_button.setEnabled(False)
            self._original_members = []
            self._original_departments = []
            self._has_changes = False
            return

        # Show group name in header
        self._header_label.setText(f"GROUP: {group.name}")

        # Get context from group URI
        context = group.uri.segments[0] if group.uri.segments else 'shots'

        # Store original state
        self._original_members = [str(m) for m in group.members]
        self._original_departments = list(group.departments)

        # Load members
        self._members_model.load_members(self._original_members)

        # Load available entities (excluding current members and entities in other groups)
        self._refresh_available_entities(context)

        # Load departments for this context
        self._refresh_departments(context)

        # Check department checkboxes based on group
        for dept_name, checkbox in self._department_checkboxes.items():
            checkbox.setChecked(dept_name in group.departments)

        self._available_tree.expandAll()
        self._members_tree.expandAll()
        self._add_button.setEnabled(True)
        self._has_changes = False

    def _refresh_available_entities(self, context: str):
        """Refresh available entities for the context."""
        root_uri_str = f'entity:/{context}'

        # Get entities in other groups
        assigned_entities = self._get_entities_in_other_groups(context)

        # Current group members
        current_members = set(self._members_model.get_member_entities())

        self._available_model.load_entities_from_uri(
            root_uri_str, assigned_entities, current_members
        )
        self._available_tree.expandAll()

    def _get_entities_in_other_groups(self, context: str) -> set[str]:
        """Get entities assigned to other groups in this context."""
        assigned = set()
        current_name = self._current_group.name if self._current_group else None

        for group in list_groups(context):
            if group.name == current_name:
                continue
            for member in group.members:
                assigned.add(str(member))

        return assigned

    def _refresh_departments(self, context: str):
        """Refresh department checkboxes based on context."""
        # Clear existing
        self._clear_departments()

        try:
            departments = list_departments(context)

            for dept in departments:
                checkbox = QCheckBox(dept.name)
                checkbox.stateChanged.connect(self._on_department_changed)
                self._department_checkboxes[dept.name] = checkbox
                self._dept_layout.addWidget(checkbox)

            self._dept_layout.addStretch()

        except Exception as e:
            print(f"Error refreshing departments: {e}")

    def _clear_departments(self):
        """Clear department checkboxes."""
        for checkbox in self._department_checkboxes.values():
            self._dept_layout.removeWidget(checkbox)
            checkbox.deleteLater()
        self._department_checkboxes.clear()

        while self._dept_layout.count():
            item = self._dept_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_department_changed(self, state):
        """Handle department checkbox change."""
        self._mark_changed()

    def has_unsaved_changes(self) -> bool:
        return self._has_changes

    def save_changes(self) -> bool:
        """Save group changes. Returns True on success."""
        if self._current_group is None:
            return False

        try:
            # Get current data
            members = self._members_model.get_member_entities()
            departments = self._get_selected_departments()

            # Validate
            if len(members) == 0:
                QMessageBox.warning(
                    self, "Cannot Save",
                    "Group must have at least one member."
                )
                return False

            if len(departments) == 0:
                QMessageBox.warning(
                    self, "Cannot Save",
                    "Group must have at least one department."
                )
                return False

            # Get context and name from current group
            context = self._current_group.uri.segments[0]
            name = self._current_group.name

            # Remove old group and add new one with updated data
            remove_group(self._current_group.uri)
            member_uris = [Uri.parse_unsafe(uri_str) for uri_str in members]
            add_group(context, name, member_uris, departments)

            # Update original state
            self._original_members = members
            self._original_departments = departments
            self._has_changes = False
            self.group_changed.emit()
            return True

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save group: {e}")
            return False

    def discard_changes(self):
        """Discard changes and reload."""
        if self._current_group:
            # Reload the group from config
            group = get_group(self._current_group.uri)
            self.set_group(group)

    def _get_selected_departments(self) -> list[str]:
        """Get list of selected department names."""
        selected = []
        for dept_name, checkbox in self._department_checkboxes.items():
            if checkbox.isChecked():
                selected.append(dept_name)
        return selected

    def _get_selected_available(self) -> list[str]:
        """Get selected available entity URIs."""
        selected = []
        for index in self._available_tree.selectedIndexes():
            item = self._available_model.itemFromIndex(index)
            if item:
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    selected.append(uri_str)
        return selected

    def _get_selected_members(self) -> list:
        """Get selected member indexes."""
        return list(self._members_tree.selectedIndexes())

    def _add_to_group(self):
        """Add selected entities to group."""
        for uri_str in self._get_selected_available():
            self._members_model.add_member(uri_str)
        self._refresh_after_transfer()
        self._mark_changed()

    def _remove_from_group(self):
        """Remove selected members from group."""
        indexes = self._get_selected_members()
        self._members_model.remove_members(indexes)
        self._refresh_after_transfer()
        self._mark_changed()

    def _on_available_double_click(self, index):
        """Handle double-click on available entity."""
        item = self._available_model.itemFromIndex(index)
        if item:
            uri_str = item.data(Qt.UserRole)
            if uri_str:
                self._members_model.add_member(uri_str)
                self._refresh_after_transfer()
                self._mark_changed()

    def _on_members_double_click(self, index):
        """Handle double-click on group member."""
        item = self._members_model.itemFromIndex(index)
        if item:
            uri_str = item.data(Qt.UserRole)
            if uri_str:
                self._members_model.remove_members([index])
                self._refresh_after_transfer()
                self._mark_changed()

    def _on_members_selection_changed(self):
        """Handle member selection change."""
        selected = self._get_selected_members()
        # Only enable remove if actual members (with URIs) are selected
        has_members = any(
            self._members_model.itemFromIndex(idx).data(Qt.UserRole)
            for idx in selected
            if self._members_model.itemFromIndex(idx)
        )
        self._remove_button.setEnabled(has_members)

    def _refresh_after_transfer(self):
        """Refresh available entities after transfer."""
        if self._current_group:
            context = self._current_group.uri.segments[0]
            self._refresh_available_entities(context)
        self._members_tree.expandAll()

    def _mark_changed(self):
        """Mark that changes have been made."""
        current_members = self._members_model.get_member_entities()
        current_departments = self._get_selected_departments()

        self._has_changes = (
            set(current_members) != set(self._original_members) or
            set(current_departments) != set(self._original_departments)
        )
        self.changes_made.emit()


class GroupDescriptionWindow(QMainWindow):
    """Group editor window with hierarchical groups tree."""

    data_changed = Signal(object)
    window_closed = Signal()

    def __init__(self, api, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.Window |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint
        )

        self.setWindowTitle("Group Editor")
        self.resize(1000, 600)

        self._api = api
        self._current_group_uri: Uri | None = None
        self._pending_changes = PendingGroupChanges()

        self._build_ui()
        self._load_groups()

    def _build_ui(self):
        central_widget = QSplitter(Qt.Horizontal, self)
        self.setCentralWidget(central_widget)

        # Left panel: Navigation
        nav_widget = QWidget()
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 0)

        # Groups section
        groups_label = QLabel("GROUPS")
        groups_label.setStyleSheet("font-weight: bold;")
        nav_layout.addWidget(groups_label)

        # Groups tree (hierarchical)
        self._groups_model = GroupsTreeModel()
        self._groups_tree = QTreeView()
        self._groups_tree.setModel(self._groups_model)
        self._groups_tree.setHeaderHidden(True)
        self._groups_tree.setSelectionMode(QTreeView.SingleSelection)
        self._groups_tree.selectionModel().selectionChanged.connect(
            self._on_group_selected
        )
        # Context menu for add/remove
        self._groups_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._groups_tree.customContextMenuRequested.connect(
            self._on_groups_context_menu
        )
        nav_layout.addWidget(self._groups_tree)

        central_widget.addWidget(nav_widget)

        # Right panel: Editor (stacked)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._editor_stack = QStackedWidget()

        # Empty panel
        empty_panel = QWidget()
        empty_layout = QVBoxLayout(empty_panel)
        empty_layout.addStretch()
        empty_label = QLabel("Select a group to edit its members and departments\n\n"
                             "Right-click on the tree to create or delete groups")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet("color: #888;")
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch()
        self._editor_stack.addWidget(empty_panel)

        # Group editor panel
        self._group_editor = GroupEditorPanel(self._api)
        self._group_editor.group_changed.connect(self._on_group_data_changed)
        self._group_editor.changes_made.connect(self._update_buttons)
        self._editor_stack.addWidget(self._group_editor)

        right_layout.addWidget(self._editor_stack, stretch=1)

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(5, 5, 5, 5)

        self._discard_button = QPushButton("Discard Changes")
        self._discard_button.clicked.connect(self._on_discard_changes)
        self._discard_button.setEnabled(False)
        button_layout.addWidget(self._discard_button)

        button_layout.addStretch()

        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self._on_save_changes)
        self._save_button.setEnabled(False)
        button_layout.addWidget(self._save_button)

        right_layout.addLayout(button_layout)

        central_widget.addWidget(right_widget)
        central_widget.setSizes([250, 750])

    def _load_groups(self):
        """Load groups into the tree."""
        self._groups_model.load_groups()
        self._groups_tree.expandAll()

    def _on_groups_context_menu(self, point):
        """Show context menu for groups tree."""
        index = self._groups_tree.indexAt(point)
        menu = QMenu(self)

        if not index.isValid():
            # Clicked on empty area - offer to create group
            new_shots_action = menu.addAction("New Shots Group...")
            new_assets_action = menu.addAction("New Assets Group...")
            selected = menu.exec_(self._groups_tree.mapToGlobal(point))
            if selected == new_shots_action:
                self._on_add_group('shots')
            elif selected == new_assets_action:
                self._on_add_group('assets')
            return

        item = self._groups_model.itemFromIndex(index)
        if item is None:
            return

        is_group = item.data(GroupsTreeModel.IsGroupRole)
        context = item.data(GroupsTreeModel.ContextRole)

        if is_group:
            # Right-clicked on a group
            delete_action = menu.addAction("Delete Group")
            selected = menu.exec_(self._groups_tree.mapToGlobal(point))
            if selected == delete_action:
                self._on_remove_group()
        else:
            # Right-clicked on a context (shots/assets)
            new_action = menu.addAction(f"New {context.title()} Group...")
            selected = menu.exec_(self._groups_tree.mapToGlobal(point))
            if selected == new_action:
                self._on_add_group(context)

    def _on_add_group(self, context: str):
        """Create a new group."""
        name, ok = QInputDialog.getText(
            self, f"New {context.title()} Group",
            "Group name:"
        )
        if not ok or not name.strip():
            return

        name = name.strip()

        # Check if name already exists
        existing_groups = list_groups(context)
        if any(g.name == name for g in existing_groups):
            QMessageBox.warning(
                self, "Error",
                f"A group named '{name}' already exists in {context}."
            )
            return

        try:
            # Create group with empty members and departments (user will add them)
            group_uri = add_group(context, name, [], [])
            self._load_groups()

            # Select the new group
            item = self._groups_model.get_item_for_uri(group_uri)
            if item:
                index = self._groups_model.indexFromItem(item)
                self._groups_tree.setCurrentIndex(index)

        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_remove_group(self):
        """Delete the selected group."""
        indexes = self._groups_tree.selectedIndexes()
        if not indexes:
            return

        item = self._groups_model.itemFromIndex(indexes[0])
        if item is None:
            return

        is_group = item.data(GroupsTreeModel.IsGroupRole)
        if not is_group:
            return

        group_uri_str = item.data(GroupsTreeModel.GroupUriRole)
        group_name = item.text()

        # Confirm deletion
        result = QMessageBox.question(
            self, "Delete Group",
            f"Are you sure you want to delete the group '{group_name}'?"
        )
        if result != QMessageBox.Yes:
            return

        try:
            group_uri = Uri.parse_unsafe(group_uri_str)
            remove_group(group_uri)
            self._load_groups()
            self._group_editor.set_group(None)
            self._editor_stack.setCurrentIndex(0)
            self._current_group_uri = None
            self.data_changed.emit(group_uri)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete group: {e}")

    def _check_unsaved_changes(self) -> bool:
        """Check for unsaved changes. Returns True if ok to proceed."""
        if self._group_editor.has_unsaved_changes():
            result = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Do you want to save them?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            if result == QMessageBox.Cancel:
                return False
            elif result == QMessageBox.Save:
                self._on_save_changes()
            # Discard falls through
        return True

    def _on_group_selected(self, selected, deselected):
        """Handle group selection."""
        if not self._check_unsaved_changes():
            # Restore previous selection
            if self._current_group_uri:
                item = self._groups_model.get_item_for_uri(self._current_group_uri)
                if item:
                    index = self._groups_model.indexFromItem(item)
                    self._groups_tree.selectionModel().blockSignals(True)
                    self._groups_tree.setCurrentIndex(index)
                    self._groups_tree.selectionModel().blockSignals(False)
            return

        indexes = self._groups_tree.selectedIndexes()
        if not indexes:
            return

        item = self._groups_model.itemFromIndex(indexes[0])
        if item is None:
            return

        is_group = item.data(GroupsTreeModel.IsGroupRole)
        if not is_group:
            # Selected a context node, not a group
            self._group_editor.set_group(None)
            self._editor_stack.setCurrentIndex(0)
            self._current_group_uri = None
            self._update_buttons()
            return

        group_uri_str = item.data(GroupsTreeModel.GroupUriRole)
        group_uri = Uri.parse_unsafe(group_uri_str)
        self._current_group_uri = group_uri

        group = get_group(group_uri)
        self._group_editor.set_group(group)
        self._editor_stack.setCurrentIndex(1)
        self._update_buttons()

    def _on_group_data_changed(self):
        """Handle group data change signal."""
        self._load_groups()
        if self._current_group_uri:
            self.data_changed.emit(self._current_group_uri)

    def _update_buttons(self):
        """Update button states."""
        has_changes = self._group_editor.has_unsaved_changes()
        self._discard_button.setEnabled(has_changes)
        self._save_button.setEnabled(has_changes)

    def _on_discard_changes(self):
        """Discard all pending changes."""
        result = QMessageBox.question(
            self, "Discard Changes",
            "Are you sure you want to discard all changes?"
        )
        if result != QMessageBox.Yes:
            return

        self._group_editor.discard_changes()
        self._update_buttons()

    def _on_save_changes(self):
        """Save all pending changes."""
        if self._group_editor.save_changes():
            self._load_groups()
            self._update_buttons()

    def closeEvent(self, event):
        """Handle window close."""
        if not self._check_unsaved_changes():
            event.ignore()
            return

        self.window_closed.emit()
        event.accept()
