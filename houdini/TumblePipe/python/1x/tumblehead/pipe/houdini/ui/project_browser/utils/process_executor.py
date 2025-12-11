"""Process executor for running publish and other workflow tasks"""

from pathlib import Path
from typing import Callable
from contextlib import contextmanager
from io import StringIO
import traceback
import uuid
import sys

from qtpy.QtCore import QObject, Signal, QTimer

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.groups import get_group
from tumblehead.config.department import list_departments
from tumblehead.pipe.paths import latest_export_path, current_staged_path, Context
from tumblehead.pipe.houdini.lops import export_layer, layer_split
from tumblehead.pipe.houdini.sops import export_rig
import tumblehead.pipe.houdini.nodes as ns

from ..models.process_task import ProcessTask, TaskStatus
from ..helpers import get_entity_type

api = default_client()


@contextmanager
def _capture_output():
    """Context manager to capture stdout and stderr during local execution"""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class ProcessExecutor(QObject):
    """Executes process tasks sequentially with progress signals"""

    task_started = Signal(str)           # task_id
    task_completed = Signal(str)         # task_id
    task_failed = Signal(str, str)       # task_id, error_message
    all_completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ProcessTask] = []
        self._mode: str = 'local'
        self._is_running: bool = False
        self._current_index: int = 0

    def set_tasks(self, tasks: list[ProcessTask]):
        """Set the list of tasks to execute"""
        self._tasks = tasks

    def set_mode(self, mode: str):
        """Set execution mode: 'local' or 'farm'"""
        self._mode = mode

    def is_running(self) -> bool:
        """Check if executor is currently running"""
        return self._is_running

    def cancel(self):
        """Cancel execution (will stop after current task)"""
        self._is_running = False

    def execute(self):
        """Start executing enabled tasks"""
        self._is_running = True
        self._current_index = 0
        # Use QTimer to allow UI updates between tasks
        QTimer.singleShot(0, self._execute_next_task)

    def _execute_next_task(self):
        """Execute the next enabled task in the queue"""
        if not self._is_running:
            self.all_completed.emit()
            return

        # Skip disabled tasks
        while self._current_index < len(self._tasks):
            task = self._tasks[self._current_index]
            if task.enabled:
                break
            task.status = TaskStatus.SKIPPED
            self._current_index += 1

        # Check if all tasks are done
        if self._current_index >= len(self._tasks):
            self._is_running = False
            self.all_completed.emit()
            return

        task = self._tasks[self._current_index]
        task.status = TaskStatus.RUNNING
        self.task_started.emit(task.id)

        # Variables to store captured output (for error reporting)
        captured_stdout = ''
        captured_stderr = ''
        stdout_capture = None
        stderr_capture = None

        try:
            if self._mode == 'local':
                if task.execute_local is not None:
                    # Capture stdout/stderr during local execution to keep console clean
                    with _capture_output() as (stdout_capture, stderr_capture):
                        task.execute_local()
                else:
                    raise RuntimeError("No local executor defined for task")
            else:  # farm
                # Farm mode - let output go to farm logs (no capture)
                if task.execute_farm is not None:
                    task.execute_farm()
                else:
                    raise RuntimeError("No farm executor defined for task")

            task.status = TaskStatus.COMPLETED
            self.task_completed.emit(task.id)

        except Exception as e:
            task.status = TaskStatus.FAILED
            # Capture full traceback for debugging
            error_msg = traceback.format_exc()

            # Retrieve captured output (must do this before StringIO objects are gone)
            if stdout_capture is not None:
                captured_stdout = stdout_capture.getvalue()
            if stderr_capture is not None:
                captured_stderr = stderr_capture.getvalue()

            # For local mode, include captured output in error message
            if self._mode == 'local' and (captured_stdout or captured_stderr):
                error_msg += "\n\n--- Captured Output ---\n"
                if captured_stdout:
                    error_msg += f"STDOUT:\n{captured_stdout}\n"
                if captured_stderr:
                    error_msg += f"STDERR:\n{captured_stderr}\n"

            task.error_message = error_msg
            self.task_failed.emit(task.id, task.error_message)

        self._current_index += 1
        # Continue with next task after a short delay to allow UI update
        QTimer.singleShot(100, self._execute_next_task)


