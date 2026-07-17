"""Open an entity's department workfile, in this session or a new one.

URI-keyed and catalog-free, so an HDA can reach it: the Asset Browser has its
own pair of these (``_pipeline_workfiles.WorkfileManager``), but they are keyed
on catalog ``asset_id`` strings, bound to catalog internals, and live in a
``sys.path``-injected panel module that a node cannot import.

Scope: the *current* project. tumblepipe's path helpers cache
``TH_PROJECT_PATH`` at import and return wrong paths for other projects (see
``_pipeline_workfiles.workfile_path_for``, which is why the catalog does its
own path math). Every caller here is asking about an entity already composed
into the open scene, so it is in the current project by construction — but do
not grow a cross-project caller onto this without fixing that first.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import util
from tumblepipe.pipe.paths import latest_hip_file_path_with_context

logger = logging.getLogger(__name__)


def latest_workfile(entity_uri: Uri, department: str) -> Optional[Path]:
    """The workfile an "open" would land on, or None if there isn't one.

    Group-aware: an entity whose department is covered by a Multi resolves to
    the *group's* shared workfile, not a local one. Both open paths below go
    through here so they cannot disagree about which file they mean — the
    Asset Browser's pair does disagree (its in-session open is group-aware,
    its new-instance open globs the member's own dept dir by mtime), and that
    is a bug, not a precedent.
    """
    try:
        path = latest_hip_file_path_with_context(entity_uri, department)
    except Exception:
        logger.debug(
            "Workfile lookup failed for %s/%s", entity_uri, department,
            exc_info=True,
        )
        return None
    if path is None or not path.exists():
        return None
    return path


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
    """
    path = latest_workfile(entity_uri, department)
    if path is None:
        hou.ui.displayMessage(
            f"No {department} workfile to open for "
            f"{entity_uri.segments[-1]}.",
            severity=hou.severityType.Warning,
        )
        return False

    try:
        # Manual update mode so the load doesn't trigger a live full-graph
        # cook on the way in. Mirrors the Browser's open flow.
        with util.update_mode(hou.updateMode.Manual):
            hou.hipFile.load(str(path), suppress_save_prompt=False)
    except hou.LoadWarning as warning:
        # Non-fatal: the scene IS open, with warnings (missing HDAs, etc).
        # Reporting this as a failure would be a lie.
        logger.warning("Opened %s with warnings: %s", path, warning)
    except Exception:
        logger.exception("Failed to open workfile %s", path)
        hou.ui.displayMessage(
            f"Could not open {path.name}. See the console for details.",
            severity=hou.severityType.Error,
        )
        return False

    logger.info("Opened workfile: %s", path)
    return True


def _houdini_executable() -> Optional[Path]:
    """The binary to relaunch — this session's own, or $HFS's as a fallback."""
    candidate = Path(sys.executable)
    if candidate.exists():
        return candidate
    hfs = os.environ.get("HFS", "")
    if hfs:
        fallback = Path(hfs) / "bin" / "houdinifx.exe"
        if fallback.exists():
            return fallback
    return None


def open_workfile_in_new_instance(entity_uri: Uri, department: str) -> bool:
    """Launch a new Houdini on the department's latest workfile.

    The child inherits this process's whole environment. Unlike the Browser's
    equivalent it overrides nothing: the Browser retargets
    ``TH_PROJECT_PATH``/``TH_CONFIG_PATH`` because it can open another
    project's asset, whereas everything reachable from here is already in the
    open project, so the inherited values are the right ones. ``TH_PIPELINE_PATH``
    is hpm-owned and must never be overridden regardless.
    """
    path = latest_workfile(entity_uri, department)
    if path is None:
        hou.ui.displayMessage(
            f"No {department} workfile to open for "
            f"{entity_uri.segments[-1]}.",
            severity=hou.severityType.Warning,
        )
        return False

    houdini = _houdini_executable()
    if houdini is None:
        hou.ui.displayMessage(
            "Could not find the Houdini executable to launch.",
            severity=hou.severityType.Error,
        )
        return False

    # Detach the child so it outlives this callback returning.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [str(houdini), str(path)],
            env=dict(os.environ),
            creationflags=flags,
        )
    except Exception:
        logger.exception("Failed to launch Houdini on %s", path)
        hou.ui.displayMessage(
            f"Could not launch Houdini on {path.name}. See the console.",
            severity=hou.severityType.Error,
        )
        return False

    logger.info("Launched new Houdini instance: %s", path)
    hou.ui.setStatusMessage(f"Opening {path.name} in a new Houdini session...")
    return True
