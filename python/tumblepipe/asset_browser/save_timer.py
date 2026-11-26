"""Autosave and the unsaved-time warning on the Save quick action.

Remy (2026-11-11): the Save button's right-click menu offers Autosave, the
way the Refresh button offers auto-refresh, and the button shows its
warning badge after a time the artist picks without saving.

An autosave does what Save does, without asking for a note: it writes the
next workfile version of the open scene's context, noted "Autosave". It
only runs when the open scene is a pipeline workfile with unsaved changes,
and never while a dialog or menu is open (a version-note prompt, the
publish window) or the timeline plays, so it cannot land in the middle of
something. A failed autosave goes to the status bar, never a modal: a
dialog every few minutes would be worse than the failure.

Both count from the last save, load or new scene (hip file events), not
from the file's mtime: a scene opened an hour after its last save is not
an hour unsaved. A save that leaves the scene unsaved (Houdini's own
backup autosave writes a copy and fires AfterSave, but the scene stays
dirty) does not restart the count (:func:`event_resets_clock`).

A cook or render holds the GUI thread, so the timer's tick arrives late
when it ends; a late tick counts as busy (:func:`tick_was_late`) so an
autosave never lands the instant a long cook finishes. With both off nothing here runs: the clock starts on the
first ask with either on, and counts from then.

:func:`is_due` and the choice labels are pure; the rest needs Houdini.
"""

from __future__ import annotations

import logging
import time
import weakref

log = logging.getLogger(__name__)

#: Autosave intervals the Save menu offers, in minutes; 0 is Off.
AUTOSAVE_CHOICES = (0, 5, 10, 15, 30)
#: Unsaved-time warnings the Save menu offers, in minutes; 0 is Off.
WARNING_CHOICES = (0, 15, 30, 60)
#: How often the autosave timer looks. The interval is minutes, so a few
#: seconds late is nothing, and a look costs one hipFile query.
TICK_MS = 15_000
#: The version note an autosave writes.
AUTOSAVE_NOTE = "Autosave"
#: Seconds past :data:`TICK_MS` after which a tick counts as late: the GUI
#: thread was held (a cook, a render, a long load), so Houdini was busy.
LATE_SLACK_S = 5.0


def is_due(minutes: int, elapsed_s: float, unsaved: bool) -> bool:
    """Whether an autosave or warning set to *minutes* (0 = off) is due,
    *elapsed_s* seconds after the last save, load or new scene."""
    return minutes > 0 and unsaved and elapsed_s >= minutes * 60


def tick_was_late(
    prev: float | None, now: float,
    interval_s: float = TICK_MS / 1000, slack_s: float = LATE_SLACK_S,
) -> bool:
    """Whether a tick at *now* came more than *slack_s* after it was due,
    *interval_s* after the tick at *prev* (``None``: the first tick)."""
    return prev is not None and now - prev > interval_s + slack_s


def event_resets_clock(kind: str, still_unsaved: bool) -> bool:
    """Whether a hip file event restarts the save count. *kind* is
    ``"save"``, ``"load"``, ``"clear"`` or anything else (no reset). A
    load or new scene always does; a save only when the scene is clean
    after it, so a backup copy (scene still dirty) does not."""
    if kind in ("load", "clear"):
        return True
    return kind == "save" and not still_unsaved


def autosave_label(minutes: int) -> str:
    return "Off" if minutes <= 0 else f"Every {minutes} min"


def warning_label(minutes: int) -> str:
    if minutes <= 0:
        return "Off"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"After {hours} hour" + ("" if hours == 1 else "s")
    return f"After {minutes} min"


class SaveClock:
    """Seconds since the scene was last saved, loaded or cleared."""

    def __init__(self, now=time.monotonic) -> None:
        self._now = now
        self._last = now()

    def reset(self) -> None:
        self._last = self._now()

    def elapsed(self) -> float:
        return self._now() - self._last


# ── Houdini glue ─────────────────────────────────────────────────────

_clock: SaveClock | None = None
_timer = None
_catalog_ref = None
_last_tick: float | None = None


def clock() -> SaveClock:
    """The session's save clock, reset by hip file events from its first
    use on."""
    global _clock
    if _clock is None:
        _clock = SaveClock()
        _register_hip_callback()
    return _clock


