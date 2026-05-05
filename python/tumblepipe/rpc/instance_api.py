"""Instance manipulation API for Houdini RPC server.

Provides high-level commands for manipulating Houdini scene state,
including scene management, node operations, parameter handling,
and geometry processing.
"""

import time
from typing import Any, Dict, List, Optional, Union
from .commands import registry
from .console_capture import capture_command_output


# Scene Management Commands
@registry.register("instance.scene.info", "Get current scene information")
@capture_command_output
def get_scene_info() -> Dict[str, Any]:
    """Get comprehensive information about the current scene.

    Returns:
        Dictionary with scene information including file path, modification status,
        frame range, FPS, and top-level node counts
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    # Get basic scene info
    scene_info = {
        "file_path": hou.hipFile.path(),
        "name": hou.hipFile.name(),
        "is_modified": hou.hipFile.hasUnsavedChanges(),
        "frame_range": {
            "start": hou.playbar.playbackRange()[0],
            "end": hou.playbar.playbackRange()[1],
        },
        "current_frame": hou.frame(),
        "fps": hou.fps(),
        "units": hou.getHoudiniUnit(),
        "up_axis": hou.getUpAxis().name(),
    }

    # Count nodes by context
    node_counts = {}
    for context in ["obj", "out", "ch", "img", "vex", "shop", "mat"]:
        try:
            context_node = hou.node(f"/{context}")
            if context_node:
                node_counts[context] = len(context_node.children())
        except:
            node_counts[context] = 0

    scene_info["node_counts"] = node_counts

    return scene_info


@registry.register("instance.scene.frame.set", "Set current frame")
@capture_command_output
def set_current_frame(frame: float) -> float:
    """Set the current frame in Houdini.

    Args:
        frame: Frame number to set

    Returns:
        The frame that was set
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    hou.setFrame(frame)
    return hou.frame()


@registry.register("instance.scene.frame.range", "Set playback range")
@capture_command_output
def set_frame_range(start: float, end: float) -> Dict[str, float]:
    """Set the playback frame range.

    Args:
        start: Start frame
        end: End frame

    Returns:
        Dictionary with start and end frame values
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    hou.playbar.setPlaybackRange(start, end)
    current_range = hou.playbar.playbackRange()

    return {"start": current_range[0], "end": current_range[1]}


# Advanced Node Operations
@registry.register("instance.node.info", "Get detailed node information")
@capture_command_output
def get_node_info(node_path: str) -> Dict[str, Any]:
    """Get comprehensive information about a node.

    Args:
        node_path: Full path to the node

    Returns:
        Dictionary with detailed node information
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    node_info = {
        "path": node.path(),
        "name": node.name(),
        "type": {
            "name": node.type().name(),
            "category": node.type().category().name(),
            "description": node.type().description(),
        },
        "parent": node.parent().path() if node.parent() else None,
        "position": list(node.position()),
        "color": list(node.color().rgb()) if hasattr(node, "color") else None,
        "comment": node.comment(),
        "is_locked": node.isLocked(),
        "is_template": node.isTemplateFlagSet(),
        "is_displayed": node.isDisplayFlagSet()
        if hasattr(node, "isDisplayFlagSet")
        else None,
        "is_render": node.isRenderFlagSet()
        if hasattr(node, "isRenderFlagSet")
        else None,
        "child_count": len(node.children()),
        "parameter_count": len(node.parms()),
        "input_count": len(node.inputs()),
        "output_count": len(node.outputs()),
    }

    # Get input connections
    inputs = []
    for i, input_node in enumerate(node.inputs()):
        if input_node:
            inputs.append(
                {
                    "index": i,
                    "node_path": input_node.path(),
                    "node_name": input_node.name(),
                }
            )
    node_info["inputs"] = inputs

    # Get output connections
    outputs = []
    for output_node in node.outputs():
        outputs.append(
            {"node_path": output_node.path(), "node_name": output_node.name()}
        )
    node_info["outputs"] = outputs

    return node_info


