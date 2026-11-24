"""The entities of the current LOP selection, and opening their workfiles.

The Houdini side of ``pipe.usd.entity_stamp``: which prims are selected in
Solaris (the viewport and the Scene Graph Tree share one selection), which
entities own them, and the one action built on that so far: the department
jump (Alt+D), open a department workfile of the selected asset, or of the
open workfile's own asset when nothing selected names one
(``pipe.department_jump.jump_target``).

The entity comes from the stamp on the prims (or the open workfile's
``context.json``) and the department from the caller. Nothing is read from
the node graph or from prim names.
"""

import logging
from pathlib import Path

import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe import department_jump
from tumblepipe.pipe.department_jump import jump_target
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
    return entity_departments(selected_entity())


def workfile_context():
    """The open workfile's pipeline ``Context`` (its ``context.json``,
    through :func:`scene_context.scene_context`); None for an untitled or
    off-pipeline scene, and for a Multi's workfile, whose context names its
    group (``groups:/``), not an entity, so it has no departments to jump
    between and no entity to save a version of."""
    from tumblepipe.pipe.houdini.scene_context import scene_context

    try:
        if not hou.hipFile.path() or hou.hipFile.isNewFile():
            return None
        context = scene_context()
    except Exception:  # noqa: BLE001  an unreadable context is no context
        logger.debug("Reading the open workfile's context failed", exc_info=True)
        return None
    if context is None or context.entity_uri.purpose != 'entity':
        return None
    return context


def workfile_entity() -> Uri | None:
    """The entity of the open workfile; None for an untitled or
    off-pipeline scene."""
    context = workfile_context()
    return None if context is None else context.entity_uri


def jump_entity() -> Uri | None:
    """The entity the department jump opens: the one the LOP selection
    belongs to, else the open workfile's asset
    (``department_jump.jump_target``)."""
    selected = [entity.uri for entity in selected_entities()]
    return jump_target(selected, None if selected else workfile_entity())


def entity_departments(entity_uri: Uri | None) -> list[str]:
    """The departments of *entity_uri*, in pool order; empty for None or
    an entity the project does not know."""
    from tumblepipe.config.department import list_entity_department_names

    if entity_uri is None:
        return []
    try:
        return list_entity_department_names(entity_uri)
    except ValueError:
        return []


def entity_active_departments(entity_uri: Uri | None) -> list[str]:
    """The departments of *entity_uri* the jump offers
    (``department_jump.active_departments``): in pool order, the ones with
    a workfile (its own, or a Multi's that covers it) that are not turned
    off on the entity, plus the open workfile's department when the open
    workfile is this entity's. Empty for None or an unknown entity."""
    from tumblepipe.config.department import get_disabled_departments
    from tumblepipe.pipe.department_jump import active_departments
    from tumblepipe.pipe.houdini.workfiles import latest_workfile

    names = entity_departments(entity_uri)
    if not names:
        return []
    try:
        off = get_disabled_departments(entity_uri)
    except ValueError:
        off = []
    context = workfile_context()
    current = (
        context.department_name
        if context is not None and context.entity_uri == entity_uri else None
    )

    def has_work(name: str) -> bool:
        try:
            return latest_workfile(entity_uri, name) is not None
        except Exception:  # noqa: BLE001  a path that cannot resolve has no work
            logger.debug("Resolving the %s workfile of %s failed",
                         name, entity_uri, exc_info=True)
            return False

    return active_departments(names, has_work, off, current)


def _status(message: str) -> None:
    hou.ui.setStatusMessage(message, severity=hou.severityType.Warning)


def save_without_asking() -> bool:
    """The "save without asking" setting: the Asset Browser's *Autosave
    (version up) on scene change*, which the jump shares, read fresh from
    disk. False when the prefs cannot be read."""
    try:
        from tumblepipe.asset_browser.prefs import load_prefs
        return bool(load_prefs().autosave_on_scene_change)
    except Exception:  # noqa: BLE001  unreadable prefs: keep asking
        logger.warning("Could not read the autosave preference", exc_info=True)
        return False


