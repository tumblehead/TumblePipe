"""Task collection: inspect the node graph and gather the ProcessTasks to run.

Walks the export / split / rig nodes for a publish context (group / shot /
asset / rig), validates each is correctly configured, and assembles the ordered
task list — including upstream-dependency discovery for a single triggered
export node. Builds the individual tasks through ``task_factory``.
"""

import uuid

from tumblepipe.config.groups import get_group
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.paths import latest_export_path, Context
from tumblepipe.pipe.houdini.lops import export_layer, layer_split
from tumblepipe.pipe.houdini.sops import export_rig
import tumblepipe.pipe.houdini.nodes as ns

from .process_task import ProcessTask
from .helpers import get_entity_type
from .task_factory import (
    _create_validation_task,
    _create_export_group_task,
    _create_build_group_task,
    _get_frame_range_values,
    _get_version_from_path,
    _uri_name,
)


def _get_downstream_departments(entity_type: str, current_department: str) -> list[str]:
    """Get list of downstream departments for an entity type"""
    if entity_type == 'shot':
        departments = list_departments('shots')
    elif entity_type == 'asset':
        departments = list_departments('assets')
    else:
        return []

    if len(departments) == 0:
        return []

    department_names = [dept.name for dept in departments]
    if current_department not in department_names:
        return []

    department_index = department_names.index(current_department)
    return department_names[department_index + 1:]


def collect_publish_tasks(context: Context) -> list[ProcessTask]:
    """
    Collect all publish tasks for the current context.

    Includes tasks for current department AND all downstream departments.
    For groups: Returns tasks for all member export nodes across all departments.
    For single entities: Returns tasks for the entity across all departments.
    """
    if context is None:
        return []

    tasks: list[ProcessTask] = []
    entity_type = get_entity_type(context.entity_uri)
    department_name = context.department_name

    # Get downstream departments to include
    downstream_departments = _get_downstream_departments(entity_type, department_name)
    all_departments = [department_name] + downstream_departments

    if entity_type == 'group':
        tasks = _collect_group_publish_tasks(context, all_departments)
    elif entity_type == 'shot':
        tasks = _collect_shot_publish_tasks(context, all_departments)
    elif entity_type == 'asset':
        if department_name == 'rig':
            tasks = _collect_rig_publish_tasks(context)
        else:
            tasks = _collect_asset_publish_tasks(context, all_departments)

    return tasks