@registry.register("instance.node.hierarchy", "Get node hierarchy")
@capture_command_output
def get_node_hierarchy(
    root_path: str = "/", max_depth: int = 3
) -> Dict[str, Any]:
    """Get hierarchical structure of nodes from a root.

    Args:
        root_path: Root node path to start from
        max_depth: Maximum depth to traverse

    Returns:
        Nested dictionary representing node hierarchy
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    def build_hierarchy(node, current_depth):
        if current_depth >= max_depth:
            return None

        node_data = {
            "name": node.name(),
            "path": node.path(),
            "type": node.type().name(),
            "child_count": len(node.children()),
            "children": {},
        }

        if current_depth < max_depth - 1:
            for child in node.children():
                child_data = build_hierarchy(child, current_depth + 1)
                if child_data:
                    node_data["children"][child.name()] = child_data

        return node_data

    root_node = hou.node(root_path)
    if root_node is None:
        raise ValueError(f"Root node not found: {root_path}")

    return build_hierarchy(root_node, 0)


@registry.register("instance.node.parameters", "Get all node parameters")
@capture_command_output
def get_node_parameters(
    node_path: str, include_values: bool = True
) -> Dict[str, Any]:
    """Get all parameters of a node with their current values.

    Args:
        node_path: Full path to the node
        include_values: Whether to include current parameter values

    Returns:
        Dictionary with parameter information
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    parameters = {}

    for parm in node.parms():
        parm_info = {
            "name": parm.name(),
            "label": parm.description(),
            "type": parm.parmTemplate().type().name(),
            "is_locked": parm.isLocked(),
            "has_expression": bool(parm.expression()),
            "expression": parm.expression() if parm.expression() else None,
        }

        if include_values:
            try:
                if parm.parmTemplate().type() in [hou.parmTemplateType.String]:
                    parm_info["value"] = parm.evalAsString()
                elif parm.parmTemplate().type() in [
                    hou.parmTemplateType.Float,
                    hou.parmTemplateType.Int,
                    hou.parmTemplateType.Angle,
                ]:
                    parm_info["value"] = parm.eval()
                else:
                    parm_info["value"] = str(parm.eval())
            except:
                parm_info["value"] = None
                parm_info["error"] = "Could not evaluate parameter"

        parameters[parm.name()] = parm_info

    return {
        "node_path": node_path,
        "parameter_count": len(parameters),
        "parameters": parameters,
    }


@registry.register("instance.node.batch_set", "Set multiple parameters at once")
@capture_command_output
def batch_set_parameters(
    node_path: str, parameters: Dict[str, Any]
) -> Dict[str, Any]:
    """Set multiple parameters on a node at once.

    Args:
        node_path: Full path to the node
        parameters: Dictionary of parameter_name -> value mappings

    Returns:
        Dictionary with results for each parameter
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    results = {}

    for parm_name, value in parameters.items():
        try:
            parm = node.parm(parm_name)
            if parm is None:
                results[parm_name] = {
                    "success": False,
                    "error": f"Parameter not found: {parm_name}",
                }
                continue

            parm.set(value)
            results[parm_name] = {"success": True, "value": value}

        except Exception as e:
            results[parm_name] = {"success": False, "error": str(e)}

    return {
        "node_path": node_path,
        "results": results,
        "success_count": sum(
            1 for r in results.values() if r.get("success", False)
        ),
        "total_count": len(parameters),
    }


# Geometry and Data Operations
@registry.register("instance.geo.info", "Get geometry information")
@capture_command_output
def get_geometry_info(node_path: str) -> Dict[str, Any]:
    """Get information about geometry at a node.

    Args:
        node_path: Full path to geometry node

    Returns:
        Dictionary with geometry information
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"Node not found: {node_path}")

    try:
        geo = node.geometry()
        if geo is None:
            return {
                "node_path": node_path,
                "has_geometry": False,
                "error": "Node has no geometry",
            }

        # Get basic geometry stats
        geo_info = {
            "node_path": node_path,
            "has_geometry": True,
            "point_count": len(geo.points()),
            "primitive_count": len(geo.prims()),
            "vertex_count": len(geo.vertices()),
            "bounds": {
                "min": list(geo.boundingBox().minvec()),
                "max": list(geo.boundingBox().maxvec()),
                "size": list(geo.boundingBox().sizevec()),
                "center": list(geo.boundingBox().center()),
            },
        }

        # Get primitive type counts
        prim_types = {}
        for prim in geo.prims():
            prim_type = prim.type().name()
            prim_types[prim_type] = prim_types.get(prim_type, 0) + 1
        geo_info["primitive_types"] = prim_types

        # Get attribute information
        point_attribs = [attr.name() for attr in geo.pointAttribs()]
        prim_attribs = [attr.name() for attr in geo.primAttribs()]
        vertex_attribs = [attr.name() for attr in geo.vertexAttribs()]
        global_attribs = [attr.name() for attr in geo.globalAttribs()]

        geo_info["attributes"] = {
            "point": point_attribs,
            "primitive": prim_attribs,
            "vertex": vertex_attribs,
            "global": global_attribs,
        }

        return geo_info

    except Exception as e:
        return {"node_path": node_path, "has_geometry": False, "error": str(e)}


