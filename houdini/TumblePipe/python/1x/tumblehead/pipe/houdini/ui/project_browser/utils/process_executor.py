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
from tumblehead.config.timeline import get_frame_range
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


class ValidationSession:
    """Tracks validation state across multiple validation tasks in a single execution.

    When a user clicks "Remember choice" in the validation dialog, the choice
    is stored here and applied to subsequent validations in the same session.
    """

    # Constants matching ValidationConfirmDialog
    CONTINUE = 1
    CANCEL = 2

    def __init__(self):
        self.remembered_choice: int | None = None

    def reset(self):
        """Reset session state for new execution."""
        self.remembered_choice = None

    def has_remembered_choice(self) -> bool:
        return self.remembered_choice is not None

    def get_remembered_choice(self) -> int | None:
        return self.remembered_choice

    def set_remembered_choice(self, choice: int):
        self.remembered_choice = choice


# Module-level session instance
_validation_session = ValidationSession()


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
        self._executed_split_paths: set[str] = set()  # Track executed layer_split nodes

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

    def _check_dependencies(self, task: ProcessTask) -> tuple[bool, str | None]:
        """Check if all dependencies completed successfully.

        Returns:
            (can_run, skip_reason) - can_run=True if deps satisfied, else skip_reason explains why
        """
        if not task.depends_on:
            return True, None

        for dep_id in task.depends_on:
            dep_task = next((t for t in self._tasks if t.id == dep_id), None)
            if dep_task is None:
                continue  # Dependency not in task list, ignore
            if dep_task.status == TaskStatus.FAILED:
                return False, f"Dependency '{dep_task.description}' failed"
            if dep_task.status == TaskStatus.SKIPPED and dep_task.enabled:
                # Only block if dependency was skipped due to its own failed dependency
                # (not because user disabled it)
                return False, f"Dependency '{dep_task.description}' was skipped"
            if dep_task.status == TaskStatus.PENDING:
                return False, f"Dependency '{dep_task.description}' not yet executed"
        return True, None

    def execute(self):
        """Start executing enabled tasks"""
        self._is_running = True
        self._current_index = 0
        self._executed_split_paths.clear()  # Reset deduplication tracking
        _validation_session.reset()  # Reset validation choices for new run
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

        # Check dependencies before running
        can_run, skip_reason = self._check_dependencies(task)
        if not can_run:
            task.status = TaskStatus.SKIPPED
            task.error_message = skip_reason
            self._current_index += 1
            QTimer.singleShot(0, self._execute_next_task)
            return

        task.status = TaskStatus.RUNNING
        self.task_started.emit(task.id)

        # Variables to store captured output (for error reporting)
        captured_stdout = ''
        captured_stderr = ''
        stdout_capture = None
        stderr_capture = None

        try:
            if task.children:
                # Parent task with children - execute each enabled child
                self._execute_grouped_task(task)
            else:
                # Regular task - execute directly
                self._execute_single_task(task)

            task.status = TaskStatus.COMPLETED
            self.task_completed.emit(task.id)

        except Exception as e:
            # Check if this is a user-initiated cancellation (no error dialog needed)
            from ..dialogs.validation_dialog import ValidationCancelled
            if isinstance(e, ValidationCancelled):
                task.status = TaskStatus.SKIPPED
                # Don't emit task_failed - this was intentional
            else:
                task.status = TaskStatus.FAILED
                # Capture full traceback for debugging
                error_msg = traceback.format_exc()
                task.error_message = error_msg
                self.task_failed.emit(task.id, task.error_message)

        self._current_index += 1
        # Continue with next task after a short delay to allow UI update
        QTimer.singleShot(100, self._execute_next_task)

    def _execute_single_task(self, task: ProcessTask):
        """Execute a single task (no children)"""
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

    def _execute_grouped_task(self, parent_task: ProcessTask):
        """Execute a grouped task by running its enabled children.

        Handles deduplication for layer_split nodes to avoid executing
        the same node multiple times when shared across variants.
        """
        if not parent_task.children:
            return

        for child in parent_task.children:
            if not child.enabled:
                child.status = TaskStatus.SKIPPED
                continue

            # Deduplication for layer_split nodes
            if child.task_type == 'export_shared' and child.node_path:
                if child.node_path in self._executed_split_paths:
                    # Already executed this layer_split, skip
                    child.status = TaskStatus.SKIPPED
                    continue
                self._executed_split_paths.add(child.node_path)

            child.status = TaskStatus.RUNNING
            self.task_started.emit(child.id)

            try:
                if self._mode == 'local':
                    if child.execute_local is not None:
                        with _capture_output() as (stdout_capture, stderr_capture):
                            child.execute_local()
                    else:
                        raise RuntimeError(f"No local executor defined for child task: {child.description}")
                else:  # farm
                    if child.execute_farm is not None:
                        child.execute_farm()
                    else:
                        raise RuntimeError(f"No farm executor defined for child task: {child.description}")

                child.status = TaskStatus.COMPLETED
                self.task_completed.emit(child.id)

            except Exception as e:
                # Check if this is a user-initiated cancellation
                from ..dialogs.validation_dialog import ValidationCancelled
                if isinstance(e, ValidationCancelled):
                    child.status = TaskStatus.SKIPPED
                    raise  # Re-raise to skip parent task too
                else:
                    child.status = TaskStatus.FAILED
                    child.error_message = traceback.format_exc()
                    self.task_failed.emit(child.id, child.error_message)
                    # Re-raise to fail the parent task
                    raise


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


