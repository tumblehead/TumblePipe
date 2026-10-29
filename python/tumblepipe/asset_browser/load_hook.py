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

from tumblepipe.pipe.houdini.ui.report import report_failure

from .prefs import load_prefs


_UNTITLED = "untitled.hip"


def _should_refresh() -> bool:
    # No GUI check here: register() never installs the callback without one.
    return load_prefs().auto_refresh_on_open


def _refresh() -> None:
    import hou
    from tumblepipe.pipe.houdini import playbar
    from tumblepipe.pipe.houdini.scene_imports import refresh_scene_imports

    with playbar.update_mode(hou.updateMode.Manual):
        refresh_scene_imports()


def _refresh_if_enabled() -> None:
    # The scene is open by now, so a failed refresh must not read as a
    # failed open - but it must reach the artist: the scene would otherwise
    # sit on the versions it was saved with and nothing would say so.
    try:
        if _should_refresh():
            _refresh()
    except Exception as exc:
        report_failure("Refreshing the scene's imports after it opened", exc)


def _apply_context_options() -> None:
    """Name the workfile's entity in context options (``@asset``, ``@shot``
    ...): a pipeline save already stores them in the hip, but a workfile
    saved before they existed, or by File > Save As, may not carry them.
    Upkeep, not an artist's action: a failure is logged, not shown."""
    import logging
    from tumblepipe.pipe.houdini import context_options
    from tumblepipe.pipe.houdini.scene_context import scene_context

    try:
        context_options.apply(scene_context())
    except Exception:
        logging.getLogger(__name__).exception(
            "Setting the workfile's context options failed"
        )


def _apply_multi_timeline() -> None:
    """Lay a shared-timeline Multi's workfile out: the playbar over every
    shot and, when the Multi asks for them, a bookmark per shot (they are
    not saved in the hip). After the refresh, whose import_shot nodes do
    the same, so a scene with none or with refresh off still gets it.
    Upkeep like the context options: a failure is logged, not shown."""
    import logging
    from tumblepipe.pipe.houdini import multi_timeline

    try:
        multi_timeline.apply()
    except Exception:
        logging.getLogger(__name__).exception(
            "Laying out the Multi's shared timeline failed"
        )


def _on_scene_opened() -> None:
    _apply_context_options()
    _refresh_if_enabled()
    _apply_multi_timeline()


def on_hip_file_event(event_type) -> None:
    import hou

    if event_type == hou.hipFileEventType.AfterLoad:
        _on_scene_opened()


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
        _on_scene_opened()
