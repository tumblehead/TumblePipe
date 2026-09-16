"""Task collection: inspect the node graph and gather the ProcessTasks to run.

Walks the export / split / rig nodes for a publish context (group / shot /
asset / rig), validates each is correctly configured, and assembles the ordered
task list — including upstream-dependency discovery for a single triggered
export node. Builds the individual tasks through ``task_factory``.
"""

import uuid

from tumblepipe.config.groups import get_group, list_groups
from tumblepipe.config.department import (
    list_departments,
    list_entity_departments,
)
from tumblepipe.pipe.paths import latest_export_path, Context
from tumblepipe.util.uri import Uri
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


def _get_downstream_departments(
    entity_type: str, current_department: str, entity_uri: Uri | None = None,
    ) -> list[str]:
    """Get list of downstream departments for an entity.

    Scoped to the entity when we have its URI: a shot that only carries
    tracking has nothing downstream of it, and offering publish tasks for
    departments it does not have would be noise. Falls back to the whole pool
    for a group (whose members can differ) or an unresolvable entity.

    Order is the pool's — an entity's assignment is a filter over it, never a
    reordering — so "downstream" stays the pipeline's notion of downstream.
    """
    if entity_type == 'shot':
        context = 'shots'
    elif entity_type == 'asset':
        context = 'assets'
    elif entity_type == 'group':
        # A Multi's URI is ``groups:/<context>/<name>``, so its first
        # segment names the context its members are drawn from. The Multi
        # carries no department assignment of its own — its members' can
        # differ — so it takes the whole pool, which is what the paragraph
        # above always promised and what this branch did not do: it fell
        # through to the bare ``return []``, so a Multi workfile offered
        # only its own department and never anything downstream of it.
        if entity_uri is None or len(entity_uri.segments) == 0:
            return []
        context = entity_uri.segments[0]
        entity_uri = None
    else:
        return []

    if entity_uri is not None:
        departments = list_entity_departments(entity_uri)
    else:
        departments = list_departments(context)

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
    downstream_departments = _get_downstream_departments(
        entity_type, department_name, entity_uri=context.entity_uri,
    )
    all_departments = [department_name] + downstream_departments

    if _is_rig_context(context, entity_type):
        owned = _owned_entity_uris(context, entity_type)
        tasks = _collect_rig_publish_tasks(owned) if owned else []
    elif entity_type == 'group':
        tasks = _collect_group_publish_tasks(context, all_departments)
    elif entity_type == 'shot':
        tasks = _collect_shot_publish_tasks(context, all_departments)
    elif entity_type == 'asset':
        tasks = _collect_asset_publish_tasks(context, all_departments)

    return tasks


def _is_rig_context(context: Context, entity_type: str | None) -> bool:
    """Does this workfile publish export_rig SOPs rather than export_layer LOPs?

    The rig department of an asset *or of an asset Multi*. The Multi half
    was missing: a character team merged their rigs into one Multi rig
    workfile and every export_rig in it went uncollected, because the
    group collector only looks for export_layer — so Publish and each
    node's own Export button both said "no export tasks".
    """
    if context.department_name != 'rig':
        return False
    if entity_type == 'asset':
        return True
    if entity_type == 'group':
        segments = context.entity_uri.segments
        return len(segments) > 0 and segments[0] == 'assets'
    return False


def _owned_entity_uris(context: Context, entity_type: str | None) -> set[Uri] | None:
    """The entities this workfile publishes: itself, or its Multi's members.

    None when the workfile is a Multi that is no longer configured.
    """
    if entity_type != 'group':
        return {context.entity_uri}
    group = get_group(context.entity_uri)
    if group is None:
        return None
    return set(group.members)


def _format_node_group(grouped: dict[str, list[str]], limit: int = 6) -> list[str]:
    """Render ``{reason key: [node paths]}`` as one indented line per key."""
    lines = []
    for key in sorted(grouped):
        paths = sorted(grouped[key])
        shown = ', '.join(paths[:limit])
        if len(paths) > limit:
            shown += f', ... ({len(paths)} nodes total)'
        lines.append(f'  {key} - {shown}')
    return lines


