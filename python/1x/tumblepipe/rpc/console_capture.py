"""Console output capture and streaming for RPC commands.

Provides thread-safe console output capturing that can stream stdout/stderr
back to RPC clients in real-time during command execution.
"""

import io
import sys
import threading
import time
from typing import Callable, Optional, TextIO, List, Dict, Any
from contextlib import contextmanager
import queue


class ConsoleCapture:
    """Thread-safe console output capture for RPC command execution.

    Captures stdout/stderr during command execution and allows streaming
    of output to RPC clients. Preserves original console behavior while
    providing access to captured output.
    """

    def __init__(self, buffer_size: int = 8192):
        """Initialize console capture.

        Args:
            buffer_size: Maximum buffer size for captured output
        """
        self._buffer_size = buffer_size
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._capture_stdout = io.StringIO()
        self._capture_stderr = io.StringIO()
        self._output_queue: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._capturing = False
        self._lock = threading.Lock()

        # Message callbacks for real-time streaming
        self._message_callbacks: List[Callable[[str, str], None]] = []

    def add_message_callback(self, callback: Callable[[str, str], None]):
        """Add callback for real-time message streaming.

        Args:
            callback: Function to call with (stream_type, message) for each output line
                     stream_type will be 'stdout' or 'stderr'
        """
        with self._lock:
            self._message_callbacks.append(callback)

    def remove_message_callback(self, callback: Callable[[str, str], None]):
        """Remove a message callback.

        Args:
            callback: Callback function to remove
        """
        with self._lock:
            if callback in self._message_callbacks:
                self._message_callbacks.remove(callback)

    def _write_captured(
        self, text: str, stream_type: str, original_stream: TextIO
    ):
        """Write text to both original stream and capture buffer.

        Args:
            text: Text to write
            stream_type: 'stdout' or 'stderr'
            original_stream: Original stream to write to
        """
        # Write to original stream first
        original_stream.write(text)
        original_stream.flush()

        # Add to capture buffer
        capture_stream = (
            self._capture_stdout
            if stream_type == "stdout"
            else self._capture_stderr
        )
        capture_stream.write(text)

        # Trigger callbacks for real-time streaming
        with self._lock:
            for callback in self._message_callbacks:
                try:
                    callback(stream_type, text)
                except Exception:
                    # Don't let callback errors break capture
                    pass

        # Add to output queue for client polling
        if text.strip():
            self._output_queue.put(
                {
                    "timestamp": time.time(),
                    "stream": stream_type,
                    "message": text.rstrip("\n\r"),
                }
            )

    @contextmanager
    def capture(self):
        """Context manager for capturing console output.

        Usage:
            with console_capture.capture():
                print("This will be captured")
                # Command execution here
        """
        if self._capturing:
            # Already capturing, just yield
            yield self
            return

        # Create wrapper classes for stdout/stderr
        class CaptureWrapper:
            def __init__(self, capture_instance, stream_type, original_stream):
                self._capture = capture_instance
                self._stream_type = stream_type
                self._original = original_stream

            def write(self, text):
                self._capture._write_captured(
                    text, self._stream_type, self._original
                )

            def flush(self):
                self._original.flush()

            def __getattr__(self, name):
                # Delegate other attributes to original stream
                return getattr(self._original, name)

        try:
            with self._lock:
                self._capturing = True
                self._capture_stdout.truncate(0)
                self._capture_stdout.seek(0)
                self._capture_stderr.truncate(0)
                self._capture_stderr.seek(0)

                # Clear the output queue
                while not self._output_queue.empty():
                    try:
                        self._output_queue.get_nowait()
                    except queue.Empty:
                        break

            # Replace stdout/stderr with capturing versions
            stdout_wrapper = CaptureWrapper(
                self, "stdout", self._original_stdout
            )
            stderr_wrapper = CaptureWrapper(
                self, "stderr", self._original_stderr
            )

            sys.stdout = stdout_wrapper
            sys.stderr = stderr_wrapper

            yield self

        finally:
            # Restore original streams
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr

            with self._lock:
                self._capturing = False

    def get_captured_output(self) -> Dict[str, str]:
        """Get all captured output as strings.

        Returns:
            Dictionary with 'stdout' and 'stderr' keys containing captured text
        """
        return {
            "stdout": self._capture_stdout.getvalue(),
            "stderr": self._capture_stderr.getvalue(),
        }

    def get_output_messages(
        self, since_timestamp: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get captured output messages since specified timestamp.

        Args:
            since_timestamp: Only return messages after this timestamp

        Returns:
            List of message dictionaries with timestamp, stream, and message keys
        """
        messages = []
        temp_queue = queue.Queue()

        # Extract all messages from queue
        while not self._output_queue.empty():
            try:
                msg = self._output_queue.get_nowait()
                if (
                    since_timestamp is None
                    or msg["timestamp"] > since_timestamp
                ):
                    messages.append(msg)
                temp_queue.put(msg)
            except queue.Empty:
                break

        # Put messages back in queue
        while not temp_queue.empty():
            try:
                self._output_queue.put(temp_queue.get_nowait())
            except queue.Empty:
                break

        return sorted(messages, key=lambda x: x["timestamp"])

    def clear_capture(self):
        """Clear all captured output."""
        with self._lock:
            self._capture_stdout.truncate(0)
            self._capture_stdout.seek(0)
            self._capture_stderr.truncate(0)
            self._capture_stderr.seek(0)

            # Clear the output queue
            while not self._output_queue.empty():
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break


# Global console capture instance for RPC server
_global_capture: Optional[ConsoleCapture] = None


def get_global_capture() -> ConsoleCapture:
    """Get or create the global console capture instance.

    Returns:
        Global ConsoleCapture instance
    """
    global _global_capture
    if _global_capture is None:
        _global_capture = ConsoleCapture()
    return _global_capture


def capture_command_output(func: Callable) -> Callable:
    """Decorator to automatically capture console output for RPC commands.

    Args:
        func: Command function to wrap

    Returns:
        Wrapped function that captures output
    """

    def wrapper(*args, **kwargs):
        capture = get_global_capture()

        with capture.capture():
            result = func(*args, **kwargs)

        # Return both result and captured output
        captured = capture.get_captured_output()
        return {
            "result": result,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    return wrapper


@contextmanager
def captured_execution():
    """Context manager for capturing output during command execution.

    Usage:
        with captured_execution() as capture:
            # Execute commands here
            print("This output will be captured")

        # Access captured output
        output = capture.get_captured_output()
    """
    capture = get_global_capture()
    with capture.capture():
        yield capture