def _collect_group_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for all group members across all specified departments.

    Creates grouped export tasks where layer_split and export_layer nodes are
    organized under a parent "Export (dept)" task for each entity/department.
    """
    tasks = []

    group = get_group(context.entity_uri)
    if group is None:
        return []

    member_uris = set(group.members)

    # Filter function for export_layer nodes
    def _is_group_export_correct(node):
        if node.native().isBypassed():
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri is None:
            return False
        dept = node.get_department_name()
        if dept not in departments:
            return False
        return entity_uri in member_uris

    # Find all export nodes for group members across all departments
    group_export_nodes = list(
        filter(
            _is_group_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type("export_layer", "Lop"),
            ),
        )
    )

    # Collect paths of layer_split nodes that are connected upstream to valid export nodes
    connected_split_paths = set()
    for export_node in group_export_nodes:
        for upstream in find_upstream_export_nodes(export_node):
            if isinstance(upstream, layer_split.LayerSplit):
                connected_split_paths.add(upstream.path())

    # Filter function for layer_split nodes - must be connected to a valid export node
    def _is_group_split_correct(node):
        if node.native().isBypassed():
            return False
        if node.path() not in connected_split_paths:
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri is None:
            return False
        dept = node.get_department_name()
        if dept not in departments:
            return False
        return entity_uri in member_uris

    # Find layer_split nodes - only connected ones
    group_split_nodes = list(
        filter(
            _is_group_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    # Group export nodes by entity URI and department
    exports_by_entity_dept: dict[tuple[str, str], list] = {}
    for export_node in group_export_nodes:
        entity_uri = export_node.get_entity_uri()
        dept = export_node.get_department_name()
        key = (str(entity_uri), dept)
        if key not in exports_by_entity_dept:
            exports_by_entity_dept[key] = []
        exports_by_entity_dept[key].append(export_node)

    # Group layer_split nodes by entity URI and department
    splits_by_entity_dept: dict[tuple[str, str], list] = {}
    for split_node in group_split_nodes:
        entity_uri = split_node.get_entity_uri()
        dept = split_node.get_department_name()
        key = (str(entity_uri), dept)
        if key not in splits_by_entity_dept:
            splits_by_entity_dept[key] = []
        splits_by_entity_dept[key].append(split_node)

    # Sort departments for consistent ordering
    dept_order = {dept: i for i, dept in enumerate(departments)}

    # Group by entity for output ordering
    entities_with_exports: dict[str, list[str]] = {}  # uri_str -> list of departments
    for (uri_str, dept) in exports_by_entity_dept.keys():
        if uri_str not in entities_with_exports:
            entities_with_exports[uri_str] = []
        entities_with_exports[uri_str].append(dept)

    # For each entity: add validation + export tasks, then build task
    for uri_str in sorted(entities_with_exports.keys()):
        depts = entities_with_exports[uri_str]
        # Sort by department order
        depts.sort(key=lambda d: dept_order.get(d, 999))

        # Get entity URI from first export node
        first_key = (uri_str, depts[0])
        entity_uri = exports_by_entity_dept[first_key][0].get_entity_uri()

        # Track export task IDs for build dependency
        export_task_ids = []

        # Create validation + export tasks per department
        for dept in depts:
            key = (uri_str, dept)
            dept_exports = exports_by_entity_dept.get(key, [])
            dept_splits = splits_by_entity_dept.get(key, [])

            # Get frame range from first export node
            first_frame, last_frame = _get_frame_range_values(entity_uri, dept_exports[0] if dept_exports else None)

            # Create validation task (export depends on this)
            validation_task = _create_validation_task(
                entity_uri=entity_uri,
                department=dept,
                export_nodes=dept_exports,
            )
            tasks.append(validation_task)

            # Create grouped export task (depends on validation)
            group_task = _create_export_group_task(
                entity_uri=entity_uri,
                department=dept,
                split_nodes=dept_splits,
                export_nodes=dept_exports,
                first_frame=first_frame,
                last_frame=last_frame,
                depends_on=[validation_task.id],
            )
            tasks.append(group_task)
            export_task_ids.append(group_task.id)

        # Collect unique variants from export nodes for this entity
        entity_variants_found = set()
        for dept in depts:
            key = (uri_str, dept)
            for export_node in exports_by_entity_dept.get(key, []):
                entity_variants_found.add(export_node.get_variant_name())

        # Add grouped build task (depends on all exports)
        if entity_variants_found:
            build_group = _create_build_group_task(
                entity_uri,
                sorted(entity_variants_found),
                depends_on=export_task_ids
            )
            tasks.append(build_group)

    return tasks


def _collect_shot_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single shot across all specified departments.

    Creates grouped export tasks where layer_split and export_layer nodes are
    organized under a parent "Export (dept)" task for each department.
    """
    tasks = []
    shot_uri = context.entity_uri

    # Filter function for export_layer nodes
    def _is_shot_export_correct(node):
        if node.native().isBypassed():
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri != shot_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find export nodes for the shot across all departments
    shot_export_nodes = list(
        filter(
            _is_shot_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type("export_layer", "Lop"),
            ),
        )
    )

    # Collect paths of layer_split nodes that are connected upstream to valid export nodes
    connected_split_paths = set()
    for export_node in shot_export_nodes:
        for upstream in find_upstream_export_nodes(export_node):
            if isinstance(upstream, layer_split.LayerSplit):
                connected_split_paths.add(upstream.path())

    # Filter function for layer_split nodes - must be connected to a valid export node
    def _is_shot_split_correct(node):
        if node.native().isBypassed():
            return False
        if node.path() not in connected_split_paths:
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri != shot_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find layer_split nodes - only connected ones
    shot_split_nodes = list(
        filter(
            _is_shot_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    # Group export nodes by department
    exports_by_dept: dict[str, list] = {}
    for export_node in shot_export_nodes:
        dept = export_node.get_department_name()
        if dept not in exports_by_dept:
            exports_by_dept[dept] = []
        exports_by_dept[dept].append(export_node)

    # Group layer_split nodes by department
    splits_by_dept: dict[str, list] = {}
    for split_node in shot_split_nodes:
        dept = split_node.get_department_name()
        if dept not in splits_by_dept:
            splits_by_dept[dept] = []
        splits_by_dept[dept].append(split_node)

    # Sort departments for consistent ordering
    dept_order = {dept: i for i, dept in enumerate(departments)}
    sorted_depts = sorted(exports_by_dept.keys(), key=lambda d: dept_order.get(d, 999))

    # Track export task IDs for build dependency
    export_task_ids = []

    # Create validation + export tasks per department
    for dept in sorted_depts:
        dept_exports = exports_by_dept.get(dept, [])
        dept_splits = splits_by_dept.get(dept, [])

        # Get frame range from first export node
        first_frame, last_frame = _get_frame_range_values(shot_uri, dept_exports[0] if dept_exports else None)

        # Create validation task (export depends on this)
        validation_task = _create_validation_task(
            entity_uri=shot_uri,
            department=dept,
            export_nodes=dept_exports,
        )
        tasks.append(validation_task)

        # Create grouped export task (depends on validation)
        group_task = _create_export_group_task(
            entity_uri=shot_uri,
            department=dept,
            split_nodes=dept_splits,
            export_nodes=dept_exports,
            first_frame=first_frame,
            last_frame=last_frame,
            depends_on=[validation_task.id],
        )
        tasks.append(group_task)
        export_task_ids.append(group_task.id)

    # Collect unique variants from export nodes for build tasks
    variants_found = set()
    for export_node in shot_export_nodes:
        variants_found.add(export_node.get_variant_name())

    # Add grouped build task (depends on all exports)
    if variants_found:
        # Get frame range from first export node (exports share the same frame range setting)
        build_first, build_last = None, None
        if shot_export_nodes:
            build_first, build_last = _get_frame_range_values(shot_uri, shot_export_nodes[0])

        build_group = _create_build_group_task(
            shot_uri,
            sorted(variants_found),
            depends_on=export_task_ids,
            first_frame=build_first,
            last_frame=build_last
        )
        tasks.append(build_group)

    return tasks


def _collect_asset_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single asset across all specified departments.

    Creates grouped export tasks where layer_split and export_layer nodes are
    organized under a parent "Export (dept)" task for each department.
    """
    tasks = []
    asset_uri = context.entity_uri

    # Filter function for export_layer nodes
    def _is_asset_export_correct(node):
        if node.native().isBypassed():
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri != context.entity_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find export nodes for the asset across all departments
    asset_export_nodes = list(
        filter(
            _is_asset_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type("export_layer", "Lop"),
            ),
        )
    )

    # Collect paths of layer_split nodes that are connected upstream to valid export nodes
    connected_split_paths = set()
    for export_node in asset_export_nodes:
        for upstream in find_upstream_export_nodes(export_node):
            if isinstance(upstream, layer_split.LayerSplit):
                connected_split_paths.add(upstream.path())

    # Filter function for layer_split nodes - must be connected to a valid export node
    def _is_asset_split_correct(node):
        if node.native().isBypassed():
            return False
        if node.path() not in connected_split_paths:
            return False
        entity_uri = node.get_entity_uri()
        if entity_uri != asset_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find layer_split nodes - only connected ones
    asset_split_nodes = list(
        filter(
            _is_asset_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    # Group export nodes by department
    exports_by_dept: dict[str, list] = {}
    for export_node in asset_export_nodes:
        dept = export_node.get_department_name()
        if dept not in exports_by_dept:
            exports_by_dept[dept] = []
        exports_by_dept[dept].append(export_node)

    # Group layer_split nodes by department
    splits_by_dept: dict[str, list] = {}
    for split_node in asset_split_nodes:
        dept = split_node.get_department_name()
        if dept not in splits_by_dept:
            splits_by_dept[dept] = []
        splits_by_dept[dept].append(split_node)

    # Sort departments for consistent ordering
    dept_order = {dept: i for i, dept in enumerate(departments)}
    sorted_depts = sorted(exports_by_dept.keys(), key=lambda d: dept_order.get(d, 999))

    # Collect unique variants from export nodes for build tasks
    variants_found = set()
    for export_node in asset_export_nodes:
        variants_found.add(export_node.get_variant_name())

    # Track export task IDs for build dependency
    export_task_ids = []

    # Create validation + export tasks per department
    for dept in sorted_depts:
        dept_exports = exports_by_dept.get(dept, [])
        dept_splits = splits_by_dept.get(dept, [])

        # Get frame range from first export node
        first_frame, last_frame = _get_frame_range_values(asset_uri, dept_exports[0] if dept_exports else None)

        # Create validation task (export depends on this)
        validation_task = _create_validation_task(
            entity_uri=asset_uri,
            department=dept,
            export_nodes=dept_exports,
        )
        tasks.append(validation_task)

        # Create grouped export task (depends on validation)
        group_task = _create_export_group_task(
            entity_uri=asset_uri,
            department=dept,
            split_nodes=dept_splits,
            export_nodes=dept_exports,
            first_frame=first_frame,
            last_frame=last_frame,
            depends_on=[validation_task.id],
        )
        tasks.append(group_task)
        export_task_ids.append(group_task.id)

    # Add grouped build task (depends on all exports)
    if variants_found:
        build_group = _create_build_group_task(
            asset_uri,
            sorted(variants_found),
            depends_on=export_task_ids
        )
        tasks.append(build_group)

    return tasks


def _collect_rig_publish_tasks(context: Context) -> list[ProcessTask]:
    """Collect publish tasks for a rig"""
    tasks = []

    def _is_rig_export_correct(node):
        asset_uri = node.get_entity_uri()
        return asset_uri == context.entity_uri

    rig_export_nodes = list(
        filter(
            _is_rig_export_correct,
            map(
                export_rig.ExportRig,
                ns.list_by_node_type("export_rig", "Sop"),
            ),
        )
    )

    for export_node in rig_export_nodes:
        asset_uri = export_node.get_entity_uri()
        variant = export_node.get_variant_name()

        export_path = latest_export_path(asset_uri, variant, 'rig')
        version = _get_version_from_path(export_path)

        node_ref = export_node
        first_frame, last_frame = _get_frame_range_values(asset_uri)

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department='rig',
            task_type='export',
            variant=variant,
            description=f"Export rig: {_uri_name(asset_uri)}",
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=None,  # export_rig is local-only
            first_frame=first_frame,
            last_frame=last_frame,
        )
        tasks.append(task)

    return tasks


def find_upstream_export_nodes(start_node) -> list:
    """
    Find upstream export nodes in the node graph.

    Traverses the node graph upstream (depth-first) to find dependent export nodes
    like LayerSplit that feed into an ExportLayer.

    Args:
        start_node: The export node to start traversing from

    Returns:
        List of upstream export nodes in dependency order (upstream first)
    """
    visited = set()
    upstream_exports = []

    def _traverse(node):
        if node is None:
            return
        node_path = node.path()
        if node_path in visited:
            return
        visited.add(node_path)

        # Check inputs first (depth-first, so upstream nodes come first)
        for input_node in node.inputs():
            if input_node is not None:
                _traverse(input_node)

        # Check if this is an export node type
        node_type_name = node.type().name().lower()
        if 'export_layer' in node_type_name:
            upstream_exports.append(export_layer.ExportLayer(node))
        elif 'layer_split' in node_type_name:
            upstream_exports.append(layer_split.LayerSplit(node))

    # Start from inputs of start_node (don't include start_node itself)
    native = start_node.native() if hasattr(start_node, 'native') else start_node
    for input_node in native.inputs():
        if input_node is not None:
            _traverse(input_node)

    return upstream_exports


def collect_tasks_for_export_node(
    export_node,
    context: Context
) -> tuple[list[ProcessTask], set[str]]:
    """
    Collect all publish tasks with selective enablement for a specific export node.

    Args:
        export_node: The export node that was clicked
        context: The current workfile context

    Returns:
        Tuple of (all_tasks, enabled_task_ids) where enabled_task_ids contains
        the IDs of tasks that should be enabled (the clicked node + upstream deps + children)
    """
    # Collect all publish tasks for full context
    all_tasks = collect_publish_tasks(context)

    # Find task IDs to enable (clicked node + upstream dependencies)
    enabled_task_ids = set()

    # Enable clicked node's task
    clicked_uri = export_node.get_entity_uri()
    clicked_dept = export_node.get_department_name()
    clicked_variant = export_node.get_variant_name() if hasattr(export_node, 'get_variant_name') else 'default'
    clicked_node_path = export_node.path() if hasattr(export_node, 'path') else None

    def _enable_matching_tasks(uri, dept, variant=None, node_path=None):
        """Enable tasks matching the given criteria, including children of grouped tasks.

        Only enables the parent group task if at least one child matches the node_path
        or is an export_shared type (layer_split). This prevents sibling export nodes
        from being enabled when clicking on a specific export node.
        """
        for task in all_tasks:
            if task.uri == uri and task.department == dept:
                # For grouped tasks, only enable parent if children match
                if task.children:
                    children_enabled = False
                    for child in task.children:
                        # Enable child if it matches node_path
                        if node_path and child.node_path == node_path:
                            enabled_task_ids.add(child.id)
                            children_enabled = True
                        elif child.task_type == 'export_shared':
                            # Enable export_shared children (layer_split nodes)
                            enabled_task_ids.add(child.id)
                            children_enabled = True
                    # Only enable parent group if children were enabled
                    if children_enabled:
                        enabled_task_ids.add(task.id)
                else:
                    # Non-grouped task, enable directly
                    enabled_task_ids.add(task.id)
            # Enable build task for the same entity
            elif task.uri == uri and task.task_type == 'build':
                if variant is None or task.variant == variant:
                    enabled_task_ids.add(task.id)
            # Enable build_group task for the same entity (grouped build tasks)
            elif task.uri == uri and task.task_type == 'build_group':
                if task.children:
                    children_enabled = False
                    for child in task.children:
                        if child.task_type == 'build':
                            if variant is None or child.variant == variant:
                                enabled_task_ids.add(child.id)
                                children_enabled = True
                    # Only enable parent if at least one child matches the variant
                    if children_enabled:
                        enabled_task_ids.add(task.id)

    if clicked_uri is not None:
        _enable_matching_tasks(clicked_uri, clicked_dept, clicked_variant, clicked_node_path)

    # Enable upstream dependency tasks (and their build tasks)
    upstream_nodes = find_upstream_export_nodes(export_node)
    for upstream_node in upstream_nodes:
        upstream_uri = upstream_node.get_entity_uri()
        upstream_dept = upstream_node.get_department_name()
        upstream_variant = upstream_node.get_variant_name() if hasattr(upstream_node, 'get_variant_name') else 'default'
        upstream_path = upstream_node.path() if hasattr(upstream_node, 'path') else None
        _enable_matching_tasks(upstream_uri, upstream_dept, upstream_variant, upstream_path)

    return all_tasks, enabled_task_ids

