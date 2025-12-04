"""Rebuild selected Houdini nodes in place, preserving settings and connections."""

import hou


# Tumblehead HDA prefix - all custom HDAs start with this
TH_NODE_PREFIX = 'th::'


def is_th_node(node):
    """Check if node is a Tumblehead custom HDA."""
    return node.type().name().lower().startswith(TH_NODE_PREFIX)


def get_dive_target(node):
    """Get the dive target path from an HDA, if it has one.

    Returns:
        str | None: The dive target path (e.g. "sopnet/create") or None
    """
    node_type = node.type()
    if not node_type:
        return None

    hdadef = node_type.definition()
    if not hdadef:
        return None

    if hdadef.hasSection('DiveTarget'):
        return hdadef.sections()['DiveTarget'].contents()

    return None


def get_editable_nodes(node):
    """Get the editable nodes paths from an HDA, if it has any.

    This is what editablesAsData() uses internally to determine
    which nodes to capture.

    Returns:
        list[str]: List of editable node paths (e.g. ["sopnet/create"])
    """
    node_type = node.type()
    if not node_type:
        return []

    hdadef = node_type.definition()
    if not hdadef:
        return []

    if hdadef.hasSection('EditableNodes'):
        content = hdadef.sections()['EditableNodes'].contents()
        return content.split() if content else []

    return []


def has_dive_target(node):
    """Check if node has a dive target (editable internal network)."""
    return get_dive_target(node) is not None


def has_editable_nodes(node):
    """Check if node has editable nodes (what editablesAsData() uses)."""
    return len(get_editable_nodes(node)) > 0


def capture_parm_values(node):
    """Capture all parameter values from a node."""
    parm_values = {}
    for parm in node.parms():
        try:
            name = parm.name()
            # Check for keyframes first
            keyframes = parm.keyframes()
            if keyframes:
                parm_values[name] = ('keyframes', keyframes)
            # Then check for expressions
            elif parm.expression():
                parm_values[name] = ('expression', parm.expression(), parm.expressionLanguage())
            # Otherwise store raw value
            else:
                parm_values[name] = ('value', parm.rawValue())
        except hou.OperationFailed:
            pass  # Skip locked/non-readable parms
    return parm_values


def restore_parm_values(node, parm_values):
    """Restore parameter values to a node."""
    # First, collect simple values to set all at once (avoids callback cascade)
    simple_values = {}
    deferred = []  # keyframes and expressions to set after

    for parm_name, parm_data in parm_values.items():
        parm = node.parm(parm_name)
        if parm is None:
            continue

        parm_type = parm_data[0]
        if parm_type == 'keyframes':
            deferred.append((parm, 'keyframes', parm_data[1]))
        elif parm_type == 'expression':
            deferred.append((parm, 'expression', parm_data[1], parm_data[2]))
        else:
            simple_values[parm_name] = parm_data[1]

    # Set all simple values at once
    if simple_values:
        try:
            node.setParms(simple_values)
        except hou.OperationFailed:
            # Fall back to one-by-one if setParms fails
            for parm_name, value in simple_values.items():
                try:
                    node.parm(parm_name).set(value)
                except (hou.OperationFailed, hou.PermissionError):
                    pass

    # Then set keyframes and expressions
    for item in deferred:
        parm = item[0]
        try:
            if item[1] == 'keyframes':
                parm.deleteAllKeyframes()
                for keyframe in item[2]:
                    parm.setKeyframe(keyframe)
            elif item[1] == 'expression':
                parm.setExpression(item[2], item[3])
        except (hou.OperationFailed, hou.PermissionError):
            pass


def capture_connections(node):
    """Capture input and output connections."""
    input_connections = []
    for conn in node.inputConnections():
        input_connections.append((
            conn.inputIndex(),
            conn.inputNode().path(),  # Store path, not node reference
            conn.outputIndex()
        ))

    output_connections = []
    for conn in node.outputConnections():
        output_connections.append((
            conn.outputIndex(),
            conn.outputNode().path(),  # Store path, not node reference
            conn.inputIndex()
        ))

    return input_connections, output_connections


def restore_connections(node, input_connections, output_connections):
    """Restore input and output connections."""
    for input_idx, input_node_path, output_idx in input_connections:
        try:
            input_node = hou.node(input_node_path)
            if input_node is not None:
                node.setInput(input_idx, input_node, output_idx)
        except hou.OperationFailed:
            pass  # Connection can't be restored, skip

    for output_idx, output_node_path, input_idx in output_connections:
        try:
            output_node = hou.node(output_node_path)
            if output_node is not None:
                output_node.setInput(input_idx, node, output_idx)
        except hou.OperationFailed:
            pass  # Connection can't be restored, skip


