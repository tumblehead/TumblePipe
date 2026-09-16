"""Refresh a scene's import nodes whenever Houdini loads one.

The refresh used to run only from the Browser's own open and reload actions,
so a scene opened any other way — File > Open, recent files, double-clicking
a ``.hip``, the Browser's Open in New Instance, the asset layer inspector —
kept the versions it was saved with and a newer publish did not appear. A
``hou.hipFile`` callback covers every one of those, and the Browser's
actions go through ``hou.hipFile.load`` like everything else.

GUI sessions only: hython and farm jobs must render the versions they were
submitted with. Gated on the ``auto_refresh_on_open`` Browser setting.
"""

from __future__ import annotations

import logging

from .prefs import load_prefs

log = logging.getLogger(__name__)

_UNTITLED = "untitled.hip"


def _should_refresh() -> bool:
    # No GUI check here: register() never installs the callback without one.
    return load_prefs().auto_refresh_on_open


def _refresh() -> None:
    import hou
    from tumblepipe.pipe.houdini import util
    from tumblepipe.pipe.houdini.scene_imports import refresh_scene_imports

    with util.update_mode(hou.updateMode.Manual):
        refresh_scene_imports()


def _refresh_if_enabled() -> None:
    try:
        if _should_refresh():
            _refresh()
    except Exception:
        # A failed refresh must not turn into a failed scene open.
        log.exception("Refreshing imports after scene load failed")


def on_hip_file_event(event_type) -> None:
    import hou

    if event_type == hou.hipFileEventType.AfterLoad:
        _refresh_if_enabled()


def _scene_loaded() -> bool:
    import hou

    return hou.hipFile.basename() != _UNTITLED


def register() -> None:
    """Install the load callback. Safe to call more than once.

    A ``.hip`` given on Houdini's command line may finish loading before
    this runs, in which case no AfterLoad will follow, so an already-open
    scene is refreshed here instead. A scene loaded later is refreshed by
    the callback, never by both: at registration it is still untitled.
    """
    import hou

    if not hou.isUIAvailable():
        return
    # Match by name, not identity: a reloaded module is a new function
    # object, and the stale one would run alongside it.
    for callback in hou.hipFile.eventCallbacks():
        if (getattr(callback, "__module__", None) == __name__
                and getattr(callback, "__name__", None) == "on_hip_file_event"):
            hou.hipFile.removeEventCallback(callback)
    hou.hipFile.addEventCallback(on_hip_file_event)

    if _scene_loaded():
        _refresh_if_enabled()
