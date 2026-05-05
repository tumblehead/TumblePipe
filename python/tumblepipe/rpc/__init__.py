"""RPC (Remote Procedure Call) system for Houdini pipeline integration.

This module provides server and client components for enabling external agents
to interact with a running Houdini instance through command-based communication.

Features:
- Thread-safe socket handling (compatible with Houdini 20/21)
- Console output streaming and capture
- Instance manipulation API for scene management
- Real-time message streaming during command execution
- Comprehensive error handling and validation

The implementation avoids asyncio limitations in older Houdini versions while
maintaining compatibility with HOM (Houdini Object Model) functions through
deferred main thread execution.
"""

from .houdini_server import (
    HoudiniRpcServer,
    start_server,
    stop_server,
    get_server,
    is_server_running,
    get_connection_info,
)
from .commands import registry
from .protocol import (
    MessageType,
    ErrorCode,
    RpcRequest,
    RpcResponse,
    RpcError,
    RpcStreamMessage,
    serialize_message,
    deserialize_message,
    create_stream_message,
)
from .console_capture import (
    ConsoleCapture,
    get_global_capture,
    capture_command_output,
    captured_execution,
)

# Import instance API commands to register them
from . import instance_api

__all__ = [
    "HoudiniRpcServer",
    "start_server",
    "stop_server",
    "get_server",
    "is_server_running",
    "get_connection_info",
    "registry",
    "MessageType",
    "ErrorCode",
    "RpcRequest",
    "RpcResponse",
    "RpcError",
    "RpcStreamMessage",
    "serialize_message",
    "deserialize_message",
    "create_stream_message",
    "ConsoleCapture",
    "get_global_capture",
    "capture_command_output",
    "captured_execution",
]