def rebuild_single_node(source_node):
    """Rebuild a single node in place."""
    parent = source_node.parent()
    node_type = source_node.type().name()
    node_name = source_node.name()
    position = source_node.position()
    color = source_node.color()
    user_data = {key: source_node.userData(key) for key in source_node.userDataDict().keys()}

    # 1. Capture parameter values
    parm_values = capture_parm_values(source_node)

    # 2. Capture connections
    input_connections, output_connections = capture_connections(source_node)

    # 3. Capture editable dive target contents (if has editable nodes)
    # Note: editablesAsData() uses 'EditableNodes' section internally
    editables_data = None
    if has_editable_nodes(source_node):
        # Use Houdini's built-in method to capture editable content
        editables_data = source_node.editablesAsData()

    # 4. Delete original node
    source_node.destroy()

    # 5. Create fresh node of same type
    new_node = parent.createNode(node_type, node_name)
    new_node.setPosition(position)
    new_node.setColor(color)

    # 6. Restore user data
    for key, value in user_data.items():
        new_node.setUserData(key, value)

    # 7. Restore parameter values
    restore_parm_values(new_node, parm_values)

    # 8. Restore connections
    restore_connections(new_node, input_connections, output_connections)

    # 9. Restore editable dive target contents (if applicable)
    if editables_data and has_editable_nodes(new_node):
        # Need children=True (default) to actually recreate internal nodes
        # This may unlock the node, so we re-lock it afterward
        new_node.setEditablesFromData(editables_data)
        # Re-lock the node if it was unlocked by setEditablesFromData
        if not new_node.isLockedHDA():
            new_node.matchCurrentDefinition()

    return new_node


def find_all_th_nodes():
    """Find all Tumblehead nodes that can be rebuilt.

    Includes TH nodes at top level and in editable dive targets.
    Excludes TH nodes in locked HDA structures.

    Returns:
        List of hou.Node objects with type starting with 'th::'
    """
    root = hou.node('/')
    all_nodes = root.allSubChildren()
    return [
        n for n in all_nodes
        if is_th_node(n) and not n.isInsideLockedHDA()
    ]


def rebuild_nodes(nodes=None):
    """
    Rebuild Tumblehead nodes in place, preserving settings and connections.

    Rebuilds from outermost to innermost, re-scanning after each level.
    This ensures nested TH nodes (inside dive targets) are properly rebuilt
    after their parent nodes recreate them via setEditablesFromData().

    If nodes is None, scans the entire scene for th:: nodes.
    Only rebuilds nodes whose type starts with 'th::' (Tumblehead HDAs).

    Args:
        nodes: List of hou.Node objects (defaults to all th:: nodes in scene)

    Returns:
        List of rebuilt nodes
    """
    if nodes is None:
        initial_nodes = find_all_th_nodes()
    else:
        initial_nodes = [n for n in nodes if is_th_node(n)]

    if not initial_nodes:
        hou.ui.displayMessage("No Tumblehead nodes found in scene", severity=hou.severityType.Warning)
        return []

    rebuilt = []
    failed = []
    rebuilt_paths = set()  # Track paths we've already rebuilt

    with hou.undos.group("Rebuild Nodes"):
        while True:
            # Re-scan for TH nodes (some may have been recreated by setEditablesFromData)
            current_th = find_all_th_nodes()

            # Filter out already rebuilt nodes (by path)
            current_th = [n for n in current_th if n.path() not in rebuilt_paths]

            if not current_th:
                break

            # Find outermost TH nodes (not nested inside another TH node in current list)
            toplevel = []
            for node in current_th:
                is_nested = False
                parent = node.parent()
                while parent is not None:
                    if is_th_node(parent) and parent in current_th:
                        is_nested = True
                        break
                    parent = parent.parent()
                if not is_nested:
                    toplevel.append(node)

            if not toplevel:
                break

            # Rebuild toplevel nodes
            for node in toplevel:
                node_name = node.name()
                node_path = node.path()
                try:
                    new_node = rebuild_single_node(node)
                    if new_node:
                        rebuilt.append(new_node)
                        rebuilt_paths.add(new_node.path())
                except Exception as e:
                    failed.append((node_name, str(e)))
                    rebuilt_paths.add(node_path)  # Don't retry failed nodes

    # Select the new nodes (with error handling for deleted nodes)
    hou.clearAllSelected()
    for node in rebuilt:
        try:
            node.setSelected(True)
        except hou.ObjectWasDeleted:
            pass

    # Report results
    if failed:
        error_details = "\n".join(f"  {name}: {error}" for name, error in failed)
        hou.ui.displayMessage(
            f"Rebuilt {len(rebuilt)} node(s).\n\nFailed ({len(failed)}):\n{error_details}",
            severity=hou.severityType.Warning
        )

    return rebuilt