def _multis_covering(entity_uri_strings) -> list[str]:
    """Names of the Multis whose membership covers every given entity.

    Used to turn "these export nodes address a foreign entity" into the
    actionable half of the sentence: the artist is usually holding a
    multi-shot scene that someone built as a plain shot, and the Multi that
    already lists those shots is the workfile they should be in.
    """
    uris = []
    for raw in entity_uri_strings:
        try:
            uris.append(Uri.parse_unsafe(raw))
        except ValueError:
            return []
    contexts = {uri.segments[0] for uri in uris if len(uri.segments) > 0}
    if len(contexts) != 1:
        return []
    covering = []
    for group in list_groups(contexts.pop()):
        members = set(group.members)
        if all(uri in members for uri in uris):
            covering.append(group.name)
    return sorted(covering)


def describe_missing_tasks(context: Context) -> str:
    """Explain why :func:`collect_publish_tasks` came back empty.

    The collectors filter the scene's export nodes silently: a node whose
    entity this workfile does not own is simply not collected. An empty
    result therefore reads the same whether the scene holds no export nodes
    at all or holds twenty that all address another entity. A layout
    department lost a day to the second case — their multi-shot scene was a
    *shot* that had been named like a Multi, so every per-shot export node in
    it was foreign to it, and the only feedback was "No export tasks found
    for the current context."

    Returns extra lines for the caller's warning, or '' when the warning has
    nothing to add.
    """
    if context is None:
        return ''

    entity_type = get_entity_type(context.entity_uri)
    if entity_type is None:
        return (
            f"This workfile's entity is {context.entity_uri}, which is "
            f"neither a shot, an asset nor a Multi."
        )

    member_uris = None
    if entity_type == 'group':
        group = get_group(context.entity_uri)
        if group is None:
            return (
                f"Multi {context.entity_uri} is no longer in this project's "
                f"configuration."
            )
        member_uris = set(group.members)

    departments = [context.department_name] + _get_downstream_departments(
        entity_type, context.department_name, entity_uri=context.entity_uri,
    )

    # Mirror the collector that actually ran: only a rig context collects
    # export_rig SOPs, every other context collects export_layer LOPs.
    if _is_rig_context(context, entity_type):
        nodes = [
            export_rig.ExportRig(native)
            for native in ns.list_by_node_type('export_rig', 'Sop')
        ]
        node_kind = 'th::export_rig'
    else:
        nodes = [
            export_layer.ExportLayer(native)
            for native in ns.list_by_node_type('export_layer', 'Lop')
        ]
        node_kind = 'th::export_layer'

    owner = (
        f'Multi {context.entity_uri}' if entity_type == 'group'
        else str(context.entity_uri)
    )
    header = f'This workfile publishes {owner} ({context.department_name}).'

    if len(nodes) == 0:
        return f'{header}\nIt contains no {node_kind} nodes.'

    foreign: dict[str, list[str]] = {}
    wrong_department: dict[str, list[str]] = {}
    bypassed: list[str] = []
    unresolved: list[str] = []

    for node in nodes:
        if node.native().isBypassed():
            bypassed.append(node.path())
            continue
        entity_uri = node.get_entity_uri()
        if entity_uri is None:
            unresolved.append(node.path())
            continue
        owned = (
            entity_uri in member_uris if member_uris is not None
            else entity_uri == context.entity_uri
        )
        if not owned:
            foreign.setdefault(str(entity_uri), []).append(node.path())
            continue
        department_name = node.get_department_name()
        if department_name not in departments:
            wrong_department.setdefault(
                str(department_name), []).append(node.path())

    lines = [header]
    if foreign:
        if member_uris is None:
            lines.append(
                "An export node only runs for the workfile's own entity. "
                "These address a different one — move them into that "
                "entity's workfile, or publish from a Multi that has it as "
                "a member:"
            )
        else:
            lines.append(
                "An export node only runs for a member of this Multi. "
                "These address a non-member — add the entity to the Multi, "
                "or move the node:"
            )
        lines.extend(_format_node_group(foreign))
        if member_uris is None:
            covering = _multis_covering(foreign.keys())
            if covering:
                lines.append(
                    'Multi ' + ', '.join(covering) + ' already lists every '
                    'one of them as a member.'
                )
    if wrong_department:
        lines.append(
            f'Departments published from here: {", ".join(departments)}. '
            f'These nodes export into another one:'
        )
        lines.extend(_format_node_group(wrong_department))
    if bypassed:
        lines.append('Bypassed: ' + ', '.join(sorted(bypassed)))
    if unresolved:
        if member_uris is not None:
            reason = (
                "a Multi has no single entity for 'from_context' to pick — "
                "set each node's Entity to the member it exports, or the "
                "Entity names something that is gone"
            )
        else:
            reason = (
                "an Entity parm naming something that is gone, or "
                "'from_context' in a workfile with no entity"
            )
        lines.append(
            f"No entity resolved ({reason}): " + ', '.join(sorted(unresolved))
        )
    return '\n'.join(lines)


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

        # Collect unique channels from export nodes for this entity
        entity_channels_found = set()
        for dept in depts:
            key = (uri_str, dept)
            for export_node in exports_by_entity_dept.get(key, []):
                entity_channels_found.add(export_node.get_channel_name())

        # Add grouped build task (depends on all exports)
        if entity_channels_found:
            build_group = _create_build_group_task(
                entity_uri,
                sorted(entity_channels_found),
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

    # Collect unique channels from export nodes for build tasks
    channels_found = set()
    for export_node in shot_export_nodes:
        channels_found.add(export_node.get_channel_name())

    # Add grouped build task (depends on all exports)
    if channels_found:
        # Get frame range from first export node (exports share the same frame range setting)
        build_first, build_last = None, None
        if shot_export_nodes:
            build_first, build_last = _get_frame_range_values(shot_uri, shot_export_nodes[0])

        build_group = _create_build_group_task(
            shot_uri,
            sorted(channels_found),
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

    # Collect unique channels from export nodes for build tasks
    channels_found = set()
    for export_node in asset_export_nodes:
        channels_found.add(export_node.get_channel_name())

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
    if channels_found:
        build_group = _create_build_group_task(
            asset_uri,
            sorted(channels_found),
            depends_on=export_task_ids
        )
        tasks.append(build_group)

    return tasks


def _collect_rig_publish_tasks(owned_uris: set[Uri]) -> list[ProcessTask]:
    """Collect publish tasks for the export_rig nodes addressing ``owned_uris``.

    One asset for an asset rig workfile, every member for an asset Multi.
    """
    tasks = []

    def _is_rig_export_correct(node):
        if node.native().isBypassed():
            return False
        return node.get_entity_uri() in owned_uris

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
        channel = export_node.get_channel_name()

        export_path = latest_export_path(asset_uri, channel, 'rig')
        version = _get_version_from_path(export_path)

        node_ref = export_node
        first_frame, last_frame = _get_frame_range_values(asset_uri)

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department='rig',
            task_type='export',
            channel=channel,
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
    native = start_node.native()
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
    # LayerSplit exports shared content and has no channel; every other
    # export wrapper implements get_channel_name.
    clicked_channel = ('default' if isinstance(export_node, layer_split.LayerSplit)
                       else export_node.get_channel_name())
    clicked_node_path = export_node.path()

    def _enable_matching_tasks(uri, dept, channel=None, node_path=None):
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
                if channel is None or task.channel == channel:
                    enabled_task_ids.add(task.id)
            # Enable build_group task for the same entity (grouped build tasks)
            elif task.uri == uri and task.task_type == 'build_group':
                if task.children:
                    children_enabled = False
                    for child in task.children:
                        if child.task_type == 'build':
                            if channel is None or child.channel == channel:
                                enabled_task_ids.add(child.id)
                                children_enabled = True
                    # Only enable parent if at least one child matches the channel
                    if children_enabled:
                        enabled_task_ids.add(task.id)

    if clicked_uri is not None:
        _enable_matching_tasks(clicked_uri, clicked_dept, clicked_channel, clicked_node_path)

    # Enable upstream dependency tasks (and their build tasks)
    upstream_nodes = find_upstream_export_nodes(export_node)
    for upstream_node in upstream_nodes:
        upstream_uri = upstream_node.get_entity_uri()
        upstream_dept = upstream_node.get_department_name()
        upstream_channel = ('default' if isinstance(upstream_node, layer_split.LayerSplit)
                            else upstream_node.get_channel_name())
        upstream_path = upstream_node.path()
        _enable_matching_tasks(upstream_uri, upstream_dept, upstream_channel, upstream_path)

    return all_tasks, enabled_task_ids

