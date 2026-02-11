from qtpy import QtWidgets, QtCore, QtGui

from tumblehead.api import default_client
from tumblehead.config.department import (
    Department, add_department, list_departments,
    set_independent, set_publishable, set_renderable, set_enabled
)

api = default_client()


class DepartmentListPanel(QtWidgets.QWidget):
    """Panel for managing departments in a single context (shots or assets)"""

    def __init__(self, context: str, parent=None):
        super().__init__(parent)
        self._context = context
        self._current_department: Department | None = None
        self._updating_ui = False  # Flag to prevent feedback loops
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        # Left panel - list and buttons
        left_panel = QtWidgets.QVBoxLayout()

        self._show_disabled_checkbox = QtWidgets.QCheckBox("Show disabled departments")
        self._show_disabled_checkbox.stateChanged.connect(self.refresh)
        left_panel.addWidget(self._show_disabled_checkbox)

        self._list = QtWidgets.QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_panel.addWidget(self._list)

        button_row = QtWidgets.QHBoxLayout()
        self._add_button = QtWidgets.QPushButton("Add")
        self._add_button.clicked.connect(self._on_add_clicked)
        self._toggle_button = QtWidgets.QPushButton("Disable")
        self._toggle_button.clicked.connect(self._on_toggle_clicked)
        self._toggle_button.setEnabled(False)
        button_row.addWidget(self._add_button)
        button_row.addWidget(self._toggle_button)
        button_row.addStretch()
        left_panel.addLayout(button_row)

        layout.addLayout(left_panel, stretch=1)

        # Right panel - properties
        self._property_panel = self._create_property_panel()
        layout.addWidget(self._property_panel, stretch=1)

    def _create_property_panel(self):
        group = QtWidgets.QGroupBox("Properties")
        layout = QtWidgets.QVBoxLayout()
        group.setLayout(layout)

        # Independent checkbox
        self._independent_checkbox = QtWidgets.QCheckBox("Independent")
        self._independent_checkbox.stateChanged.connect(
            lambda s: self._on_property_changed('independent', s == QtCore.Qt.Checked))
        layout.addWidget(self._independent_checkbox)
        layout.addWidget(self._create_help_label("Department can work independently of other departments"))

        layout.addSpacing(10)

        # Publishable checkbox
        self._publishable_checkbox = QtWidgets.QCheckBox("Publishable")
        self._publishable_checkbox.stateChanged.connect(
            lambda s: self._on_property_changed('publishable', s == QtCore.Qt.Checked))
        layout.addWidget(self._publishable_checkbox)
        layout.addWidget(self._create_help_label("Department outputs can be published"))

        layout.addSpacing(10)

        # Renderable checkbox
        self._renderable_checkbox = QtWidgets.QCheckBox("Renderable")
        self._renderable_checkbox.stateChanged.connect(
            lambda s: self._on_property_changed('renderable', s == QtCore.Qt.Checked))
        layout.addWidget(self._renderable_checkbox)
        layout.addWidget(self._create_help_label("Department supports rendering operations"))

        layout.addSpacing(10)

        # Enabled checkbox
        self._enabled_checkbox = QtWidgets.QCheckBox("Enabled")
        self._enabled_checkbox.stateChanged.connect(
            lambda s: self._on_property_changed('enabled', s == QtCore.Qt.Checked))
        layout.addWidget(self._enabled_checkbox)
        layout.addWidget(self._create_help_label("Disabled departments are hidden from normal use"))

        layout.addSpacing(10)

        # Generated label (read-only)
        self._generated_label = QtWidgets.QLabel("Generated: No")
        layout.addWidget(self._generated_label)
        layout.addWidget(self._create_help_label("Python-generated departments cannot be modified"))

        layout.addStretch()

        # Disable all controls initially
        self._set_property_panel_enabled(False)

        return group

    def _create_help_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("color: #919191; font-size: 9px;")
        label.setWordWrap(True)
        return label

    def _set_property_panel_enabled(self, enabled: bool):
        """Enable or disable the property panel controls."""
        self._independent_checkbox.setEnabled(enabled)
        self._publishable_checkbox.setEnabled(enabled)
        self._renderable_checkbox.setEnabled(enabled)
        self._enabled_checkbox.setEnabled(enabled)

    def refresh(self):
        """Reload department list from config."""
        # Refresh the cache first
        api.config.refresh_cache('departments')

        # Remember current selection
        current_name = None
        if self._current_department:
            current_name = self._current_department.name

        self._list.clear()
        include_disabled = self._show_disabled_checkbox.isChecked()
        departments = list_departments(self._context, include_disabled=include_disabled)

        for dept in departments:
            suffix = ""
            if not dept.enabled:
                suffix = " (disabled)"
            elif dept.generated:
                suffix = " (generated)"
            item = QtWidgets.QListWidgetItem(dept.name + suffix)
            item.setData(QtCore.Qt.UserRole, dept)
            if not dept.enabled or dept.generated:
                item.setForeground(QtGui.QColor("#919191"))
            self._list.addItem(item)

        # Restore selection if possible
        if current_name:
            for i in range(self._list.count()):
                item = self._list.item(i)
                dept = item.data(QtCore.Qt.UserRole)
                if dept.name == current_name:
                    self._list.setCurrentRow(i)
                    break

    def _on_selection_changed(self, current, previous):
        """Update property panel when selection changes."""
        if current is None:
            self._current_department = None
            self._set_property_panel_enabled(False)
            self._toggle_button.setEnabled(False)
            return

        dept: Department = current.data(QtCore.Qt.UserRole)
        self._current_department = dept

        # Update property panel
        self._updating_ui = True
        try:
            self._independent_checkbox.setChecked(dept.independent)
            self._publishable_checkbox.setChecked(dept.publishable)
            self._renderable_checkbox.setChecked(dept.renderable)
            self._enabled_checkbox.setChecked(dept.enabled)
            self._generated_label.setText(f"Generated: {'Yes' if dept.generated else 'No'}")

            # Enable/disable based on whether department is generated
            editable = not dept.generated
            self._set_property_panel_enabled(editable)

            # Update toggle button
            self._toggle_button.setEnabled(editable)
            self._toggle_button.setText("Enable" if not dept.enabled else "Disable")
        finally:
            self._updating_ui = False

    def _on_add_clicked(self):
        """Show input dialog and create new department."""
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Add Department",
            f"Enter new department name for {self._context}:")

        if not ok or not name.strip():
            return

        name = name.strip()

        # Check for duplicate (including disabled departments)
        existing = [d.name for d in list_departments(self._context, include_disabled=True)]
        if name in existing:
            QtWidgets.QMessageBox.warning(
                self, "Error",
                f"Department '{name}' already exists in {self._context}.")
            return

        # Create with defaults
        try:
            add_department(
                self._context, name,
                independent=False,
                publishable=True,
                renderable=False,
                generated=False,
                enabled=True
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Error",
                f"Failed to create department: {e}")
            return

        self.refresh()

        # Select the new department
        for i in range(self._list.count()):
            item = self._list.item(i)
            dept = item.data(QtCore.Qt.UserRole)
            if dept.name == name:
                self._list.setCurrentRow(i)
                break

    def _on_toggle_clicked(self):
        """Toggle enabled state of selected department."""
        if self._current_department is None:
            return

        if self._current_department.generated:
            QtWidgets.QMessageBox.warning(
                self, "Cannot Modify",
                "Generated departments cannot be disabled.")
            return

        new_enabled = not self._current_department.enabled
        set_enabled(self._context, self._current_department.name, new_enabled)
        self.refresh()

    def _on_property_changed(self, property_name: str, value: bool):
        """Save property change immediately."""
        if self._updating_ui or self._current_department is None:
            return

        if self._current_department.generated:
            return

        name = self._current_department.name

        if property_name == 'independent':
            set_independent(self._context, name, value)
        elif property_name == 'publishable':
            set_publishable(self._context, name, value)
        elif property_name == 'renderable':
            set_renderable(self._context, name, value)
        elif property_name == 'enabled':
            set_enabled(self._context, name, value)
            # Update toggle button text
            self._toggle_button.setText("Enable" if not value else "Disable")

        # Refresh to update the cached department object
        self.refresh()