def _get_version_from_path(export_path) -> str | None:
    """Extract version string from export path"""
    if export_path is None:
        return None
    # Path typically ends with .../v001/... or similar
    parts = export_path.parts
    for part in reversed(parts):
        if part.startswith('v') and part[1:].isdigit():
            return part
    return None


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
    """Collect publish tasks for all group members across all specified departments"""
    tasks = []

    group = get_group(context.entity_uri)
    if group is None:
        return []

    member_uris = set(group.members)

    # Filter function for layer_split nodes
    def _is_group_split_correct(node):
        entity_uri = node.get_entity_uri()
        if entity_uri is None:
            return False
        dept = node.get_department_name()
        if dept not in departments:
            return False
        return entity_uri in member_uris

    # Find and add layer_split tasks first (shared content)
    group_split_nodes = list(
        filter(
            _is_group_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    for split_node in group_split_nodes:
        entity_uri = split_node.get_entity_uri()
        dept = split_node.get_department_name()
        node_ref = split_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department=dept,
            task_type='export_shared',
            description=f"Export Shared ({dept})",
            current_version=None,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=None,  # layer_split is local-only
        )
        tasks.append(task)

    def _is_group_export_correct(node):
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

    # Group export nodes by entity URI to organize tasks properly
    exports_by_entity: dict[str, list] = {}
    for export_node in group_export_nodes:
        entity_uri = export_node.get_entity_uri()
        uri_str = str(entity_uri)
        if uri_str not in exports_by_entity:
            exports_by_entity[uri_str] = []
        exports_by_entity[uri_str].append(export_node)

    # Sort departments for consistent ordering
    dept_order = {dept: i for i, dept in enumerate(departments)}

    # For each entity: add export tasks, then build task
    for uri_str in sorted(exports_by_entity.keys()):
        entity_exports = exports_by_entity[uri_str]
        # Sort by department order
        entity_exports.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

        entity_uri = entity_exports[0].get_entity_uri()

        # Add export tasks for this entity
        for export_node in entity_exports:
            dept = export_node.get_department_name()
            variant = export_node.get_variant_name()
            export_path = latest_export_path(entity_uri, variant, dept)
            version = _get_version_from_path(export_path)
            node_ref = export_node

            task = ProcessTask(
                id=str(uuid.uuid4()),
                uri=entity_uri,
                department=dept,
                task_type='export',
                description=f"Export ({dept})",
                current_version=version,
                execute_local=lambda n=node_ref: n.execute(force_local=True),
                execute_farm=lambda n=node_ref: n._export_farm(),
            )
            tasks.append(task)

        # Add build task for this entity (after all exports)
        build_task = _create_build_task(entity_uri)
        tasks.append(build_task)

    return tasks


def _collect_shot_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single shot across all specified departments"""
    tasks = []
    shot_uri = context.entity_uri

    # Filter function for layer_split nodes
    def _is_shot_split_correct(node):
        entity_uri = node.get_entity_uri()
        if entity_uri != shot_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find and add layer_split tasks first (shared content)
    shot_split_nodes = list(
        filter(
            _is_shot_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    for split_node in shot_split_nodes:
        dept = split_node.get_department_name()
        node_ref = split_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=shot_uri,
            department=dept,
            task_type='export_shared',
            description=f"Export Shared ({dept})",
            current_version=None,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=None,  # layer_split is local-only
        )
        tasks.append(task)

    def _is_shot_export_correct(node):
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

    # Sort by department order
    dept_order = {dept: i for i, dept in enumerate(departments)}
    shot_export_nodes.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

    for export_node in shot_export_nodes:
        dept = export_node.get_department_name()
        variant = export_node.get_variant_name()

        export_path = latest_export_path(shot_uri, variant, dept)
        version = _get_version_from_path(export_path)

        node_ref = export_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=shot_uri,
            department=dept,
            task_type='export',
            description=f"Export ({dept})",
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=lambda n=node_ref: n._export_farm(),
        )
        tasks.append(task)

    # Add build task for this shot (after all exports)
    if shot_export_nodes:
        build_task = _create_build_task(shot_uri)
        tasks.append(build_task)

    return tasks


def _collect_asset_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single asset across all specified departments"""
    tasks = []
    asset_uri = context.entity_uri

    # Filter function for layer_split nodes
    def _is_asset_split_correct(node):
        entity_uri = node.get_entity_uri()
        if entity_uri != asset_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    # Find and add layer_split tasks first (shared content)
    asset_split_nodes = list(
        filter(
            _is_asset_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type("layer_split", "Lop"),
            ),
        )
    )

    for split_node in asset_split_nodes:
        dept = split_node.get_department_name()
        node_ref = split_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department=dept,
            task_type='export_shared',
            description=f"Export Shared ({dept})",
            current_version=None,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=None,  # layer_split is local-only
        )
        tasks.append(task)

    def _is_asset_export_correct(node):
        entity_uri = node.get_entity_uri()
        if entity_uri != context.entity_uri:
            return False
        dept = node.get_department_name()
        return dept in departments

    asset_export_nodes = list(
        filter(
            _is_asset_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type("export_layer", "Lop"),
            ),
        )
    )

    # Sort by department order
    dept_order = {dept: i for i, dept in enumerate(departments)}
    asset_export_nodes.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

    for export_node in asset_export_nodes:
        asset_uri = export_node.get_entity_uri()
        dept = export_node.get_department_name()
        variant = export_node.get_variant_name()

        export_path = latest_export_path(asset_uri, variant, dept)
        version = _get_version_from_path(export_path)

        node_ref = export_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department=dept,
            task_type='export',
            description=f"Export {_uri_name(asset_uri)}",
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=lambda n=node_ref: n._export_farm(),
        )
        tasks.append(task)

    # Add build task for this asset (after all exports)
    if asset_export_nodes:
        asset_uri = context.entity_uri
        build_task = _create_build_task(asset_uri)
        tasks.append(build_task)

    return tasks


