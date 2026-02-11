"""
DaVinci Resolve daily edit generation utilities.

This module provides functionality to automatically generate daily edit
timelines from completed renders, typically run at end of day as a
scheduled task.

Features:
- Query pipeline for completed renders (by date or explicit list)
- Build ordered timeline from shots
- Apply burn-in overlays (timecode, shot name, version)
- Render to review format

Usage:
    from tumblehead.apps.resolve import DailyGenerator, DailyConfig

    config = DailyConfig(
        output_path=Path("/renders/dailies/"),
        render_preset='H.264 Master',
        burn_in_template='review'
    )

    generator = DailyGenerator(config)
    timeline = generator.generate_daily(date='2024-01-15')
    output_path = generator.render_daily(timeline)
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Callable
from datetime import datetime, timedelta
from pathlib import Path
import logging


@dataclass
class DailyConfig:
    """Configuration for daily edit generation.

    Attributes:
        output_path: Directory for rendered daily output.
        render_preset: Resolve render preset name.
        burn_in_template: Burn-in template ('none', 'default', 'review', 'client').
        include_audio: Whether to include audio tracks.
        timeline_prefix: Prefix for daily timeline names.
        sort_by: How to sort shots ('shot_name', 'render_time', 'sequence').
        fps: Frame rate for timeline (None = project default).
        resolution: Resolution tuple (None = project default).
    """
    output_path: Path
    render_preset: str = 'H.264 Master'
    burn_in_template: str = 'review'
    include_audio: bool = True
    timeline_prefix: str = 'Daily'
    sort_by: str = 'shot_name'
    fps: Optional[float] = None
    resolution: Optional[tuple[int, int]] = None


@dataclass
class RenderInfo:
    """Information about a completed render.

    Attributes:
        shot: Shot name (e.g., 'SEQ010_SHOT020').
        department: Department name (e.g., 'comp', 'lighting').
        version: Version string (e.g., 'v0003').
        render_path: Path to rendered frames/files.
        first_frame: First frame number.
        last_frame: Last frame number.
        render_time: When the render completed.
        user: User who submitted the render.
        metadata: Additional metadata.
    """
    shot: str
    department: str
    version: str
    render_path: Path
    first_frame: int
    last_frame: int
    render_time: Optional[datetime] = None
    user: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class DailyGenerator:
    """Generator for daily edit timelines.

    This class handles the workflow of:
    1. Querying for completed renders
    2. Importing media to Resolve
    3. Building timeline from shots
    4. Applying burn-ins
    5. Rendering the daily

    Example:
        config = DailyConfig(output_path=Path("/dailies/"))
        generator = DailyGenerator(config)

        # Generate from today's renders
        timeline = generator.generate_daily()

        # Or from explicit list
        renders = [RenderInfo(...), RenderInfo(...)]
        timeline = generator.generate_from_renders(renders)

        # Render to file
        output = generator.render_daily(timeline)
    """

    def __init__(
        self,
        config: DailyConfig,
        client: Optional[Any] = None,
        render_query_func: Optional[Callable] = None
    ):
        """Initialize the daily generator.

        Args:
            config: Daily generation configuration.
            client: Optional ResolveClient (creates one if not provided).
            render_query_func: Optional function to query renders from pipeline.
                              Signature: (date: str) -> list[RenderInfo]
        """
        self.config = config
        self._render_query_func = render_query_func

        # Import ResolveClient here to avoid circular imports
        if client is None:
            from tumblehead.apps.resolve.client import ResolveClient
            self._client = ResolveClient()
        else:
            self._client = client

        self._connected = False

    @property
    def client(self) -> Any:
        """Get the Resolve client, connecting if needed."""
        if not self._connected and not self._client.is_connected:
            self._client.connect()
            self._connected = True
        return self._client

    def query_renders(
        self,
        date: Optional[str] = None,
        departments: Optional[list[str]] = None,
        shots: Optional[list[str]] = None
    ) -> list[RenderInfo]:
        """Query the pipeline for completed renders.

        Args:
            date: Date string (YYYY-MM-DD), defaults to today.
            departments: Optional filter by departments.
            shots: Optional filter by shot names.

        Returns:
            List of RenderInfo objects for matching renders.
        """
        if self._render_query_func is None:
            logging.warning("No render query function configured")
            return []

        # Default to today
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        # Call the configured query function
        try:
            renders = self._render_query_func(date)
        except Exception as e:
            logging.error(f"Failed to query renders: {e}")
            return []

        # Apply filters
        if departments:
            renders = [r for r in renders if r.department in departments]
        if shots:
            renders = [r for r in renders if r.shot in shots]

        return renders

    def sort_renders(self, renders: list[RenderInfo]) -> list[RenderInfo]:
        """Sort renders according to config.sort_by.

        Args:
            renders: List of renders to sort.

        Returns:
            Sorted list of renders.
        """
        sort_key = self.config.sort_by

        if sort_key == 'shot_name':
            return sorted(renders, key=lambda r: r.shot)
        elif sort_key == 'render_time':
            return sorted(renders, key=lambda r: r.render_time or datetime.min)
        elif sort_key == 'sequence':
            # Sort by sequence number extracted from shot name
            def extract_seq_shot(r):
                parts = r.shot.split('_')
                seq = int(''.join(filter(str.isdigit, parts[0]))) if parts else 0
                shot = int(''.join(filter(str.isdigit, parts[1]))) if len(parts) > 1 else 0
                return (seq, shot)
            return sorted(renders, key=extract_seq_shot)
        else:
            return renders

    def import_render(self, render: RenderInfo) -> Optional[Any]:
        """Import a render to the Resolve media pool.

        Args:
            render: RenderInfo describing the render to import.

        Returns:
            MediaPoolItem clip, or None on failure.
        """
        client = self.client

        # Create folder structure: Dailies/YYYY-MM-DD/department
        date_str = datetime.now().strftime('%Y-%m-%d')
        folder_path = f"Dailies/{date_str}/{render.department}"

        folder = client.get_or_create_folder(folder_path)
        if not folder:
            logging.error(f"Failed to create folder: {folder_path}")
            return None

        # Determine first frame path
        render_path = render.render_path
        first_frame_name = f"{render.shot}.{str(render.first_frame).zfill(4)}.exr"
        first_frame_path = render_path / first_frame_name

        # Import frame sequence
        clip = client.import_frame_sequence(
            first_frame_path,
            folder=folder,
            start_frame=render.first_frame,
            end_frame=render.last_frame
        )

        if clip:
            # Set pipeline metadata
            client.set_clip_pipeline_info(
                clip,
                shot=render.shot,
                version=render.version,
                department=render.department
            )
            logging.info(f"Imported: {render.shot} {render.version}")
        else:
            logging.warning(f"Failed to import: {render.shot}")

        return clip

    def build_daily_timeline(
        self,
        renders: list[RenderInfo],
        clips: list[Any],
        timeline_name: Optional[str] = None
    ) -> Optional[Any]:
        """Build a timeline from imported clips.

        Args:
            renders: List of RenderInfo (for metadata).
            clips: List of MediaPoolItem clips.
            timeline_name: Optional timeline name (defaults to Daily_YYYY-MM-DD).

        Returns:
            Timeline object, or None on failure.
        """
        client = self.client

        # Generate timeline name
        if timeline_name is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            timeline_name = f"{self.config.timeline_prefix}_{date_str}"

        # Build shot list
        shots = []
        for render, clip in zip(renders, clips):
            if clip is None:
                continue
            shots.append({
                'name': render.shot,
                'clip': clip,
                'start': render.first_frame,
                'end': render.last_frame
            })

        if not shots:
            logging.error("No valid clips for timeline")
            return None

        # Create timeline
        timeline = client.build_shot_timeline(timeline_name, shots)

        if timeline:
            # Apply resolution/fps if configured
            if self.config.fps:
                timeline.SetSetting('timelineFrameRate', str(self.config.fps))
            if self.config.resolution:
                w, h = self.config.resolution
                timeline.SetSetting('timelineResolutionWidth', str(w))
                timeline.SetSetting('timelineResolutionHeight', str(h))

            logging.info(f"Created daily timeline: {timeline_name}")
        else:
            logging.error(f"Failed to create timeline: {timeline_name}")

        return timeline

    def apply_burn_in(self, timeline: Any) -> bool:
        """Apply burn-in overlay to timeline.

        Note: This requires a Fusion composition on the timeline.
        For complex burn-ins, consider using a pre-built Resolve macro.

        Args:
            timeline: Timeline to apply burn-in to.

        Returns:
            True if successful.
        """
        template = self.config.burn_in_template

        if template == 'none':
            return True

        # Burn-ins in Resolve are typically applied via:
        # 1. Data Burn-In effect in Color page
        # 2. Fusion Text+ nodes
        # 3. Render settings burn-in option

        # For now, we'll use the render settings approach
        # which is available in the Deliver page settings
        project = self.client.get_current_project()
        if not project:
            return False

        # Enable render burn-in if supported by preset
        settings = {}
        if template in ('default', 'review', 'client'):
            # These settings depend on the render preset supporting burn-in
            # Most professional presets do
            settings['EnableBurnIn'] = True

            # Configure burn-in elements based on template
            if template == 'review':
                # Include: timecode, reel name, source clip
                settings['BurnInTimecode'] = True
                settings['BurnInSourceClipName'] = True
            elif template == 'client':
                # Minimal: just timecode
                settings['BurnInTimecode'] = True
            else:  # default
                settings['BurnInTimecode'] = True
                settings['BurnInReelName'] = True

        try:
            project.SetRenderSettings(settings)
            logging.info(f"Applied burn-in template: {template}")
            return True
        except Exception as e:
            logging.warning(f"Could not apply burn-in settings: {e}")
            return False

    def render_daily(
        self,
        timeline: Any,
        wait: bool = True,
        timeout: Optional[int] = None
    ) -> Optional[Path]:
        """Render the daily timeline.

        Args:
            timeline: Timeline to render.
            wait: Whether to wait for render completion.
            timeout: Timeout in seconds if waiting.

        Returns:
            Path to rendered file, or None on failure.
        """
        client = self.client

        # Generate output filename
        date_str = datetime.now().strftime('%Y-%m-%d')
        timeline_name = timeline.GetName() if hasattr(timeline, 'GetName') else 'Daily'
        filename = f"{timeline_name}"

        # Ensure output directory exists
        self.config.output_path.mkdir(parents=True, exist_ok=True)

        # Apply burn-in settings
        self.apply_burn_in(timeline)

        # Render timeline
        output_path = client.render_timeline(
            timeline=timeline,
            preset=self.config.render_preset,
            output_path=self.config.output_path,
            filename=filename,
            wait=wait,
            timeout=timeout
        )

        if output_path:
            logging.info(f"Rendered daily to: {output_path}")

        return output_path

    def generate_daily(
        self,
        date: Optional[str] = None,
        departments: Optional[list[str]] = None,
        shots: Optional[list[str]] = None
    ) -> Optional[Any]:
        """Generate a daily timeline from renders on a given date.

        This is the main entry point for automatic daily generation.

        Args:
            date: Date string (YYYY-MM-DD), defaults to today.
            departments: Optional filter by departments.
            shots: Optional filter by shot names.

        Returns:
            Timeline object, or None if no renders found.
        """
        # Query renders
        renders = self.query_renders(date, departments, shots)
        if not renders:
            logging.info(f"No renders found for date: {date or 'today'}")
            return None

        logging.info(f"Found {len(renders)} renders")

        # Sort renders
        renders = self.sort_renders(renders)

        # Import each render
        clips = []
        for render in renders:
            clip = self.import_render(render)
            clips.append(clip)

        # Filter out failed imports
        valid_pairs = [(r, c) for r, c in zip(renders, clips) if c is not None]
        if not valid_pairs:
            logging.error("No clips imported successfully")
            return None

        renders, clips = zip(*valid_pairs)

        # Build timeline
        timeline = self.build_daily_timeline(list(renders), list(clips))

        return timeline

    def generate_from_renders(
        self,
        renders: list[RenderInfo],
        timeline_name: Optional[str] = None
    ) -> Optional[Any]:
        """Generate a daily timeline from an explicit list of renders.

        Args:
            renders: List of RenderInfo objects.
            timeline_name: Optional custom timeline name.

        Returns:
            Timeline object, or None on failure.
        """
        if not renders:
            logging.error("No renders provided")
            return None

        # Sort renders
        renders = self.sort_renders(renders)

        # Import each render
        clips = []
        for render in renders:
            clip = self.import_render(render)
            clips.append(clip)

        # Filter out failed imports
        valid_pairs = [(r, c) for r, c in zip(renders, clips) if c is not None]
        if not valid_pairs:
            logging.error("No clips imported successfully")
            return None

        renders, clips = zip(*valid_pairs)

        # Build timeline
        timeline = self.build_daily_timeline(list(renders), list(clips), timeline_name)

        return timeline

    def generate_and_render(
        self,
        date: Optional[str] = None,
        departments: Optional[list[str]] = None,
        shots: Optional[list[str]] = None,
        wait: bool = True,
        timeout: Optional[int] = None
    ) -> Optional[Path]:
        """Generate and render a daily in one step.

        This is a convenience method combining generate_daily() and render_daily().

        Args:
            date: Date string (YYYY-MM-DD), defaults to today.
            departments: Optional filter by departments.
            shots: Optional filter by shot names.
            wait: Whether to wait for render completion.
            timeout: Timeout in seconds if waiting.

        Returns:
            Path to rendered file, or None on failure.
        """
        timeline = self.generate_daily(date, departments, shots)
        if not timeline:
            return None

        return self.render_daily(timeline, wait=wait, timeout=timeout)


def create_daily_config_from_project(project_name: str) -> DailyConfig:
    """Create a DailyConfig with project-specific defaults.

    Args:
        project_name: Name of the project.

    Returns:
        DailyConfig configured for the project.
    """
    # This could read from pipeline config, for now uses sensible defaults
    return DailyConfig(
        output_path=Path(f"/renders/{project_name}/dailies/"),
        render_preset='H.264 Master',
        burn_in_template='review',
        include_audio=True,
        timeline_prefix=f'{project_name}_Daily',
        sort_by='sequence'
    )