class DepartmentDialog(QtWidgets.QDialog):
    """Dialog for managing departments across shots and assets contexts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Departments")
        self.setMinimumSize(600, 400)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Tab widget for shots and assets
        tabs = QtWidgets.QTabWidget()
        self._shots_panel = DepartmentListPanel('shots')
        self._assets_panel = DepartmentListPanel('assets')
        tabs.addTab(self._shots_panel, "Shots")
        tabs.addTab(self._assets_panel, "Assets")
        layout.addWidget(tabs)

        # Close button
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        button_box.rejected.connect(self.close)
        layout.addWidget(button_box)


class AssetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)


class ShotDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)


class PresetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)


class DefaultsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)


class ProjectConfig(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.layout = QtWidgets.QVBoxLayout()
        self.setLayout(self.layout)

        # Edit departments button
        self.edit_departments_button = QtWidgets.QPushButton('Edit Departments')
        self.layout.addWidget(self.edit_departments_button)
        self.edit_departments_button.clicked.connect(self.edit_departments)

        # Edit assets button
        self.edit_assets_button = QtWidgets.QPushButton('Edit Assets')
        self.layout.addWidget(self.edit_assets_button)
        self.edit_assets_button.clicked.connect(self.edit_assets)

        # Edit shots button
        self.edit_shots_button = QtWidgets.QPushButton('Edit Shots')
        self.layout.addWidget(self.edit_shots_button)
        self.edit_shots_button.clicked.connect(self.edit_shots)

        # Edit defaults button
        self.edit_defaults_button = QtWidgets.QPushButton('Edit Defaults')
        self.layout.addWidget(self.edit_defaults_button)
        self.edit_defaults_button.clicked.connect(self.edit_defaults)

        # Add stretch
        self.layout.addStretch()

    def edit_departments(self):
        department_dialog = DepartmentDialog(self)
        department_dialog.exec_()

    def edit_assets(self):
        asset_dialog = AssetDialog(self)
        asset_dialog.exec_()

    def edit_shots(self):
        shot_dialog = ShotDialog(self)
        shot_dialog.exec_()

    def edit_defaults(self):
        defaults_dialog = DefaultsDialog(self)
        defaults_dialog.exec_()


def create():
    widget = ProjectConfig()
    return widget