def on_hip_file_event(event_type) -> None:
    import hou
    kinds = {
        hou.hipFileEventType.AfterSave: "save",
        hou.hipFileEventType.AfterLoad: "load",
        hou.hipFileEventType.AfterClear: "clear",
    }
    kind = kinds.get(event_type)
    if kind is None:
        return
    still_unsaved = kind == "save" and bool(hou.hipFile.hasUnsavedChanges())
    if event_resets_clock(kind, still_unsaved):
        clock().reset()


def _register_hip_callback() -> None:
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


def poll(catalog) -> bool:
    """The Save button's warning badge, asked every second on the GUI
    thread; also starts the autosave timer once Autosave is on."""
    prefs = getattr(catalog, "_prefs", None)
    if prefs is None:
        return False
    if int(getattr(prefs, "autosave_minutes", 0) or 0) > 0:
        start(catalog)
    return warning_due(prefs)


def start(catalog) -> None:
    """Start the autosave timer for *catalog* (idempotent). GUI thread."""
    global _timer, _catalog_ref
    _catalog_ref = weakref.ref(catalog)
    clock()
    if _timer is not None:
        return
    from PySide6.QtCore import QTimer
    _timer = QTimer()
    _timer.setInterval(TICK_MS)
    _timer.timeout.connect(_tick)
    _timer.start()


def stop() -> None:
    global _timer, _last_tick
    _last_tick = None
    if _timer is not None:
        _timer.stop()
        _timer.deleteLater()
        _timer = None


def unsaved() -> bool:
    """Whether the open scene is a saved file with changes since. An
    untitled scene does not count: the pipeline Save cannot save it."""
    import hou
    from tumblepipe.pipe.houdini.untitled import scene_is_untitled
    return not scene_is_untitled() and hou.hipFile.hasUnsavedChanges()


def warning_due(prefs) -> bool:
    """Whether the Save button shows its unsaved-time warning badge."""
    minutes = int(getattr(prefs, "unsaved_warning_minutes", 0) or 0)
    if minutes <= 0:
        return False
    return is_due(minutes, clock().elapsed(), unsaved())


def unsaved_minutes() -> int | None:
    """Whole minutes the open scene has been unsaved, ``None`` when it
    has no unsaved changes."""
    if not unsaved():
        return None
    return int(clock().elapsed() // 60)


def _busy() -> bool:
    import hou
    from PySide6.QtWidgets import QApplication
    if QApplication.activeModalWidget() is not None:
        return True
    if QApplication.activePopupWidget() is not None:
        return True
    return bool(hou.playbar.isPlaying())


def should_autosave(
    minutes: int, elapsed_s: float, unsaved: bool, busy: bool,
    in_pipeline: bool,
) -> bool:
    """Whether a tick autosaves: due (:func:`is_due`), nothing modal open
    or playing, and the scene is a pipeline workfile Save can version."""
    return is_due(minutes, elapsed_s, unsaved) and not busy and in_pipeline


def tick(catalog, late: bool = False) -> bool:
    """One look of the autosave timer for *catalog*; whether it saved.
    *late*: the tick came late (:func:`tick_was_late`), so Houdini was
    busy and this look holds off.

    The checks run cheapest first and stop at the first no, so an idle
    tick with Autosave off or a clean scene asks nothing else.
    """
    minutes = int(getattr(catalog._prefs, "autosave_minutes", 0) or 0)
    if minutes <= 0:
        return False
    try:
        if not is_due(minutes, clock().elapsed(), unsaved()):
            return False
        if not should_autosave(
            minutes, clock().elapsed(), True, late or _busy(),
            catalog._scene.get_loaded_scene_context() is not None,
        ):
            return False
    except Exception:
        log.exception("Autosave check failed")
        return False
    # Count again from now whether or not the save lands: a failing
    # autosave retries next interval, not every tick.
    clock().reset()
    catalog._scene.autosave_current_scene()
    return True


def _tick() -> None:
    global _last_tick
    now = time.monotonic()
    late = tick_was_late(_last_tick, now)
    _last_tick = now
    catalog = _catalog_ref() if _catalog_ref is not None else None
    if catalog is not None:
        tick(catalog, late=late)
