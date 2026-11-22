"""Keep a saved Solaris viewer camera from crashing Houdini on open.

A Scene Viewer looking through a USD camera saves a line like this in the
hip's desktop section::

    viewcamera -c /stage/animate1 -n /cameras/render_camera anim.panetab2.world.persp1

When the hip is opened, Houdini replays it while it builds the desktop,
before the stage has cooked, and can crash in its own camera code
(``OP3D_ViewCommandStasher::applyStashedCommands`` ->
``JEDI_CameraHandler::updateFromLopCamera`` -> ``HUSD_DataHandle::readLock``,
SideFX #161915). The crash then comes back on every open of that file, so
the artist is locked out of their work (two artists, 2026-11-16 and
2026-11-18, both on files saved with a viewer through the shot camera).

So the line never stays in a saved hip:

* **After every save** the ``viewcamera`` lines that name a USD prim (``-n``)
  are blanked in the file just written: ``#`` and spaces, the same number
  of bytes, because Houdini reads a hip's sections by their stored length
  and a shorter section breaks every section after it. A ``#`` line is an
  hscript comment, so the viewer opens in its own perspective view.
* **Before every save** the cameras those viewers look through are kept in
  the hip itself (user data on ``/``), so...
* **after every load**, once the scene has loaded and the stage has cooked,
  each viewer is put back on its camera, as the artist left it.

GUI sessions only: hython never builds a desktop. Every step is upkeep: a
failure is logged, never shown, and never stops a save or a load.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from tumblepipe.util.io import write_bytes_atomic

log = logging.getLogger(__name__)

USER_DATA_KEY = "tumblepipe_viewer_cameras"

# A replayed viewer camera on a USD prim: `viewcamera ... -n <prim> ...`.
# OBJ cameras (`viewcamera -c /obj/cam1 <viewport>`) carry no -n and are safe.
_LOP_VIEWCAMERA = re.compile(rb"(?m)^viewcamera [^\r\n]*? -n [^\r\n]*")


def blank_lop_viewcameras(data: bytes) -> tuple[bytes, int]:
    """``data`` with every USD-camera ``viewcamera`` line blanked at the
    same length, and how many lines were blanked."""
    count = 0

    def blank(match):
        nonlocal count
        count += 1
        return b"#" + b" " * (len(match.group(0)) - 1)

    out = _LOP_VIEWCAMERA.sub(blank, data)
    return out, count


def strip_file(path: str) -> int:
    """Blank the USD-camera ``viewcamera`` lines of the hip at ``path`` in
    place. Returns how many were blanked; the file is only rewritten when
    there was one, through a temporary file and a rename, so a failure
    leaves the saved file as Houdini wrote it."""
    with open(path, "rb") as handle:
        data = handle.read()
    out, count = blank_lop_viewcameras(data)
    if not count:
        return 0
    if len(out) != len(data):  # never: every line keeps its length
        raise RuntimeError("blanking changed the file length")
    write_bytes_atomic(Path(path), out)
    return count


# --- the cameras the viewers look through --------------------------------

def _scene_viewers():
    import hou

    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.SceneViewer:
            yield pane_tab


def collect_cameras() -> list[dict]:
    """``[{"pane", "viewport", "camera"}]`` for each viewport that looks
    through a USD camera now."""
    found = []
    for viewer in _scene_viewers():
        for viewport in viewer.viewports():
            if viewport.camera() is not None:
                continue  # an OBJ camera: saved and replayed safely
            path = getattr(viewport, "cameraPath", lambda: "")()
            if path:
                found.append({"pane": viewer.name(),
                              "viewport": viewport.name(),
                              "camera": path})
    return found


def _remember_cameras() -> None:
    import hou

    root = hou.node("/")
    cameras = collect_cameras()
    if cameras:
        root.setUserData(USER_DATA_KEY, json.dumps(cameras))
    elif root.userData(USER_DATA_KEY) is not None:
        root.destroyUserData(USER_DATA_KEY)


def _restore_cameras() -> None:
    import hou

    raw = hou.node("/").userData(USER_DATA_KEY)
    if not raw:
        return
    cameras = json.loads(raw)
    stage = hou.node("/stage")
    display = stage.displayNode() if stage is not None else None
    if display is not None:
        display.stage()  # cook it: the camera lookup reads the stage
    viewers = {viewer.name(): viewer for viewer in _scene_viewers()}
    for entry in cameras:
        viewer = viewers.get(entry.get("pane"))
        if viewer is None:
            continue
        for viewport in viewer.viewports():
            if viewport.name() != entry.get("viewport"):
                continue
            try:
                # A USD prim path, as lookdev_studio's turntable does.
                viewport.setCamera(entry["camera"])
            except Exception:
                log.warning("Could not put %s.%s back on %s", entry.get("pane"),
                            entry.get("viewport"), entry.get("camera"),
                            exc_info=True)


def _restore_later() -> None:
    """Restore once Houdini is idle again: the desktop is built and the
    load's own work (import refresh, timeline) is done."""
    import hdefereval

    def run():
        try:
            _restore_cameras()
        except Exception:
            log.exception("Putting the viewers back on their cameras failed")

    hdefereval.executeDeferredAfterWaiting(run, 5)


# --- the hip file callback ------------------------------------------------

def on_hip_file_event(event_type) -> None:
    import hou

    kinds = hou.hipFileEventType
    try:
        if event_type == kinds.BeforeSave:
            _remember_cameras()
        elif event_type == kinds.AfterSave:
            count = strip_file(hou.hipFile.path())
            if count:
                log.info("Kept %d Solaris viewer camera(s) out of the saved "
                         "desktop (SideFX #161915)", count)
        elif event_type == kinds.AfterLoad:
            _restore_later()
    except Exception:
        log.exception("Viewer camera guard (%s) failed", event_type)


def register() -> None:
    """Install the callback in a GUI session. Safe to call more than once."""
    import hou

    if not hou.isUIAvailable():
        return
    for callback in hou.hipFile.eventCallbacks():
        if (getattr(callback, "__module__", None) == __name__
                and getattr(callback, "__name__", None) == "on_hip_file_event"):
            hou.hipFile.removeEventCallback(callback)
    hou.hipFile.addEventCallback(on_hip_file_event)
