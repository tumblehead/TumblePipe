"""USD Viewer Settings Dialog."""

from pathlib import Path
from typing import Optional

from qtpy import QtWidgets, QtCore, QtGui

from ..viewers.usd_viewer import USDViewerLauncher, USDViewerType


class USDViewerSettingsDialog(QtWidgets.QDialog):
    """Dialog for configuring USD viewer settings.

    Allows users to:
    - Set paths to viewer executables (3D-Info, USD Manager, usdview)
    - Choose preferred default viewer
    - Test viewer configurations
    """

    def __init__(self, parent=None):
        """Initialize the USD viewer settings dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("USD Viewer Settings")
        self.setMinimumWidth(600)

        self._launcher = USDViewerLauncher()
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        """Setup the user interface."""
        layout = QtWidgets.QVBoxLayout(self)

        # Header
        header_label = QtWidgets.QLabel(
            "Configure external USD viewers for viewing USD files outside Houdini."
        )
        header_label.setWordWrap(True)
        layout.addWidget(header_label)

        layout.addSpacing(10)

        # Viewer paths group
        paths_group = QtWidgets.QGroupBox("Viewer Executable Paths")
        paths_layout = QtWidgets.QFormLayout(paths_group)

        # 3D-Info path
        self._three_d_info_path = QtWidgets.QLineEdit()
        three_d_info_browse = QtWidgets.QPushButton("Browse...")
        three_d_info_browse.clicked.connect(lambda: self._browse_viewer(USDViewerType.THREE_D_INFO))
        three_d_info_layout = QtWidgets.QHBoxLayout()
        three_d_info_layout.addWidget(self._three_d_info_path)
        three_d_info_layout.addWidget(three_d_info_browse)
        paths_layout.addRow("3D-Info:", three_d_info_layout)

        # USD Manager path
        self._usd_manager_path = QtWidgets.QLineEdit()
        usd_manager_browse = QtWidgets.QPushButton("Browse...")
        usd_manager_browse.clicked.connect(lambda: self._browse_viewer(USDViewerType.USD_MANAGER))
        usd_manager_layout = QtWidgets.QHBoxLayout()
        usd_manager_layout.addWidget(self._usd_manager_path)
        usd_manager_layout.addWidget(usd_manager_browse)
        paths_layout.addRow("USD Manager:", usd_manager_layout)

        # usdview path
        self._usdview_path = QtWidgets.QLineEdit()
        usdview_browse = QtWidgets.QPushButton("Browse...")
        usdview_browse.clicked.connect(lambda: self._browse_viewer(USDViewerType.USDVIEW))
        usdview_layout = QtWidgets.QHBoxLayout()
        usdview_layout.addWidget(self._usdview_path)
        usdview_layout.addWidget(usdview_browse)
        paths_layout.addRow("usdview:", usdview_layout)

        layout.addWidget(paths_group)

        # Preferences group
        prefs_group = QtWidgets.QGroupBox("Preferences")
        prefs_layout = QtWidgets.QFormLayout(prefs_group)

        # Preferred viewer dropdown
        self._preferred_viewer = QtWidgets.QComboBox()
        self._preferred_viewer.addItem("3D-Info (Artist-Friendly)", USDViewerType.THREE_D_INFO)
        self._preferred_viewer.addItem("USD Manager (File Browser/Editor)", USDViewerType.USD_MANAGER)
        self._preferred_viewer.addItem("usdview (Pixar Official)", USDViewerType.USDVIEW)
        prefs_layout.addRow("Default Viewer:", self._preferred_viewer)

        layout.addWidget(prefs_group)

        # Help text
        help_label = QtWidgets.QLabel(
            "<b>Where to get viewers:</b><br>"
            "• <b>3D-Info:</b> Download from <a href='https://gitlab.com/3d-info/3d-info/-/releases'>GitLab</a> "
            "(Recommended for artists)<br>"
            "• <b>USD Manager:</b> Download from "
            "<a href='https://github.com/dreamworksanimation/usdmanager'>GitHub</a> "
            "(Recommended for TDs)<br>"
            "• <b>usdview:</b> Included with USD installation or Houdini"
        )
        help_label.setOpenExternalLinks(True)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 10px; border-radius: 5px; }")
        layout.addWidget(help_label)

        layout.addStretch()

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()

        test_button = QtWidgets.QPushButton("Test Viewers")
        test_button.clicked.connect(self._test_viewers)
        button_layout.addWidget(test_button)

        save_button = QtWidgets.QPushButton("Save")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save_and_close)
        button_layout.addWidget(save_button)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

    def _load_settings(self):
        """Load current settings into the UI."""
        # Load paths
        path = self._launcher.get_viewer_path(USDViewerType.THREE_D_INFO)
        if path:
            self._three_d_info_path.setText(str(path))

        path = self._launcher.get_viewer_path(USDViewerType.USD_MANAGER)
        if path:
            self._usd_manager_path.setText(str(path))

        path = self._launcher.get_viewer_path(USDViewerType.USDVIEW)
        if path:
            self._usdview_path.setText(str(path))

        # Load preferred viewer
        preferred = self._launcher.get_preferred_viewer()
        index = self._preferred_viewer.findData(preferred)
        if index >= 0:
            self._preferred_viewer.setCurrentIndex(index)

    def _browse_viewer(self, viewer_type: USDViewerType):
        """Browse for a viewer executable.

        Args:
            viewer_type: Type of viewer to browse for
        """
        title_map = {
            USDViewerType.THREE_D_INFO: "Select 3D-Info Executable",
            USDViewerType.USD_MANAGER: "Select USD Manager Executable",
            USDViewerType.USDVIEW: "Select usdview Executable"
        }

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            title_map[viewer_type],
            "",
            "Executables (*.exe *.py *.sh);;All Files (*.*)"
        )

        if file_path:
            if viewer_type == USDViewerType.THREE_D_INFO:
                self._three_d_info_path.setText(file_path)
            elif viewer_type == USDViewerType.USD_MANAGER:
                self._usd_manager_path.setText(file_path)
            elif viewer_type == USDViewerType.USDVIEW:
                self._usdview_path.setText(file_path)

    def _test_viewers(self):
        """Test the configured viewers."""
        results = []

        # Test each configured viewer
        viewers_to_test = [
            (USDViewerType.THREE_D_INFO, self._three_d_info_path.text()),
            (USDViewerType.USD_MANAGER, self._usd_manager_path.text()),
            (USDViewerType.USDVIEW, self._usdview_path.text())
        ]

        for viewer_type, path_str in viewers_to_test:
            if not path_str:
                continue

            path = Path(path_str)
            if path.exists():
                results.append(f"✓ {viewer_type.value}: Found at {path}")
            else:
                results.append(f"✗ {viewer_type.value}: NOT FOUND at {path}")

        if not results:
            results.append("No viewers configured.")

        QtWidgets.QMessageBox.information(
            self,
            "Viewer Test Results",
            "\n".join(results)
        )

    def _save_and_close(self):
        """Save settings and close the dialog."""
        # Save paths
        path_str = self._three_d_info_path.text().strip()
        self._launcher.set_viewer_path(
            USDViewerType.THREE_D_INFO,
            Path(path_str) if path_str else None
        )

        path_str = self._usd_manager_path.text().strip()
        self._launcher.set_viewer_path(
            USDViewerType.USD_MANAGER,
            Path(path_str) if path_str else None
        )

        path_str = self._usdview_path.text().strip()
        self._launcher.set_viewer_path(
            USDViewerType.USDVIEW,
            Path(path_str) if path_str else None
        )

        # Save preferred viewer
        preferred = self._preferred_viewer.currentData()
        if preferred:
            self._launcher.set_preferred_viewer(preferred)

        self.accept()


def show_usd_viewer_settings(parent=None) -> bool:
    """Show the USD viewer settings dialog.

    Args:
        parent: Parent widget

    Returns:
        True if user clicked Save, False if cancelled
    """
    dialog = USDViewerSettingsDialog(parent)
    return dialog.exec_() == QtWidgets.QDialog.Accepted
