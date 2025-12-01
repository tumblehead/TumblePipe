"""RPC protocol definitions and message handling.

Defines the communication protocol between Houdini server and external clients,
including message serialization, command routing, and response handling.
"""

import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Union
from enum import Enum


class MessageType(Enum):
    """Message type enumeration for RPC communication."""

    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    STREAM = "stream"


class ErrorCode(Enum):
    """Error codes for RPC communication."""

    UNKNOWN_COMMAND = "unknown_command"
    INVALID_PARAMS = "invalid_params"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass
class RpcMessage:
    """Base RPC message structure."""

    id: str
    type: MessageType
    timestamp: float


@dataclass
class RpcRequest(RpcMessage):
    """RPC request message."""

    command: str
    params: Dict[str, Any]

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = MessageType(self.type)


@dataclass
class RpcResponse(RpcMessage):
    """RPC response message."""

    result: Any

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = MessageType(self.type)


@dataclass
class RpcError(RpcMessage):
    """RPC error message."""

    error_code: ErrorCode
    error_message: str

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = MessageType(self.type)
        if isinstance(self.error_code, str):
            self.error_code = ErrorCode(self.error_code)


@dataclass
class RpcStreamMessage(RpcMessage):
    """RPC streaming message for real-time output."""

    stream_type: str  # 'stdout', 'stderr', 'info'
    content: str
    sequence: int = 0

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = MessageType(self.type)


def serialize_message(
    message: Union[RpcRequest, RpcResponse, RpcError, RpcStreamMessage],
) -> str:
    """Serialize RPC message to JSON string.

    Args:
        message: RPC message to serialize

    Returns:
        JSON string representation of the message
    """
    data = asdict(message)

    # Convert enums to their values
    if "type" in data:
        data["type"] = data["type"].value
    if "error_code" in data:
        data["error_code"] = data["error_code"].value

    return json.dumps(data, separators=(",", ":"))


def deserialize_message(
    data: str,
) -> Union[RpcRequest, RpcResponse, RpcError, RpcStreamMessage]:
    """Deserialize JSON string to RPC message.

    Args:
        data: JSON string to deserialize

    Returns:
        RPC message object

    Raises:
        ValueError: If message format is invalid
        json.JSONDecodeError: If JSON is malformed
    """
    try:
        raw_data = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")

    if not isinstance(raw_data, dict):
        raise ValueError("Message must be a JSON object")

    # Validate required fields
    required_fields = ["id", "type", "timestamp"]
    for field in required_fields:
        if field not in raw_data:
            raise ValueError(f"Missing required field: {field}")

    msg_type = MessageType(raw_data["type"])

    # Create appropriate message type based on content
    if msg_type == MessageType.REQUEST:
        if "command" not in raw_data or "params" not in raw_data:
            raise ValueError("Request message missing command or params")
        return RpcRequest(**raw_data)
    elif msg_type == MessageType.RESPONSE:
        if "result" not in raw_data:
            raise ValueError("Response message missing result")
        return RpcResponse(**raw_data)
    elif msg_type == MessageType.ERROR:
        if "error_code" not in raw_data or "error_message" not in raw_data:
            raise ValueError(
                "Error message missing error_code or error_message"
            )
        return RpcError(**raw_data)
    elif msg_type == MessageType.STREAM:
        if "stream_type" not in raw_data or "content" not in raw_data:
            raise ValueError("Stream message missing stream_type or content")
        return RpcStreamMessage(**raw_data)
    else:
        # For PING/PONG, return basic message
        return RpcMessage(
            **{
                k: v
                for k, v in raw_data.items()
                if k in ["id", "type", "timestamp"]
            }
        )


def create_request(
    request_id: str, command: str, params: Dict[str, Any], timestamp: float
) -> RpcRequest:
    """Create an RPC request message.

    Args:
        request_id: Unique identifier for the request
        command: Command name to execute
        params: Command parameters
        timestamp: Message timestamp

    Returns:
        RPC request message
    """
    return RpcRequest(
        id=request_id,
        type=MessageType.REQUEST,
        timestamp=timestamp,
        command=command,
        params=params,
    )


def create_response(
    request_id: str, result: Any, timestamp: float
) -> RpcResponse:
    """Create an RPC response message.

    Args:
        request_id: ID of the request being responded to
        result: Command execution result
        timestamp: Message timestamp

    Returns:
        RPC response message
    """
    return RpcResponse(
        id=request_id,
        type=MessageType.RESPONSE,
        timestamp=timestamp,
        result=result,
    )


def create_error(
    request_id: str, error_code: ErrorCode, error_message: str, timestamp: float
) -> RpcError:
    """Create an RPC error message.

    Args:
        request_id: ID of the request that caused the error
        error_code: Error classification
        error_message: Human-readable error description
        timestamp: Message timestamp

    Returns:
        RPC error message
    """
    return RpcError(
        id=request_id,
        type=MessageType.ERROR,
        timestamp=timestamp,
        error_code=error_code,
        error_message=error_message,
    )


def create_stream_message(
    request_id: str,
    stream_type: str,
    content: str,
    sequence: int,
    timestamp: float,
) -> RpcStreamMessage:
    """Create an RPC stream message.

    Args:
        request_id: ID of the request generating this stream
        stream_type: Type of stream ('stdout', 'stderr', 'info')
        content: Content of the stream message
        sequence: Sequence number for ordering
        timestamp: Message timestamp

    Returns:
        RPC stream message
    """
    return RpcStreamMessage(
        id=request_id,
        type=MessageType.STREAM,
        timestamp=timestamp,
        stream_type=stream_type,
        content=content,
        sequence=sequence,
    )
