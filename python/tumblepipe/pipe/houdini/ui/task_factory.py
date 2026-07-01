"""Task construction: build runnable ProcessTasks for validation / export / build.

Factories that turn an entity + department into ProcessTasks (each carrying its
local and farm execution closures), plus the small helpers shared with
``task_collection``. Depends on the runner only for the shared
ValidationSession state.
"""

import uuid

from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import get_frame_range
from tumblepipe.pipe.paths import latest_export_path, current_staged_path

from .process_task import ProcessTask, TaskStatus
from .process_executor import ValidationSession, _validation_session
from .helpers import get_entity_type


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


def _entity_context_from_uri(entity_uri: Uri | None) -> str:
    """Map an entity URI to the 'shots' or 'assets' validator context."""
    if entity_uri is None:
        return 'assets'
    uri_str = str(entity_uri)
    if uri_str.startswith('entity:/shots/'):
        return 'shots'
    return 'assets'


def _uri_name(uri: Uri) -> str:
    """Extract a short display name from a URI"""
    if uri is None:
        return "Unknown"
    # Use uri.parts() which returns (purpose, list[str])
    _, parts = uri.parts()
    if parts:
        return parts[-1]
    return str(uri)


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
    entity_context = _entity_context_from_uri(entity_uri)
    validation_ctx = {'entity_uri': str(entity_uri)} if entity_uri is not None else {}

    def validate_local_fn():
        from tumblepipe.pipe.houdini.validators import validate_stage_for_department
        from tumblepipe.pipe.houdini.validators.base import ValidationResult
        from .validation_dialog import ValidationConfirmDialog, ValidationCancelled
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
            result = validate_stage_for_department(
                root, entity_context, department, validation_ctx
            )
            combined_result.merge(result)

        # No validation failures - continue normally
        if combined_result.passed:
            return

        # Check if user already made a remembered choice
        if _validation_session.has_remembered_choice():
            if _validation_session.get_remembered_choice() == ValidationSession.CONTINUE:
                return  # Continue without prompting
            else:
                raise ValidationCancelled("Export cancelled by user")

        # Show dialog and get user choice
        entity_name = _uri_name(entity_uri)
        dialog = ValidationConfirmDialog(
            validation_result=combined_result,
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
        from tumblepipe.pipe.houdini.validators import validate_stage_for_department
        for export_node in export_nodes:
            native = export_node.native()
            stage_node = native.node('IN_stage')
            if stage_node is None:
                continue
            stage = stage_node.stage()
            if stage is None:
                continue
            root = stage.GetPseudoRoot()
            result = validate_stage_for_department(
                root, entity_context, department, validation_ctx
            )
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
    from tumblepipe.pipe.paths import next_staged_file_path
    from tumblepipe.config.timeline import get_frame_range, BlockRange
    from tumblepipe.farm.tasks.build import build as build_task

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
    from tumblepipe.farm.jobs.houdini.build import job as build_job

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

