from qtpy.QtCore import Signal
from qtpy import QtWidgets

from tumblehead.api import default_client
from tumblehead.apps.deadline import Deadline
from tumblehead.util.uri import Uri

api = default_client()


class SettingsView(QtWidgets.QWidget):
    auto_refresh_changed = Signal(bool)
    auto_propagate_changed = Signal(bool)
    propagation_priority_changed = Signal(int)
    propagation_pool_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Settings
        self.setMinimumHeight(0)

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Create auto refresh settings group
        refresh_group = QtWidgets.QGroupBox("Auto Refresh")
        refresh_layout = QtWidgets.QVBoxLayout()
        refresh_layout.setSpacing(5)
        refresh_group.setLayout(refresh_layout)
        layout.addWidget(refresh_group)

        # Create auto refresh checkbox
        self._auto_refresh_checkbox = QtWidgets.QCheckBox("Automatically refresh dependencies when opening scenes")
        self._auto_refresh_checkbox.setChecked(True)
        self._auto_refresh_checkbox.stateChanged.connect(self._on_auto_refresh_changed)
        refresh_layout.addWidget(self._auto_refresh_checkbox)

        # Add help text
        help_label = QtWidgets.QLabel("When enabled, dependencies will be automatically updated when switching departments.")
        help_label.setStyleSheet("color: #919191; font-size: 9px;")
        help_label.setWordWrap(True)
        refresh_layout.addWidget(help_label)

        # Create auto propagate settings group
        propagate_group = QtWidgets.QGroupBox("Auto Propagate")
        propagate_layout = QtWidgets.QVBoxLayout()
        propagate_layout.setSpacing(5)
        propagate_group.setLayout(propagate_layout)
        layout.addWidget(propagate_group)

        # Create auto propagate checkbox
        self._auto_propagate_checkbox = QtWidgets.QCheckBox("Automatically propagate publishes to downstream departments and shots")
        self._auto_propagate_checkbox.setChecked(True)
        self._auto_propagate_checkbox.stateChanged.connect(self._on_auto_propagate_changed)
        propagate_layout.addWidget(self._auto_propagate_checkbox)

        # Add help text for propagate
        propagate_help_label = QtWidgets.QLabel("When enabled, publishing an entity will automatically republish dependent departments and rebuild affected shots.")
        propagate_help_label.setStyleSheet("color: #919191; font-size: 9px;")
        propagate_help_label.setWordWrap(True)
        propagate_layout.addWidget(propagate_help_label)

        # Create propagation priority setting
        priority_layout = QtWidgets.QHBoxLayout()
        priority_layout.setSpacing(10)
        priority_label = QtWidgets.QLabel("Propagation Priority:")
        priority_label.setFixedWidth(120)
        priority_layout.addWidget(priority_label)
        priority_layout.addStretch()

        self._propagation_priority_spinbox = QtWidgets.QSpinBox()
        self._propagation_priority_spinbox.setMinimum(0)
        self._propagation_priority_spinbox.setMaximum(100)
        self._propagation_priority_spinbox.setValue(50)
        self._propagation_priority_spinbox.valueChanged.connect(self._on_propagation_priority_changed)
        priority_layout.addWidget(self._propagation_priority_spinbox)
        propagate_layout.addLayout(priority_layout)

        # Create propagation pool setting
        pool_layout = QtWidgets.QHBoxLayout()
        pool_layout.setSpacing(10)
        pool_label = QtWidgets.QLabel("Propagation Pool:")
        pool_label.setFixedWidth(120)
        pool_layout.addWidget(pool_label)
        pool_layout.addStretch()

        self._propagation_pool_combobox = QtWidgets.QComboBox()
        pool_names = self._list_pool_names()
        if len(pool_names) > 0:
            self._propagation_pool_combobox.addItems(pool_names)
        else:
            self._propagation_pool_combobox.addItems(["general"])
        self._propagation_pool_combobox.currentTextChanged.connect(self._on_propagation_pool_changed)
        pool_layout.addWidget(self._propagation_pool_combobox)
        propagate_layout.addLayout(pool_layout)

        # Add stretch to push everything to the top
        layout.addStretch()

    def _list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        defaults_uri = Uri.parse_unsafe('defaults:/houdini/lops/submit_render')
        default_values = api.config.get_properties(defaults_uri)
        if default_values is None: return []
        if 'pools' not in default_values: return []
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]

    def refresh_pool_list(self):
        pool_names = self._list_pool_names()
        current_pool = self._propagation_pool_combobox.currentText()
        self._propagation_pool_combobox.clear()
        if len(pool_names) > 0:
            self._propagation_pool_combobox.addItems(pool_names)
            if current_pool in pool_names:
                self._propagation_pool_combobox.setCurrentText(current_pool)

    def _on_auto_refresh_changed(self):
        self.auto_refresh_changed.emit(self._auto_refresh_checkbox.isChecked())

    def _on_auto_propagate_changed(self):
        self.auto_propagate_changed.emit(self._auto_propagate_checkbox.isChecked())

    def _on_propagation_priority_changed(self):
        self.propagation_priority_changed.emit(self._propagation_priority_spinbox.value())

    def _on_propagation_pool_changed(self):
        self.propagation_pool_changed.emit(self._propagation_pool_combobox.currentText())

    def get_auto_refresh_enabled(self):
        return self._auto_refresh_checkbox.isChecked()

    def set_auto_refresh_enabled(self, enabled):
        self._auto_refresh_checkbox.setChecked(enabled)

    def get_auto_propagate_enabled(self):
        return self._auto_propagate_checkbox.isChecked()

    def set_auto_propagate_enabled(self, enabled):
        self._auto_propagate_checkbox.setChecked(enabled)

    def get_propagation_priority(self):
        return self._propagation_priority_spinbox.value()

    def set_propagation_priority(self, priority):
        self._propagation_priority_spinbox.setValue(priority)

    def get_propagation_pool(self):
        return self._propagation_pool_combobox.currentText()

    def set_propagation_pool(self, pool):
        index = self._propagation_pool_combobox.findText(pool)
        if index >= 0:
            self._propagation_pool_combobox.setCurrentIndex(index)