def _get_frame_range_values(entity_uri: Uri, export_node=None) -> tuple[int | None, int | None]:
    """Get first and last frame for display in process dialog.

    Args:
        entity_uri: The entity URI to get frame range for
        export_node: Optional export node to get frame range from (preferred)

    Returns:
        Tuple of (first_frame, last_frame) or (None, None) if unavailable
    """
    # Try to get from export node first (has node-specific settings)
    if export_node is not None and hasattr(export_node, 'get_frame_range'):
        result = export_node.get_frame_range()
        if result is not None:
            frame_range, _ = result
            render_range = frame_range.full_range()
            return (render_range.first_frame, render_range.last_frame)

    # Fall back to entity config
    frame_range = get_frame_range(entity_uri)
    if frame_range is not None:
        render_range = frame_range.full_range()
        return (render_range.first_frame, render_range.last_frame)

    return (None, None)


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


def _uri_name(uri: Uri) -> str:
    """Extract a short display name from a URI"""
    if uri is None:
        return "Unknown"
    # Use uri.parts() which returns (purpose, list[str])
    _, parts = uri.parts()
    if parts:
        return parts[-1]
    return str(uri)


# Track layer_split nodes executed in current session to avoid duplicates
_executed_split_paths: set[str] = set()


def reset_executed_splits():
    """Reset tracking for new execution session."""
    global _executed_split_paths
    _executed_split_paths = set()


def _create_validation_task(
    entity_uri: Uri,
    department: str,
    export_nodes: list,
    variant: str = 'default'
) -> ProcessTask:
    """Create a validation task for export nodes.

    Runs stage validation on all export nodes before they are executed.
    The validation checks for issues like RenderVar name mismatches.

    When validation fails, shows an interactive dialog allowing
    the user to continue or cancel the export.

    Args:
        entity_uri: The entity URI
        department: Department name
        export_nodes: List of export nodes to validate
        variant: Variant name

    Returns:
        ProcessTask for validation
    """
    def validate_local_fn():
        from tumblehead.pipe.houdini.validators import validate_stage
        from tumblehead.pipe.houdini.validators.base import ValidationResult
        from ..dialogs.validation_dialog import ValidationConfirmDialog, ValidationCancelled
        import hou

        # Collect all validation results
        combined_result = ValidationResult()

        for export_node in export_nodes:
            native = export_node.native()
            stage_node = native.node('IN_stage')
            if stage_node is None:
                continue
            stage = stage_node.stage()
            if stage is None:
                continue
            root = stage.GetPseudoRoot()
            result = validate_stage(root)
            combined_result.merge(result)

        # No validation failures - continue normally
        if combined_result.passed:
            return

        combined_message = combined_result.format_message()

        # Check if user already made a remembered choice
        if _validation_session.has_remembered_choice():
            if _validation_session.get_remembered_choice() == ValidationSession.CONTINUE:
                return  # Continue without prompting
            else:
                raise ValidationCancelled("Export cancelled by user")

        # Show dialog and get user choice
        entity_name = _uri_name(entity_uri)
        dialog = ValidationConfirmDialog(
            validation_message=combined_message,
            department=department,
            entity_name=entity_name,
            parent=hou.qt.mainWindow()
        )
        dialog.exec_()

        # Handle user choice
        if dialog.remember_choice:
            _validation_session.set_remembered_choice(dialog.user_choice)

        if dialog.user_choice == ValidationConfirmDialog.CONTINUE:
            return  # Task passes, exports will run
        else:
            raise ValidationCancelled("Export cancelled by user")

    def validate_farm_fn():
        # Farm execution: no UI available, use original blocking behavior
        from tumblehead.pipe.houdini.validators import validate_stage
        for export_node in export_nodes:
            native = export_node.native()
            stage_node = native.node('IN_stage')
            if stage_node is None:
                continue
            stage = stage_node.stage()
            if stage is None:
                continue
            root = stage.GetPseudoRoot()
            result = validate_stage(root)
            if not result.passed:
                raise RuntimeError(f"Validation failed:\n{result.format_message()}")

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=entity_uri,
        department=department,
        task_type='validate',
        description=f"Validate ({department})",
        execute_local=validate_local_fn,
        execute_farm=validate_farm_fn,
        variant=variant,
        status=TaskStatus.PENDING
    )


