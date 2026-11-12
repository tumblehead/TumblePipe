"""Task construction: build runnable ProcessTasks for validation / export / build.

Factories that turn an entity + department into ProcessTasks (each carrying its
local and farm execution closures), plus the small helpers shared with
``task_collection``. Depends on the runner only for the shared
ValidationSession state.
"""

import logging
import uuid

from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import get_export_frame_range
from tumblepipe.pipe.paths import latest_export_path, current_staged_path

from .process_task import ProcessTask, TaskStatus
from . import task_range
from .process_executor import ValidationSession, _validation_session
from . import validation_preflight as preflight
from .helpers import get_entity_type

logger = logging.getLogger(__name__)


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
    """First and last frame for the process dialog, rolls included.

    An export node's own Frame Range setting decides its range (and raises
    when it does not resolve). Without a node, the entity's authored range;
    (None, None) for an entity with none. A static asset gets
    its first frame only (``get_export_frame_range``).
    """
    if export_node is not None:
        frame_range = export_node.get_frame_range()
    else:
        frame_range = get_export_frame_range(entity_uri)
        if frame_range is None:
            return (None, None)
    render_range = frame_range.full_range()
    return (render_range.first_frame, render_range.last_frame)


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


def _unseen_issues(issues: list, entity_uri: Uri, department: str) -> list:
    """The ``issues`` the up-front dialog did not already accept (#56)."""
    return preflight.unseen_issues(
        issues, _validation_session.accepted, str(entity_uri), department,
    )


def _validate_in_place(
    issues: list, entity_uri: Uri, department: str, ask_on_warnings: bool,
):
    """A validation run in place, during the run: ask about the issues the
    up-front dialog did not show (ones this run's upstream exports brought
    in), if they are worth a prompt (``preflight.asks``)."""
    unseen = _unseen_issues(issues, entity_uri, department)
    if preflight.asks(unseen, ask_on_warnings):
        _ask_to_continue(unseen, entity_uri, department)


def _ask_to_continue(issues: list, entity_uri: Uri, department: str):
    """Show ``issues`` in the validation prompt; raise ValidationCancelled
    unless the artist continues (or remembered Continue this run)."""
    from tumblepipe.pipe.houdini.validators.base import ValidationResult
    from .validation_dialog import ValidationConfirmDialog, ValidationCancelled
    import hou

    # Check if user already made a remembered choice
    if _validation_session.has_remembered_choice():
        if _validation_session.get_remembered_choice() == ValidationSession.CONTINUE:
            return  # Continue without prompting
        raise ValidationCancelled("Export cancelled by user")

    dialog = ValidationConfirmDialog(
        validation_result=ValidationResult(
            passed=not preflight.has_error(issues), issues=list(issues),
        ),
        department=department,
        entity_name=_uri_name(entity_uri),
        parent=hou.qt.mainWindow()
    )
    dialog.exec_()

    if dialog.remember_choice:
        _validation_session.set_remembered_choice(dialog.user_choice)

    if dialog.user_choice != ValidationConfirmDialog.CONTINUE:
        raise ValidationCancelled("Export cancelled by user")


def _create_validation_task(
    entity_uri: Uri,
    department: str,
    export_nodes: list,
    channel: str | None = None
) -> ProcessTask:
    """Create a validation task for export nodes.

    Runs stage validation on all export nodes before they are executed.
    The validation checks for issues like RenderVar name mismatches.

    The process dialog runs it once up front, before anything exports
    (``collect_issues``, #56), and asks about every shot's issues in one
    dialog. It runs again in place, where a downstream department may see
    what an upstream export of the run wrote: an issue the artist accepted
    up front passes, and only a new error shows the prompt, allowing the
    user to continue or cancel the export.

    Args:
        entity_uri: The entity URI
        department: Department name
        export_nodes: List of export nodes to validate
        channel: Channel name, when the task validates a single channel

    Returns:
        ProcessTask for validation. Without ``channel`` it records the channels
        of ``export_nodes`` in ``channels``: one validation covers every
        channel's export of the department.
    """
    entity_context = _entity_context_from_uri(entity_uri)
    validation_ctx = {'entity_uri': str(entity_uri)} if entity_uri is not None else {}

    def collect_issues_fn() -> list:
        """The issues the prompt would show: every issue of a failed
        validation, none when it passed."""
        from tumblepipe.pipe.houdini.validators import validate_stage_for_department
        from tumblepipe.pipe.houdini.validators.base import ValidationResult

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

        # No validation failures - continue normally.
        #
        # Warnings deliberately do not block an export, but they must not
        # vanish either: this is the farm/process-dialog export path, so there
        # is no dialog to carry them and they were previously dropped on the
        # floor. Log them so they land in the session log the artist and the
        # farm worker both keep.
        if combined_result.passed:
            if combined_result.warnings:
                logger.warning(
                    "Export validation passed with %d warning(s) for %s/%s:\n%s",
                    len(combined_result.warnings),
                    entity_context, department,
                    combined_result.format_message(),
                )
            return []
        return list(combined_result.issues)

    def validate_local_fn():
        # Only an error the run brought in asks; a warning alongside it
        # shows in the same prompt, as before.
        _validate_in_place(
            collect_issues_fn(), entity_uri, department, ask_on_warnings=False)

    def validate_farm_fn():
        # Submitting to a farm: nothing exported here changed the stage, so
        # an issue is new only if the up-front dialog did not show it.
        from tumblepipe.pipe.houdini.validators.base import ValidationResult
        issues = _unseen_issues(collect_issues_fn(), entity_uri, department)
        if preflight.has_error(issues):
            result = ValidationResult(passed=False, issues=issues)
            raise RuntimeError(f"Validation failed:\n{result.format_message()}")

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=entity_uri,
        department=department,
        task_type='validate',
        description=f"Validate ({department})",
        execute_local=validate_local_fn,
        execute_farm=validate_farm_fn,
        collect_issues=collect_issues_fn,
        channel=channel,
        channels=None if channel else [
            node.get_channel_name() for node in export_nodes
            if hasattr(node, 'get_channel_name')
        ],
        status=TaskStatus.PENDING,
        # Default OFF for publishes until validator conventions and the
        # warning/error severity balance are settled — the current output
        # causes more confusion than it prevents. The task still appears as
        # an (unchecked) checkbox so artists can opt in per-publish, and the
        # executor treats a user-disabled validation dependency as non-blocking
        # (see ProcessExecutor._check_dependencies).
        enabled=False,
    )


