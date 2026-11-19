"""The entities of the current LOP selection, and opening their workfiles.

The Houdini side of ``pipe.usd.entity_stamp``: which prims are selected in
Solaris (the viewport and the Scene Graph Tree share one selection), which
entities own them, and the one action built on that so far: open the
selected asset's department workfile.

The entity comes from the stamp on the prims and the department from the
caller. Nothing is read from the node graph or from prim names.
"""

import logging

import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.usd.entity_stamp import PrimEntity, distinct_entities

logger = logging.getLogger(__name__)


def _viewer_stage(pane):
    """The stage ``pane`` (a Scene Viewer) shows, or None."""
    getter = getattr(pane, 'stage', None)
    if getter is not None:
        try:
            stage = getter()
        except Exception:  # noqa: BLE001  not looking at a LOP network
            stage = None
        if stage is not None:
            return stage
    try:
        network = pane.pwd()
        display = network.displayNode()
        return display.stage() if display is not None else None
    except Exception:  # noqa: BLE001  not a LOP network, or nothing displayed
        return None


def viewer_selection(pane):
    """``(stage, [prim paths])`` selected in ``pane``, or None when it is
    not a Scene Viewer on a stage with prims selected."""
    if pane is None:
        return None
    try:
        if pane.type() != hou.paneTabType.SceneViewer:
            return None
    except Exception:  # noqa: BLE001  a pane being torn down
        return None
    getter = getattr(pane, 'currentSceneGraphSelection', None)
    if getter is None:
        return None
    try:
        paths = [str(path) for path in getter()]
    except Exception as error:  # noqa: BLE001  not a LOP viewer
        logger.debug("No LOP selection in %s: %s", pane, error)
        return None
    if not paths:
        return None
    stage = _viewer_stage(pane)
    if stage is None:
        return None
    return stage, paths


def lop_selection():
    """``(stage, [prim paths])`` of the current LOP selection, or None.

    The Scene Viewer under the cursor answers first (it is the one a radial
    opened over), then any other Scene Viewer with prims selected.
    """
    under_cursor = hou.ui.paneTabUnderCursor()
    found = viewer_selection(under_cursor)
    if found is not None:
        return found
    for pane in hou.ui.paneTabs():
        if pane is under_cursor:
            continue
        found = viewer_selection(pane)
        if found is not None:
            return found
    return None


def selected_entities() -> list[PrimEntity]:
    """The entities that own the selected LOP prims, one entry per entity,
    in selection order. Empty when nothing is selected or nothing selected
    belongs to a pipeline entity (see ``entity_stamp.owning_entity``)."""
    selection = lop_selection()
    if selection is None:
        return []
    stage, paths = selection
    return distinct_entities(stage, paths)


def selected_entity() -> Uri | None:
    """The one entity the LOP selection belongs to, or None when the
    selection names none, or more than one."""
    entities = selected_entities()
    return entities[0].uri if len(entities) == 1 else None


def selected_entity_departments() -> list[str]:
    """The departments of the selected entity, in pool order; empty when
    the selection does not name exactly one entity."""
    from tumblepipe.config.department import list_entity_department_names

    entity_uri = selected_entity()
    if entity_uri is None:
        return []
    try:
        return list_entity_department_names(entity_uri)
    except ValueError:
        return []


def _status(message: str) -> None:
    hou.ui.setStatusMessage(message, severity=hou.severityType.Warning)


def open_selected_workfile(department: str, new_instance: bool = False) -> bool:
    """Open the ``department`` workfile of the entity that owns the LOP
    selection: in this session, or in a new Houdini (``new_instance``).

    Returns whether a workfile was opened. A selection that names no
    entity, or several, opens nothing and says why in the status bar; so
    does an entity that does not have the department. Must be called on
    the GUI thread (``workfiles.open_workfile_in_session``).
    """
    from tumblepipe.config.department import list_entity_department_names
    from tumblepipe.pipe.houdini import workfiles
    from tumblepipe.pipe.houdini.ui.report import report_failure

    selection = lop_selection()
    if selection is None:
        _status("Select a prim in the Solaris viewport first.")
        return False
    entities = distinct_entities(*selection)
    if not entities:
        _status(
            "The selection belongs to no pipeline entity. Assets exported "
            "before the entity stamp need a new export to carry it."
        )
        return False
    if len(entities) > 1:
        names = ", ".join(entity.uri.segments[-1] for entity in entities)
        _status(f"The selection spans several entities ({names}). Select one.")
        return False

    entity_uri = entities[0].uri
    name = entity_uri.segments[-1]
    try:
        departments = list_entity_department_names(entity_uri)
    except ValueError:
        departments = []
    if department not in departments:
        _status(f"{name} has no {department} department.")
        return False

    action = f"Opening the {department} workfile of {name}"
    try:
        if new_instance:
            return workfiles.open_workfile_in_new_instance(entity_uri, department)
        return workfiles.open_workfile_in_session(entity_uri, department)
    except Exception as error:  # noqa: BLE001  the action's boundary
        report_failure(action, error)
        return False