# Console and Message Management
@registry.register("instance.console.messages", "Get recent console messages")
def get_console_messages(
    since_timestamp: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Get recent console messages from command execution.

    Args:
        since_timestamp: Only return messages after this timestamp

    Returns:
        List of message dictionaries with timestamp, stream, and message
    """
    from .console_capture import get_global_capture

    capture = get_global_capture()
    return capture.get_output_messages(since_timestamp)


@registry.register("instance.console.clear", "Clear captured console output")
def clear_console_capture() -> str:
    """Clear all captured console output.

    Returns:
        Confirmation message
    """
    from .console_capture import get_global_capture

    capture = get_global_capture()
    capture.clear_capture()
    return "Console capture cleared"


# Utility Commands
@registry.register("instance.eval.python", "Execute Python code")
@capture_command_output
def eval_python(code: str, return_result: bool = True) -> Dict[str, Any]:
    """Execute Python code in Houdini context.

    Args:
        code: Python code to execute
        return_result: Whether to return the result of the last expression

    Returns:
        Dictionary with execution results and captured output

    Note:
        This is a powerful command - use with caution. Code is executed
        in the Houdini Python environment with full access.
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    # Create execution environment with hou module available
    exec_globals = {"__builtins__": __builtins__, "hou": hou}
    exec_locals = {}

    result = None
    error = None

    try:
        if return_result:
            # Try to evaluate as expression first
            try:
                result = eval(code, exec_globals, exec_locals)
            except SyntaxError:
                # If not a single expression, execute as statements
                exec(code, exec_globals, exec_locals)
                result = None
        else:
            exec(code, exec_globals, exec_locals)
            result = None

    except Exception as e:
        error = str(e)

    return {
        "code": code,
        "result": result,
        "error": error,
        "locals": {k: str(v) for k, v in exec_locals.items()},
        "success": error is None,
    }


@registry.register("instance.time.benchmark", "Benchmark code execution")
@capture_command_output
def benchmark_code(code: str, iterations: int = 1) -> Dict[str, Any]:
    """Benchmark Python code execution in Houdini.

    Args:
        code: Python code to benchmark
        iterations: Number of times to run the code

    Returns:
        Dictionary with timing information
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("Houdini module not available")

    exec_globals = {"__builtins__": __builtins__, "hou": hou}

    times = []

    for i in range(iterations):
        start_time = time.perf_counter()
        try:
            exec(code, exec_globals)
            end_time = time.perf_counter()
            times.append(end_time - start_time)
        except Exception as e:
            return {"success": False, "error": str(e), "iteration": i}

    # Calculate statistics
    total_time = sum(times)
    avg_time = total_time / len(times)
    min_time = min(times)
    max_time = max(times)

    return {
        "success": True,
        "iterations": iterations,
        "times": times,
        "total_time": total_time,
        "average_time": avg_time,
        "min_time": min_time,
        "max_time": max_time,
        "code": code,
    }
