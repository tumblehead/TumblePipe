"""RPC server implementation for running inside Houdini context.

Provides a threaded server that can handle RPC requests from external clients
while running within a Houdini session, enabling agent automation and
remote control of Houdini operations.
"""

import asyncio
import json
import logging
import socket
import threading
import time
import uuid
from contextlib import suppress
from typing import Optional, Callable

from ..util.ipc import free_port, Server as BaseServer
from .protocol import (
    MessageType,
    ErrorCode,
    RpcRequest,
    RpcResponse,
    RpcError,
    deserialize_message,
    serialize_message,
    create_response,
    create_error,
)
from .commands import registry


class HoudiniRpcServer:
    """RPC server for handling remote commands in Houdini context."""

    def __init__(
        self,
        host: str = "localhost",
        port: Optional[int] = None,
        log_level: int = logging.INFO,
    ):
        """Initialize the RPC server.

        Args:
            host: Host interface to bind to
            port: Port to bind to (auto-assigned if None)
            log_level: Logging level for server operations
        """
        self._host = host
        self._port = port or free_port()
        self._server: Optional[BaseServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

        # Set up logging
        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(log_level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    @property
    def host(self) -> str:
        """Get server host."""
        return self._host

    @property
    def port(self) -> int:
        """Get server port."""
        return self._port

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running

    def get_connection_info(self) -> dict:
        """Get connection information for clients.

        Returns:
            Dictionary with host, port, and status information
        """
        return {
            "host": self._host,
            "port": self._port,
            "running": self._running,
            "commands": registry.list_commands(),
        }

    async def _handle_message(self, raw_message: str) -> str:
        """Handle incoming RPC message.

        Args:
            raw_message: Raw message string from client

        Returns:
            Response message string
        """
        request_id = str(uuid.uuid4())
        timestamp = time.time()

        try:
            # Parse incoming message
            message = deserialize_message(raw_message)

            if not isinstance(message, RpcRequest):
                return serialize_message(
                    create_error(
                        request_id,
                        ErrorCode.INVALID_PARAMS,
                        "Expected request message",
                        timestamp,
                    )
                )

            request_id = message.id
            self._logger.info(f"Processing command: {message.command}")

            # Handle ping specially
            if message.command == "system.ping":
                result = "pong"
            else:
                # Execute command through registry
                result = registry.execute(message.command, message.params)

            # Create response
            response = create_response(request_id, result, timestamp)
            return serialize_message(response)

        except ValueError as e:
            # Parameter validation or command not found
            self._logger.warning(f"Invalid request: {e}")
            error = create_error(
                request_id, ErrorCode.INVALID_PARAMS, str(e), timestamp
            )
            return serialize_message(error)

        except Exception as e:
            # Unexpected error during command execution
            self._logger.error(f"Command execution error: {e}", exc_info=True)
            error = create_error(
                request_id, ErrorCode.EXECUTION_ERROR, str(e), timestamp
            )
            return serialize_message(error)

    def _run_server(self):
        """Run the asyncio server in a separate thread."""
        # Create new event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._start_server())
        except Exception as e:
            self._logger.error(f"Server error: {e}", exc_info=True)
        finally:
            self._loop.close()
            self._loop = None

    async def _start_server(self):
        """Start the asyncio server."""
        self._server = BaseServer(self._host, self._port, self._handle_message)

        try:
            async with self._server:
                self._running = True
                self._logger.info(
                    f"RPC server started on {self._host}:{self._port}"
                )

                # Keep server running until stopped
                while self._running:
                    await asyncio.sleep(1)

        except Exception as e:
            self._logger.error(f"Server startup error: {e}")
            raise
        finally:
            self._running = False
            self._server = None

    def start(self):
        """Start the RPC server in a background thread.

        This allows the server to run alongside Houdini's main thread
        without blocking the user interface.
        """
        if self._running:
            self._logger.warning("Server is already running")
            return

        self._server_thread = threading.Thread(
            target=self._run_server, name="HoudiniRpcServer", daemon=True
        )
        self._server_thread.start()

        # Wait a moment for server to start
        for _ in range(50):  # 5 seconds max
            if self._running:
                break
            time.sleep(0.1)

        if not self._running:
            raise RuntimeError("Failed to start RPC server")

        self._logger.info(f"RPC server running at {self._host}:{self._port}")

    def stop(self):
        """Stop the RPC server."""
        if not self._running:
            self._logger.warning("Server is not running")
            return

        self._running = False

        # Wait for server thread to finish
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
            if self._server_thread.is_alive():
                self._logger.warning("Server thread did not stop gracefully")

        self._logger.info("RPC server stopped")

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


def check_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding.

    Args:
        host: Host interface to check
        port: Port number to check

    Returns:
        True if port is available
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


# Global server instance for Houdini integration
_server_instance: Optional[HoudiniRpcServer] = None


def start_server(
    host: str = "localhost", port: Optional[int] = None
) -> HoudiniRpcServer:
    """Start the global RPC server instance.

    Args:
        host: Host interface to bind to
        port: Port to bind to (auto-assigned if None)

    Returns:
        Running server instance
    """
    global _server_instance

    if _server_instance and _server_instance.is_running:
        return _server_instance

    _server_instance = HoudiniRpcServer(host, port)
    _server_instance.start()
    return _server_instance


def stop_server():
    """Stop the global RPC server instance."""
    global _server_instance

    if _server_instance:
        _server_instance.stop()
        _server_instance = None


def get_server() -> Optional[HoudiniRpcServer]:
    """Get the current server instance.

    Returns:
        Current server instance or None if not running
    """
    return _server_instance


def is_server_running() -> bool:
    """Check if the global server is running.

    Returns:
        True if server is running
    """
    return _server_instance is not None and _server_instance.is_running


def get_connection_info() -> Optional[dict]:
    """Get connection information for the running server.

    Returns:
        Connection info dict or None if server not running
    """
    if _server_instance:
        return _server_instance.get_connection_info()
    return None
