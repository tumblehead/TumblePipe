"""Node → ProcessDialog entry point.

Called from an export / split / rig node's ``execute()`` to open the
ProcessDialog for the node's workfile context. The call is *direct*, on the
button-callback thread — an exception here surfaces in Houdini's own callback
error dialog rather than vanishing. (The docstring used to say "deferred";
nothing has deferred it since the node modules stopped importing the dialog at
load time, and "it must be failing silently somewhere" is a bad thing to
believe about a path that actually reports.)

Kept in its own tiny module so the node modules can reach it without importing
the Qt dialog / task-collection subsystem at load time (they cook headlessly on
the farm), and so ``process_executor`` no longer imports ``process_dialog`` —
the two used to cycle.
"""


from .task_collection import (
    collect_tasks_for_export_node,
    describe_missing_tasks,
)


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
    from tumblepipe.pipe.houdini.scene_context import scene_context
    from .process_dialog import ProcessDialog

    # Config reads are coherent: an entity's frame range / departments /
    # downstream deps written to disk after this session started (shot import,
    # a producer or another machine/session editing config) are picked up on
    # the next read. No manual refresh is needed before collecting them here.
    context = scene_context()
    if context is None:
        hou.ui.displayMessage(
            "Cannot determine workfile context. Save the file first.",
            severity=hou.severityType.Error
        )
        return

    all_tasks, enabled_task_ids = collect_tasks_for_export_node(export_node, context)
    if not all_tasks:
        # Say which nodes were dropped and why. Every collector filters
        # silently, so the bare warning read identically to an artist whose
        # scene held no export nodes and to one whose twenty export nodes all
        # addressed an entity this workfile does not own.
        hou.ui.displayMessage(
            "No export tasks found for the current context.",
            details=describe_missing_tasks(context) or None,
            details_expanded=True,
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


def _all_task_ids(tasks) -> set:
    """Every task id in the tree — parents and nested children — for enabling all."""
    ids: set = set()

    def _walk(ts):
        for task in ts:
            ids.add(task.id)
            if task.children:
                _walk(task.children)

    _walk(tasks)
    return ids


def _show_over(dialog, anchor) -> None:
    """Show ``dialog`` centred on ``anchor`` (the window the artist was in),
    in front.

    The dialog stays parented to Houdini's main window, so it outlives a
    transient anchor such as a popup; only its position follows the anchor.
    Centred on the main window, it opened wherever that window was: behind an
    undocked Asset Browser, or on another monitor.
    """
    dialog.show()
    if anchor is not None and anchor.isVisible():
        frame = dialog.frameGeometry()
        frame.moveCenter(anchor.frameGeometry().center())
        # An anchor near a screen edge would push the centred dialog off it.
        screen = anchor.screen().availableGeometry()
        frame.moveLeft(max(screen.left(), min(frame.left(), screen.right() - frame.width() + 1)))
        frame.moveTop(max(screen.top(), min(frame.top(), screen.bottom() - frame.height() + 1)))
        dialog.move(frame.topLeft())
    dialog.raise_()
    dialog.activateWindow()


def open_process_dialog_for_publish(
    context, dialog_title: str = "Publish", on_closed=None,
):
    """Open ProcessDialog for a whole scene's publish, with every task enabled.

    Like :func:`open_process_dialog_for_node`, but for the Asset Browser Publish
    quick action: it publishes the loaded scene's entity as one action, so it
    enables *all* the context's export/build tasks rather than a single clicked
    node's subset. This is the one-window replacement for the old headless
    per-node loop (a bare execute() per node opened one dialog per export node).

    ``context`` is the loaded scene's workfile context, which the caller
    (``SceneManager.get_loaded_scene_context``) has already read.

    Returns the dialog, or None when there is nothing to publish (the reason
    is shown). The dialog is modal but does not block: ``on_closed()`` runs
    when it closes. The caller runs inside a ``run_on_main_thread`` event-loop
    callback, and Houdini runs no other such callback until that one returns:
    blocking here in ``exec_()`` froze every Asset Browser update - the
    Publish spinner, a reopened browser's cards - for as long as the window
    stayed open, and for the rest of the session when it opened out of sight.
    """
    import hou
    from PySide6 import QtCore, QtWidgets
    from .task_collection import collect_publish_tasks
    from .process_dialog import ProcessDialog

    if context is None:
        hou.ui.displayMessage(
            "Publish: the loaded scene has no pipeline context.",
            severity=hou.severityType.Warning,
        )
        return None

    all_tasks = collect_publish_tasks(context)
    if not all_tasks:
        hou.ui.displayMessage(
            "Publish: no export tasks found for the current scene.",
            details=describe_missing_tasks(context) or None,
            details_expanded=True,
            severity=hou.severityType.Warning,
        )
        return None

    def save_scene():
        hou.hipFile.save()

    anchor = QtWidgets.QApplication.activeWindow()
    dialog = ProcessDialog(
        title=dialog_title,
        tasks=all_tasks,
        current_department=context.department_name,
        pre_execute_callback=save_scene,
        initial_enabled_task_ids=_all_task_ids(all_tasks),
        parent=hou.qt.mainWindow(),
    )
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
    if on_closed is not None:
        dialog.finished.connect(lambda _result: on_closed())
    _show_over(dialog, anchor)
    return dialog

