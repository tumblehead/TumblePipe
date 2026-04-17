"""Houdini callback integration for RPC event-driven workflows.

This module provides integration between Houdini's callback system and the RPC
server, enabling external tools to register for and receive notifications about
Houdini events.
"""

import json
import threading
import time
import weakref
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

try:
    import hou

    HOUDINI_AVAILABLE = True
except ImportError:
    HOUDINI_AVAILABLE = False


class CallbackEventManager:
    """Manages Houdini callbacks and event notifications for RPC clients."""

    def __init__(self, max_event_history: int = 1000):
        """Initialize the callback event manager.

        Args:
            max_event_history: Maximum number of events to keep in history
        """
        self._max_event_history = max_event_history
        self._event_history: deque = deque(maxlen=max_event_history)
        self._event_subscribers: Dict[str, Set[str]] = defaultdict(set)
        self._callback_ids: Dict[str, Any] = {}
        self._lock = threading.RLock()

        # Weak references to avoid memory leaks
        self._active_clients: Dict[str, Any] = weakref.WeakValueDictionary()

        if HOUDINI_AVAILABLE:
            self._setup_default_callbacks()

    def _setup_default_callbacks(self):
        """Set up default Houdini callbacks for common events."""
        try:
            # Node events
            self._callback_ids["node_created"] = hou.addNodeEventCallback(
                hou.nodeEventType.BeingCreated,
                lambda event_type, **kwargs: self._handle_node_event(
                    "node_created", event_type, **kwargs
                ),
            )

            self._callback_ids["node_deleted"] = hou.addNodeEventCallback(
                hou.nodeEventType.BeingDeleted,
                lambda event_type, **kwargs: self._handle_node_event(
                    "node_deleted", event_type, **kwargs
                ),
            )

            # Scene events
            self._callback_ids["scene_loaded"] = hou.addEventCallback(
                (
                    hou.hipFileEventType.BeforeLoad,
                    hou.hipFileEventType.AfterLoad,
                ),
                lambda event_type, **kwargs: self._handle_scene_event(
                    "scene_load", event_type, **kwargs
                ),
            )

            self._callback_ids["scene_saved"] = hou.addEventCallback(
                (
                    hou.hipFileEventType.BeforeSave,
                    hou.hipFileEventType.AfterSave,
                ),
                lambda event_type, **kwargs: self._handle_scene_event(
                    "scene_save", event_type, **kwargs
                ),
            )

            # Frame change events
            self._callback_ids["frame_changed"] = hou.playbar.addEventCallback(
                lambda: self._handle_playbar_event("frame_changed")
            )

        except Exception as e:
            print(f"Warning: Could not set up some Houdini callbacks: {e}")

    def _handle_node_event(self, event_name: str, event_type, **kwargs):
        """Handle node-related events."""
        try:
            node = kwargs.get("node")
            if node is None:
                return

            event_data = {
                "event_type": event_name,
                "node_path": node.path(),
                "node_name": node.name(),
                "node_type": node.type().name(),
                "timestamp": time.time(),
            }

            self._emit_event(event_name, event_data)

        except Exception as e:
            print(f"Error in node event handler: {e}")

    def _handle_scene_event(self, event_name: str, event_type, **kwargs):
        """Handle scene-related events."""
        try:
            file_path = (
                hou.hipFile.path()
                if hasattr(hou.hipFile, "path")
                else "untitled"
            )

            event_data = {
                "event_type": event_name,
                "file_path": file_path,
                "houdini_event_type": str(event_type),
                "timestamp": time.time(),
            }

            self._emit_event(event_name, event_data)

        except Exception as e:
            print(f"Error in scene event handler: {e}")

    def _handle_playbar_event(self, event_name: str):
        """Handle playbar/timeline events."""
        try:
            if not hasattr(hou, "frame"):
                return

            event_data = {
                "event_type": event_name,
                "current_frame": hou.frame(),
                "frame_range": {
                    "start": hou.playbar.frameRange()[0],
                    "end": hou.playbar.frameRange()[1],
                },
                "timestamp": time.time(),
            }

            self._emit_event(event_name, event_data)

        except Exception as e:
            print(f"Error in playbar event handler: {e}")

    def _emit_event(self, event_type: str, event_data: Dict[str, Any]):
        """Emit an event to all subscribers."""
        with self._lock:
            # Add unique event ID
            event_data["event_id"] = str(uuid4())

            # Add to history
            self._event_history.append(event_data)

            # Notify subscribers (would be implemented with actual RPC notification)
            subscribers = self._event_subscribers.get(event_type, set())
            if subscribers:
                # In a real implementation, this would send RPC notifications
                # to subscribed clients
                pass

    def subscribe_to_events(
        self, client_id: str, event_types: List[str]
    ) -> Dict[str, Any]:
        """Subscribe a client to specific event types.

        Args:
            client_id: Unique identifier for the RPC client
            event_types: List of event types to subscribe to

        Returns:
            Subscription result information
        """
        with self._lock:
            for event_type in event_types:
                self._event_subscribers[event_type].add(client_id)

            return {
                "client_id": client_id,
                "subscribed_events": event_types,
                "total_subscriptions": sum(
                    len(subs) for subs in self._event_subscribers.values()
                ),
            }

    def unsubscribe_from_events(
        self, client_id: str, event_types: List[str] = None
    ) -> Dict[str, Any]:
        """Unsubscribe a client from event types.

        Args:
            client_id: Unique identifier for the RPC client
            event_types: List of event types to unsubscribe from, or None for all

        Returns:
            Unsubscription result information
        """
        with self._lock:
            if event_types is None:
                # Unsubscribe from all events
                for subscribers in self._event_subscribers.values():
                    subscribers.discard(client_id)
                remaining = []
            else:
                # Unsubscribe from specific events
                remaining = []
                for event_type in event_types:
                    if event_type in self._event_subscribers:
                        self._event_subscribers[event_type].discard(client_id)
                    else:
                        remaining.append(event_type)

            return {
                "client_id": client_id,
                "unsubscribed_events": event_types or "all",
                "not_found": remaining,
            }

    def get_event_history(
        self, event_types: List[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent event history.

        Args:
            event_types: Filter by specific event types, or None for all
            limit: Maximum number of events to return

        Returns:
            List of recent events
        """
        with self._lock:
            events = list(self._event_history)

            # Filter by event types if specified
            if event_types:
                events = [
                    e for e in events if e.get("event_type") in event_types
                ]

            # Apply limit
            return events[-limit:] if limit > 0 else events

    def get_subscription_info(self) -> Dict[str, Any]:
        """Get information about current subscriptions.

        Returns:
            Subscription information
        """
        with self._lock:
            return {
                "event_types": list(self._event_subscribers.keys()),
                "subscription_counts": {
                    event_type: len(subscribers)
                    for event_type, subscribers in self._event_subscribers.items()
                },
                "total_events_in_history": len(self._event_history),
            }

    def cleanup(self):
        """Clean up callbacks and resources."""
        if not HOUDINI_AVAILABLE:
            return

        try:
            # Remove Houdini callbacks
            for callback_name, callback_id in self._callback_ids.items():
                try:
                    if callback_name in ["node_created", "node_deleted"]:
                        hou.removeNodeEventCallback(callback_id)
                    elif callback_name in ["scene_loaded", "scene_saved"]:
                        hou.removeEventCallback(callback_id)
                    elif callback_name == "frame_changed":
                        hou.playbar.removeEventCallback(callback_id)
                except Exception as e:
                    print(f"Error removing callback {callback_name}: {e}")

            self._callback_ids.clear()

        except Exception as e:
            print(f"Error during callback cleanup: {e}")


# Global callback manager instance
_callback_manager: Optional[CallbackEventManager] = None


def get_callback_manager() -> CallbackEventManager:
    """Get the global callback manager instance."""
    global _callback_manager
    if _callback_manager is None:
        _callback_manager = CallbackEventManager()
    return _callback_manager


# RPC command integration
if HOUDINI_AVAILABLE:
    # These would be registered in the main commands.py file
    def register_callback_commands(registry):
        """Register callback-related RPC commands."""

        @registry.register("callbacks.subscribe", "Subscribe to Houdini events")
        def subscribe_to_events(
            client_id: str, event_types: List[str]
        ) -> Dict[str, Any]:
            """Subscribe to Houdini events through RPC."""
            manager = get_callback_manager()
            return manager.subscribe_to_events(client_id, event_types)

        @registry.register(
            "callbacks.unsubscribe", "Unsubscribe from Houdini events"
        )
        def unsubscribe_from_events(
            client_id: str, event_types: List[str] = None
        ) -> Dict[str, Any]:
            """Unsubscribe from Houdini events through RPC."""
            manager = get_callback_manager()
            return manager.unsubscribe_from_events(client_id, event_types)

        @registry.register("callbacks.history", "Get event history")
        def get_event_history(
            event_types: List[str] = None, limit: int = 100
        ) -> List[Dict[str, Any]]:
            """Get recent event history through RPC."""
            manager = get_callback_manager()
            return manager.get_event_history(event_types, limit)

        @registry.register("callbacks.info", "Get subscription information")
        def get_subscription_info() -> Dict[str, Any]:
            """Get callback subscription information."""
            manager = get_callback_manager()
            return manager.get_subscription_info()
