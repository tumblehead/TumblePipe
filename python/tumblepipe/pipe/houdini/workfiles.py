"""Open an entity's department workfile, in this session or a new one.

URI-keyed and catalog-free, so both an HDA and the Asset Browser use it.
Everything resolves against the *active* project: a caller about another
project's entity activates that project first (the Asset Browser does).
"""

import logging
from pathlib import Path
from typing import Optional

import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import playbar
from tumblepipe.pipe.houdini.launch import launch_houdini
from tumblepipe.pipe.paths import latest_hip_file_path_with_context

logger = logging.getLogger(__name__)


def latest_workfile(entity_uri: Uri, department: str) -> Optional[Path]:
    """The workfile an "open" would land on, or None if there isn't one.

    Group-aware: an entity whose department is covered by a Multi resolves to
    the *group's* shared workfile, not a local one. Both open paths below go
    through here so they cannot disagree about which file they mean.
    """
    path = latest_hip_file_path_with_context(entity_uri, department)
    if path is None or not path.exists():
        return None
    return path


def _no_workfile(entity_uri: Uri, department: str) -> None:
    hou.ui.displayMessage(
        f"No {department} workfile to open for {entity_uri.segments[-1]}.",
        severity=hou.severityType.Warning,
    )


def open_workfile_in_session(entity_uri: Uri, department: str) -> bool:
    """Load the department's latest workfile into this Houdini session.

    Returns False when there was nothing to open. Must be called on the GUI
    thread — ``hou.hipFile.load`` off it crashes Houdini. Node parm callbacks
    already are, which is why there is no ``run_on_main_thread`` hop here;
    do not add a caller that isn't.

    Unsaved work is left to Houdini's own save prompt rather than the
    Browser's ``prepare_scene_swap`` (which saves a new *version* on the way
    out). That behaviour belongs to the Browser's model of a scene swap;
    half-copying it here would be worse than not having it.

    A load that fails raises; the caller is the action's boundary and
    reports it.
    """
    path = latest_workfile(entity_uri, department)
    if path is None:
        _no_workfile(entity_uri, department)
        return False

    try:
        # Manual update mode so the load doesn't trigger a live full-graph
        # cook on the way in. Mirrors the Browser's open flow.
        with playbar.update_mode(hou.updateMode.Manual):
            hou.hipFile.load(str(path), suppress_save_prompt=False)
    except hou.LoadWarning as warning:
        # Non-fatal: the scene IS open, with warnings (missing HDAs, etc).
        # Reporting this as a failure would be a lie.
        logger.warning("Opened %s with warnings: %s", path, warning)

    logger.info("Opened workfile: %s", path)
    return True


def open_workfile_in_new_instance(
    entity_uri: Uri, department: str,
    env_overrides: dict[str, str] | None = None,
) -> bool:
    """Launch a new Houdini on the department's latest workfile.

    The child is this session's build with this process's environment, plus
    ``env_overrides`` — the Asset Browser passes the target project's
    ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` when it opens another project's
    asset (that project must be active here, so the workfile resolves).
    ``TH_PIPELINE_PATH`` is hpm-owned and never overridden. Returns False
    when there was nothing to open; a launch that fails raises.
    """
    path = latest_workfile(entity_uri, department)
    if path is None:
        _no_workfile(entity_uri, department)
        return False

    launch_houdini(path, env_overrides)
    logger.info("Launched new Houdini instance: %s", path)
    hou.ui.setStatusMessage(f"Opening {path.name} in a new Houdini session...")
    return True