def _create_export_group_task(
    entity_uri: Uri,
    department: str,
    split_nodes: list,
    export_nodes: list,
    first_frame: int | None,
    last_frame: int | None,
    depends_on: list[str] | None = None
) -> ProcessTask:
    """
    Create a parent export task that groups layer_split and export_layer nodes.

    Args:
        entity_uri: The entity URI
        department: The department name
        split_nodes: List of layer_split nodes for shared content
        export_nodes: List of export_layer nodes
        first_frame: First frame (with roll)
        last_frame: Last frame (with roll)
        depends_on: Optional list of task IDs this task depends on

    Returns:
        A ProcessTask with children for each node
    """
    parent_id = str(uuid.uuid4())
    children = []

    # Add layer_split children (shared content)
    for split_node in split_nodes:
        node_ref = split_node
        child = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department=department,
            task_type='export_shared',
            description="Export Shared",
            node_path=split_node.path(),
            parent_id=parent_id,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=lambda n=node_ref: n.execute(force_local=True),  # Farm script handles this
            first_frame=first_frame,
            last_frame=last_frame,
        )
        children.append(child)

    # Add export_layer children
    for export_node in export_nodes:
        variant = export_node.get_variant_name()
        export_path = latest_export_path(entity_uri, variant, department)
        version = _get_version_from_path(export_path)
        node_ref = export_node

        child = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department=department,
            task_type='export',
            variant=variant,
            description=f"Export ({variant})" if variant else "Export",
            node_path=export_node.path(),
            parent_id=parent_id,
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True),
            execute_farm=lambda n=node_ref: n._export_farm(),
            first_frame=first_frame,
            last_frame=last_frame,
        )
        children.append(child)

    # Create parent task
    parent = ProcessTask(
        id=parent_id,
        uri=entity_uri,
        department=department,
        task_type='export_group',
        description=f"Export ({department})",
        children=children,
        first_frame=first_frame,
        last_frame=last_frame,
        depends_on=depends_on or [],
    )
    return parent


def _get_build_version(entity_uri: Uri, variant_name: str = 'default') -> str | None:
    """Get the current build version for an entity"""
    try:
        build_path = current_staged_path(entity_uri, variant_name)
        return _get_version_from_path(build_path)
    except Exception:
        return None


def _create_build_task(
    entity_uri: Uri,
    variant_name: str = 'default',
    depends_on: list[str] | None = None
) -> ProcessTask:
    """Create a build task for an entity (shot or asset).

    Args:
        entity_uri: The entity URI
        variant_name: Variant name (default: 'default')
        depends_on: Optional list of task IDs this task depends on (typically export tasks)

    Returns:
        ProcessTask for building the USD
    """
    version = _get_build_version(entity_uri, variant_name)
    first_frame, last_frame = _get_frame_range_values(entity_uri)

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=entity_uri,
        department='staged',
        task_type='build',
        variant=variant_name,
        description=f"Build USD ({variant_name})",
        current_version=version,
        execute_local=lambda uri=entity_uri, v=variant_name: _execute_build_local(uri, v),
        execute_farm=lambda uri=entity_uri, v=variant_name: _execute_build_farm(uri, v),
        first_frame=first_frame,
        last_frame=last_frame,
        depends_on=depends_on or [],
    )


