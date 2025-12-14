"""Rebuild Houdini nodes in place using Python wrapper API.

This module rebuilds nodes by capturing values through wrapper class getters
and restoring them via setters. This approach is robust to HDA internal
structure changes since only the "public API" parameters are preserved.
"""

import hou
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import (
    import_layer, import_shot, layer_split, export_layer, import_asset
)
from tumblehead.pipe.houdini.sops import import_rig


# Map node type names to (wrapper_class, [(getter, setter), ...])
NODE_CONFIGS = {
    'import_layer': (import_layer.ImportLayer, [
        ('get_entity_uri', 'set_entity_uri'),
        ('get_department_name', 'set_department_name'),
        ('get_variant_name', 'set_variant_name'),
        ('get_version_name', 'set_version_name'),
        ('get_include_layerbreak', 'set_include_layerbreak'),
    ]),
    'import_shot': (import_shot.ImportShot, [
        ('get_shot_uri', 'set_shot_uri'),
        ('get_department_name', 'set_department_name'),
        ('get_include_procedurals', 'set_include_procedurals'),
    ]),
    'layer_split': (layer_split.LayerSplit, [
        ('get_entity_uri', 'set_entity_uri'),
        ('get_department_name', 'set_department_name'),
    ]),
    'export_layer': (export_layer.ExportLayer, [
        ('get_entity_uri', 'set_entity_uri'),
        ('get_department_name', 'set_department_name'),
        ('get_variant_name', 'set_variant_name'),
    ]),
    'import_asset': (import_asset.ImportAsset, [
        ('get_asset_uri', 'set_asset_uri'),
        ('get_variant_name', 'set_variant_name'),
        ('get_exclude_department_names', 'set_exclude_department_names'),
        ('get_include_layerbreak', 'set_include_layerbreak'),
    ]),
    'import_rig': (import_rig.ImportRig, [
        ('get_entity_uri', 'set_entity_uri'),
        ('get_version_name', 'set_version_name'),
        ('get_instances', 'set_instances'),
    ]),
}


def get_node_type_name(node):
    """Extract the base type name from a TH node (e.g., 'import_layer' from 'th::import_layer::1.0')."""
    type_name = node.type().name().lower()
    if type_name.startswith('th::'):
        parts = type_name.split('::')
        if len(parts) >= 2:
            return parts[1]
    return None


def capture_wrapper_values(wrapper, type_name):
    """Capture values using wrapper getter methods.

    Args:
        wrapper: The wrapper instance (e.g., ImportLayer, ImportShot)
        type_name: The node type name (e.g., 'import_layer')

    Returns:
        dict mapping getter_name to captured value
    """
    if type_name not in NODE_CONFIGS:
        return {}

    _, getter_setter_pairs = NODE_CONFIGS[type_name]
    values = {}

    for getter_name, _ in getter_setter_pairs:
        getter = getattr(wrapper, getter_name, None)
        if getter:
            try:
                values[getter_name] = getter()
            except Exception:
                pass  # Skip if getter fails

    return values


def restore_wrapper_values(wrapper, type_name, values):
    """Restore values using wrapper setter methods.

    Args:
        wrapper: The wrapper instance (e.g., ImportLayer, ImportShot)
        type_name: The node type name (e.g., 'import_layer')
        values: dict mapping getter_name to value (from capture_wrapper_values)
    """
    if type_name not in NODE_CONFIGS:
        return

    _, getter_setter_pairs = NODE_CONFIGS[type_name]

    for getter_name, setter_name in getter_setter_pairs:
        if getter_name in values:
            setter = getattr(wrapper, setter_name, None)
            if setter:
                try:
                    setter(values[getter_name])
                except Exception:
                    pass  # Skip if setter fails


def capture_connections(node):
    """Capture input and output connections.

    Stores node paths (not references) to survive node destruction.

    Returns:
        (input_connections, output_connections) where each is a list of
        (index, node_path, connector_index) tuples.
    """
    input_connections = []
    for conn in node.inputConnections():
        input_connections.append((
            conn.inputIndex(),
            conn.inputNode().path(),
            conn.outputIndex()
        ))

    output_connections = []
    for conn in node.outputConnections():
        output_connections.append((
            conn.outputIndex(),
            conn.outputNode().path(),
            conn.inputIndex()
        ))

    return input_connections, output_connections


def restore_connections(node, input_connections, output_connections):
    """Restore input and output connections by resolving paths."""
    for input_idx, input_node_path, output_idx in input_connections:
        try:
            input_node = hou.node(input_node_path)
            if input_node is not None:
                node.setInput(input_idx, input_node, output_idx)
        except hou.OperationFailed:
            pass

    for output_idx, output_node_path, input_idx in output_connections:
        try:
            output_node = hou.node(output_node_path)
            if output_node is not None:
                output_node.setInput(input_idx, node, output_idx)
        except hou.OperationFailed:
            pass


def rebuild_node(source_node):
    """Rebuild a single node in place using wrapper API.

    Preserves: position, color, user data, wrapper values (via getters/setters), connections.

    Returns:
        hou.Node: The newly created node, or None on failure.
    """
    # Get node type info
    type_name = get_node_type_name(source_node)
    if type_name is None or type_name not in NODE_CONFIGS:
        return None

    wrapper_class, _ = NODE_CONFIGS[type_name]

    # Capture node metadata
    parent = source_node.parent()
    node_type = source_node.type().name()
    node_name = source_node.name()
    position = source_node.position()
    color = source_node.color()
    user_data = {key: source_node.userData(key) for key in source_node.userDataDict().keys()}

    # Wrap and capture values via getters
    wrapper = wrapper_class(source_node)
    values = capture_wrapper_values(wrapper, type_name)

    # Capture connections
    input_connections, output_connections = capture_connections(source_node)

    # Delete original
    source_node.destroy()

    # Create fresh node
    new_node = parent.createNode(node_type, node_name)
    new_node.setPosition(position)
    new_node.setColor(color)

    # Restore user data
    for key, value in user_data.items():
        new_node.setUserData(key, value)

    # Wrap and restore values via setters
    new_wrapper = wrapper_class(new_node)
    restore_wrapper_values(new_wrapper, type_name, values)

    # Restore connections
    restore_connections(new_node, input_connections, output_connections)

    return new_node


def rebuild_nodes_by_type(type_names, context="Lop"):
    """Rebuild all nodes matching specified type patterns.

    Args:
        type_names: List of type name patterns (e.g., ['import_layer', 'import_shot'])
        context: Node context ('Lop', 'Sop', 'Cop', etc.)

    Returns:
        (rebuilt_nodes, failed_info) where:
        - rebuilt_nodes: list of successfully rebuilt hou.Node objects
        - failed_info: list of (node_path, error_message) tuples
    """
    rebuilt = []
    failed = []

    # Collect all nodes to rebuild
    nodes_to_rebuild = []
    for type_name in type_names:
        nodes_to_rebuild.extend(ns.list_by_node_type(type_name, context))

    # Rebuild each node
    for node in nodes_to_rebuild:
        node_path = node.path()
        try:
            new_node = rebuild_node(node)
            if new_node:
                rebuilt.append(new_node)
        except Exception as e:
            failed.append((node_path, str(e)))

    return rebuilt, failed
