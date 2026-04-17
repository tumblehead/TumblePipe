from qtpy.QtCore import Signal
from qtpy import QtWidgets

from ..dialogs.usd_viewer_settings import show_usd_viewer_settings


class SettingsView(QtWidgets.QWidget):
    auto_refresh_changed = Signal(bool)
    rebuild_nodes_changed = Signal(bool)

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

        # Create rebuild nodes checkbox
        self._rebuild_nodes_checkbox = QtWidgets.QCheckBox("Rebuild import/export nodes when refreshing")
        self._rebuild_nodes_checkbox.setChecked(False)
        self._rebuild_nodes_checkbox.stateChanged.connect(self._on_rebuild_nodes_changed)
        refresh_layout.addWidget(self._rebuild_nodes_checkbox)

        # Add help text for rebuild
        rebuild_help_label = QtWidgets.QLabel("When enabled, import/export nodes will be recreated to use latest HDA definitions.")
        rebuild_help_label.setStyleSheet("color: #919191; font-size: 9px;")
        rebuild_help_label.setWordWrap(True)
        refresh_layout.addWidget(rebuild_help_label)

        # Create USD viewer settings group
        usd_group = QtWidgets.QGroupBox("USD Viewers")
        usd_layout = QtWidgets.QVBoxLayout()
        usd_layout.setSpacing(5)
        usd_group.setLayout(usd_layout)
        layout.addWidget(usd_group)

        # Add description
        usd_desc = QtWidgets.QLabel(
            "Configure external USD viewers (3D-Info, USD Manager, usdview) for viewing "
            "USD files outside Houdini."
        )
        usd_desc.setWordWrap(True)
        usd_desc.setStyleSheet("color: #919191; font-size: 9px;")
        usd_layout.addWidget(usd_desc)

        # Add configure button
        self._configure_usd_button = QtWidgets.QPushButton("Configure USD Viewers...")
        self._configure_usd_button.clicked.connect(self._on_configure_usd_viewers)
        usd_layout.addWidget(self._configure_usd_button)

        # Add stretch to push everything to the top
        layout.addStretch()

    def _on_auto_refresh_changed(self):
        self.auto_refresh_changed.emit(self._auto_refresh_checkbox.isChecked())

    def _on_rebuild_nodes_changed(self):
        self.rebuild_nodes_changed.emit(self._rebuild_nodes_checkbox.isChecked())

    def _on_configure_usd_viewers(self):
        """Open USD viewer configuration dialog."""
        show_usd_viewer_settings(self)

    def get_auto_refresh_enabled(self):
        return self._auto_refresh_checkbox.isChecked()

    def set_auto_refresh_enabled(self, enabled):
        self._auto_refresh_checkbox.setChecked(enabled)

    def get_rebuild_nodes_enabled(self):
        return self._rebuild_nodes_checkbox.isChecked()

    def set_rebuild_nodes_enabled(self, enabled):
        self._rebuild_nodes_checkbox.setChecked(enabled)
