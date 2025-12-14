"""USD viewer launcher for external tools like 3D-Info and USD Manager."""

from pathlib import Path
from typing import Optional
import subprocess
import logging
from enum import Enum

from qtpy import QtCore


logger = logging.getLogger(__name__)


class USDViewerType(Enum):
    """Available USD viewer types."""

    THREE_D_INFO = "3d-info"
    USD_MANAGER = "usd-manager"
    USDVIEW = "usdview"
    AUTO = "auto"


class USDViewerLauncher(QtCore.QObject):
    """Handles launching external USD viewers.

    This class manages launching various USD viewing applications like
    3D-Info (artist-friendly viewer), USD Manager (file browser/editor),
    and usdview (Pixar's official viewer).

    Signals:
        viewer_launched: Emitted when a viewer is successfully launched (viewer_type, file_path)
        viewer_failed: Emitted when viewer launch fails (viewer_type, error_message)
    """

    viewer_launched = QtCore.Signal(str, str)  # viewer_type, file_path
    viewer_failed = QtCore.Signal(str, str)    # viewer_type, error_message

    # Supported USD file extensions
    USD_EXTENSIONS = {'.usd', '.usda', '.usdc', '.usdz'}

    def __init__(self, parent=None):
        """Initialize the USD viewer launcher.

        Args:
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self._settings = QtCore.QSettings('Tumblehead', 'ProjectBrowser')

    def launch_viewer(self, file_path: Path, viewer_type: USDViewerType = USDViewerType.AUTO) -> bool:
        """Launch a USD viewer for the given file.

        Args:
            file_path: Path to the USD file to view
            viewer_type: Type of viewer to launch (defaults to AUTO for preference-based)

        Returns:
            True if viewer was launched successfully, False otherwise
        """
        if not self.is_usd_file(file_path):
            logger.warning(f"File is not a USD file: {file_path}")
            return False

        if not file_path.exists():
            error_msg = f"File does not exist: {file_path}"
            logger.error(error_msg)
            self.viewer_failed.emit(viewer_type.value, error_msg)
            return False

        # Resolve AUTO to actual viewer type
        if viewer_type == USDViewerType.AUTO:
            viewer_type = self.get_preferred_viewer()

        viewer_path = self._get_viewer_path(viewer_type)
        if not viewer_path:
            error_msg = f"Viewer not configured: {viewer_type.value}"
            logger.warning(error_msg)
            self.viewer_failed.emit(viewer_type.value, error_msg)
            return False

        if not viewer_path.exists():
            error_msg = f"Viewer executable not found: {viewer_path}"
            logger.error(error_msg)
            self.viewer_failed.emit(viewer_type.value, error_msg)
            return False

        try:
            # Launch viewer as detached process
            self._launch_process(viewer_path, file_path)
            logger.info(f"Launched {viewer_type.value} for {file_path}")
            self.viewer_launched.emit(viewer_type.value, str(file_path))
            return True
        except Exception as e:
            error_msg = f"Failed to launch {viewer_type.value}: {str(e)}"
            logger.error(error_msg)
            self.viewer_failed.emit(viewer_type.value, error_msg)
            return False

    def _get_3d_info_from_bin_path(self) -> Optional[Path]:
        """Get path to 3D-Info executable from TH_BIN_PATH environment variable.

        Looks for 3D-Info in: %TH_BIN_PATH%/cst_3dinfo-v0.4.4/cst_3dinfo.exe

        Returns:
            Path to 3D-Info if found, None otherwise.
        """
        import os
        import platform

        # Check if TH_BIN_PATH is set
        bin_path = os.environ.get('TH_BIN_PATH')
        if not bin_path:
            return None

        # Platform-specific executable name
        system = platform.system().lower()
        if system == 'windows':
            executable_path = Path(bin_path) / 'cst_3dinfo-v0.4.4' / 'cst_3dinfo.exe'
        elif system == 'linux':
            # Future: adjust for Linux distribution name/path
            executable_path = Path(bin_path) / 'cst_3dinfo-v0.4.4' / 'cst_3dinfo'
        elif system == 'darwin':
            # Future: adjust for macOS distribution name/path
            executable_path = Path(bin_path) / 'cst_3dinfo-v0.4.4' / 'cst_3dinfo'
        else:
            return None

        return executable_path if executable_path.exists() else None

    def _get_usdview_from_houdini(self) -> Optional[Path]:
        """Get path to usdview from Houdini's bundled tools.

        Uses hou.getenv('HFS') to get Houdini installation directory,
        which works reliably from within a running Houdini session.

        Returns:
            Path to usdview if found, None otherwise.
        """
        import platform

        try:
            import hou
            hfs = hou.getenv('HFS')
        except (ImportError, AttributeError):
            return None

        if not hfs:
            return None

        # Platform-specific executable/script name
        system = platform.system().lower()
        if system == 'windows':
            # Windows uses .cmd wrapper script that calls hython
            executable_path = Path(hfs) / 'bin' / 'usdview.cmd'
        elif system == 'linux':
            executable_path = Path(hfs) / 'bin' / 'usdview'
        elif system == 'darwin':
            executable_path = Path(hfs) / 'bin' / 'usdview'
        else:
            return None

        return executable_path if executable_path.exists() else None

    def launch_3d_info(self, file_path: Path) -> bool:
        """Launch 3D-Info viewer for the given USD file.

        Args:
            file_path: Path to the USD file

        Returns:
            True if successful, False otherwise
        """
        return self.launch_viewer(file_path, USDViewerType.THREE_D_INFO)

    def launch_usd_manager(self, file_path: Path) -> bool:
        """Launch USD Manager for the given USD file.

        Args:
            file_path: Path to the USD file

        Returns:
            True if successful, False otherwise
        """
        return self.launch_viewer(file_path, USDViewerType.USD_MANAGER)

    def launch_usdview(self, file_path: Path) -> bool:
        """Launch usdview for the given USD file.

        Args:
            file_path: Path to the USD file

        Returns:
            True if successful, False otherwise
        """
        return self.launch_viewer(file_path, USDViewerType.USDVIEW)

    @classmethod
    def is_usd_file(cls, path: Path) -> bool:
        """Check if the given path is a USD file.

        Args:
            path: Path to check

        Returns:
            True if the file has a USD extension, False otherwise
        """
        return path.suffix.lower() in cls.USD_EXTENSIONS

    def get_preferred_viewer(self) -> USDViewerType:
        """Get the user's preferred USD viewer from settings.

        Returns:
            The preferred viewer type, defaults to USDVIEW
        """
        viewer_str = self._settings.value('usd_viewer/preferred', 'USDVIEW')
        try:
            return USDViewerType[viewer_str]
        except KeyError:
            return USDViewerType.USDVIEW

    def set_preferred_viewer(self, viewer_type: USDViewerType):
        """Set the user's preferred USD viewer.

        Args:
            viewer_type: The viewer type to set as preferred
        """
        if viewer_type == USDViewerType.AUTO:
            return  # Can't set AUTO as preference
        self._settings.setValue('usd_viewer/preferred', viewer_type.name)

    def get_viewer_path(self, viewer_type: USDViewerType) -> Optional[Path]:
        """Get the configured path for a viewer type.

        Args:
            viewer_type: The viewer type to get path for

        Returns:
            Path to viewer executable, or None if not configured
        """
        return self._get_viewer_path(viewer_type)

    def set_viewer_path(self, viewer_type: USDViewerType, path: Optional[Path]):
        """Set the path for a viewer type.

        Args:
            viewer_type: The viewer type to configure
            path: Path to the viewer executable (None to clear)
        """
        key = f'usd_viewer/{viewer_type.value}_path'
        if path is None:
            self._settings.remove(key)
        else:
            self._settings.setValue(key, str(path))

    def is_viewer_configured(self, viewer_type: USDViewerType) -> bool:
        """Check if a viewer type is configured and available.

        For 3D-Info: Checks TH_BIN_PATH environment variable, then user-configured path.
        For other viewers: Checks user-configured path only.

        Args:
            viewer_type: The viewer type to check

        Returns:
            True if configured and executable exists, False otherwise
        """
        if viewer_type == USDViewerType.AUTO:
            viewer_type = self.get_preferred_viewer()

        path = self._get_viewer_path(viewer_type)
        return path is not None and path.exists()

    def get_available_viewers(self) -> list[USDViewerType]:
        """Get list of configured and available viewers.

        Returns:
            List of viewer types that are configured and have valid executables
        """
        available = []
        for viewer_type in [USDViewerType.THREE_D_INFO, USDViewerType.USD_MANAGER, USDViewerType.USDVIEW]:
            if self.is_viewer_configured(viewer_type):
                available.append(viewer_type)
        return available

    def _get_viewer_path(self, viewer_type: USDViewerType) -> Optional[Path]:
        """Internal method to get viewer path from environment, then settings.

        For 3D-Info: Checks TH_BIN_PATH environment variable first, then user-configured path.
        For usdview: Checks Houdini bundled version via hou.getenv('HFS'), then user-configured path.
        For other viewers: Uses only user-configured path.

        Args:
            viewer_type: The viewer type

        Returns:
            Path object or None
        """
        # For 3D-Info, check TH_BIN_PATH environment variable first
        if viewer_type == USDViewerType.THREE_D_INFO:
            env_path = self._get_3d_info_from_bin_path()
            if env_path:
                return env_path

        # For usdview, check Houdini bundled version first
        if viewer_type == USDViewerType.USDVIEW:
            houdini_path = self._get_usdview_from_houdini()
            if houdini_path:
                return houdini_path

        # Fall back to user-configured path from settings
        key = f'usd_viewer/{viewer_type.value}_path'
        path_str = self._settings.value(key)
        if path_str:
            return Path(path_str)
        return None

    def _launch_process(self, viewer_path: Path, file_path: Path):
        """Launch the viewer process.

        Args:
            viewer_path: Path to viewer executable
            file_path: Path to USD file to open

        Raises:
            subprocess.SubprocessError: If process launch fails
        """
        # Convert paths to strings
        viewer_str = str(viewer_path)
        file_str = str(file_path)

        # Set working directory to viewer's directory
        # This is needed for viewers like 3D-Info that look for resources relative to cwd
        cwd = str(viewer_path.parent)

        # Build command - try different argument formats for 3D-Info
        # Some viewers need --file or -f flag, others just take the path
        import os
        exe_name = os.path.basename(viewer_str).lower()

        if 'cst_3dinfo' in exe_name or '3d-info' in exe_name or '3dinfo' in exe_name:
            # Try with --file flag for 3D-Info
            cmd = [viewer_str, '--file', file_str]
        else:
            # Default: just pass file path as argument
            cmd = [viewer_str, file_str]

        # Launch as detached process so it doesn't block
        subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )


# Convenience function for quick access
def launch_usd_viewer(file_path: Path, viewer_type: USDViewerType = USDViewerType.AUTO) -> bool:
    """Launch a USD viewer for the given file.

    Convenience function that creates a launcher instance and launches the viewer.

    Args:
        file_path: Path to the USD file to view
        viewer_type: Type of viewer to launch

    Returns:
        True if viewer was launched successfully, False otherwise
    """
    launcher = USDViewerLauncher()
    return launcher.launch_viewer(file_path, viewer_type)
