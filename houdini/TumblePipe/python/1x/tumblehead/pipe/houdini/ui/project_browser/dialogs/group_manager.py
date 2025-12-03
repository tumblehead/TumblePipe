from pathlib import Path

from qtpy import QtWidgets
from qtpy.QtCore import Qt
import hou

from tumblehead.pipe.houdini.ui.project_browser.models.group import GroupListModel
from tumblehead.pipe.houdini.ui.project_browser.dialogs.group_editor import GroupEditorWidget


class GroupManagerDialog(QtWidgets.QDialog):
    """Main dialog for managing entity groups"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.config_path = Path(hou.getenv('TH_CONFIG_PATH'))

        self.setWindowTitle("Entity Groups Manager")
        self.resize(1100, 700)

        self.group_list_model = GroupListModel(api)

        self.current_group_name = None

        self._create_ui()

        self.refresh_groups()

    def _create_ui(self):
        """Create the main UI layout"""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        splitter = QtWidgets.QSplitter(Qt.Horizontal)

        groups_panel = self._create_groups_panel()
        splitter.addWidget(groups_panel)

        editor_panel = self._create_editor_panel()
        splitter.addWidget(editor_panel)

        splitter.setSizes([250, 850])

        layout.addWidget(splitter)

    def _create_toolbar(self):
        """Create toolbar with action buttons"""
        toolbar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar.setLayout(layout)

        self.new_button = QtWidgets.QPushButton("New Group")
        self.new_button.clicked.connect(self.create_new_group)
        layout.addWidget(self.new_button)

        self.delete_button = QtWidgets.QPushButton("Delete Group")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_group)
        layout.addWidget(self.delete_button)

        layout.addStretch()

        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        layout.addWidget(self.close_button)

        return toolbar

    def _create_groups_panel(self):
        """Create the left panel with groups list"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        title = QtWidgets.QLabel("Groups")
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(title)

        self.groups_list = QtWidgets.QListView()
        self.groups_list.setModel(self.group_list_model)
        self.groups_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.groups_list.selectionModel().currentChanged.connect(self.on_group_selected)
        layout.addWidget(self.groups_list)

        return panel

    def _create_editor_panel(self):
        """Create the right panel with group editor"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        title = QtWidgets.QLabel("Group Editor")
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(title)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        self.editor_widget = GroupEditorWidget(self.api, parent=self)
        self.editor_widget.setEnabled(False)
        self.editor_widget.group_saved.connect(self.on_group_saved)
        self.editor_widget.group_cancelled.connect(self.on_group_cancelled)
        self.editor_widget.validation_changed.connect(self._on_validation_changed)
        layout.addWidget(self.editor_widget, stretch=1)

        buttons = self._create_action_buttons()
        layout.addWidget(buttons)

        return panel

    def _create_action_buttons(self):
        """Create save/cancel buttons"""
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)

        layout.addStretch()

        self.save_button = QtWidgets.QPushButton("Save")
        self.save_button.clicked.connect(self._save_group)
        self.save_button.setEnabled(False)
        layout.addWidget(self.save_button)

        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_edit)
        layout.addWidget(self.cancel_button)

        return widget

    def _on_validation_changed(self, is_valid, message):
        """Handle validation state changes from editor"""
        self.save_button.setEnabled(is_valid)

    def _save_group(self):
        """Save the current group"""
        if self.editor_widget.save_group():
            pass

    def _cancel_edit(self):
        """Cancel current edit"""
        self.editor_widget.cancel_edit()
        self.refresh_groups()

    def refresh_groups(self):
        """Refresh the groups list"""
        self.group_list_model.load_groups()

        self.groups_list.clearSelection()
        self.editor_widget.clear()
        self.editor_widget.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.current_group_name = None

    def create_new_group(self):
        """Create a new blank group and open editor"""
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "New Group",
            "Enter group name:"
        )

        if not accepted or not name:
            return

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
            self.save_button.setEnabled(False)
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

        result = QtWidgets.QMessageBox.question(
            self,
            "Delete Group",
            f"Are you sure you want to delete the group '{self.current_group_name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )

        if result != QtWidgets.QMessageBox.Yes:
            return

        try:
            shot_groups = self.api.config.list_groups('shots')
            asset_groups = self.api.config.list_groups('assets')
            all_groups = shot_groups + asset_groups
            group = next((g for g in all_groups if g.uri.segments[-1] == self.current_group_name), None)

            if not group:
                raise ValueError(f"Group '{self.current_group_name}' not found")

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
        if hasattr(self.api.config, '_cached_groups'):
            delattr(self.api.config, '_cached_groups')

        self.refresh_groups()

        hou.ui.displayMessage(f"Group '{group_name}' saved successfully.")

    def on_group_cancelled(self):
        """Handle group edit cancelled"""
        self.refresh_groups()

    def select_group_by_name(self, context, group_name):
        """Select a group by its context and name

        Args:
            context: Group context ('shots' or 'assets')
            group_name: The name of the group to select
        """
        for row in range(self.group_list_model.rowCount()):
            index = self.group_list_model.index(row, 0)
            group = self.group_list_model.data(index, Qt.UserRole)
            if group is None:
                continue

            if len(group.uri.segments) >= 3:
                group_context = group.uri.segments[1]
                name = group.uri.segments[2]
                if group_context == context and name == group_name:
                    self.groups_list.setCurrentIndex(index)
                    return True

        return False
