"""
DaVinci Resolve integration for the TumbleHead pipeline.

This module provides a client for interacting with DaVinci Resolve via its
Python scripting API. Resolve must be running for the API to work.

Environment Setup (handled automatically):
    RESOLVE_SCRIPT_API: Path to Resolve's Developer/Scripting folder
    RESOLVE_SCRIPT_LIB: Path to fusionscript.dll
    PYTHONPATH: Must include the Modules folder

Usage:
    from tumblehead.apps.resolve import ResolveClient

    client = ResolveClient()
    if client.connect():
        project = client.get_current_project()
        print(f"Current project: {project.GetName()}")
"""

from dataclasses import dataclass, field
from typing import Optional, Any
from pathlib import Path
import subprocess
import platform
import logging
import sys
import os


# Type aliases for Resolve API objects (actual types come from DaVinciResolveScript)
Resolve = Any
ProjectManager = Any
Project = Any
MediaPool = Any
MediaPoolItem = Any
MediaPoolFolder = Any
Timeline = Any
TimelineItem = Any


@dataclass
class ResolveInstallation:
    """Information about a DaVinci Resolve installation."""
    version: str
    path: Path
    script_api_path: Path
    script_lib_path: Path
    modules_path: Path
    executable_path: Path


def _find_resolve_installations() -> dict[str, ResolveInstallation]:
    """Find all DaVinci Resolve installations on the system.

    Returns:
        Dictionary mapping version strings to ResolveInstallation objects.
    """
    result = {}

    if platform.system() != 'Windows':
        logging.warning("Resolve installation discovery only supports Windows currently")
        return result

    # Check common installation locations
    program_files = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))
    program_data = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData'))

    # Resolve installs to: C:/Program Files/Blackmagic Design/DaVinci Resolve/
    resolve_base = program_files / 'Blackmagic Design'
    if not resolve_base.exists():
        return result

    for item in resolve_base.iterdir():
        if not item.is_dir():
            continue
        if not item.name.startswith('DaVinci Resolve'):
            continue

        # Check for Resolve executable
        resolve_exe = item / 'Resolve.exe'
        if not resolve_exe.exists():
            continue

        # Check for scripting API
        script_api_path = program_data / 'Blackmagic Design' / 'DaVinci Resolve' / 'Support' / 'Developer' / 'Scripting'
        script_lib_path = item / 'fusionscript.dll'
        modules_path = script_api_path / 'Modules'

        # Try to determine version from folder name or exe
        version = item.name.replace('DaVinci Resolve', '').strip()
        if not version:
            version = 'default'

        if script_api_path.exists() and script_lib_path.exists():
            result[version] = ResolveInstallation(
                version=version,
                path=item,
                script_api_path=script_api_path,
                script_lib_path=script_lib_path,
                modules_path=modules_path,
                executable_path=resolve_exe
            )

    return result


def _setup_resolve_environment(installation: ResolveInstallation) -> dict[str, str]:
    """Set up environment variables for Resolve scripting API.

    Args:
        installation: ResolveInstallation with paths to configure.

    Returns:
        Dictionary of environment variables to set.
    """
    env = os.environ.copy()
    env['RESOLVE_SCRIPT_API'] = str(installation.script_api_path)
    env['RESOLVE_SCRIPT_LIB'] = str(installation.script_lib_path)

    # Add modules path to PYTHONPATH
    pythonpath = env.get('PYTHONPATH', '')
    modules_str = str(installation.modules_path)
    if modules_str not in pythonpath:
        if pythonpath:
            env['PYTHONPATH'] = f"{modules_str};{pythonpath}"
        else:
            env['PYTHONPATH'] = modules_str

    # Also add to sys.path for current process
    if modules_str not in sys.path:
        sys.path.insert(0, modules_str)

    return env


@dataclass
class MediaPoolItemInfo:
    """Information about a media pool item."""
    name: str
    clip_type: str
    file_path: str
    duration: int
    fps: float
    resolution: tuple[int, int]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimelineInfo:
    """Information about a timeline."""
    name: str
    frame_start: int
    frame_end: int
    fps: float
    resolution: tuple[int, int]
    track_count: dict[str, int] = field(default_factory=dict)


@dataclass
class ProjectInfo:
    """Information about a Resolve project."""
    name: str
    timeline_count: int
    current_timeline: Optional[str]
    fps: float
    resolution: tuple[int, int]


