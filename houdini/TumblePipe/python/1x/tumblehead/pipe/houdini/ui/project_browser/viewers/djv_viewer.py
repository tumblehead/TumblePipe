"""DJV viewer launcher for EXR sequences and OTIO timelines."""

from pathlib import Path
from typing import Optional
import subprocess
import logging
import os
import platform
import tempfile

from qtpy import QtCore

from tumblehead.util.uri import Uri
from tumblehead.pipe.paths import get_render, Render, Layer, AOV


logger = logging.getLogger(__name__)


class DJVViewerLauncher(QtCore.QObject):
    """Handles launching DJV for EXR sequences and OTIO timelines.

    This class manages launching DJV with support for:
    - Single EXR image sequences
    - OTIO timelines (for multi-shot review)
    - OCIO color management (via project's $OCIO config)

    Signals:
        viewer_launched: Emitted when DJV is successfully launched (file_path)
        viewer_failed: Emitted when launch fails (error_message)
    """

    viewer_launched = QtCore.Signal(str)  # file_path
    viewer_failed = QtCore.Signal(str)    # error_message

    def __init__(self, parent=None):
        """Initialize the DJV viewer launcher.

        Args:
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self._settings = QtCore.QSettings('Tumblehead', 'ProjectBrowser')

    def get_djv_path(self) -> Optional[Path]:
        """Get path to DJV executable.

        Lookup order:
        1. TH_DJV_PATH environment variable (full path to executable)
        2. TH_BIN_PATH/djv/bin/djv.exe (bundled installation)
        3. User-configured path from QSettings

        Returns:
            Path to DJV executable, or None if not found
        """
        # Priority 1: TH_DJV_PATH environment variable
        djv_path_env = os.environ.get('TH_DJV_PATH')
        if djv_path_env:
            djv_path = Path(djv_path_env)
            if djv_path.exists():
                return djv_path
            logger.warning(f"TH_DJV_PATH set but file not found: {djv_path}")

        # Priority 2: TH_BIN_PATH bundled installation
        bin_path = os.environ.get('TH_BIN_PATH')
        if bin_path:
            system = platform.system().lower()
            if system == 'windows':
                # Try common DJV installation patterns
                for djv_dir in Path(bin_path).glob('djv*'):
                    candidate = djv_dir / 'bin' / 'djv.exe'
                    if candidate.exists():
                        return candidate
                    # Also check root of djv directory
                    candidate = djv_dir / 'djv.exe'
                    if candidate.exists():
                        return candidate
            else:
                # Linux/macOS
                for djv_dir in Path(bin_path).glob('djv*'):
                    candidate = djv_dir / 'bin' / 'djv'
                    if candidate.exists():
                        return candidate

        # Priority 3: User-configured path from settings
        user_path = self._settings.value('djv_viewer/path')
        if user_path:
            user_path = Path(user_path)
            if user_path.exists():
                return user_path

        return None

    def is_configured(self) -> bool:
        """Check if DJV is configured and available.

        Returns:
            True if DJV executable is found, False otherwise
        """
        return self.get_djv_path() is not None

    def set_djv_path(self, path: Optional[Path]):
        """Set user-configured DJV path.

        Args:
            path: Path to DJV executable, or None to clear
        """
        if path is None:
            self._settings.remove('djv_viewer/path')
        else:
            self._settings.setValue('djv_viewer/path', str(path))

    def launch_sequence(self, frame_path: Path) -> bool:
        """Launch DJV to view a single EXR sequence.

        Args:
            frame_path: Path to an EXR file in the sequence (with frame number)
                        or a pattern like 'shot_beauty.####.exr'

        Returns:
            True if DJV was launched successfully, False otherwise
        """
        djv_path = self.get_djv_path()
        if not djv_path:
            error_msg = "DJV not configured. Set TH_DJV_PATH environment variable."
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return False

        try:
            self._launch_process(djv_path, [str(frame_path)])
            logger.info(f"Launched DJV for sequence: {frame_path}")
            self.viewer_launched.emit(str(frame_path))
            return True
        except Exception as e:
            error_msg = f"Failed to launch DJV: {str(e)}"
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return False

    def launch_timeline(self, otio_path: Path) -> bool:
        """Launch DJV to view an OTIO timeline.

        Args:
            otio_path: Path to the .otio timeline file

        Returns:
            True if DJV was launched successfully, False otherwise
        """
        djv_path = self.get_djv_path()
        if not djv_path:
            error_msg = "DJV not configured. Set TH_DJV_PATH environment variable."
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return False

        if not otio_path.exists():
            error_msg = f"OTIO file not found: {otio_path}"
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return False

        try:
            self._launch_process(djv_path, [str(otio_path)])
            logger.info(f"Launched DJV for timeline: {otio_path}")
            self.viewer_launched.emit(str(otio_path))
            return True
        except Exception as e:
            error_msg = f"Failed to launch DJV: {str(e)}"
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return False

    def launch_render_timeline(
        self,
        shots: list[tuple[Uri, str]],  # (entity_uri, shot_name)
        render_department: str,
        layer_name: str,
        aov_name: str,
        fps: float = 24.0
    ) -> bool:
        """Create and launch an OTIO timeline for multiple shot renders.

        Args:
            shots: List of (entity_uri, shot_name) tuples in sequence order
            render_department: Render department name (e.g., 'render')
            layer_name: Render layer name (e.g., 'slapcomp', 'beauty')
            aov_name: AOV name (e.g., 'beauty', 'normal')
            fps: Frames per second for the timeline

        Returns:
            True if successful, False otherwise
        """
        # Create OTIO timeline
        otio_path = self.create_render_timeline(
            shots, render_department, layer_name, aov_name, fps
        )
        if otio_path is None:
            return False

        # Launch DJV with the timeline
        return self.launch_timeline(otio_path)

    def create_render_timeline(
        self,
        shots: list[tuple[Uri, str]],  # (entity_uri, shot_name)
        render_department: str,
        layer_name: str,
        aov_name: str,
        fps: float = 24.0,
        output_path: Optional[Path] = None
    ) -> Optional[Path]:
        """Create an OTIO timeline from shot render sequences.

        Args:
            shots: List of (entity_uri, shot_name) tuples in sequence order
            render_department: Render department name
            layer_name: Render layer name
            aov_name: AOV name
            fps: Frames per second
            output_path: Optional output path (defaults to temp file)

        Returns:
            Path to generated .otio file, or None on error
        """
        try:
            import opentimelineio as otio
        except ImportError:
            error_msg = "opentimelineio not installed. Add it to requirements.txt."
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return None

        timeline = otio.schema.Timeline(name="Render Review")
        track = otio.schema.Track(name="renders", kind=otio.schema.TrackKind.Video)

        missing_renders = []

        for entity_uri, shot_name in shots:
            # Get render data
            render = get_render(entity_uri, render_department)
            if render is None:
                missing_renders.append(shot_name)
                continue

            # Get latest complete layer
            layer = render.get_latest_complete_layer(layer_name)
            if layer is None:
                # Try to get latest layer even if incomplete
                layer = render.get_latest_layer(layer_name)
                if layer is None:
                    missing_renders.append(shot_name)
                    continue

            # Get AOV
            aov = layer.get_aov(aov_name)
            if aov is None:
                # If AOV not found, try the layer itself (for non-AOV renders)
                missing_renders.append(f"{shot_name} (no {aov_name} AOV)")
                continue

            # Get frame path pattern and range
            frame_range = layer.get_frame_range()
            if frame_range is None:
                missing_renders.append(f"{shot_name} (no frame range)")
                continue

            # Convert frame pattern to DJV/OTIO format
            # OTIO expects file:// URLs for local files
            frame_path = aov.get_aov_frame_path("%04d")  # Use printf-style padding
            frame_url = frame_path.as_uri() if hasattr(frame_path, 'as_uri') else f"file:///{frame_path}"

            # Create OTIO clip
            clip = otio.schema.Clip(
                name=f"{shot_name}_{layer_name}",
                media_reference=otio.schema.ImageSequenceReference(
                    target_url_base=str(frame_path.parent) + "/",
                    name_prefix=frame_path.stem.rsplit('.', 1)[0] + ".",
                    name_suffix="." + frame_path.suffix.lstrip('.'),
                    start_frame=frame_range.first_frame,
                    frame_zero_padding=4,
                    rate=fps,
                    available_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(frame_range.first_frame, fps),
                        duration=otio.opentime.RationalTime(len(frame_range), fps)
                    )
                ),
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(frame_range.first_frame, fps),
                    duration=otio.opentime.RationalTime(len(frame_range), fps)
                )
            )
            track.append(clip)

        if missing_renders:
            logger.warning(f"Missing renders for: {', '.join(missing_renders)}")

        if len(track) == 0:
            error_msg = "No valid renders found for selected shots."
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return None

        timeline.tracks.append(track)

        # Write to file
        if output_path is None:
            output_path = Path(tempfile.gettempdir()) / "render_review.otio"

        try:
            otio.adapters.write_to_file(timeline, str(output_path))
            logger.info(f"Created OTIO timeline: {output_path}")
            return output_path
        except Exception as e:
            error_msg = f"Failed to write OTIO file: {str(e)}"
            logger.error(error_msg)
            self.viewer_failed.emit(error_msg)
            return None

    def _launch_process(self, djv_path: Path, args: list[str]):
        """Launch DJV process with given arguments.

        Args:
            djv_path: Path to DJV executable
            args: Command line arguments

        Raises:
            subprocess.SubprocessError: If process launch fails
        """
        # Build command
        cmd = [str(djv_path)] + args

        # Add OCIO config if available
        ocio_config = os.environ.get('OCIO')
        if ocio_config and Path(ocio_config).exists():
            cmd.extend(['-ocio_config', ocio_config])

        # Set working directory
        cwd = str(djv_path.parent)

        # Copy environment
        env = os.environ.copy()

        # Launch as detached process
        subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )


# Convenience function for quick access
def launch_djv_sequence(frame_path: Path) -> bool:
    """Launch DJV to view an EXR sequence.

    Convenience function that creates a launcher instance and launches.

    Args:
        frame_path: Path to an EXR file in the sequence

    Returns:
        True if DJV was launched successfully, False otherwise
    """
    launcher = DJVViewerLauncher()
    return launcher.launch_sequence(frame_path)