def _collect_rig_publish_tasks(context: Context) -> list[ProcessTask]:
    """Collect publish tasks for a rig"""
    tasks = []

    def _is_rig_export_correct(node):
        asset_uri = node.get_asset_uri()
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
        asset_uri = export_node.get_asset_uri()

        # Rig exports use 'default' variant
        export_path = latest_export_path(asset_uri, 'default', 'rig')
        version = _get_version_from_path(export_path)

        node_ref = export_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department='rig',
            task_type='export',
            description=f"Export rig: {_uri_name(asset_uri)}",
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=None,  # export_rig is local-only
        )
        tasks.append(task)

    return tasks


def _uri_name(uri: Uri) -> str:
    """Extract a short display name from a URI"""
    if uri is None:
        return "Unknown"
    # Use uri.parts() which returns (purpose, list[str])
    _, parts = uri.parts()
    if parts:
        return parts[-1]
    return str(uri)


def _get_build_version(shot_uri: Uri) -> str | None:
    """Get the current build version for a shot"""
    try:
        build_path = current_staged_path(shot_uri)
        return _get_version_from_path(build_path)
    except Exception:
        return None


def _create_build_task(shot_uri: Uri) -> ProcessTask:
    """Create a build task for a shot"""
    version = _get_build_version(shot_uri)

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=shot_uri,
        department='staged',
        task_type='build',
        description="Build USD",
        current_version=version,
        execute_local=lambda uri=shot_uri: _execute_build_local(uri),
        execute_farm=lambda uri=shot_uri: _execute_build_farm(uri),
    )


def _execute_build_local(entity_uri: Uri):
    """Execute build locally using build task.

    Handles both shots and assets:
    - Shots: require frame range
    - Assets: frame range is optional (animated assets have it, static don't)
    """
    from tumblehead.pipe.paths import next_staged_file_path
    from tumblehead.config.timeline import get_frame_range
    from tumblehead.farm.tasks.build import build as build_task

    # Determine entity type for validation
    entity_type = get_entity_type(entity_uri)

    # Get the output file path for the build (includes .usda filename)
    output_path = next_staged_file_path(entity_uri)

    # Try to get frame range - works for both shots AND animated assets
    frame_range = get_frame_range(entity_uri)
    render_range = frame_range.full_range() if frame_range is not None else None

    # Print diagnostic info (will be captured and shown on error)
    print('=' * 40)
    print('Build Task Debug Info')
    print('=' * 40)
    print(f'Entity URI: {entity_uri}')
    print(f'Entity type: {entity_type}')
    print(f'Output path: {output_path}')
    print(f'Frame range: {render_range}')
    print('=' * 40)

    if entity_type == 'shot':
        # Shots REQUIRE frame range
        if render_range is None:
            raise RuntimeError(f"No frame range found for shot: {entity_uri}")

    # Assets: render_range is optional (animated assets have it, static don't)

    # Execute the build
    result = build_task.main(entity_uri, output_path, render_range)
    if result != 0:
        raise RuntimeError(f"Build failed with exit code {result}")


