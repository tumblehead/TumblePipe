"""Houdini startup integration for RPC server.

Provides automatic startup functionality for the RPC server when Houdini
launches, including environment detection and graceful error handling.
"""

import hou
import os
import logging
from pathlib import Path

from .houdini_server import start_server, is_server_running
from ..api import is_dev


def auto_start_rpc_server():
    """Automatically start RPC server if conditions are met.

    Starts the RPC server automatically when:
    - Development mode is enabled (TH_DEV=1)
    - No server is already running
    - Houdini is in GUI mode (not batch)
    """
    # Check if we should auto-start
    if not should_auto_start():
        return

    # Check if server already running
    if is_server_running():
        print("RPC server already running, skipping auto-start")
        return

    try:
        # Start server with default settings
        server = start_server()

        # Show startup message
        message = f"RPC server started on {server.host}:{server.port}"
        print(f"[Pipeline] {message}")

        if hou.isUIAvailable():
            hou.ui.setStatusMessage(message, severity=hou.severityType.Message)

        # Log available commands for reference
        from .thread_safe_commands import registry

        commands = registry.list_commands()
        print(f"[Pipeline] Available commands: {', '.join(commands)}")

    except Exception as e:
        error_msg = f"Failed to auto-start RPC server: {e}"
        print(f"[Pipeline] ERROR: {error_msg}")

        if hou.isUIAvailable():
            hou.ui.displayMessage(
                f"RPC Server Auto-start Failed\\n\\n{error_msg}",
                severity=hou.severityType.ImportantMessage,
                default_choice=0,
                close_choice=0,
            )


def should_auto_start() -> bool:
    """Determine if RPC server should auto-start.

    Returns:
        True if auto-start conditions are met
    """
    # Check development mode
    if not is_dev():
        return False

    # Check if Houdini is in GUI mode
    if not hou.isUIAvailable():
        print("[Pipeline] Skipping RPC auto-start in batch mode")
        return False

    # Check for explicit disable flag
    if os.environ.get("TH_RPC_DISABLE", "0") == "1":
        print("[Pipeline] RPC auto-start disabled by TH_RPC_DISABLE")
        return False

    return True


def create_startup_message() -> str:
    """Create startup message for RPC server.

    Returns:
        Formatted startup message
    """
    if is_server_running():
        from .houdini_server import get_connection_info

        info = get_connection_info()
        if info:
            return (
                f"RPC Server: {info['host']}:{info['port']} "
                f"({len(info['commands'])} commands)"
            )

    return "RPC Server: Not running"


def setup_logging():
    """Setup logging for RPC server operations."""
    # Only setup logging in development mode
    if not is_dev():
        return

    # Create logs directory if it doesn't exist
    log_dir = Path.home() / "houdini_rpc_logs"
    log_dir.mkdir(exist_ok=True)

    # Setup file logging
    log_file = log_dir / "rpc_server.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )


def register_event_handlers():
    """Register Houdini event handlers for RPC integration."""

    # Register file save handler to update server status
    def on_file_save():
        """Handle file save event."""
        if is_server_running():
            # Could add custom logic here, like notifying external clients
            pass

    # Register scene load handler
    def on_scene_load():
        """Handle scene load event."""
        if is_server_running():
            # Could add custom logic here
            pass

    # Note: Houdini event handling would need to be implemented
    # using hou event callbacks if available


def shutdown_rpc_server():
    """Shutdown RPC server on Houdini exit."""
    if is_server_running():
        from .houdini_server import stop_server

        try:
            stop_server()
            print("[Pipeline] RPC server shutdown complete")
        except Exception as e:
            print(f"[Pipeline] Error during RPC shutdown: {e}")


# Startup sequence
def initialize():
    """Initialize RPC system on Houdini startup."""
    try:
        # Setup logging
        setup_logging()

        # Register event handlers
        register_event_handlers()

        # Auto-start server if conditions are met
        auto_start_rpc_server()

        # Print status message
        status = create_startup_message()
        print(f"[Pipeline] {status}")

    except Exception as e:
        print(f"[Pipeline] RPC initialization error: {e}")


# Entry point for 456.py or startup scripts
if __name__ == "__main__":
    initialize()
