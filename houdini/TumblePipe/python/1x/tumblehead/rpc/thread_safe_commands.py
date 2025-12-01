"""Thread-safe command registry for Houdini RPC server.

This module provides thread-safe wrappers for Houdini commands to address
the threading limitations where HOM functions can cause thread lock contention.
"""

import functools
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional, Type, get_type_hints

from .protocol import ErrorCode


class ThreadSafeCommandRegistry:
    """Thread-safe registry for RPC commands with Houdini integration.

    This registry addresses Houdini's threading limitations by providing
    mechanisms to execute HOM-dependent commands safely from background threads.
    """

    def __init__(self, max_workers: int = 4):
        """Initialize the thread-safe command registry.

        Args:
            max_workers: Maximum number of threads for command execution
        """
        self._commands: Dict[str, Callable] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="HouRPC"
        )
        self._main_thread_id = threading.get_ident()

        # Try to detect if we're in Houdini and get the main thread executor
        self._houdini_available = False
        self._hdefereval = None

        try:
            import hou
            import hdefereval

            self._houdini_available = True
            self._hdefereval = hdefereval
        except ImportError:
            pass

    def register(
        self,
        name: str,
        description: str = "",
        params_schema: Optional[Dict[str, Type]] = None,
        thread_safe: bool = True,
    ):
        """Decorator to register a command function.

        Args:
            name: Command name for RPC calls
            description: Human-readable command description
            params_schema: Expected parameter types for validation
            thread_safe: If False, command will be executed in main thread (Houdini context)

        Returns:
            Decorator function
        """

        def decorator(func: Callable) -> Callable:
            # Extract type hints if no schema provided
            if params_schema is None:
                type_hints = get_type_hints(func)
                # Remove return type
                type_hints.pop("return", None)
                schema = type_hints
            else:
                schema = params_schema

            # Store command and metadata
            self._commands[name] = func
            self._metadata[name] = {
                "description": description,
                "params_schema": schema,
                "function_name": func.__name__,
                "module": func.__module__,
                "thread_safe": thread_safe,
            }

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            return wrapper

        return decorator

    def list_commands(self) -> List[str]:
        """Get list of registered command names."""
        return list(self._commands.keys())

    def get_command_info(self, command_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific command."""
        return self._metadata.get(command_name)

    def validate_params(
        self, command_name: str, params: Dict[str, Any]
    ) -> bool:
        """Validate parameters against command schema."""
        if command_name not in self._metadata:
            raise ValueError(f"Unknown command: {command_name}")

        schema = self._metadata[command_name]["params_schema"]

        # Get function signature for more detailed validation
        func = self._commands[command_name]
        sig = inspect.signature(func)

        # Check required parameters
        for param_name, param in sig.parameters.items():
            # Skip *args and **kwargs from wrapper functions
            if param_name in ["args", "kwargs"]:
                continue
            if (
                param.default == inspect.Parameter.empty
                and param_name not in params
            ):
                raise ValueError(f"Missing required parameter: {param_name}")

        # Check parameter types (basic validation)
        for param_name, value in params.items():
            if param_name in schema:
                expected_type = schema[param_name]

                # Skip type checking for typing.Any
                if (
                    expected_type is type(None)
                    or str(expected_type) == "typing.Any"
                ):
                    continue

                # Handle special typing cases
                if hasattr(expected_type, "__origin__"):
                    # Skip complex generic types for now
                    continue

                try:
                    if not isinstance(value, expected_type):
                        # Allow None for optional parameters
                        param = sig.parameters.get(param_name)
                        if (
                            param
                            and param.default is not None
                            and value is None
                        ):
                            continue
                        raise ValueError(
                            f"Parameter {param_name} expected {expected_type.__name__}, "
                            f"got {type(value).__name__}"
                        )
                except TypeError:
                    # If isinstance fails (e.g., with typing.Any), skip type checking
                    continue

        return True

    def execute(self, command_name: str, params: Dict[str, Any]) -> Any:
        """Execute a registered command with thread safety considerations.

        Args:
            command_name: Name of the command to execute
            params: Parameters to pass to the command

        Returns:
            Command execution result

        Raises:
            ValueError: If command not found or parameters invalid
            Exception: Any exception raised by the command
        """
        if command_name not in self._commands:
            raise ValueError(f"Unknown command: {command_name}")

        # Validate parameters
        self.validate_params(command_name, params)

        func = self._commands[command_name]
        metadata = self._metadata[command_name]

        # Check if command is thread-safe
        if metadata.get("thread_safe", True):
            # Execute directly in current thread
            return func(**params)
        else:
            # Execute in main thread using Houdini's deferred execution
            return self._execute_in_main_thread(func, params)

    def _execute_in_main_thread(
        self, func: Callable, params: Dict[str, Any]
    ) -> Any:
        """Execute function in Houdini's main thread.

        Args:
            func: Function to execute
            params: Parameters for the function

        Returns:
            Function result
        """
        if not self._houdini_available:
            # No Houdini available, execute directly (may cause issues)
            return func(**params)

        # Check if we're already in the main thread
        current_thread_id = threading.get_ident()
        if current_thread_id == self._main_thread_id:
            return func(**params)

        # Use Houdini's deferred execution to run in main thread
        result_container = {"result": None, "error": None, "done": False}

        def execute_and_store():
            try:
                result_container["result"] = func(**params)
            except Exception as e:
                result_container["error"] = e
            finally:
                result_container["done"] = True

        # Execute in main thread
        self._hdefereval.executeInMainThreadWithResult(execute_and_store)

        # Wait for completion (with timeout)
        timeout = 30.0
        start_time = time.time()

        while not result_container["done"]:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Command {func.__name__} timed out after {timeout}s"
                )
            time.sleep(0.01)

        # Check for errors
        if result_container["error"]:
            raise result_container["error"]

        return result_container["result"]

    def shutdown(self):
        """Shutdown the thread executor."""
        if self._executor:
            self._executor.shutdown(wait=True)


# Global thread-safe command registry
registry = ThreadSafeCommandRegistry()


# Core system commands (thread-safe)
@registry.register("system.ping", "Test server connectivity", thread_safe=True)
def ping() -> str:
    """Test server connectivity."""
    return "pong"


@registry.register("system.time", "Get server timestamp", thread_safe=True)
def get_time() -> float:
    """Get current server timestamp."""
    return time.time()


@registry.register(
    "system.shutdown", "Shutdown the RPC server", thread_safe=True
)
def shutdown_server() -> str:
    """Shutdown the RPC server."""
    # This will be handled by the server itself
    return "Server shutdown requested"


@registry.register("system.reload", "Reload RPC modules", thread_safe=True)
def reload_modules() -> str:
    """Force reload of RPC modules."""
    import importlib
    import sys

    modules_to_reload = [
        "tumblehead.rpc.thread_safe_commands",
        "tumblehead.rpc.protocol",
        "tumblehead.rpc.houdini_server",
    ]

    reloaded = []
    for module_name in modules_to_reload:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                reloaded.append(module_name)
            except Exception as e:
                reloaded.append(f"{module_name}: ERROR - {e}")
        else:
            reloaded.append(f"{module_name}: Not loaded")

    return f"Reloaded modules: {', '.join(reloaded)}"


@registry.register(
    "system.commands", "List available commands", thread_safe=True
)
def list_commands() -> List[Dict[str, Any]]:
    """List all available commands with metadata."""
    import json

    commands = []
    for name in registry.list_commands():
        info = registry.get_command_info(name)

        # Convert type objects to strings for JSON serialization
        params_schema = {}
        if info and info.get("params_schema"):
            for param_name, param_type in info["params_schema"].items():
                try:
                    if hasattr(param_type, "__name__"):
                        params_schema[param_name] = param_type.__name__
                    elif hasattr(param_type, "__origin__"):
                        # Handle generic types like List[str]
                        params_schema[param_name] = str(param_type)
                    else:
                        params_schema[param_name] = str(param_type)
                except Exception:
                    # Fallback for any problematic types
                    params_schema[param_name] = "unknown"

        # Ensure all values are JSON serializable
        command_info = {
            "name": str(name),
            "description": str(info.get("description", "") if info else ""),
            "parameters": params_schema,
            "thread_safe": bool(
                info.get("thread_safe", True) if info else True
            ),
        }

        # Test JSON serialization before adding to list
        try:
            json.dumps(command_info)
            commands.append(command_info)
        except Exception as e:
            # Add debug info if serialization fails
            commands.append(
                {
                    "name": str(name),
                    "description": f"Serialization error: {e}",
                    "parameters": {},
                    "thread_safe": True,
                }
            )

    # Test the entire result for JSON serialization
    try:
        json.dumps(commands)
    except Exception as e:
        return [{"error": f"Commands list serialization failed: {e}"}]

    return commands


# Houdini-specific commands (require main thread execution)
try:
    import hou

    @registry.register(
        "scene.save", "Save current Houdini scene", thread_safe=False
    )
    def save_scene(filepath: str = None) -> str:
        """Save the current Houdini scene."""
        if filepath:
            hou.hipFile.save(filepath)
        else:
            hou.hipFile.save()
        return hou.hipFile.path()

    @registry.register(
        "scene.load", "Load a Houdini scene file", thread_safe=False
    )
    def load_scene(filepath: str) -> str:
        """Load a Houdini scene file."""
        hou.hipFile.load(filepath)
        return hou.hipFile.path()

    @registry.register("scene.clear", "Clear current scene", thread_safe=False)
    def clear_scene() -> str:
        """Clear the current scene to start fresh."""
        hou.hipFile.clear()
        return "Scene cleared"

    @registry.register(
        "scene.path", "Get current scene file path", thread_safe=False
    )
    def get_scene_path() -> str:
        """Get the path of the current scene file."""
        return hou.hipFile.path()

    @registry.register("node.create", "Create a new node", thread_safe=False)
    def create_node(parent_path: str, node_type: str, name: str = None) -> str:
        """Create a new node in the specified parent."""
        parent = hou.node(parent_path)
        if parent is None:
            raise ValueError(f"Parent node not found: {parent_path}")

        node = parent.createNode(node_type, name)
        return node.path()

    @registry.register("node.delete", "Delete a node", thread_safe=False)
    def delete_node(node_path: str) -> str:
        """Delete the specified node."""
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        node.destroy()
        return f"Node deleted: {node_path}"

    @registry.register("node.list", "List child nodes", thread_safe=False)
    def list_nodes(parent_path: str = "/") -> List[Dict[str, str]]:
        """List child nodes of the specified parent."""
        parent = hou.node(parent_path)
        if parent is None:
            raise ValueError(f"Parent node not found: {parent_path}")

        nodes = []
        for child in parent.children():
            nodes.append(
                {
                    "name": child.name(),
                    "path": child.path(),
                    "type": child.type().name(),
                }
            )

        return nodes

    @registry.register("node.exists", "Check if node exists", thread_safe=False)
    def node_exists(node_path: str) -> bool:
        """Check if a node exists at the given path."""
        node = hou.node(node_path)
        return node is not None

    @registry.register("parm.set", "Set parameter value", thread_safe=False)
    def set_parameter(node_path: str, parm_name: str, value: Any) -> Any:
        """Set a parameter value on the specified node."""
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        parm = node.parm(parm_name)
        if parm is None:
            raise ValueError(f"Parameter not found: {parm_name}")

        parm.set(value)
        return value

    @registry.register("parm.get", "Get parameter value", thread_safe=False)
    def get_parameter(node_path: str, parm_name: str) -> Any:
        """Get a parameter value from the specified node."""
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        parm = node.parm(parm_name)
        if parm is None:
            raise ValueError(f"Parameter not found: {parm_name}")

        return parm.eval()

    @registry.register(
        "selection.get", "Get current selection", thread_safe=False
    )
    def get_selection() -> List[str]:
        """Get currently selected nodes."""
        selected_nodes = hou.selectedNodes()
        return [node.path() for node in selected_nodes]

    @registry.register(
        "selection.set", "Set current selection", thread_safe=False
    )
    def set_selection(node_paths: List[str]) -> List[str]:
        """Set the current selection to specified nodes."""
        nodes = []
        for path in node_paths:
            node = hou.node(path)
            if node:
                nodes.append(node)

        hou.clearAllSelected()
        for node in nodes:
            node.setSelected(True)

        return [node.path() for node in nodes]

except ImportError:
    # Houdini module not available, skip Houdini-specific commands
    pass


# Python execution commands (thread-safe)
import traceback
import importlib
import sys
from .console_capture import capture_command_output


@registry.register(
    "python.exec", "Execute Python code with output capture", thread_safe=True
)
def execute_python(code: str) -> Dict[str, Any]:
    """Execute Python code and capture output.

    Args:
        code: Python code string to execute

    Returns:
        Dictionary with result, stdout, stderr, and messages
    """
    from .console_capture import get_global_capture

    try:
        capture = get_global_capture()

        with capture.capture():
            # Create a local execution context
            exec_globals = globals().copy()
            exec_locals = {}

            # Execute the code
            exec(code, exec_globals, exec_locals)

        # Get captured output
        captured = capture.get_captured_output()

        return {
            "result": "Code executed successfully",
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    except Exception as e:
        # Return detailed error information
        error_msg = f"Execution error: {str(e)}\n{traceback.format_exc()}"
        return {
            "result": error_msg,
            "stdout": "",
            "stderr": error_msg,
            "messages": [],
        }


@registry.register(
    "python.eval",
    "Evaluate Python expression with output capture",
    thread_safe=True,
)
def evaluate_python(expression: str) -> Dict[str, Any]:
    """Evaluate Python expression and return result.

    Args:
        expression: Python expression to evaluate

    Returns:
        Dictionary with result, stdout, stderr, and messages
    """
    from .console_capture import get_global_capture

    try:
        capture = get_global_capture()

        with capture.capture():
            # Create a local execution context
            eval_globals = globals().copy()

            # Evaluate the expression
            result = eval(expression, eval_globals)

            # Convert result to string representation
            result_str = repr(result)
            print(f"Result: {result_str}")

        # Get captured output
        captured = capture.get_captured_output()

        return {
            "result": result_str,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    except Exception as e:
        # Return detailed error information
        error_msg = f"Evaluation error: {str(e)}\n{traceback.format_exc()}"
        return {
            "result": error_msg,
            "stdout": "",
            "stderr": error_msg,
            "messages": [],
        }


@registry.register("python.reload", "Reload a Python module", thread_safe=True)
def reload_python_module(module_name: str) -> Dict[str, Any]:
    """Reload a Python module for debugging.

    Args:
        module_name: Name of the module to reload (e.g., 'tumblehead.api')

    Returns:
        Dictionary with result and captured output
    """
    from .console_capture import get_global_capture

    try:
        capture = get_global_capture()

        with capture.capture():
            if module_name in sys.modules:
                module = sys.modules[module_name]
                reloaded_module = importlib.reload(module)
                result_msg = f"Successfully reloaded module: {module_name}"
                print(result_msg)
            else:
                error_msg = f"Module '{module_name}' not found in sys.modules"
                print(error_msg, file=sys.stderr)
                result_msg = error_msg

        # Get captured output
        captured = capture.get_captured_output()

        return {
            "result": result_msg,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    except Exception as e:
        error_msg = f"Failed to reload module '{module_name}': {str(e)}\n{traceback.format_exc()}"
        return {
            "result": error_msg,
            "stdout": "",
            "stderr": error_msg,
            "messages": [],
        }


@registry.register("python.import", "Import a Python module", thread_safe=True)
def import_module_cmd(module_name: str, alias: str = None) -> Dict[str, Any]:
    """Import a Python module.

    Args:
        module_name: Name of the module to import
        alias: Optional alias for the module

    Returns:
        Dictionary with result and captured output
    """
    from .console_capture import get_global_capture

    try:
        capture = get_global_capture()

        with capture.capture():
            module = importlib.import_module(module_name)

            # Add to globals so it's available for subsequent commands
            if alias:
                globals()[alias] = module
                result_msg = f"Successfully imported {module_name} as {alias}"
            else:
                # Import without alias - add module name parts to globals
                parts = module_name.split(".")
                globals()[parts[0]] = sys.modules[parts[0]]
                result_msg = f"Successfully imported {module_name}"

            print(result_msg)

        # Get captured output
        captured = capture.get_captured_output()

        return {
            "result": result_msg,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    except Exception as e:
        error_msg = f"Failed to import module '{module_name}': {str(e)}\n{traceback.format_exc()}"
        return {
            "result": error_msg,
            "stdout": "",
            "stderr": error_msg,
            "messages": [],
        }


@registry.register(
    "python.modules", "List loaded Python modules", thread_safe=True
)
def list_modules_cmd(pattern: str = None) -> List[str]:
    """List loaded Python modules, optionally filtered by pattern.

    Args:
        pattern: Optional pattern to filter module names

    Returns:
        List of module names
    """
    modules = list(sys.modules.keys())

    if pattern:
        import re

        pattern_re = re.compile(pattern, re.IGNORECASE)
        modules = [m for m in modules if pattern_re.search(m)]

    return sorted(modules)


@registry.register(
    "python.inspect", "Inspect a Python object", thread_safe=True
)
def inspect_python_object(object_path: str) -> Dict[str, Any]:
    """Inspect a Python object and return information about it.

    Args:
        object_path: Dot-separated path to object (e.g., 'hou.node')

    Returns:
        Dictionary with result and captured output
    """
    from .console_capture import get_global_capture

    try:
        capture = get_global_capture()

        with capture.capture():
            # Evaluate the object path
            obj = eval(object_path, globals())

            import inspect

            info_lines = [f"Object: {object_path}"]
            info_lines.append(f"Type: {type(obj).__name__}")

            if hasattr(obj, "__doc__") and obj.__doc__:
                info_lines.append(f"Docstring: {obj.__doc__}")

            if inspect.ismodule(obj):
                info_lines.append("Module attributes:")
                for attr in sorted(dir(obj)):
                    if not attr.startswith("_"):
                        info_lines.append(f"  {attr}")

            elif inspect.isclass(obj):
                info_lines.append("Class methods:")
                for name, method in inspect.getmembers(obj, inspect.ismethod):
                    if not name.startswith("_"):
                        info_lines.append(f"  {name}")

            elif inspect.isfunction(obj) or inspect.ismethod(obj):
                try:
                    sig = inspect.signature(obj)
                    info_lines.append(f"Signature: {sig}")
                except ValueError:
                    pass

            result = "\n".join(info_lines)
            print(result)

        # Get captured output
        captured = capture.get_captured_output()

        return {
            "result": result,
            "stdout": captured["stdout"],
            "stderr": captured["stderr"],
            "messages": capture.get_output_messages(),
        }

    except Exception as e:
        error_msg = f"Failed to inspect '{object_path}': {str(e)}\n{traceback.format_exc()}"
        return {
            "result": error_msg,
            "stdout": "",
            "stderr": error_msg,
            "messages": [],
        }
