"""Streaming response system for large dataset operations.

This module provides streaming capabilities for RPC operations that return
large amounts of data, enabling efficient transfer and processing of geometry
data, scene hierarchies, and other large datasets.
"""

import json
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Union
from contextlib import contextmanager

try:
    import hou

    HOUDINI_AVAILABLE = True
except ImportError:
    HOUDINI_AVAILABLE = False


class StreamingSession:
    """Manages a streaming data session for large responses."""

    def __init__(
        self, session_id: str, total_chunks: int, chunk_size: int = 1000
    ):
        """Initialize streaming session.

        Args:
            session_id: Unique session identifier
            total_chunks: Total number of chunks in the stream
            chunk_size: Size of each chunk
        """
        self._session_id = session_id
        self._total_chunks = total_chunks
        self._chunk_size = chunk_size
        self._current_chunk = 0
        self._data_iterator: Optional[Iterator] = None
        self._metadata: Dict[str, Any] = {}
        self._created_time = time.time()
        self._last_access_time = time.time()
        self._lock = threading.Lock()
        self._completed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def total_chunks(self) -> int:
        return self._total_chunks

    @property
    def current_chunk(self) -> int:
        return self._current_chunk

    @property
    def is_completed(self) -> bool:
        return self._completed

    @property
    def progress(self) -> float:
        """Get progress as percentage (0.0 to 1.0)."""
        if self._total_chunks == 0:
            return 1.0
        return self._current_chunk / self._total_chunks

    def set_data_iterator(
        self, iterator: Iterator, metadata: Dict[str, Any] = None
    ):
        """Set the data iterator for this session."""
        with self._lock:
            self._data_iterator = iterator
            self._metadata = metadata or {}

    def get_next_chunk(self) -> Optional[Dict[str, Any]]:
        """Get the next chunk of data."""
        with self._lock:
            if self._completed or self._data_iterator is None:
                return None

            self._last_access_time = time.time()

            try:
                chunk_data = []
                for _ in range(self._chunk_size):
                    try:
                        item = next(self._data_iterator)
                        chunk_data.append(item)
                    except StopIteration:
                        self._completed = True
                        break

                if not chunk_data and self._completed:
                    return None

                chunk_response = {
                    "session_id": self._session_id,
                    "chunk_number": self._current_chunk,
                    "total_chunks": self._total_chunks,
                    "data": chunk_data,
                    "completed": self._completed,
                    "progress": self.progress,
                    "metadata": self._metadata,
                }

                self._current_chunk += 1
                return chunk_response

            except Exception as e:
                self._completed = True
                return {
                    "session_id": self._session_id,
                    "error": str(e),
                    "completed": True,
                }

    def get_info(self) -> Dict[str, Any]:
        """Get session information."""
        return {
            "session_id": self._session_id,
            "total_chunks": self._total_chunks,
            "current_chunk": self._current_chunk,
            "completed": self._completed,
            "progress": self.progress,
            "created_time": self._created_time,
            "last_access_time": self._last_access_time,
            "metadata": self._metadata,
        }


