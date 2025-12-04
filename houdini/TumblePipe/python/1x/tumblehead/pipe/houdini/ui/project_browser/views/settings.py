from qtpy.QtCore import Signal
from qtpy import QtWidgets


class SettingsView(QtWidgets.QWidget):
    auto_refresh_changed = Signal(bool)

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

        # Add stretch to push everything to the top
        layout.addStretch()

    def _on_auto_refresh_changed(self):
        self.auto_refresh_changed.emit(self._auto_refresh_checkbox.isChecked())

    def get_auto_refresh_enabled(self):
        return self._auto_refresh_checkbox.isChecked()

    def set_auto_refresh_enabled(self, enabled):
        self._auto_refresh_checkbox.setChecked(enabled)
