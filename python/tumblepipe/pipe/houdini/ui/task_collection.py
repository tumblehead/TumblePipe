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
            uris.append(Uri.parse(raw))
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
    kind = _RIG_EXPORTS if _is_rig_context(context, entity_type) else _LAYER_EXPORTS
    nodes = _list_nodes(kind)
    node_kind = f'th::{kind[1]}'

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


def collect_publish_tasks(context: Context) -> list[ProcessTask]:
    """Collect all publish tasks for the current context.

    The workfile publishes its own entity — or, in a Multi's workfile, every
    member — in its own department and every one downstream of it. A rig
    context publishes its export_rig SOPs; every other context its
    export_layer LOPs (and the layer_split LOPs upstream of them).
    """
    if context is None:
        return []
    entity_type = get_entity_type(context.entity_uri)
    owned = _owned_entity_uris(context, entity_type)
    if not owned:
        return []
    if _is_rig_context(context, entity_type):
        return _collect_rig_publish_tasks(owned)
    departments = [context.department_name] + _get_downstream_departments(
        entity_type, context.department_name, entity_uri=context.entity_uri,
    )
    return _collect_layer_publish_tasks(owned, departments)


def _publishes(node, owned: set[Uri], departments: list[str]) -> bool:
    """The predicate every collected node passes: live, owned, in scope."""
    if node.native().isBypassed():
        return False
    if node.get_entity_uri() not in owned:
        return False
    return node.get_department_name() in departments


# The node kinds a publish collects: (wrapper module, node type name,
# category). The wrapper class is looked up when listing.
_LAYER_EXPORTS = (export_layer, 'export_layer', 'Lop')
_LAYER_SPLITS = (layer_split, 'layer_split', 'Lop')
_RIG_EXPORTS = (export_rig, 'export_rig', 'Sop')

_WRAPPERS = {
    'export_layer': 'ExportLayer',
    'layer_split': 'LayerSplit',
    'export_rig': 'ExportRig',
}


def _list_nodes(kind) -> list:
    module, type_name, category = kind
    wrapper = getattr(module, _WRAPPERS[type_name])
    return [wrapper(native) for native in ns.list_by_node_type(type_name, category)]


def _collect_layer_publish_tasks(
    owned: set[Uri], departments: list[str],
    ) -> list[ProcessTask]:
    """Validation + grouped export tasks per entity and department, then a
    grouped build task per entity.

    A layer_split node is collected only when it is upstream of a collected
    export node; it runs inside its department's export group.
    """
    export_nodes = [
        node for node in _list_nodes(_LAYER_EXPORTS)
        if _publishes(node, owned, departments)
    ]
    connected_split_paths = {
        upstream.path()
        for export_node in export_nodes
        for upstream in find_upstream_export_nodes(export_node)
        if isinstance(upstream, layer_split.LayerSplit)
    }
    split_nodes = [
        node for node in _list_nodes(_LAYER_SPLITS)
        if node.path() in connected_split_paths
        and _publishes(node, owned, departments)
    ]

    def _by_entity_and_department(nodes):
        grouped: dict[str, dict[str, list]] = {}
        for node in nodes:
            grouped.setdefault(str(node.get_entity_uri()), {}).setdefault(
                node.get_department_name(), []
            ).append(node)
        return grouped

    exports = _by_entity_and_department(export_nodes)
    splits = _by_entity_and_department(split_nodes)

    tasks = []
    for uri_str in sorted(exports):
        entity_exports = exports[uri_str]
        entity_uri = Uri.parse(uri_str)
        export_task_ids = []
        for department in sorted(entity_exports, key=departments.index):
            department_exports = entity_exports[department]
            first_frame, last_frame = _get_frame_range_values(
                entity_uri, department_exports[0]
            )
            validation_task = _create_validation_task(
                entity_uri=entity_uri,
                department=department,
                export_nodes=department_exports,
            )
            tasks.append(validation_task)
            group_task = _create_export_group_task(
                entity_uri=entity_uri,
                department=department,
                split_nodes=splits.get(uri_str, {}).get(department, []),
                export_nodes=department_exports,
                first_frame=first_frame,
                last_frame=last_frame,
                depends_on=[validation_task.id],
            )
            tasks.append(group_task)
            export_task_ids.append(group_task.id)

        # One build per entity, over every channel its exports publish. Its
        # frame range is the first export node's: exports of one entity
        # share the range setting.
        all_entity_exports = [
            node for nodes in entity_exports.values() for node in nodes
        ]
        build_first, build_last = _get_frame_range_values(
            entity_uri, all_entity_exports[0]
        )
        tasks.append(_create_build_group_task(
            entity_uri,
            sorted({node.get_channel_name() for node in all_entity_exports}),
            depends_on=export_task_ids,
            first_frame=build_first,
            last_frame=build_last,
        ))
    return tasks


def _collect_rig_publish_tasks(owned_uris: set[Uri]) -> list[ProcessTask]:
    """Collect publish tasks for the export_rig nodes addressing ``owned_uris``.

    One asset for an asset rig workfile, every member for an asset Multi.
    """
    tasks = []
    rig_export_nodes = [
        node for node in _list_nodes(_RIG_EXPORTS)
        if _publishes(node, owned_uris, ['rig'])
    ]
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