class StreamingManager:
    """Manages multiple streaming sessions."""

    def __init__(self, session_timeout: float = 3600.0):
        """Initialize streaming manager.

        Args:
            session_timeout: Session timeout in seconds
        """
        self._sessions: Dict[str, StreamingSession] = {}
        self._session_timeout = session_timeout
        self._lock = threading.RLock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._cleanup_running = False

        # Start cleanup thread
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Start the cleanup thread for expired sessions."""
        if self._cleanup_thread is not None:
            return

        def cleanup_loop():
            while self._cleanup_running:
                self._cleanup_expired_sessions()
                time.sleep(60)

        self._cleanup_running = True
        self._cleanup_thread = threading.Thread(
            target=cleanup_loop, daemon=True
        )
        self._cleanup_thread.start()

    def _cleanup_expired_sessions(self):
        """Clean up expired sessions."""
        current_time = time.time()
        expired_sessions = []

        with self._lock:
            for session_id, session in self._sessions.items():
                if (
                    current_time - session._last_access_time
                    > self._session_timeout
                ):
                    expired_sessions.append(session_id)

            for session_id in expired_sessions:
                del self._sessions[session_id]

    def create_session(
        self,
        data_iterator: Iterator,
        chunk_size: int = 1000,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Create a new streaming session.

        Args:
            data_iterator: Iterator providing the data to stream
            chunk_size: Number of items per chunk
            metadata: Optional metadata for the session

        Returns:
            Session ID
        """
        session_id = str(uuid.uuid4())

        # Estimate total chunks (if possible)
        total_chunks = -1
        if hasattr(data_iterator, "__len__"):
            try:
                total_items = len(data_iterator)
                total_chunks = (total_items + chunk_size - 1) // chunk_size
            except (TypeError, AttributeError):
                pass

        session = StreamingSession(session_id, total_chunks, chunk_size)
        session.set_data_iterator(data_iterator, metadata)

        with self._lock:
            self._sessions[session_id] = session

        return session_id

    def get_chunk(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get the next chunk from a session.

        Args:
            session_id: Session identifier

        Returns:
            Chunk data or None if session not found
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None

            chunk = session.get_next_chunk()

            # Clean up completed sessions
            if session.is_completed:
                del self._sessions[session_id]

            return chunk

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            return session.get_info() if session else None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        with self._lock:
            return [session.get_info() for session in self._sessions.values()]

    def cancel_session(self, session_id: str) -> bool:
        """Cancel a streaming session.

        Args:
            session_id: Session identifier

        Returns:
            True if session was found and cancelled
        """
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False


# Global streaming manager
_streaming_manager: Optional[StreamingManager] = None


def get_streaming_manager() -> StreamingManager:
    """Get the global streaming manager instance."""
    global _streaming_manager
    if _streaming_manager is None:
        _streaming_manager = StreamingManager()
    return _streaming_manager


# Streaming data generators for Houdini
if HOUDINI_AVAILABLE:

    def stream_geometry_points(
        node_path: str, attributes: List[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """Generator for streaming geometry point data.

        Args:
            node_path: Path to geometry node
            attributes: List of attributes to include

        Yields:
            Point data dictionaries
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        geo = node.geometry()
        if geo is None:
            raise ValueError(f"No geometry found: {node_path}")

        points = geo.points()

        for point in points:
            point_data = {
                "point_number": point.number(),
                "position": list(point.position()),
            }

            # Add requested attributes
            if attributes:
                point_data["attributes"] = {}
                for attr_name in attributes:
                    try:
                        value = point.attribValue(attr_name)
                        point_data["attributes"][attr_name] = value
                    except hou.OperationFailed:
                        pass

            yield point_data

    def stream_geometry_primitives(node_path: str) -> Iterator[Dict[str, Any]]:
        """Generator for streaming geometry primitive data.

        Args:
            node_path: Path to geometry node

        Yields:
            Primitive data dictionaries
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        geo = node.geometry()
        if geo is None:
            raise ValueError(f"No geometry found: {node_path}")

        primitives = geo.prims()

        for prim in primitives:
            prim_data = {
                "primitive_number": prim.number(),
                "type": prim.type().name(),
                "vertex_count": len(prim.vertices()),
                "points": [v.point().number() for v in prim.vertices()],
            }

            yield prim_data

    def stream_scene_hierarchy(
        root_path: str = "/", max_depth: int = -1
    ) -> Iterator[Dict[str, Any]]:
        """Generator for streaming scene hierarchy data.

        Args:
            root_path: Root node path
            max_depth: Maximum depth to traverse

        Yields:
            Node data dictionaries
        """

        def traverse_hierarchy(node, current_depth=0):
            if max_depth >= 0 and current_depth > max_depth:
                return

            node_data = {
                "path": node.path(),
                "name": node.name(),
                "type": node.type().name(),
                "category": node.type().category().name(),
                "depth": current_depth,
                "has_children": len(node.children()) > 0,
            }

            # Add geometry info if available
            if hasattr(node, "geometry") and node.geometry() is not None:
                geo = node.geometry()
                node_data["geometry_info"] = {
                    "points": len(geo.points()),
                    "primitives": len(geo.prims()),
                }

            yield node_data

            # Recursively traverse children
            for child in node.children():
                yield from traverse_hierarchy(child, current_depth + 1)

        root_node = hou.node(root_path)
        if root_node is None:
            raise ValueError(f"Root node not found: {root_path}")

        yield from traverse_hierarchy(root_node)


# RPC command integration
def register_streaming_commands(registry):
    """Register streaming-related RPC commands."""

    @registry.register(
        "stream.geometry.points.start", "Start streaming geometry points"
    )
    def start_stream_geometry_points(
        node_path: str, attributes: List[str] = None, chunk_size: int = 1000
    ) -> Dict[str, Any]:
        """Start streaming geometry point data."""
        if not HOUDINI_AVAILABLE:
            raise RuntimeError("Houdini not available")

        try:
            data_iterator = stream_geometry_points(node_path, attributes)
            metadata = {
                "node_path": node_path,
                "attributes": attributes or [],
                "data_type": "geometry_points",
            }

            manager = get_streaming_manager()
            session_id = manager.create_session(
                data_iterator, chunk_size, metadata
            )

            return {
                "session_id": session_id,
                "stream_type": "geometry_points",
                "node_path": node_path,
                "chunk_size": chunk_size,
            }

        except Exception as e:
            raise ValueError(f"Failed to start geometry points stream: {e}")

    @registry.register(
        "stream.scene.hierarchy.start", "Start streaming scene hierarchy"
    )
    def start_stream_scene_hierarchy(
        root_path: str = "/", max_depth: int = -1, chunk_size: int = 100
    ) -> Dict[str, Any]:
        """Start streaming scene hierarchy data."""
        if not HOUDINI_AVAILABLE:
            raise RuntimeError("Houdini not available")

        try:
            data_iterator = stream_scene_hierarchy(root_path, max_depth)
            metadata = {
                "root_path": root_path,
                "max_depth": max_depth,
                "data_type": "scene_hierarchy",
            }

            manager = get_streaming_manager()
            session_id = manager.create_session(
                data_iterator, chunk_size, metadata
            )

            return {
                "session_id": session_id,
                "stream_type": "scene_hierarchy",
                "root_path": root_path,
                "chunk_size": chunk_size,
            }

        except Exception as e:
            raise ValueError(f"Failed to start scene hierarchy stream: {e}")

    @registry.register(
        "stream.get_chunk", "Get next chunk from streaming session"
    )
    def get_stream_chunk(session_id: str) -> Optional[Dict[str, Any]]:
        """Get the next chunk from a streaming session."""
        manager = get_streaming_manager()
        chunk = manager.get_chunk(session_id)

        if chunk is None:
            raise ValueError(f"Session not found: {session_id}")

        return chunk

    @registry.register("stream.info", "Get streaming session information")
    def get_stream_info(session_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a streaming session."""
        manager = get_streaming_manager()
        info = manager.get_session_info(session_id)

        if info is None:
            raise ValueError(f"Session not found: {session_id}")

        return info

    @registry.register("stream.list", "List all active streaming sessions")
    def list_streams() -> List[Dict[str, Any]]:
        """List all active streaming sessions."""
        manager = get_streaming_manager()
        return manager.list_sessions()

    @registry.register("stream.cancel", "Cancel a streaming session")
    def cancel_stream(session_id: str) -> Dict[str, Any]:
        """Cancel a streaming session."""
        manager = get_streaming_manager()
        cancelled = manager.cancel_session(session_id)

        return {"session_id": session_id, "cancelled": cancelled}
