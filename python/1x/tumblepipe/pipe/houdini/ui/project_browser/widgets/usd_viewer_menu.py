"""USD Viewer context menu widget."""

from pathlib import Path
from typing import Optional

from qtpy import QtWidgets, QtCore

from ..viewers.usd_viewer import USDViewerLauncher, USDViewerType
from ..dialogs.usd_viewer_settings import show_usd_viewer_settings


class USDViewerContextMenu:
    """Helper class for adding USD viewer options to context menus.

    This class provides utilities to add "View in..." options to context menus
    when the selected item is a USD file.
    """

    @staticmethod
    def add_usd_menu_actions(
        menu: QtWidgets.QMenu,
        file_path: Optional[Path],
        launcher: USDViewerLauncher
    ) -> bool:
        """Add USD viewer actions to a context menu if applicable.

        Args:
            menu: The menu to add actions to
            file_path: The file path being right-clicked (None for non-file items)
            launcher: The USD viewer launcher instance

        Returns:
            True if USD viewer actions were added, False otherwise
        """
        # Only add USD viewer options for USD files
        if file_path is None or not file_path.is_file():
            return False

        if not launcher.is_usd_file(file_path):
            return False

        # Add a separator before USD viewer options
        menu.addSeparator()

        # Get available viewers
        available_viewers = launcher.get_available_viewers()

        if not available_viewers:
            # No viewers configured - add option to configure
            configure_action = menu.addAction("Configure USD Viewers...")
            configure_action.setData(('configure', None))
            return True

        # Create submenu for viewer options
        view_menu = menu.addMenu("View USD in...")

        # Add action for each configured viewer
        viewer_names = {
            USDViewerType.THREE_D_INFO: "3D-Info (Artist-Friendly)",
            USDViewerType.USD_MANAGER: "USD Manager (File Browser)",
            USDViewerType.USDVIEW: "usdview (Pixar Official)"
        }

        for viewer_type in available_viewers:
            action = view_menu.addAction(viewer_names.get(viewer_type, viewer_type.value))
            action.setData(('viewer', viewer_type))

        # Add separator and configure option
        view_menu.addSeparator()
        configure_action = view_menu.addAction("Configure Viewers...")
        configure_action.setData(('configure', None))

        return True

    @staticmethod
    def handle_usd_menu_action(
        action: QtWidgets.QAction,
        file_path: Path,
        launcher: USDViewerLauncher,
        parent: QtWidgets.QWidget
    ) -> bool:
        """Handle selection of a USD viewer menu action.

        Args:
            action: The action that was selected
            file_path: The USD file path to open
            launcher: The USD viewer launcher instance
            parent: Parent widget for dialogs

        Returns:
            True if action was handled, False otherwise
        """
        if action is None:
            return False

        data = action.data()
        if not data:
            return False

        action_type, viewer_type = data

        if action_type == 'configure':
            # Show settings dialog
            show_usd_viewer_settings(parent)
            return True
        elif action_type == 'viewer':
            # Launch the selected viewer
            success = launcher.launch_viewer(file_path, viewer_type)
            if not success:
                QtWidgets.QMessageBox.warning(
                    parent,
                    "Viewer Launch Failed",
                    f"Failed to launch {viewer_type.value} for {file_path.name}\n\n"
                    "Check that the viewer is correctly configured in USD Viewer Settings."
                )
            return True

        return False
