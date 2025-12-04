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
from tumblehead.pipe.paths import latest_export_path, latest_staged_path, Context
from tumblehead.pipe.houdini.lops import (
    export_asset_layer,
    export_shot_layer,
    export_render_layer,
)
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

        try:
            if self._mode == 'local':
                if task.execute_local is not None:
                    # Capture stdout/stderr during local execution to keep console clean
                    with _capture_output() as (stdout_capture, stderr_capture):
                        task.execute_local()
                    # Store captured output in case we need it for error reporting
                    captured_stdout = stdout_capture.getvalue()
                    captured_stderr = stderr_capture.getvalue()
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

    def _is_group_export_correct(node):
        dept = node.get_department_name()
        if dept not in departments:
            return False
        shot_uri = node.get_shot_uri()
        return shot_uri in member_uris

    # Find all export nodes for group members across all departments
    group_export_nodes = list(
        filter(
            _is_group_export_correct,
            map(
                export_shot_layer.ExportShotLayer,
                ns.list_by_node_type("export_shot_layer", "Lop"),
            ),
        )
    )

    # Group export nodes by shot URI to organize tasks properly
    exports_by_shot: dict[str, list] = {}
    for export_node in group_export_nodes:
        shot_uri = export_node.get_shot_uri()
        uri_str = str(shot_uri)
        if uri_str not in exports_by_shot:
            exports_by_shot[uri_str] = []
        exports_by_shot[uri_str].append(export_node)

    # Sort departments for consistent ordering
    dept_order = {dept: i for i, dept in enumerate(departments)}

    # For each shot: add export tasks, then build task
    for uri_str in sorted(exports_by_shot.keys()):
        shot_exports = exports_by_shot[uri_str]
        # Sort by department order
        shot_exports.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

        shot_uri = shot_exports[0].get_shot_uri()

        # Add export tasks for this shot
        for export_node in shot_exports:
            dept = export_node.get_department_name()
            export_path = latest_export_path(shot_uri, dept)
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
        build_task = _create_build_task(shot_uri)
        tasks.append(build_task)

    return tasks


def _collect_shot_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single shot across all specified departments"""
    tasks = []
    shot_uri = context.entity_uri

    def _is_shot_export_correct(node):
        dept = node.get_department_name()
        if dept not in departments:
            return False
        node_shot_uri = node.get_shot_uri()
        return node_shot_uri == shot_uri

    # Find export nodes for the shot across all departments
    shot_export_nodes = list(
        filter(
            _is_shot_export_correct,
            map(
                export_shot_layer.ExportShotLayer,
                ns.list_by_node_type("export_shot_layer", "Lop"),
            ),
        )
    )

    # Sort by department order
    dept_order = {dept: i for i, dept in enumerate(departments)}
    shot_export_nodes.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

    for export_node in shot_export_nodes:
        dept = export_node.get_department_name()

        export_path = latest_export_path(shot_uri, dept)
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

    # Also collect render layer export nodes
    def _is_render_layer_export_correct(node):
        dept = node.get_department_name()
        if dept not in departments:
            return False
        node_shot_uri = node.get_shot_uri()
        return node_shot_uri == shot_uri

    render_export_nodes = list(
        filter(
            _is_render_layer_export_correct,
            map(
                export_render_layer.ExportRenderLayer,
                ns.list_by_node_type("export_render_layer", "Lop"),
            ),
        )
    )

    for export_node in render_export_nodes:
        dept = export_node.get_department_name()
        layer_name = getattr(export_node, 'get_render_layer_name', lambda: 'render')()

        node_ref = export_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=shot_uri,
            department=dept,
            task_type='export',
            description=f"Export render layer: {layer_name}",
            current_version=None,
            execute_local=lambda n=node_ref: n.execute(force_local=True) if hasattr(n, 'execute') else None,
            execute_farm=lambda n=node_ref: n._export_farm() if hasattr(n, '_export_farm') else None,
        )
        tasks.append(task)

    # Add build task for this shot (after all exports)
    if shot_export_nodes or render_export_nodes:
        build_task = _create_build_task(shot_uri)
        tasks.append(build_task)

    return tasks


def _collect_asset_publish_tasks(context: Context, departments: list[str]) -> list[ProcessTask]:
    """Collect publish tasks for a single asset across all specified departments"""
    tasks = []

    def _is_asset_export_correct(node):
        dept = node.get_department_name()
        if dept not in departments:
            return False
        asset_uri = node.get_asset_uri()
        return asset_uri == context.entity_uri

    asset_export_nodes = list(
        filter(
            _is_asset_export_correct,
            map(
                export_asset_layer.ExportAssetLayer,
                ns.list_by_node_type("export_asset_layer", "Lop"),
            ),
        )
    )

    # Sort by department order
    dept_order = {dept: i for i, dept in enumerate(departments)}
    asset_export_nodes.sort(key=lambda n: dept_order.get(n.get_department_name(), 999))

    for export_node in asset_export_nodes:
        asset_uri = export_node.get_asset_uri()
        dept = export_node.get_department_name()

        export_path = latest_export_path(asset_uri, dept)
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

        export_path = latest_export_path(asset_uri, 'rig')
        version = _get_version_from_path(export_path)

        node_ref = export_node

        task = ProcessTask(
            id=str(uuid.uuid4()),
            uri=asset_uri,
            department='rig',
            task_type='export',
            description=f"Export rig: {_uri_name(asset_uri)}",
            current_version=version,
            execute_local=lambda n=node_ref: n.execute(force_local=True) if hasattr(n, 'execute') else n.execute(),
            execute_farm=lambda n=node_ref: n._export_farm() if hasattr(n, '_export_farm') else None,
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
        build_path = latest_staged_path(shot_uri)
        return _get_version_from_path(build_path)
    except Exception:
        return None


def _create_build_task(shot_uri: Uri) -> ProcessTask:
    """Create a build task for a shot"""
    version = _get_build_version(shot_uri)

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=shot_uri,
        department='build',
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

    # Get the output file path for the build (includes .usda filename)
    output_path = next_staged_file_path(entity_uri)

    # Try to get frame range - works for both shots AND animated assets
    frame_range = get_frame_range(entity_uri)
    render_range = frame_range.full_range() if frame_range is not None else None

    # Determine entity type for validation
    entity_type = get_entity_type(entity_uri)

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
