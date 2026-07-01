"""Node → ProcessDialog entry point.

Called (deferred) from an export / split / rig node's ``execute()`` to open the
ProcessDialog for the node's workfile context. Kept in its own tiny module so
the node modules can reach it without importing the Qt dialog / task-collection
subsystem at load time (they cook headlessly on the farm), and so
``process_executor`` no longer imports ``process_dialog`` — the two used to cycle.
"""

from pathlib import Path

from .task_collection import collect_tasks_for_export_node


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
    from tumblepipe.pipe.paths import get_workfile_context
    from .process_dialog import ProcessDialog

    # Config reads are coherent: an entity's frame range / departments /
    # downstream deps written to disk after this session started (shot import,
    # a producer or another machine/session editing config) are picked up on
    # the next read. No manual refresh is needed before collecting them here.
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

