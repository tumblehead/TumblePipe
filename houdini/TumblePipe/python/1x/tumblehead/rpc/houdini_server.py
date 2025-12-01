"""Houdini-compatible RPC server implementation.

This implementation uses traditional socket handling with threading to avoid
asyncio limitations in Houdini 20/21, while providing the same RPC interface.
"""

import json
import logging
import os
import socket
import socketserver
import threading
import time
import uuid
from typing import Optional, Callable, Dict, Any

from ..util.ipc import free_port
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


class HoudiniRpcHandler(socketserver.BaseRequestHandler):
    """Request handler for RPC connections."""

    def __init__(self, request, client_address, server):
        self.logger = logging.getLogger(__name__)
        super().__init__(request, client_address, server)

    def handle(self):
        """Handle incoming RPC request."""
        try:
            # Receive message (up to 16KB)
            data = self.request.recv(16384)
            if not data:
                return

            raw_message = data.decode("utf-8")
            self.logger.debug(
                f"Received message from {self.client_address}: {raw_message[:100]}..."
            )

            # Process message
            response = self._handle_rpc_message(raw_message)

            # Send response
            self.request.sendall(response.encode("utf-8"))

        except Exception as e:
            self.logger.error(
                f"Error handling request from {self.client_address}: {e}"
            )
            # Send error response
            error_response = self._create_error_response(
                "internal_error", str(e)
            )
            try:
                self.request.sendall(error_response.encode("utf-8"))
            except:
                pass

    def _handle_rpc_message(self, raw_message: str) -> str:
        """Process RPC message and return response."""
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
            self.logger.info(f"Processing command: {message.command}")

            # Execute command through registry
            # Note: This runs in the handler thread, but HOM calls
            # should be made thread-safe by the registry
            result = registry.execute(message.command, message.params)

            # Create response
            response = create_response(request_id, result, timestamp)
            return serialize_message(response)

        except ValueError as e:
            # Parameter validation or command not found
            self.logger.warning(f"Invalid request: {e}")
            error = create_error(
                request_id, ErrorCode.INVALID_PARAMS, str(e), timestamp
            )
            return serialize_message(error)

        except Exception as e:
            # Unexpected error during command execution
            self.logger.error(f"Command execution error: {e}", exc_info=True)
            error = create_error(
                request_id, ErrorCode.EXECUTION_ERROR, str(e), timestamp
            )
            return serialize_message(error)

    def _create_error_response(self, error_code: str, message: str) -> str:
        """Create a basic error response."""
        error = create_error(
            str(uuid.uuid4()), ErrorCode.INTERNAL_ERROR, message, time.time()
        )
        return serialize_message(error)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server that allows reuse of address."""

    allow_reuse_address = True
    daemon_threads = True


class HoudiniRpcServer:
    """RPC server compatible with Houdini's threading limitations.

    This server uses traditional socket handling with threading instead
    of asyncio to avoid Houdini 20/21 asyncio limitations.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: Optional[int] = None,
        log_level: int = logging.INFO,
    ):
        """Initialize the RPC server.

        Args:
            host: Host interface to bind to
            port: Port to bind to (configurable via TH_COMMAND_PORT, default 1332)
            log_level: Logging level for server operations
        """
        self._host = host
        self._server: Optional[ThreadedTCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False

        # Set up logging first
        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(log_level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

        # Get port from environment variable or use provided/default
        if port is None:
            # Check TH_COMMAND_PORT environment variable
            env_port = os.environ.get("TH_COMMAND_PORT")
            if env_port:
                try:
                    preferred_port = int(env_port)
                    self._port = self._find_available_port([preferred_port])
                    self._logger.info(
                        f"Using TH_COMMAND_PORT: {preferred_port}"
                    )
                except ValueError:
                    self._logger.warning(
                        f"Invalid TH_COMMAND_PORT value: {env_port}, using default"
                    )
                    self._port = self._find_available_port([1332, 1333, 1334])
            else:
                # Use default port 1332 with fallbacks
                self._port = self._find_available_port([1332, 1333, 1334])
        else:
            self._port = port

    def _find_available_port(self, preferred_ports):
        """Find an available port from preferred list, fallback to free port."""
        for port in preferred_ports:
            try:
                # Try to bind to the port to see if it's available
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
                )
                test_socket.bind((self._host, port))
                test_socket.close()
                return port
            except OSError:
                continue

        # If all preferred ports are busy, use free_port as fallback
        return free_port()

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
        return self._running and self._server is not None

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information for clients."""
        return {
            "host": self._host,
            "port": self._port,
            "running": self.is_running,
            "commands": registry.list_commands(),
        }

    def start(self):
        """Start the RPC server in a background thread."""
        if self._running:
            self._logger.warning("Server is already running")
            return

        try:
            # Create server
            self._server = ThreadedTCPServer(
                (self._host, self._port), HoudiniRpcHandler
            )
            actual_port = self._server.server_address[1]
            self._port = actual_port

            self._logger.info(f"Created server at {self._host}:{self._port}")

            # Start server in background thread
            self._server_thread = threading.Thread(
                target=self._run_server,
                name="HoudiniRpcServer",
                daemon=True,
            )
            self._server_thread.start()

            # Wait a moment for server to start
            time.sleep(0.1)

            if self._server_thread.is_alive():
                self._running = True
                self._logger.info(
                    f"RPC server started on {self._host}:{self._port}"
                )
            else:
                raise RuntimeError("Server thread failed to start")

        except Exception as e:
            self._logger.error(f"Failed to start server: {e}")
            self._cleanup()
            raise

    def _run_server(self):
        """Run the server (called in background thread)."""
        try:
            self._logger.info("Server thread starting...")
            self._server.serve_forever()
        except Exception as e:
            self._logger.error(f"Server error: {e}", exc_info=True)
        finally:
            self._logger.info("Server thread ending...")
            self._running = False

    def stop(self):
        """Stop the RPC server."""
        if not self._running:
            self._logger.warning("Server is not running")
            return

        self._logger.info("Stopping RPC server...")
        self._running = False

        # Shutdown server
        if self._server:
            self._server.shutdown()
            self._server.server_close()

        # Wait for server thread to finish
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
            if self._server_thread.is_alive():
                self._logger.warning("Server thread did not stop gracefully")

        self._cleanup()
        self._logger.info("RPC server stopped")

    def _cleanup(self):
        """Clean up server resources."""
        self._server = None
        self._server_thread = None
        self._running = False

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


def check_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding."""
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
    """Start the global RPC server instance."""
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
    """Get the current server instance."""
    return _server_instance


def is_server_running() -> bool:
    """Check if the global server is running."""
    return _server_instance is not None and _server_instance.is_running


def get_connection_info() -> Optional[Dict[str, Any]]:
    """Get connection information for the running server."""
    if _server_instance:
        return _server_instance.get_connection_info()
    return None
