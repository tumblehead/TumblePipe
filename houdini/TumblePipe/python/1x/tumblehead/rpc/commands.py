"""Command registry and handlers for Houdini RPC server.

Provides a registry system for RPC commands that can be executed within
the Houdini context, including parameter validation and result handling.
"""

import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Type, get_type_hints
from functools import wraps

from .protocol import ErrorCode


class CommandRegistry:
    """Registry for RPC commands available in Houdini context."""

    def __init__(self):
        self._commands: Dict[str, Callable] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        description: str = "",
        params_schema: Optional[Dict[str, Type]] = None,
    ):
        """Decorator to register a command function.

        Args:
            name: Command name for RPC calls
            description: Human-readable command description
            params_schema: Expected parameter types for validation

        Returns:
            Decorator function

        Example:
            @registry.register("node.create", "Create a new node")
            def create_node(node_type: str, name: str = None) -> str:
                # Implementation
                return node_path
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
            }

            @wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            return wrapper

        return decorator

    def list_commands(self) -> List[str]:
        """Get list of registered command names.

        Returns:
            List of available command names
        """
        return list(self._commands.keys())

    def get_command_info(self, command_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific command.

        Args:
            command_name: Name of the command

        Returns:
            Command metadata dict or None if not found
        """
        return self._metadata.get(command_name)

    def validate_params(
        self, command_name: str, params: Dict[str, Any]
    ) -> bool:
        """Validate parameters against command schema.

        Args:
            command_name: Name of the command
            params: Parameters to validate

        Returns:
            True if parameters are valid

        Raises:
            ValueError: If validation fails
        """
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
        """Execute a registered command with given parameters.

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

        # Execute command
        func = self._commands[command_name]
        return func(**params)


# Global command registry
registry = CommandRegistry()


# Core Houdini commands
@registry.register("system.ping", "Test server connectivity")
def ping() -> str:
    """Test server connectivity."""
    return "pong"


@registry.register("system.time", "Get server timestamp")
def get_time() -> float:
    """Get current server timestamp."""
    return time.time()


@registry.register("system.commands", "List available commands")
def list_commands() -> List[Dict[str, Any]]:
    """List all available commands with metadata."""
    commands = []
    for name in registry.list_commands():
        info = registry.get_command_info(name)

        # Convert type objects to string names for JSON serialization
        params_schema = {}
        for param_name, param_type in info["params_schema"].items():
            if hasattr(param_type, "__name__"):
                params_schema[param_name] = param_type.__name__
            else:
                params_schema[param_name] = str(param_type)

        commands.append(
            {
                "name": name,
                "description": info["description"],
                "parameters": params_schema,
            }
        )
    return commands


# Houdini-specific commands (requires hou module)
try:
    import hou
    from .callbacks import register_callback_commands
    from .streaming import register_streaming_commands
    from .cache import register_cache_commands, cached_command

    @registry.register("scene.save", "Save current Houdini scene")
    def save_scene(filepath: str = None) -> str:
        """Save the current Houdini scene.

        Args:
            filepath: Optional path to save to, defaults to current file

        Returns:
            Path to saved file
        """
        if filepath:
            hou.hipFile.save(filepath)
        else:
            hou.hipFile.save()
        return hou.hipFile.path()

    @registry.register("scene.load", "Load a Houdini scene file")
    def load_scene(filepath: str) -> str:
        """Load a Houdini scene file.

        Args:
            filepath: Path to the scene file to load

        Returns:
            Path to loaded file
        """
        hou.hipFile.load(filepath)
        return hou.hipFile.path()

    @registry.register("scene.clear", "Clear current scene")
    def clear_scene() -> str:
        """Clear the current scene to start fresh."""
        hou.hipFile.clear()
        return "Scene cleared"

    @registry.register("node.create", "Create a new node")
    def create_node(parent_path: str, node_type: str, name: str = None) -> str:
        """Create a new node in the specified parent.

        Args:
            parent_path: Path to parent node (e.g., "/obj")
            node_type: Type of node to create (e.g., "geo")
            name: Optional name for the node

        Returns:
            Full path to created node
        """
        parent = hou.node(parent_path)
        if parent is None:
            raise ValueError(f"Parent node not found: {parent_path}")

        node = parent.createNode(node_type, name)
        return node.path()

    @registry.register("node.delete", "Delete a node")
    def delete_node(node_path: str) -> str:
        """Delete the specified node.

        Args:
            node_path: Full path to the node to delete

        Returns:
            Confirmation message
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        node.destroy()
        return f"Node deleted: {node_path}"

    @registry.register("node.list", "List child nodes")
    def list_nodes(parent_path: str = "/") -> List[Dict[str, str]]:
        """List child nodes of the specified parent.

        Args:
            parent_path: Path to parent node, defaults to root

        Returns:
            List of node info dictionaries
        """
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

    @registry.register("parm.set", "Set parameter value")
    def set_parameter(node_path: str, parm_name: str, value: Any) -> Any:
        """Set a parameter value on the specified node.

        Args:
            node_path: Full path to the node
            parm_name: Name of the parameter
            value: Value to set

        Returns:
            The value that was set
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        parm = node.parm(parm_name)
        if parm is None:
            raise ValueError(f"Parameter not found: {parm_name}")

        parm.set(value)
        return value

    @registry.register("parm.get", "Get parameter value")
    def get_parameter(node_path: str, parm_name: str) -> Any:
        """Get a parameter value from the specified node.

        Args:
            node_path: Full path to the node
            parm_name: Name of the parameter

        Returns:
            Current parameter value
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        parm = node.parm(parm_name)
        if parm is None:
            raise ValueError(f"Parameter not found: {parm_name}")

        return parm.eval()

    # Enhanced geometry analysis commands with caching
    @registry.register(
        "geo.analyze", "Analyze geometry structure and properties"
    )
    @cached_command(ttl=60.0)
    def analyze_geometry(
        node_path: str,
        include_attributes: bool = True,
        include_groups: bool = True,
    ) -> Dict[str, Any]:
        """Analyze geometry structure using full HOM capabilities.

        Args:
            node_path: Path to geometry node
            include_attributes: Include attribute information
            include_groups: Include group information

        Returns:
            Comprehensive geometry analysis
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        geo = node.geometry()
        if geo is None:
            raise ValueError(f"Node has no geometry: {node_path}")

        # Basic geometry stats
        result = {
            "points": len(geo.points()),
            "primitives": len(geo.prims()),
            "vertices": len(geo.vertices()) if hasattr(geo, "vertices") else 0,
            "bounding_box": {
                "min": list(geo.boundingBox().minvec()),
                "max": list(geo.boundingBox().maxvec()),
                "size": list(geo.boundingBox().sizevec()),
                "center": list(geo.boundingBox().center()),
            },
        }

        # Primitive type breakdown
        prim_types = {}
        for prim in geo.prims():
            prim_type = prim.type().name()
            prim_types[prim_type] = prim_types.get(prim_type, 0) + 1
        result["primitive_types"] = prim_types

        # Attribute information
        if include_attributes:
            result["attributes"] = {
                "point": [
                    {
                        "name": attr.name(),
                        "type": attr.dataType().name(),
                        "size": attr.size(),
                    }
                    for attr in geo.pointAttribs()
                ],
                "primitive": [
                    {
                        "name": attr.name(),
                        "type": attr.dataType().name(),
                        "size": attr.size(),
                    }
                    for attr in geo.primAttribs()
                ],
                "vertex": [
                    {
                        "name": attr.name(),
                        "type": attr.dataType().name(),
                        "size": attr.size(),
                    }
                    for attr in geo.vertexAttribs()
                ],
                "detail": [
                    {
                        "name": attr.name(),
                        "type": attr.dataType().name(),
                        "size": attr.size(),
                    }
                    for attr in geo.globalAttribs()
                ],
            }

        # Group information
        if include_groups:
            result["groups"] = {
                "point": [
                    {"name": group.name(), "size": len(group.points())}
                    for group in geo.pointGroups()
                ],
                "primitive": [
                    {"name": group.name(), "size": len(group.prims())}
                    for group in geo.primGroups()
                ],
                "edge": [
                    {"name": group.name(), "size": len(group.edges())}
                    for group in geo.edgeGroups()
                ],
            }

        return result

    @registry.register(
        "geo.points.sample", "Sample point positions and attributes"
    )
    def sample_geometry_points(
        node_path: str, max_samples: int = 1000, attributes: List[str] = None
    ) -> Dict[str, Any]:
        """Sample point data from geometry for analysis.

        Args:
            node_path: Path to geometry node
            max_samples: Maximum number of points to sample
            attributes: List of attribute names to include

        Returns:
            Sampled point data
        """
        node = hou.node(node_path)
        if node is None:
            raise ValueError(f"Node not found: {node_path}")

        geo = node.geometry()
        if geo is None:
            raise ValueError(f"Node has no geometry: {node_path}")

        points = geo.points()
        num_points = len(points)

        # Sample points if too many
        if num_points > max_samples:
            step = num_points // max_samples
            sampled_points = points[::step][:max_samples]
        else:
            sampled_points = points

        # Collect position data
        result = {
            "total_points": num_points,
            "sampled_points": len(sampled_points),
            "positions": [list(pt.position()) for pt in sampled_points],
        }

        # Collect attribute data if specified
        if attributes:
            attr_data = {}
            for attr_name in attributes:
                attr = geo.findPointAttrib(attr_name)
                if attr is not None:
                    attr_data[attr_name] = [
                        pt.attribValue(attr_name) for pt in sampled_points
                    ]
            result["attributes"] = attr_data

        return result

    # Advanced scene query commands with caching
    @registry.register("scene.query.hierarchy", "Get complete scene hierarchy")
    @cached_command(ttl=30.0)
    def query_scene_hierarchy(
        root_path: str = "/", max_depth: int = -1
    ) -> Dict[str, Any]:
        """Query complete scene hierarchy with detailed node information.

        Args:
            root_path: Root node path to start from
            max_depth: Maximum depth to traverse (-1 for unlimited)

        Returns:
            Hierarchical scene structure
        """

        def build_hierarchy(node, current_depth=0):
            if max_depth >= 0 and current_depth > max_depth:
                return None

            node_info = {
                "name": node.name(),
                "path": node.path(),
                "type": node.type().name(),
                "category": node.type().category().name(),
                "has_errors": node.errors() != "",
                "has_warnings": node.warnings() != "",
                "is_locked": node.isLocked()
                if hasattr(node, "isLocked")
                else False,
                "is_bypassed": node.isBypassed()
                if hasattr(node, "isBypassed")
                else False,
                "children": [],
            }

            # Add geometry info if applicable
            if hasattr(node, "geometry") and node.geometry() is not None:
                geo = node.geometry()
                node_info["geometry"] = {
                    "points": len(geo.points()),
                    "primitives": len(geo.prims()),
                }

            # Recursively add children
            for child in node.children():
                child_info = build_hierarchy(child, current_depth + 1)
                if child_info is not None:
                    node_info["children"].append(child_info)

            return node_info

        root_node = hou.node(root_path)
        if root_node is None:
            raise ValueError(f"Root node not found: {root_path}")

        return build_hierarchy(root_node)

    @registry.register(
        "scene.query.performance", "Get scene performance metrics"
    )
    def query_scene_performance() -> Dict[str, Any]:
        """Query scene performance and resource usage metrics.

        Returns:
            Scene performance information
        """
        # Get current frame and time info
        result = {
            "current_frame": hou.frame(),
            "frame_range": {
                "start": hou.playbar.frameRange()[0],
                "end": hou.playbar.frameRange()[1],
            },
            "fps": hou.fps(),
            "time_dependent_nodes": [],
        }

        # Find time-dependent nodes
        for node in hou.node("/").allSubChildren():
            if node.isTimeDependent():
                result["time_dependent_nodes"].append(
                    {"path": node.path(), "type": node.type().name()}
                )

        # Memory and cache info (if available)
        try:
            result["memory"] = {
                "geometry_cache_memory": hou.geometryCacheMemoryUsage(),
                "image_cache_memory": hou.imageCacheMemoryUsage(),
            }
        except AttributeError:
            # These functions may not be available in all Houdini versions
            pass

        return result

    # Batch operation commands for efficiency
    @registry.register(
        "batch.nodes.create", "Create multiple nodes efficiently"
    )
    def batch_create_nodes(
        operations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Create multiple nodes in batch for improved performance.

        Args:
            operations: List of node creation operations with keys:
                       - parent_path: Parent node path
                       - node_type: Type of node
                       - name: Optional node name
                       - parameters: Optional dict of parameter values

        Returns:
            List of creation results with paths and any errors
        """
        results = []

        for i, op in enumerate(operations):
            try:
                # Validate operation
                if "parent_path" not in op or "node_type" not in op:
                    raise ValueError(f"Operation {i}: missing required fields")

                # Create node
                parent = hou.node(op["parent_path"])
                if parent is None:
                    raise ValueError(f"Parent not found: {op['parent_path']}")

                node = parent.createNode(op["node_type"], op.get("name"))

                # Set parameters if provided
                if "parameters" in op:
                    for parm_name, value in op["parameters"].items():
                        parm = node.parm(parm_name)
                        if parm is not None:
                            parm.set(value)

                results.append(
                    {"success": True, "path": node.path(), "name": node.name()}
                )

            except Exception as e:
                results.append(
                    {"success": False, "error": str(e), "operation_index": i}
                )

        return results

    @registry.register(
        "batch.parameters.set", "Set multiple parameters efficiently"
    )
    def batch_set_parameters(
        operations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Set multiple parameters across nodes efficiently.

        Args:
            operations: List of parameter operations with keys:
                       - node_path: Path to node
                       - parameters: Dict of parameter_name: value

        Returns:
            List of operation results
        """
        results = []

        for i, op in enumerate(operations):
            try:
                if "node_path" not in op or "parameters" not in op:
                    raise ValueError(f"Operation {i}: missing required fields")

                node = hou.node(op["node_path"])
                if node is None:
                    raise ValueError(f"Node not found: {op['node_path']}")

                set_params = {}
                errors = []

                for parm_name, value in op["parameters"].items():
                    parm = node.parm(parm_name)
                    if parm is not None:
                        parm.set(value)
                        set_params[parm_name] = value
                    else:
                        errors.append(f"Parameter not found: {parm_name}")

                results.append(
                    {
                        "success": True,
                        "node_path": op["node_path"],
                        "set_parameters": set_params,
                        "errors": errors,
                    }
                )

            except Exception as e:
                results.append(
                    {"success": False, "error": str(e), "operation_index": i}
                )

        return results

    @registry.register(
        "batch.geometry.analyze", "Analyze multiple geometries efficiently"
    )
    def batch_analyze_geometry(
        node_paths: List[str], include_attributes: bool = False
    ) -> List[Dict[str, Any]]:
        """Analyze multiple geometry nodes efficiently.

        Args:
            node_paths: List of geometry node paths
            include_attributes: Include detailed attribute information

        Returns:
            List of geometry analysis results
        """
        results = []

        for node_path in node_paths:
            try:
                node = hou.node(node_path)
                if node is None:
                    results.append(
                        {
                            "success": False,
                            "node_path": node_path,
                            "error": "Node not found",
                        }
                    )
                    continue

                geo = node.geometry()
                if geo is None:
                    results.append(
                        {
                            "success": False,
                            "node_path": node_path,
                            "error": "No geometry",
                        }
                    )
                    continue

                # Basic analysis
                analysis = {
                    "success": True,
                    "node_path": node_path,
                    "points": len(geo.points()),
                    "primitives": len(geo.prims()),
                    "vertices": len(geo.vertices())
                    if hasattr(geo, "vertices")
                    else 0,
                    "bbox_size": list(geo.boundingBox().sizevec()),
                }

                # Detailed attributes if requested
                if include_attributes:
                    analysis["attributes"] = {
                        "point_count": len(geo.pointAttribs()),
                        "prim_count": len(geo.primAttribs()),
                        "vertex_count": len(geo.vertexAttribs()),
                        "detail_count": len(geo.globalAttribs()),
                    }

                results.append(analysis)

            except Exception as e:
                results.append(
                    {"success": False, "node_path": node_path, "error": str(e)}
                )

        return results

    # Scene template and workflow commands
    @registry.register(
        "workflow.scene.setup", "Set up scene with template workflow"
    )
    def setup_scene_workflow(
        template_type: str, project_settings: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Set up a scene using predefined workflow templates.

        Args:
            template_type: Type of template (asset, shot, lookdev, etc.)
            project_settings: Optional project-specific settings

        Returns:
            Setup results with created nodes and paths
        """
        results = {
            "template_type": template_type,
            "created_nodes": [],
            "setup_cameras": [],
            "setup_lights": [],
            "render_settings": None,
        }

        try:
            if template_type == "asset_modeling":
                # Create standard asset modeling setup
                geo_node = hou.node("/obj").createNode("geo", "asset_geo")
                results["created_nodes"].append(geo_node.path())

                # Create camera for asset review
                cam_node = hou.node("/obj").createNode("cam", "asset_camera")
                cam_node.parm("tx").set(5)
                cam_node.parm("ty").set(3)
                cam_node.parm("tz").set(5)
                results["setup_cameras"].append(cam_node.path())

            elif template_type == "shot_lighting":
                # Create standard shot lighting setup
                cam_node = hou.node("/obj").createNode("cam", "shot_camera")
                key_light = hou.node("/obj").createNode("hlight", "key_light")
                fill_light = hou.node("/obj").createNode("hlight", "fill_light")
                rim_light = hou.node("/obj").createNode("hlight", "rim_light")

                # Configure lighting
                key_light.parm("light_intensity").set(2.0)
                fill_light.parm("light_intensity").set(0.5)
                rim_light.parm("light_intensity").set(1.5)

                results["setup_cameras"].append(cam_node.path())
                results["setup_lights"].extend(
                    [key_light.path(), fill_light.path(), rim_light.path()]
                )

            elif template_type == "procedural_generation":
                # Create procedural generation network
                geo_node = hou.node("/obj").createNode("geo", "procedural_geo")

                # Add common procedural nodes inside
                scatter = geo_node.createNode("scatter", "scatter1")
                copy = geo_node.createNode("copytopoints", "copy1")

                results["created_nodes"].extend(
                    [geo_node.path(), scatter.path(), copy.path()]
                )

            else:
                raise ValueError(f"Unknown template type: {template_type}")

            results["success"] = True

        except Exception as e:
            results["success"] = False
            results["error"] = str(e)

        return results

    # Register callback-related commands
    register_callback_commands(registry)

    # Register streaming-related commands
    register_streaming_commands(registry)

    # Register cache management commands
    register_cache_commands(registry)

except ImportError:
    # Houdini module not available, skip Houdini-specific commands
    pass


# Note: Python execution commands are now implemented in thread_safe_commands.py
# This avoids conflicts with the thread-safe command registry used by the server