class ResolveClient:
    """Client for interacting with DaVinci Resolve via its Python API.

    This client provides methods to:
    - Connect to a running Resolve instance
    - Query project and timeline information
    - Import media to the media pool
    - Relink clips to new render paths
    - Trigger renders

    Example:
        client = ResolveClient()
        if client.connect():
            info = client.get_project_info()
            print(f"Project: {info.name}")

            folders = client.list_media_pool_folders()
            for folder in folders:
                print(f"  Folder: {folder}")
    """

    def __init__(self, version: Optional[str] = None):
        """Initialize the Resolve client.

        Args:
            version: Optional Resolve version to use. If None, uses the first
                    installation found.
        """
        self._installation: Optional[ResolveInstallation] = None
        self._resolve: Optional[Resolve] = None
        self._version = version

        # Find installations
        installations = _find_resolve_installations()
        if not installations:
            logging.warning("No DaVinci Resolve installations found")
            return

        if version and version in installations:
            self._installation = installations[version]
        else:
            # Use first available
            self._installation = next(iter(installations.values()))

        if self._installation:
            _setup_resolve_environment(self._installation)

    @property
    def is_available(self) -> bool:
        """Check if Resolve is installed and available."""
        return self._installation is not None

    @property
    def is_connected(self) -> bool:
        """Check if connected to a running Resolve instance."""
        return self._resolve is not None

    @property
    def installation(self) -> Optional[ResolveInstallation]:
        """Get the current Resolve installation info."""
        return self._installation

    def connect(self) -> bool:
        """Connect to a running DaVinci Resolve instance.

        Returns:
            True if connection successful, False otherwise.
        """
        if not self._installation:
            logging.error("No Resolve installation available")
            return False

        try:
            import DaVinciResolveScript as dvr_script
            self._resolve = dvr_script.scriptapp("Resolve")
            if self._resolve is None:
                logging.error("Failed to connect to Resolve. Is it running?")
                return False
            logging.info("Connected to DaVinci Resolve")
            return True
        except ImportError as e:
            logging.error(f"Failed to import DaVinciResolveScript: {e}")
            logging.error(f"Modules path: {self._installation.modules_path}")
            return False
        except Exception as e:
            logging.error(f"Failed to connect to Resolve: {e}")
            return False

    def get_resolve(self) -> Optional[Resolve]:
        """Get the raw Resolve API object.

        Returns:
            The Resolve object, or None if not connected.
        """
        return self._resolve

    def get_project_manager(self) -> Optional[ProjectManager]:
        """Get the project manager.

        Returns:
            The ProjectManager object, or None if not connected.
        """
        if not self._resolve:
            return None
        return self._resolve.GetProjectManager()

    def get_current_project(self) -> Optional[Project]:
        """Get the currently open project.

        Returns:
            The Project object, or None if not connected or no project open.
        """
        pm = self.get_project_manager()
        if not pm:
            return None
        return pm.GetCurrentProject()

    def get_project_info(self) -> Optional[ProjectInfo]:
        """Get information about the current project.

        Returns:
            ProjectInfo object, or None if not connected.
        """
        project = self.get_current_project()
        if not project:
            return None

        current_timeline = project.GetCurrentTimeline()
        timeline_name = current_timeline.GetName() if current_timeline else None

        # Get project settings
        fps = float(project.GetSetting('timelineFrameRate') or 24.0)
        width = int(project.GetSetting('timelineResolutionWidth') or 1920)
        height = int(project.GetSetting('timelineResolutionHeight') or 1080)

        return ProjectInfo(
            name=project.GetName(),
            timeline_count=project.GetTimelineCount(),
            current_timeline=timeline_name,
            fps=fps,
            resolution=(width, height)
        )

    def get_media_pool(self) -> Optional[MediaPool]:
        """Get the media pool for the current project.

        Returns:
            The MediaPool object, or None if not connected.
        """
        project = self.get_current_project()
        if not project:
            return None
        return project.GetMediaPool()

    def get_root_folder(self) -> Optional[MediaPoolFolder]:
        """Get the root folder of the media pool.

        Returns:
            The root MediaPoolFolder, or None if not connected.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return None
        return media_pool.GetRootFolder()

    def list_media_pool_folders(self, folder: Optional[MediaPoolFolder] = None, prefix: str = "") -> list[str]:
        """List all folders in the media pool.

        Args:
            folder: Starting folder (defaults to root).
            prefix: Path prefix for nested folders.

        Returns:
            List of folder paths.
        """
        if folder is None:
            folder = self.get_root_folder()
        if not folder:
            return []

        result = []
        name = folder.GetName()
        current_path = f"{prefix}/{name}" if prefix else name
        result.append(current_path)

        # Recurse into subfolders
        subfolders = folder.GetSubFolderList()
        if subfolders:
            for subfolder in subfolders:
                result.extend(self.list_media_pool_folders(subfolder, current_path))

        return result

    def list_media_pool_clips(self, folder: Optional[MediaPoolFolder] = None) -> list[MediaPoolItemInfo]:
        """List all clips in a media pool folder.

        Args:
            folder: Folder to list clips from (defaults to root).

        Returns:
            List of MediaPoolItemInfo objects.
        """
        if folder is None:
            folder = self.get_root_folder()
        if not folder:
            return []

        result = []
        clips = folder.GetClipList()
        if clips:
            for clip in clips:
                try:
                    props = clip.GetClipProperty() or {}
                    result.append(MediaPoolItemInfo(
                        name=clip.GetName(),
                        clip_type=props.get('Type', 'Unknown'),
                        file_path=props.get('File Path', ''),
                        duration=int(props.get('Frames', 0)),
                        fps=float(props.get('FPS', 24.0)),
                        resolution=(
                            int(props.get('Resolution', '1920x1080').split('x')[0]),
                            int(props.get('Resolution', '1920x1080').split('x')[1])
                        ),
                        properties=props
                    ))
                except Exception as e:
                    logging.warning(f"Failed to get clip info: {e}")

        return result

    def get_current_timeline(self) -> Optional[Timeline]:
        """Get the currently active timeline.

        Returns:
            The Timeline object, or None if not connected.
        """
        project = self.get_current_project()
        if not project:
            return None
        return project.GetCurrentTimeline()

    def get_timeline_info(self, timeline: Optional[Timeline] = None) -> Optional[TimelineInfo]:
        """Get information about a timeline.

        Args:
            timeline: Timeline to query (defaults to current timeline).

        Returns:
            TimelineInfo object, or None if not available.
        """
        if timeline is None:
            timeline = self.get_current_timeline()
        if not timeline:
            return None

        # Get timeline settings
        fps = float(timeline.GetSetting('timelineFrameRate') or 24.0)
        width = int(timeline.GetSetting('timelineResolutionWidth') or 1920)
        height = int(timeline.GetSetting('timelineResolutionHeight') or 1080)

        # Count tracks
        track_count = {
            'video': timeline.GetTrackCount('video'),
            'audio': timeline.GetTrackCount('audio'),
            'subtitle': timeline.GetTrackCount('subtitle')
        }

        return TimelineInfo(
            name=timeline.GetName(),
            frame_start=timeline.GetStartFrame(),
            frame_end=timeline.GetEndFrame(),
            fps=fps,
            resolution=(width, height),
            track_count=track_count
        )

    def list_timelines(self) -> list[str]:
        """List all timelines in the current project.

        Returns:
            List of timeline names.
        """
        project = self.get_current_project()
        if not project:
            return []

        result = []
        count = project.GetTimelineCount()
        for i in range(1, count + 1):  # Resolve uses 1-based indexing
            timeline = project.GetTimelineByIndex(i)
            if timeline:
                result.append(timeline.GetName())

        return result

    def open_page(self, page_name: str) -> bool:
        """Switch to a specific Resolve page.

        Args:
            page_name: One of 'media', 'cut', 'edit', 'fusion', 'color', 'fairlight', 'deliver'

        Returns:
            True if successful.
        """
        if not self._resolve:
            return False
        return self._resolve.OpenPage(page_name)

    def get_current_page(self) -> Optional[str]:
        """Get the name of the currently active page.

        Returns:
            Page name, or None if not connected.
        """
        if not self._resolve:
            return None
        return self._resolve.GetCurrentPage()

    # =========================================================================
    # Phase 2: Media Operations
    # =========================================================================

    def get_folder_by_path(self, path: str) -> Optional[MediaPoolFolder]:
        """Get a media pool folder by its path.

        Args:
            path: Folder path like 'Master/Renders/SEQ010' or 'Renders/SEQ010'.
                  The path is relative to the root folder.

        Returns:
            The MediaPoolFolder if found, None otherwise.
        """
        if not path:
            return self.get_root_folder()

        root = self.get_root_folder()
        if not root:
            return None

        # Split path and traverse
        parts = [p for p in path.split('/') if p]
        current = root

        # Skip root folder name if it matches the first part
        if parts and parts[0] == root.GetName():
            parts = parts[1:]

        for part in parts:
            subfolders = current.GetSubFolderList() or []
            found = None
            for subfolder in subfolders:
                if subfolder.GetName() == part:
                    found = subfolder
                    break
            if not found:
                return None
            current = found

        return current

    def get_or_create_folder(self, path: str) -> Optional[MediaPoolFolder]:
        """Get or create a media pool folder by path.

        Args:
            path: Folder path like 'Master/Renders/SEQ010'.
                  Creates intermediate folders as needed.

        Returns:
            The MediaPoolFolder (existing or newly created), or None on failure.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return None

        root = self.get_root_folder()
        if not root:
            return None

        if not path:
            return root

        # Split path and traverse/create
        parts = [p for p in path.split('/') if p]
        current = root

        # Skip root folder name if it matches the first part
        if parts and parts[0] == root.GetName():
            parts = parts[1:]

        for part in parts:
            subfolders = current.GetSubFolderList() or []
            found = None
            for subfolder in subfolders:
                if subfolder.GetName() == part:
                    found = subfolder
                    break

            if found:
                current = found
            else:
                # Need to create this folder
                # First, set current folder as the active folder
                media_pool.SetCurrentFolder(current)
                new_folder = media_pool.AddSubFolder(current, part)
                if not new_folder:
                    logging.error(f"Failed to create folder: {part}")
                    return None
                current = new_folder

        return current

    def set_current_folder(self, folder: MediaPoolFolder) -> bool:
        """Set the current/active media pool folder.

        Args:
            folder: The folder to make active.

        Returns:
            True if successful.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return False
        return media_pool.SetCurrentFolder(folder)

    def import_media(
        self,
        file_paths: list[Path],
        folder: Optional[MediaPoolFolder] = None
    ) -> list[MediaPoolItem]:
        """Import media files to the media pool.

        Args:
            file_paths: List of file paths to import.
            folder: Target folder (defaults to current folder).

        Returns:
            List of imported MediaPoolItem objects.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return []

        # Set target folder if specified
        if folder:
            media_pool.SetCurrentFolder(folder)

        # Convert paths to strings
        path_strings = [str(p) for p in file_paths]

        # Import media
        clips = media_pool.ImportMedia(path_strings)
        return clips if clips else []

    def import_frame_sequence(
        self,
        first_frame_path: Path,
        folder: Optional[MediaPoolFolder] = None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None
    ) -> Optional[MediaPoolItem]:
        """Import a frame sequence as a single clip.

        Args:
            first_frame_path: Path to the first frame (e.g., shot.0001.exr).
            folder: Target folder (defaults to current folder).
            start_frame: Optional start frame override.
            end_frame: Optional end frame override.

        Returns:
            The imported MediaPoolItem, or None on failure.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return None

        # Set target folder if specified
        if folder:
            media_pool.SetCurrentFolder(folder)

        # Build import descriptor for frame sequence
        import_info = {
            "FilePath": str(first_frame_path)
        }

        if start_frame is not None:
            import_info["StartIndex"] = start_frame
        if end_frame is not None:
            import_info["EndIndex"] = end_frame

        # Import as sequence
        clips = media_pool.ImportMedia([import_info])
        if clips and len(clips) > 0:
            return clips[0]
        return None

    def find_clips_by_name(
        self,
        name_pattern: str,
        folder: Optional[MediaPoolFolder] = None,
        recursive: bool = True
    ) -> list[MediaPoolItem]:
        """Find clips by name pattern.

        Args:
            name_pattern: Name pattern to match (supports * wildcard).
            folder: Starting folder (defaults to root).
            recursive: Whether to search subfolders.

        Returns:
            List of matching MediaPoolItem objects.
        """
        import fnmatch

        if folder is None:
            folder = self.get_root_folder()
        if not folder:
            return []

        result = []

        # Search clips in this folder
        clips = folder.GetClipList() or []
        for clip in clips:
            if fnmatch.fnmatch(clip.GetName(), name_pattern):
                result.append(clip)

        # Recurse into subfolders
        if recursive:
            subfolders = folder.GetSubFolderList() or []
            for subfolder in subfolders:
                result.extend(self.find_clips_by_name(name_pattern, subfolder, recursive))

        return result

    def find_clips_by_path(
        self,
        path_pattern: str,
        folder: Optional[MediaPoolFolder] = None,
        recursive: bool = True
    ) -> list[MediaPoolItem]:
        """Find clips by file path pattern.

        Args:
            path_pattern: File path pattern to match (supports * wildcard).
            folder: Starting folder (defaults to root).
            recursive: Whether to search subfolders.

        Returns:
            List of matching MediaPoolItem objects.
        """
        import fnmatch

        if folder is None:
            folder = self.get_root_folder()
        if not folder:
            return []

        result = []

        # Search clips in this folder
        clips = folder.GetClipList() or []
        for clip in clips:
            try:
                props = clip.GetClipProperty() or {}
                file_path = props.get('File Path', '')
                if file_path and fnmatch.fnmatch(file_path, path_pattern):
                    result.append(clip)
            except Exception:
                pass

        # Recurse into subfolders
        if recursive:
            subfolders = folder.GetSubFolderList() or []
            for subfolder in subfolders:
                result.extend(self.find_clips_by_path(path_pattern, subfolder, recursive))

        return result

    def relink_clips(
        self,
        clips: list[MediaPoolItem],
        new_folder_path: Path
    ) -> bool:
        """Relink clips to a new folder location.

        Args:
            clips: List of clips to relink.
            new_folder_path: New folder path containing the media files.

        Returns:
            True if relinking was successful.
        """
        media_pool = self.get_media_pool()
        if not media_pool or not clips:
            return False

        return media_pool.RelinkClips(clips, str(new_folder_path))

    def relink_clips_to_version(
        self,
        shot_pattern: str,
        old_version: str,
        new_version: str,
        base_render_path: Optional[Path] = None
    ) -> int:
        """Relink clips for a shot from old version to new version.

        This finds all clips matching the shot pattern that reference the old
        version path and relinks them to the new version path.

        Args:
            shot_pattern: Shot name pattern (e.g., 'SEQ010_SHOT020*').
            old_version: Old version string (e.g., 'v0002').
            new_version: New version string (e.g., 'v0003').
            base_render_path: Base render path (if None, infers from clip paths).

        Returns:
            Number of clips relinked.
        """
        # Find clips matching the shot pattern
        clips = self.find_clips_by_name(shot_pattern)
        if not clips:
            return 0

        count = 0
        for clip in clips:
            try:
                props = clip.GetClipProperty() or {}
                file_path = props.get('File Path', '')
                if not file_path:
                    continue

                # Check if this clip references the old version
                if old_version not in file_path:
                    continue

                # Calculate new path
                new_path = file_path.replace(old_version, new_version)
                new_folder = str(Path(new_path).parent)

                # Relink this single clip
                if self.relink_clips([clip], Path(new_folder)):
                    count += 1
                    logging.info(f"Relinked clip {clip.GetName()} to {new_version}")
                else:
                    logging.warning(f"Failed to relink clip {clip.GetName()}")
            except Exception as e:
                logging.warning(f"Error relinking clip: {e}")

        return count

    def set_clip_property(
        self,
        clip: MediaPoolItem,
        property_name: str,
        value: Any
    ) -> bool:
        """Set a property on a media pool clip.

        Args:
            clip: The clip to modify.
            property_name: Property name (e.g., 'Comments', 'Keywords', 'Description').
            value: Value to set.

        Returns:
            True if successful.
        """
        try:
            return clip.SetClipProperty(property_name, value)
        except Exception as e:
            logging.error(f"Failed to set clip property {property_name}: {e}")
            return False

    def set_clip_metadata(
        self,
        clip: MediaPoolItem,
        metadata: dict[str, Any]
    ) -> bool:
        """Set multiple metadata properties on a clip.

        Args:
            clip: The clip to modify.
            metadata: Dictionary of property names to values.

        Returns:
            True if all properties were set successfully.
        """
        success = True
        for key, value in metadata.items():
            if not self.set_clip_property(clip, key, value):
                success = False
        return success

    def set_clip_pipeline_info(
        self,
        clip: MediaPoolItem,
        shot: str,
        version: str,
        department: Optional[str] = None,
        extra: Optional[dict] = None
    ) -> bool:
        """Set pipeline version info on a clip's Comments field.

        Stores structured pipeline info in the Comments field for later retrieval.
        Format: "pipeline:v0003|shot:SEQ010_SHOT020|dept:comp"

        Args:
            clip: The clip to modify.
            shot: Shot name.
            version: Version string (e.g., 'v0003').
            department: Optional department name.
            extra: Optional extra key-value pairs.

        Returns:
            True if successful.
        """
        parts = [f"pipeline:{version}", f"shot:{shot}"]
        if department:
            parts.append(f"dept:{department}")
        if extra:
            for key, value in extra.items():
                parts.append(f"{key}:{value}")

        comment = "|".join(parts)
        return self.set_clip_property(clip, "Comments", comment)

    def get_clip_pipeline_info(self, clip: MediaPoolItem) -> Optional[dict[str, str]]:
        """Extract pipeline version info from a clip's Comments field.

        Args:
            clip: The clip to query.

        Returns:
            Dictionary of pipeline info, or None if not found.
        """
        try:
            props = clip.GetClipProperty() or {}
            comments = props.get('Comments', '')
            if not comments or 'pipeline:' not in comments:
                return None

            result = {}
            for part in comments.split('|'):
                if ':' in part:
                    key, value = part.split(':', 1)
                    result[key] = value

            # Map common keys to standard names
            return {
                'version': result.get('pipeline'),
                'shot': result.get('shot'),
                'department': result.get('dept'),
                **{k: v for k, v in result.items() if k not in ('pipeline', 'shot', 'dept')}
            }
        except Exception as e:
            logging.warning(f"Failed to get pipeline info from clip: {e}")
            return None


    # =========================================================================
    # Phase 4: Timeline Operations
    # =========================================================================

    def create_timeline(
        self,
        name: str,
        fps: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> Optional[Timeline]:
        """Create a new empty timeline.

        Args:
            name: Name for the new timeline.
            fps: Frame rate (defaults to project setting).
            width: Resolution width (defaults to project setting).
            height: Resolution height (defaults to project setting).

        Returns:
            The new Timeline object, or None on failure.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return None

        # Create timeline with project defaults
        timeline = media_pool.CreateEmptyTimeline(name)
        if not timeline:
            logging.error(f"Failed to create timeline: {name}")
            return None

        # Apply settings if specified
        if fps is not None:
            timeline.SetSetting('timelineFrameRate', str(fps))
        if width is not None:
            timeline.SetSetting('timelineResolutionWidth', str(width))
        if height is not None:
            timeline.SetSetting('timelineResolutionHeight', str(height))

        logging.info(f"Created timeline: {name}")
        return timeline

    def create_timeline_from_clips(
        self,
        name: str,
        clips: list[MediaPoolItem]
    ) -> Optional[Timeline]:
        """Create a timeline from a list of clips.

        Args:
            name: Name for the new timeline.
            clips: List of MediaPoolItem clips to add.

        Returns:
            The new Timeline object, or None on failure.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return None

        timeline = media_pool.CreateTimelineFromClips(name, clips)
        if not timeline:
            logging.error(f"Failed to create timeline from clips: {name}")
            return None

        logging.info(f"Created timeline from {len(clips)} clips: {name}")
        return timeline

    def get_timeline_by_name(self, name: str) -> Optional[Timeline]:
        """Get a timeline by its name.

        Args:
            name: Timeline name to find.

        Returns:
            The Timeline object, or None if not found.
        """
        project = self.get_current_project()
        if not project:
            return None

        count = project.GetTimelineCount()
        for i in range(1, count + 1):
            timeline = project.GetTimelineByIndex(i)
            if timeline and timeline.GetName() == name:
                return timeline

        return None

    def set_current_timeline(self, timeline: Timeline) -> bool:
        """Set the current/active timeline.

        Args:
            timeline: The timeline to make active.

        Returns:
            True if successful.
        """
        project = self.get_current_project()
        if not project:
            return False
        return project.SetCurrentTimeline(timeline)

    def add_clips_to_timeline(
        self,
        clips: list[MediaPoolItem],
        timeline: Optional[Timeline] = None,
        track_index: int = 1,
        record_frame: Optional[int] = None
    ) -> bool:
        """Add clips to a timeline.

        Args:
            clips: List of clips to add.
            timeline: Target timeline (defaults to current).
            track_index: Video track index (1-based).
            record_frame: Optional starting frame position.

        Returns:
            True if successful.
        """
        media_pool = self.get_media_pool()
        if not media_pool:
            return False

        if timeline:
            project = self.get_current_project()
            if project:
                project.SetCurrentTimeline(timeline)

        # Build clip info with optional positioning
        clip_infos = []
        for clip in clips:
            info = {"mediaPoolItem": clip, "trackIndex": track_index}
            if record_frame is not None:
                info["recordFrame"] = record_frame
            clip_infos.append(info)

        # Append to timeline
        result = media_pool.AppendToTimeline(clip_infos)
        return result is not None and len(result) > 0

    def build_shot_timeline(
        self,
        timeline_name: str,
        shots: list[dict]
    ) -> Optional[Timeline]:
        """Build a timeline from a shot list with frame ranges.

        Each shot dict should have:
            - 'name': Shot name (for logging)
            - 'clip': MediaPoolItem clip
            - 'start': Source start frame (optional)
            - 'end': Source end frame (optional)

        Args:
            timeline_name: Name for the timeline.
            shots: List of shot dictionaries.

        Returns:
            The new Timeline, or None on failure.
        """
        if not shots:
            logging.error("No shots provided for timeline")
            return None

        # Create empty timeline
        timeline = self.create_timeline(timeline_name)
        if not timeline:
            return None

        # Set as current timeline
        self.set_current_timeline(timeline)

        media_pool = self.get_media_pool()
        if not media_pool:
            return None

        # Add each shot
        for shot in shots:
            clip = shot.get('clip')
            if not clip:
                logging.warning(f"Shot missing clip: {shot.get('name', 'unknown')}")
                continue

            # Build clip info
            clip_info = {"mediaPoolItem": clip}

            # Set frame range if specified
            if 'start' in shot:
                clip_info["startFrame"] = shot['start']
            if 'end' in shot:
                clip_info["endFrame"] = shot['end']

            # Append to timeline
            result = media_pool.AppendToTimeline([clip_info])
            if result:
                logging.info(f"Added shot to timeline: {shot.get('name', clip.GetName())}")
            else:
                logging.warning(f"Failed to add shot: {shot.get('name', clip.GetName())}")

        return timeline

    def get_timeline_items(
        self,
        timeline: Optional[Timeline] = None,
        track_type: str = 'video',
        track_index: int = 1
    ) -> list[TimelineItem]:
        """Get all items in a timeline track.

        Args:
            timeline: Timeline to query (defaults to current).
            track_type: Track type ('video', 'audio', 'subtitle').
            track_index: Track index (1-based).

        Returns:
            List of TimelineItem objects.
        """
        if timeline is None:
            timeline = self.get_current_timeline()
        if not timeline:
            return []

        items = timeline.GetItemListInTrack(track_type, track_index)
        return items if items else []

    # =========================================================================
    # Phase 4: Render Operations
    # =========================================================================

    def get_render_presets(self) -> list[str]:
        """Get list of available render presets.

        Returns:
            List of preset names.
        """
        project = self.get_current_project()
        if not project:
            return []

        presets = project.GetRenderPresetList()
        return presets if presets else []

    def set_render_preset(self, preset_name: str) -> bool:
        """Load a render preset.

        Args:
            preset_name: Name of the preset to load.

        Returns:
            True if successful.
        """
        project = self.get_current_project()
        if not project:
            return False

        return project.LoadRenderPreset(preset_name)

    def set_render_settings(
        self,
        preset: Optional[str] = None,
        output_path: Optional[Path] = None,
        filename_prefix: Optional[str] = None,
        format_override: Optional[str] = None,
        codec_override: Optional[str] = None
    ) -> bool:
        """Configure render settings.

        Args:
            preset: Render preset name to load.
            output_path: Output directory path.
            filename_prefix: Filename prefix for output.
            format_override: Format override (e.g., 'mp4', 'mov').
            codec_override: Codec override (e.g., 'H.264', 'ProRes').

        Returns:
            True if all settings were applied successfully.
        """
        project = self.get_current_project()
        if not project:
            return False

        success = True

        # Load preset if specified
        if preset:
            if not project.LoadRenderPreset(preset):
                logging.warning(f"Failed to load render preset: {preset}")
                success = False

        # Set output path
        if output_path:
            if not project.SetRenderSettings({'TargetDir': str(output_path)}):
                logging.warning(f"Failed to set output path: {output_path}")
                success = False

        # Set filename prefix
        if filename_prefix:
            if not project.SetRenderSettings({'CustomName': filename_prefix}):
                logging.warning(f"Failed to set filename prefix: {filename_prefix}")
                success = False

        # Set format override
        if format_override:
            if not project.SetRenderSettings({'FormatWidth': format_override}):
                logging.warning(f"Failed to set format: {format_override}")
                success = False

        # Set codec override
        if codec_override:
            if not project.SetRenderSettings({'VideoCodec': codec_override}):
                logging.warning(f"Failed to set codec: {codec_override}")
                success = False

        return success

    def add_render_job(
        self,
        timeline: Optional[Timeline] = None,
        mark_in: Optional[int] = None,
        mark_out: Optional[int] = None,
        preset: Optional[str] = None,
        output_path: Optional[Path] = None,
        filename: Optional[str] = None
    ) -> Optional[str]:
        """Add a render job to the queue.

        Args:
            timeline: Timeline to render (defaults to current).
            mark_in: Optional in point (frame).
            mark_out: Optional out point (frame).
            preset: Optional render preset to use.
            output_path: Optional output directory.
            filename: Optional output filename.

        Returns:
            Job ID string, or None on failure.
        """
        project = self.get_current_project()
        if not project:
            return None

        # Set timeline if specified
        if timeline:
            project.SetCurrentTimeline(timeline)

        # Apply settings
        if preset:
            project.LoadRenderPreset(preset)

        settings = {}
        if output_path:
            settings['TargetDir'] = str(output_path)
        if filename:
            settings['CustomName'] = filename
        if mark_in is not None:
            settings['MarkIn'] = mark_in
        if mark_out is not None:
            settings['MarkOut'] = mark_out

        if settings:
            project.SetRenderSettings(settings)

        # Add render job
        job_id = project.AddRenderJob()
        if job_id:
            logging.info(f"Added render job: {job_id}")
        else:
            logging.error("Failed to add render job")

        return job_id

    def get_render_jobs(self) -> list[dict]:
        """Get all render jobs in the queue.

        Returns:
            List of render job dictionaries.
        """
        project = self.get_current_project()
        if not project:
            return []

        jobs = project.GetRenderJobList()
        return jobs if jobs else []

    def delete_render_job(self, job_id: str) -> bool:
        """Delete a render job from the queue.

        Args:
            job_id: The job ID to delete.

        Returns:
            True if successful.
        """
        project = self.get_current_project()
        if not project:
            return False

        return project.DeleteRenderJob(job_id)

    def delete_all_render_jobs(self) -> bool:
        """Delete all render jobs from the queue.

        Returns:
            True if successful.
        """
        project = self.get_current_project()
        if not project:
            return False

        return project.DeleteAllRenderJobs()

    def start_render(self, job_ids: Optional[list[str]] = None) -> bool:
        """Start rendering.

        Args:
            job_ids: Optional list of specific job IDs to render.
                    If None, renders all jobs in queue.

        Returns:
            True if render started successfully.
        """
        project = self.get_current_project()
        if not project:
            return False

        if job_ids:
            return project.StartRendering(job_ids)
        else:
            return project.StartRendering()

    def stop_render(self) -> None:
        """Stop the current render."""
        project = self.get_current_project()
        if project:
            project.StopRendering()

    def is_rendering(self) -> bool:
        """Check if a render is in progress.

        Returns:
            True if rendering is in progress.
        """
        project = self.get_current_project()
        if not project:
            return False

        return project.IsRenderingInProgress()

    def get_render_status(self) -> dict:
        """Get current render progress and status.

        Returns:
            Dictionary with render status info:
            - 'rendering': bool
            - 'progress': float (0-100)
            - 'job_id': current job ID
            - 'time_remaining': estimated time remaining
        """
        project = self.get_current_project()
        if not project:
            return {'rendering': False}

        is_rendering = project.IsRenderingInProgress()
        if not is_rendering:
            return {'rendering': False}

        # Get render job status
        jobs = project.GetRenderJobList() or []
        status = project.GetRenderJobStatus(jobs[-1] if jobs else '')

        return {
            'rendering': True,
            'progress': status.get('CompletionPercentage', 0) if status else 0,
            'job_id': status.get('JobId', '') if status else '',
            'time_remaining': status.get('EstimatedTimeRemainingInMs', 0) if status else 0
        }

    def wait_for_render(
        self,
        timeout_seconds: Optional[int] = None,
        poll_interval: float = 1.0
    ) -> bool:
        """Wait for the current render to complete.

        Args:
            timeout_seconds: Maximum time to wait (None = no timeout).
            poll_interval: How often to check status (seconds).

        Returns:
            True if render completed successfully, False if timed out or failed.
        """
        import time

        start_time = time.time()
        while self.is_rendering():
            if timeout_seconds is not None:
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    logging.warning(f"Render timed out after {timeout_seconds} seconds")
                    return False

            time.sleep(poll_interval)

        logging.info("Render completed")
        return True

    def render_timeline(
        self,
        timeline: Optional[Timeline] = None,
        preset: str = 'H.264 Master',
        output_path: Optional[Path] = None,
        filename: Optional[str] = None,
        wait: bool = True,
        timeout: Optional[int] = None
    ) -> Optional[Path]:
        """Convenience method to render a timeline to a file.

        Args:
            timeline: Timeline to render (defaults to current).
            preset: Render preset name.
            output_path: Output directory (defaults to project default).
            filename: Output filename (defaults to timeline name).
            wait: Whether to wait for render to complete.
            timeout: Timeout in seconds if waiting.

        Returns:
            Path to rendered file if successful, None otherwise.
        """
        project = self.get_current_project()
        if not project:
            return None

        # Set timeline
        if timeline:
            project.SetCurrentTimeline(timeline)
        else:
            timeline = project.GetCurrentTimeline()

        if not timeline:
            logging.error("No timeline available to render")
            return None

        timeline_name = timeline.GetName()

        # Set filename default
        if not filename:
            filename = timeline_name

        # Clear existing jobs
        self.delete_all_render_jobs()

        # Add render job
        job_id = self.add_render_job(
            timeline=timeline,
            preset=preset,
            output_path=output_path,
            filename=filename
        )

        if not job_id:
            return None

        # Start render
        if not self.start_render([job_id]):
            logging.error("Failed to start render")
            return None

        # Wait if requested
        if wait:
            if not self.wait_for_render(timeout_seconds=timeout):
                return None

        # Return expected output path
        if output_path:
            return output_path / f"{filename}.mp4"  # Assumes MP4 output
        return None


def get_default_client() -> ResolveClient:
    """Get a default ResolveClient instance.

    Returns:
        A ResolveClient configured with the first available installation.
    """
    return ResolveClient()