def _execute_build_farm(entity_uri: Uri):
    """Submit build job to farm.

    Handles both shots and assets.
    """
    from tumblehead.farm.jobs.houdini.build import job as build_job

    # Prepare config for farm submission
    config = {
        'entity_uri': str(entity_uri),
        'priority': 50,  # Default priority
        'pool_name': 'houdini'  # Default pool
    }

    build_job.submit(config)


def _collect_build_tasks_for_shots(shot_uris: set[Uri]) -> list[ProcessTask]:
    """Create build tasks for a set of shot URIs"""
    tasks = []
    for shot_uri in sorted(shot_uris, key=str):
        task = _create_build_task(shot_uri)
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
        the IDs of tasks that should be enabled (the clicked node + upstream deps)
    """
    # Collect all publish tasks for full context
    all_tasks = collect_publish_tasks(context)

    # Find task IDs to enable (clicked node + upstream dependencies)
    enabled_task_ids = set()

    # Enable clicked node's task
    clicked_uri = export_node.get_entity_uri()
    clicked_dept = export_node.get_department_name()

    # Handle ExportRig which uses get_asset_uri instead of get_entity_uri
    if clicked_uri is None and hasattr(export_node, 'get_asset_uri'):
        clicked_uri = export_node.get_asset_uri()
        clicked_dept = 'rig'

    if clicked_uri is not None:
        for task in all_tasks:
            # Enable export task for clicked department
            if task.uri == clicked_uri and task.department == clicked_dept:
                enabled_task_ids.add(task.id)
            # Also enable build task for the same entity
            elif task.uri == clicked_uri and task.task_type == 'build':
                enabled_task_ids.add(task.id)

    # Enable upstream dependency tasks (and their build tasks)
    upstream_nodes = find_upstream_export_nodes(export_node)
    for upstream_node in upstream_nodes:
        upstream_uri = upstream_node.get_entity_uri()
        upstream_dept = upstream_node.get_department_name()
        for task in all_tasks:
            if task.uri == upstream_uri and task.department == upstream_dept:
                enabled_task_ids.add(task.id)
            elif task.uri == upstream_uri and task.task_type == 'build':
                enabled_task_ids.add(task.id)

    return all_tasks, enabled_task_ids


def open_process_dialog_for_node(export_node, dialog_title: str = "Export") -> None:
    """
    Open ProcessDialog for a specific export node with selective enablement.

    Shows all entities (if in a group workfile) but only enables the specific
    entity whose export button was clicked, plus any upstream dependent export nodes.

    Args:
        export_node: The export node that triggered the dialog
        dialog_title: Title for the dialog window
    """
    import hou
    from tumblehead.pipe.paths import get_workfile_context
    from ..dialogs.process_dialog import ProcessDialog

    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        hou.ui.displayMessage(
            "Cannot determine workfile context. Save the file first.",
            severity=hou.severityType.Error
        )
        return

    all_tasks, enabled_task_ids = collect_tasks_for_export_node(export_node, context)
    if not all_tasks:
        hou.ui.displayMessage(
            "No export tasks found for the current context.",
            severity=hou.severityType.Warning
        )
        return

    def save_scene():
        hou.hipFile.save()

    dialog = ProcessDialog(
        title=dialog_title,
        tasks=all_tasks,
        current_department=context.department_name,
        pre_execute_callback=save_scene,
        initial_enabled_task_ids=enabled_task_ids,
        parent=hou.qt.mainWindow()
    )
    dialog.exec_()