def _create_rig_validation_task(asset_uri: Uri, export_node, channel: str) -> ProcessTask:
    """Validate the character an export_rig node is about to write.

    A rig publishes SOP geometry, not a stage, so it has its own checks
    (``validators.rig``): the Main control's viz is off (error), the shapes
    carry a path under the asset (error) and big shapes carry normals
    (warning). The character is the export_rig node's input.

    Unlike the stage validation this one is on by default and shows its
    dialog for warnings too: both checks are about what every shot that loads
    the rig gets, and a logged warning nobody reads changes nothing. The
    artist can still Continue past it. export_rig is local-only, so there is
    no farm path. Like the stage validation it runs up front too (#56), and
    an issue accepted there does not ask again.
    """
    def collect_issues_fn() -> list:
        from tumblepipe.pipe.houdini.validators import validate_rig_character
        from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path

        source = export_node.native().input(0)
        geo = source.geometry() if source is not None else None
        joint_nodes = [] if source is None else [
            node.path() for node in [source, *source.inputAncestors()]
            if 'apex_joints' in node.type().name()
        ]
        result = validate_rig_character(
            geo,
            asset_prim_path=uri_to_prim_path(asset_uri),
            joint_nodes=joint_nodes,
        )
        if result.issues:
            logger.warning(
                "Rig validation for %s (%s):\n%s",
                _uri_name(asset_uri), channel, result.format_message(),
            )
        return list(result.issues)

    def validate_local_fn():
        _validate_in_place(
            collect_issues_fn(), asset_uri, 'rig', ask_on_warnings=True)

    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=asset_uri,
        department='rig',
        task_type='validate',
        description=f"Validate rig: {_uri_name(asset_uri)}",
        execute_local=validate_local_fn,
        execute_farm=None,  # export_rig is local-only
        collect_issues=collect_issues_fn,
        channel=channel,
        status=TaskStatus.PENDING,
    )


def _run_range(task: ProcessTask):
    """The BlockRange an export node runs over for ``task``: the range the
    artist set for this submit (#76), or None, and the node reads its own
    Frame Range parms as always. The node's parms are never changed."""
    return task_range.block_range(task_range.run_range(task))


def _wire_export(task: ProcessTask, node) -> None:
    """An export_layer row's callbacks: in this session, and a publish job
    (This machine or the Render farm). Each reads the row's range when it
    runs, so an edit made after the task was built still counts."""
    task.execute_local = lambda: node.execute(
        force_local=True, render_range=_run_range(task))
    task.execute_farm = lambda: node._export_farm(
        render_range=_run_range(task))
    task.execute_farm_with_builds = lambda build: node._export_farm(
        build=build, render_range=_run_range(task))


def _wire_shared_export(task: ProcessTask, node) -> None:
    """A layer_split row's callbacks: it exports here in every mode."""
    task.execute_local = lambda: node.execute(
        force_local=True, render_range=_run_range(task))
    task.execute_farm = task.execute_local