def _create_build_group_task(
    entity_uri: Uri,
    variants: list[str],
    depends_on: list[str] | None = None,
    first_frame: int | None = None,
    last_frame: int | None = None
) -> ProcessTask:
    """
    Create a parent build task that groups variant build tasks.

    Args:
        entity_uri: The entity URI
        variants: List of variant names to build
        depends_on: Optional list of task IDs this task depends on
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)

    Returns:
        A ProcessTask with children for each variant
    """
    parent_id = str(uuid.uuid4())
    children = []

    # Use override if provided, otherwise get from entity config
    if first_frame is None or last_frame is None:
        first_frame, last_frame = _get_frame_range_values(entity_uri)

    # Add child task for each variant
    for variant_name in variants:
        version = _get_build_version(entity_uri, variant_name)
        child = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department='staged',
            task_type='build',
            variant=variant_name,
            description=f"Build USD ({variant_name})",
            parent_id=parent_id,
            current_version=version,
            execute_local=lambda uri=entity_uri, v=variant_name, ff=first_frame, lf=last_frame: _execute_build_local(uri, v, ff, lf),
            execute_farm=lambda uri=entity_uri, v=variant_name, ff=first_frame, lf=last_frame: _execute_build_farm(uri, v, ff, lf),
            first_frame=first_frame,
            last_frame=last_frame,
        )
        children.append(child)

    # Create parent task
    parent = ProcessTask(
        id=parent_id,
        uri=entity_uri,
        department='staged',
        task_type='build_group',
        description="Build USD",
        children=children,
        first_frame=first_frame,
        last_frame=last_frame,
        depends_on=depends_on or [],
    )
    return parent


def _execute_build_local(entity_uri: Uri, variant_name: str = 'default', first_frame: int | None = None, last_frame: int | None = None):
    """Execute build locally using build task.

    Handles both shots and assets:
    - Shots: require frame range
    - Assets: frame range is optional (animated assets have it, static don't)

    Args:
        entity_uri: The entity URI
        variant_name: The variant to build
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)
    """
    from tumblehead.pipe.paths import next_staged_file_path
    from tumblehead.config.timeline import get_frame_range, BlockRange
    from tumblehead.farm.tasks.build import build as build_task

    # Determine entity type for validation
    entity_type = get_entity_type(entity_uri)

    # Get the output file path for the build (includes .usda filename)
    output_path = next_staged_file_path(entity_uri, variant_name)

    # Use provided frame range if available, otherwise get from entity config
    if first_frame is not None and last_frame is not None:
        render_range = BlockRange(first_frame, last_frame)
    else:
        frame_range = get_frame_range(entity_uri)
        render_range = frame_range.full_range() if frame_range is not None else None

    # Print diagnostic info (will be captured and shown on error)
    print('=' * 40)
    print('Build Task Debug Info')
    print('=' * 40)
    print(f'Entity URI: {entity_uri}')
    print(f'Variant: {variant_name}')
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
    result = build_task.main(entity_uri, output_path, render_range, variant_name)
    if result != 0:
        raise RuntimeError(f"Build failed with exit code {result}")


def _execute_build_farm(entity_uri: Uri, variant_name: str = 'default', first_frame: int | None = None, last_frame: int | None = None):
    """Submit build job to farm.

    Handles both shots and assets.

    Args:
        entity_uri: The entity URI
        variant_name: The variant to build
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)
    """
    from tumblehead.farm.jobs.houdini.build import job as build_job

    # Prepare config for farm submission
    config = {
        'entity_uri': str(entity_uri),
        'variant_name': variant_name,
        'priority': 50,  # Default priority
        'pool_name': 'houdini'  # Default pool
    }

    # Pass custom frame range if provided
    if first_frame is not None and last_frame is not None:
        config['first_frame'] = first_frame
        config['last_frame'] = last_frame

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