def set_save_without_asking(value: bool) -> bool:
    """Save the "save without asking" setting, and bring every open Asset
    Browser catalog's copy up to date (its scene swap reads its own copy).
    Returns whether it was saved."""
    from tumblepipe.asset_browser.prefs import update_prefs
    from tumblepipe.pipe.houdini.ui.report import report_failure

    value = bool(value)
    try:
        update_prefs(autosave_on_scene_change=value)
    except Exception as error:  # noqa: BLE001  the toggle's boundary
        report_failure("Saving the autosave preference", error)
        return False
    try:
        from tumbletrove import asset_browser as ab
        registry = getattr(ab, "_registry", None)
        for catalog in getattr(registry, "catalogs", None) or ():
            prefs = getattr(catalog, "_prefs", None)
            if prefs is not None and hasattr(prefs, "autosave_on_scene_change"):
                prefs.autosave_on_scene_change = value
    except Exception:  # noqa: BLE001  no browser open: nothing to update
        logger.debug("No Asset Browser catalog to update", exc_info=True)
    hou.ui.setStatusMessage(
        "Opening another workfile now saves a new version without asking."
        if value else
        "Opening another workfile now leaves unsaved work to Houdini's prompt.",
        severity=hou.severityType.Message,
    )
    return True


def _save_before_jump(department: str) -> bool:
    """Deal with the open scene's unsaved work before the jump loads
    another workfile in this session. Returns whether the jump may go on.

    With *save without asking* on, a pipeline workfile with unsaved
    changes is written as its next version, noted "Autosave before
    opening <department>", and the load then has nothing to prompt about.
    Otherwise the load shows Houdini's own save prompt. A save that fails
    is reported and stops the jump, so the load never goes ahead over
    work the artist asked to keep.
    """
    from tumblepipe.pipe.houdini.ui.report import report_failure

    unsaved = not hou.hipFile.isNewFile() and hou.hipFile.hasUnsavedChanges()
    if not unsaved:
        return True
    context = workfile_context()
    action = department_jump.before_jump(
        unsaved, context is not None, save_without_asking())
    if action != department_jump.SAVE:
        return True
    try:
        from tumblepipe.asset_browser.houdini import session_nc_type
        from tumblepipe.pipe.houdini.workfile_save import commit_next_workfile
        path = commit_next_workfile(
            context.entity_uri, context.department_name,
            prev_context=context, nc_type=session_nc_type(),
            note=department_jump.jump_note(department),
        )
    except Exception as error:  # noqa: BLE001  the jump's boundary
        report_failure("Saving before opening another workfile", error)
        return False
    logger.info("Saved %s before the department jump", path)
    hou.ui.setStatusMessage(f"Saved {Path(path).name}", severity=hou.severityType.Message)
    return True


def open_selected_workfile(department: str, new_instance: bool = False) -> bool:
    """Open the ``department`` workfile of the jump's entity
    (:func:`jump_entity`): in this session, or in a new Houdini
    (``new_instance``).

    Returns whether a workfile was opened. A selection that names several
    entities opens nothing and says why in the status bar; so does nothing
    to open (no entity selected and no asset workfile open) and an entity
    that does not have the department. In this session, unsaved work is
    saved first or left to Houdini's prompt (:func:`_save_before_jump`).
    Must be called on the GUI thread
    (``workfiles.open_workfile_in_session``).
    """
    from tumblepipe.pipe.houdini import workfiles
    from tumblepipe.pipe.houdini.ui.report import report_failure

    selection = lop_selection()
    entities = distinct_entities(*selection) if selection is not None else []
    if len(entities) > 1:
        names = ", ".join(entity.uri.segments[-1] for entity in entities)
        _status(f"The selection spans several entities ({names}). Select one.")
        return False
    selected = [entity.uri for entity in entities]
    entity_uri = jump_target(selected, None if selected else workfile_entity())
    if entity_uri is None:
        if selection is not None:
            _status(
                "The selection belongs to no pipeline entity. Assets exported "
                "before the entity stamp need a new export to carry it."
            )
        else:
            _status(
                "Select an asset in the Solaris viewport, or open an asset's "
                "workfile, first."
            )
        return False

    name = entity_uri.segments[-1]
    if department not in entity_departments(entity_uri):
        _status(f"{name} has no {department} department.")
        return False

    action = f"Opening the {department} workfile of {name}"
    try:
        if new_instance:
            return workfiles.open_workfile_in_new_instance(entity_uri, department)
        if not _save_before_jump(department):
            return False
        return workfiles.open_workfile_in_session(entity_uri, department)
    except Exception as error:  # noqa: BLE001  the action's boundary
        report_failure(action, error)
        return False