def _wire_build(task: ProcessTask) -> None:
    """A Build USD row's callbacks, over the row's range this submit."""
    def span():
        return task_range.effective_range(task) or (None, None)
    task.execute_local = lambda: _execute_build_local(
        task.uri, task.channel, *span())
    task.execute_farm = lambda: _execute_build_farm(
        task.uri, task.channel, *span())


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
            first_frame=first_frame,
            last_frame=last_frame,
        )
        _wire_shared_export(child, node_ref)
        children.append(child)

    # Add export_layer children
    for export_node in export_nodes:
        channel = export_node.get_channel_name()
        export_path = latest_export_path(entity_uri, channel, department)
        version = _get_version_from_path(export_path)
        node_ref = export_node

        child = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department=department,
            task_type='export',
            channel=channel,
            description=f"Export ({channel})" if channel else "Export",
            node_path=export_node.path(),
            parent_id=parent_id,
            current_version=version,
            first_frame=first_frame,
            last_frame=last_frame,
        )
        _wire_export(child, node_ref)
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


def _get_build_version(entity_uri: Uri, channel_name: str = 'default') -> str | None:
    """The current build version for an entity, or None before its first."""
    return _get_version_from_path(current_staged_path(entity_uri, channel_name))


def _create_build_group_task(
    entity_uri: Uri,
    channels: list[str],
    depends_on: list[str] | None = None,
    first_frame: int | None = None,
    last_frame: int | None = None
) -> ProcessTask:
    """
    Create a parent build task that groups channel build tasks.

    Args:
        entity_uri: The entity URI
        channels: List of channel names to build
        depends_on: Optional list of task IDs this task depends on
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)

    Returns:
        A ProcessTask with children for each channel
    """
    parent_id = str(uuid.uuid4())
    children = []

    # Use override if provided, otherwise get from entity config
    if first_frame is None or last_frame is None:
        first_frame, last_frame = _get_frame_range_values(entity_uri)

    # Add child task for each channel
    for channel_name in channels:
        version = _get_build_version(entity_uri, channel_name)
        child = ProcessTask(
            id=str(uuid.uuid4()),
            uri=entity_uri,
            department='staged',
            task_type='build',
            channel=channel_name,
            description=f"Build USD ({channel_name})",
            parent_id=parent_id,
            current_version=version,
            first_frame=first_frame,
            last_frame=last_frame,
        )
        _wire_build(child)
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


def _execute_build_local(entity_uri: Uri, channel_name: str = 'default', first_frame: int | None = None, last_frame: int | None = None):
    """Execute build locally using build task.

    Handles both shots and assets:
    - Shots: require frame range
    - Assets: frame range is optional (animated assets have it, static don't)

    Args:
        entity_uri: The entity URI
        channel_name: The channel to build
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)
    """
    from tumblepipe.pipe.paths import next_staged_file_path
    from tumblepipe.config.timeline import get_export_frame_range, BlockRange
    from tumblepipe.pipe.staged_build import build_staged

    # Determine entity type for validation
    entity_type = get_entity_type(entity_uri)

    # Get the output file path for the build (includes .usda filename)
    output_path = next_staged_file_path(entity_uri, channel_name)

    # Use provided frame range if available, otherwise get from entity config
    if first_frame is not None and last_frame is not None:
        render_range = BlockRange(first_frame, last_frame)
    else:
        frame_range = get_export_frame_range(entity_uri)
        render_range = frame_range.full_range() if frame_range is not None else None

    # Print diagnostic info (will be captured and shown on error)
    print('=' * 40)
    print('Build Task Debug Info')
    print('=' * 40)
    print(f'Entity URI: {entity_uri}')
    print(f'Channel: {channel_name}')
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
    build_staged(entity_uri, output_path, render_range, channel_name)

    return _get_version_from_path(output_path)


def _execute_build_farm(entity_uri: Uri, channel_name: str = 'default', first_frame: int | None = None, last_frame: int | None = None):
    """Submit build job to farm.

    Handles both shots and assets.

    Args:
        entity_uri: The entity URI
        channel_name: The channel to build
        first_frame: Optional first frame override (from export node)
        last_frame: Optional last frame override (from export node)
    """
    from tumblepipe.config.farm import get_default_pool, get_default_priority
    from tumblepipe.farm.pipelines import build as build_job

    # The build task has no node of its own to carry a pool or priority, so
    # it takes the project's defaults, as a node left on them would.
    pool_name = get_default_pool()
    if pool_name is None:
        raise RuntimeError(
            'The build job takes the project\'s default pool, but the '
            'project sets none. Set farm.default_pool in the project config.'
        )
    config = {
        'entity_uri': str(entity_uri),
        'channel_name': channel_name,
        'priority': get_default_priority(),
        'pool_name': pool_name,
    }

    # Pass custom frame range if provided
    if first_frame is not None and last_frame is not None:
        config['first_frame'] = first_frame
        config['last_frame'] = last_frame

    build_job.submit(config)

