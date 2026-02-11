"""
DaVinci Resolve background service.

This service monitors a command queue and executes Resolve operations.
It supports two operating modes:

1. Interactive Mode: Connects to an already-running Resolve instance
2. Headless Mode: Launches Resolve with -nogui for batch operations

Usage:
    from tumblehead.apps.resolve import ResolveService

    service = ResolveService(queue_path=Path('/path/to/queue'))
    service.start()  # Blocks and processes commands

Or run directly:
    python -m tumblehead.apps.resolve.service --queue-path /path/to/queue
"""

from typing import Optional, Callable
from pathlib import Path
from datetime import datetime
import subprocess
import logging
import signal
import time
import sys

from tumblehead.apps.resolve.client import ResolveClient
from tumblehead.apps.resolve.queue import (
    ResolveCommand,
    read_pending_commands,
    move_to_running,
    mark_command_complete,
    mark_command_failed,
    cleanup_old_commands
)


class ResolveService:
    """Background service for processing Resolve commands.

    This service:
    - Polls a queue directory for pending commands
    - Connects to Resolve (or launches headless)
    - Executes commands and updates their status
    - Handles graceful shutdown on SIGINT/SIGTERM

    Example:
        service = ResolveService(
            queue_path=Path('export:/other/resolve_queue'),
            poll_interval=5.0
        )
        service.start()
    """

    def __init__(
        self,
        queue_path: Path,
        poll_interval: float = 5.0,
        headless_mode: bool = False,
        auto_cleanup: bool = True,
        cleanup_days: int = 7
    ):
        """Initialize the service.

        Args:
            queue_path: Path to the queue directory.
            poll_interval: Seconds between queue polls.
            headless_mode: Whether to launch Resolve in headless mode.
            auto_cleanup: Whether to auto-cleanup old completed commands.
            cleanup_days: Days to keep completed commands.
        """
        self.queue_path = queue_path
        self.poll_interval = poll_interval
        self.headless_mode = headless_mode
        self.auto_cleanup = auto_cleanup
        self.cleanup_days = cleanup_days

        self._client: Optional[ResolveClient] = None
        self._headless_process: Optional[subprocess.Popen] = None
        self._running = False
        self._handlers: dict[str, Callable] = {}

        # Register default command handlers
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register handlers for built-in command types."""
        self._handlers = {
            'import_render': self._handle_import_render,
            'relink': self._handle_relink,
            'create_timeline': self._handle_create_timeline,
            'render': self._handle_render,
            'generate_daily': self._handle_generate_daily,
        }

    def register_handler(self, action: str, handler: Callable):
        """Register a custom command handler.

        Args:
            action: Command action name.
            handler: Function(client, command) -> dict that processes the command.
        """
        self._handlers[action] = handler

    def _connect(self) -> bool:
        """Connect to Resolve (launch headless if configured).

        Returns:
            True if connected successfully.
        """
        self._client = ResolveClient()

        if not self._client.is_available:
            logging.error("Resolve is not installed")
            return False

        if self.headless_mode:
            return self._launch_headless()
        else:
            return self._client.connect()

    def _launch_headless(self) -> bool:
        """Launch Resolve in headless mode and connect.

        Returns:
            True if launched and connected successfully.
        """
        if not self._client or not self._client.installation:
            return False

        resolve_exe = self._client.installation.executable_path
        logging.info(f"Launching Resolve in headless mode: {resolve_exe}")

        try:
            self._headless_process = subprocess.Popen(
                [str(resolve_exe), '-nogui'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait for Resolve to start and API to become available
            max_wait = 60  # seconds
            wait_interval = 2
            elapsed = 0

            while elapsed < max_wait:
                time.sleep(wait_interval)
                elapsed += wait_interval

                if self._client.connect():
                    logging.info("Connected to headless Resolve")
                    return True

                logging.debug(f"Waiting for Resolve API... ({elapsed}s)")

            logging.error("Timeout waiting for Resolve to start")
            self._shutdown_headless()
            return False

        except Exception as e:
            logging.error(f"Failed to launch Resolve: {e}")
            return False

    def _shutdown_headless(self):
        """Shut down headless Resolve process."""
        if self._headless_process:
            logging.info("Shutting down headless Resolve")
            self._headless_process.terminate()
            try:
                self._headless_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._headless_process.kill()
            self._headless_process = None

    def _ensure_project(self, project_name: str) -> bool:
        """Ensure the specified project is open.

        Args:
            project_name: Name of the project to open.

        Returns:
            True if project is open.
        """
        if not self._client:
            return False

        current_project = self._client.get_current_project()
        if current_project and current_project.GetName() == project_name:
            return True

        # Need to open the project
        pm = self._client.get_project_manager()
        if not pm:
            return False

        # Try to load the project
        project = pm.LoadProject(project_name)
        if project:
            logging.info(f"Opened project: {project_name}")
            return True

        logging.error(f"Failed to open project: {project_name}")
        return False

    def start(self):
        """Start the service (blocking).

        This method blocks and continuously processes commands until
        stop() is called or a signal is received.
        """
        logging.info(f"Starting Resolve service, queue: {self.queue_path}")

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Connect to Resolve
        if not self._connect():
            logging.error("Failed to connect to Resolve")
            return

        self._running = True
        last_cleanup = time.time()

        while self._running:
            try:
                # Process pending commands
                self._process_queue()

                # Periodic cleanup
                if self.auto_cleanup:
                    now = time.time()
                    if now - last_cleanup > 3600:  # Every hour
                        cleanup_old_commands(self.queue_path, self.cleanup_days)
                        last_cleanup = now

                # Wait before next poll
                time.sleep(self.poll_interval)

            except Exception as e:
                logging.error(f"Error in service loop: {e}")
                time.sleep(self.poll_interval)

        # Cleanup
        self._shutdown_headless()
        logging.info("Resolve service stopped")

    def stop(self):
        """Signal the service to stop."""
        logging.info("Stopping Resolve service...")
        self._running = False

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logging.info(f"Received signal {signum}")
        self.stop()

    def _process_queue(self):
        """Process all pending commands in the queue."""
        pending = read_pending_commands(self.queue_path)

        for command_path, command in pending:
            if not self._running:
                break

            logging.info(f"Processing command: {command.action} ({command.command_id})")

            # Move to running
            running_path = move_to_running(command_path, self.queue_path)

            try:
                # Ensure correct project is open
                if command.project:
                    if not self._ensure_project(command.project):
                        raise Exception(f"Failed to open project: {command.project}")

                # Find and execute handler
                handler = self._handlers.get(command.action)
                if not handler:
                    raise Exception(f"Unknown action: {command.action}")

                result = handler(self._client, command)
                mark_command_complete(running_path, self.queue_path, result)

            except Exception as e:
                error_msg = str(e)
                logging.error(f"Command failed: {error_msg}")
                mark_command_failed(running_path, self.queue_path, error_msg)

    def process_single(self, command: ResolveCommand) -> dict:
        """Process a single command (for testing).

        Args:
            command: Command to process.

        Returns:
            Result dictionary.
        """
        if not self._client:
            if not self._connect():
                raise Exception("Failed to connect to Resolve")

        if command.project:
            if not self._ensure_project(command.project):
                raise Exception(f"Failed to open project: {command.project}")

        handler = self._handlers.get(command.action)
        if not handler:
            raise Exception(f"Unknown action: {command.action}")

        return handler(self._client, command)

    # =========================================================================
    # Command Handlers
    # =========================================================================

    def _handle_import_render(
        self,
        client: ResolveClient,
        command: ResolveCommand
    ) -> dict:
        """Handle import_render command."""
        params = command.params
        render_path = Path(params['render_path'])
        folder_path = params.get('folder_path')
        shot = params.get('shot', '')
        department = params.get('department', '')
        version = params.get('version', '')

        # Get or create target folder
        folder = None
        if folder_path:
            folder = client.get_or_create_folder(folder_path)
            if not folder:
                raise Exception(f"Failed to create folder: {folder_path}")

        # Import the media
        frame_range = params.get('frame_range')
        if frame_range:
            # Import as sequence
            first_frame = next(render_path.glob('*'), None)
            if not first_frame:
                raise Exception(f"No files found in: {render_path}")

            clip = client.import_frame_sequence(
                first_frame,
                folder=folder,
                start_frame=frame_range[0] if len(frame_range) > 0 else None,
                end_frame=frame_range[1] if len(frame_range) > 1 else None
            )
            clips = [clip] if clip else []
        else:
            # Import all files
            files = list(render_path.glob('*'))
            clips = client.import_media(files, folder=folder)

        if not clips:
            raise Exception(f"Failed to import media from: {render_path}")

        # Set pipeline metadata on imported clips
        for clip in clips:
            if shot and version:
                client.set_clip_pipeline_info(
                    clip,
                    shot=shot,
                    version=version,
                    department=department
                )

        return {
            'imported_count': len(clips),
            'clip_names': [c.GetName() for c in clips if c]
        }

    def _handle_relink(
        self,
        client: ResolveClient,
        command: ResolveCommand
    ) -> dict:
        """Handle relink command."""
        params = command.params
        shot_pattern = params['shot_pattern']
        old_version = params['old_version']
        new_version = params['new_version']

        count = client.relink_clips_to_version(
            shot_pattern,
            old_version,
            new_version
        )

        return {
            'relinked_count': count,
            'shot_pattern': shot_pattern,
            'old_version': old_version,
            'new_version': new_version
        }

    def _handle_create_timeline(
        self,
        client: ResolveClient,
        command: ResolveCommand
    ) -> dict:
        """Handle create_timeline command."""
        params = command.params
        timeline_name = params['timeline_name']
        shots = params.get('shots', [])

        # Get media pool
        media_pool = client.get_media_pool()
        if not media_pool:
            raise Exception("Failed to get media pool")

        # Find clips for each shot
        clips = []
        for shot_info in shots:
            clip_name = shot_info.get('clip_name') or shot_info.get('name')
            found = client.find_clips_by_name(clip_name)
            if found:
                clips.append(found[0])
            else:
                logging.warning(f"Clip not found: {clip_name}")

        if not clips:
            raise Exception("No clips found for timeline")

        # Create timeline with clips
        timeline = media_pool.CreateTimelineFromClips(timeline_name, clips)
        if not timeline:
            raise Exception(f"Failed to create timeline: {timeline_name}")

        return {
            'timeline_name': timeline_name,
            'clip_count': len(clips)
        }

    def _handle_render(
        self,
        client: ResolveClient,
        command: ResolveCommand
    ) -> dict:
        """Handle render command."""
        params = command.params
        timeline_name = params['timeline_name']
        preset = params['preset']
        output_path = params['output_path']

        project = client.get_current_project()
        if not project:
            raise Exception("No project open")

        # Find and set timeline
        timeline_count = project.GetTimelineCount()
        timeline = None
        for i in range(1, timeline_count + 1):
            t = project.GetTimelineByIndex(i)
            if t and t.GetName() == timeline_name:
                timeline = t
                break

        if not timeline:
            raise Exception(f"Timeline not found: {timeline_name}")

        project.SetCurrentTimeline(timeline)

        # Set render settings
        project.LoadRenderPreset(preset)
        project.SetRenderSettings({
            'TargetDir': output_path
        })

        # Set in/out points if specified
        if 'mark_in' in params:
            timeline.SetStartFrame(params['mark_in'])
        if 'mark_out' in params:
            timeline.SetEndFrame(params['mark_out'])

        # Add render job
        job_id = project.AddRenderJob()
        if not job_id:
            raise Exception("Failed to add render job")

        # Start rendering
        project.StartRendering()

        # Wait for render to complete
        while project.IsRenderingInProgress():
            time.sleep(1)

        return {
            'job_id': job_id,
            'timeline_name': timeline_name,
            'output_path': output_path
        }

    def _handle_generate_daily(
        self,
        client: ResolveClient,
        command: ResolveCommand
    ) -> dict:
        """Handle generate_daily command.

        This is a placeholder - full implementation requires pipeline integration
        to query for recent renders.
        """
        params = command.params
        date = params.get('date', datetime.now().strftime('%Y-%m-%d'))

        # This would need pipeline integration to:
        # 1. Query for renders from the specified date
        # 2. Build a timeline with those renders
        # 3. Apply burn-ins
        # 4. Render to output

        return {
            'status': 'not_implemented',
            'message': 'Daily generation requires pipeline integration',
            'date': date
        }


def main():
    """Main entry point for running the service."""
    import argparse

    parser = argparse.ArgumentParser(description='DaVinci Resolve Background Service')
    parser.add_argument(
        '--queue-path',
        type=Path,
        required=True,
        help='Path to the command queue directory'
    )
    parser.add_argument(
        '--poll-interval',
        type=float,
        default=5.0,
        help='Seconds between queue polls (default: 5)'
    )
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Launch Resolve in headless mode'
    )
    parser.add_argument(
        '--no-cleanup',
        action='store_true',
        help='Disable automatic cleanup of old commands'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s %(levelname)s: %(message)s'
    )

    # Create and start service
    service = ResolveService(
        queue_path=args.queue_path,
        poll_interval=args.poll_interval,
        headless_mode=args.headless,
        auto_cleanup=not args.no_cleanup
    )

    service.start()


if __name__ == '__main__':
    main()
