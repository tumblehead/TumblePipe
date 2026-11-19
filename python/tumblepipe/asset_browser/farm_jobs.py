"""The Farm Manager quick action's click, hover, menu, dot, pulse and announcement.

When a batch the artist submitted in this Houdini session ends
(``farm.manager.finished``, fed by the farm watcher's thread):

- one line goes to Houdini's status bar, ``SQ010/SH020 render done: 48
  frames, 3 passes · 12 min on the farm`` (a warning when something failed);
- when Houdini is not the application in front, the same line goes to the
  OS as a desktop notification (``util.desktop_notify``: a line, no
  buttons); on Windows a click on it opens the folder of the job the line
  is about (``Announcement.folder``: its frames, else a playblast's daily
  folder; nothing for a failure without frames);
- and Houdini's taskbar button flashes until Houdini is activated
  (:func:`flash_taskbar`: ``QApplication.alert`` on Houdini's main window).
  Windows shows its notifications on the main display only, whichever
  screen the artist works on, while the taskbar button is on the screen
  Houdini is on. Not with Reduce motion on;
- the Farm Manager button asks TumbleTrove for one pulse, which TumbleTrove
  plays or refuses (hidden browser, reduced motion, Houdini cooking or
  playing back), and shows a dot at its foot, accent for finished, danger
  for failed, until the artist opens the Farm Manager from the button.

The button itself (Remy, 2026-11-18: "I want to click it to open the
manager but instead I see two summaries? Let's make it just one and only on
hover. Clicking the button should always open the manger window"):

- **a click always opens the Farm Manager** (:func:`open_manager`), brought
  to the front and restored when it was minimised, and marks the ended
  batches seen, so the dot goes. The catalog gives TumbleTrove no popover
  for the button (:data:`CLICK_POPOVER` is None), which is what makes a
  click run the action on every TumbleTrove;
- **the hover is the one summary** (:func:`hover_html`): the artist's
  recent jobs as the watcher counted them, then, while some ended since the
  Farm Manager was last opened from the button, "Finished since you looked"
  and their lines, failures first;
- **a right-click** offers *Open Farm Manager* and each ended batch's main
  action (:func:`menu_items`): *Preview in Cinedex*, or *Show in Jobs* for
  a failure or a batch with no frames, so the outputs stay one click away;
  a failure that wrote frames also has *Preview N Frames in Cinedex*.

The dot and the pulse need TumbleTrove 0.60.0
(``Catalog.get_quick_action_dot`` / ``request_quick_action_pulse``). With an
older TumbleTrove those hooks are never asked; the click, the hover, the
status line and the OS notification work the same.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from tumblepipe.farm.manager import finished

log = logging.getLogger(__name__)

#: The quick action the popover hangs on.
ACTION_ID = "farm_manager"


#: What the catalog answers TumbleTrove's ``get_quick_action_popover`` for
#: the button: no popover, so a click runs the action (TumbleTrove's
#: ``click_outcome``) and the hover is the only summary on screen.
CLICK_POPOVER = None

#: The right-click menu's first entry, and TumbleTrove's separator row.
MENU_OPEN = "Open Farm Manager"
MENU_SEPARATOR = "__separator__"


def open_manager(scene, session: finished.SessionWatch, done_cb=None) -> None:
    """A click on the button: open the Farm Manager (in front, restored when
    minimised: ``farm.manager.window.show_manager``), and mark the session's
    ended batches seen, as the artist is now looking at their jobs."""
    session.mark_seen()
    scene.open_farm_manager(done_cb)


def hover_html(summary, unseen, now, *, hours: float, dim: str | None = None,
               warn: str | None = None) -> str:
    """The button's hover, its one summary: the artist's recent jobs
    (``activity.summary_html``), then what ended since they last opened the
    Farm Manager from the button (``finished.hover_html``; nothing when all
    is seen). ``dim`` and ``warn`` are the theme's colours; None keeps each
    function's default."""
    from tumblepipe.farm.manager.activity import summary_html
    colours = {key: value for key, value in (("dim", dim), ("warn", warn)) if value}
    return (
        summary_html(summary, now, hours=hours, **colours)
        + finished.hover_html(unseen, **colours)
    )


def menu_items(scene, session: finished.SessionWatch, *, preview=None) -> list:
    """The button's right-click menu, as ``(label, action)``: *Open Farm
    Manager* (the click), then, under a separator, an entry per ended batch
    of the session, failures first, each running its main action (a failed
    batch that wrote frames has a second entry that previews them)
    (:func:`run_button`; ``preview`` replaces Cinedex in tests).

    Never empty: TumbleTrove gives a button a right-click menu only when
    the catalog has items for it as the toolbar is built, which is before
    any batch has ended, and asks again at each right-click."""
    items = [(MENU_OPEN, lambda: open_manager(scene, session))]
    ended = [
        (label, lambda key=key, button=button: run_button(
            scene, session, key, button, preview=preview))
        for label, key, button in finished.menu_entries(session.reports())
    ]
    if ended:
        items += [(MENU_SEPARATOR, None)] + ended
    return items


def houdini_in_front() -> bool:
    """Whether Houdini is the application in front (its window active)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        return app is not None and app.applicationState() == Qt.ApplicationActive
    except Exception:
        return True


def houdini_window():
    """Houdini's main window, or None (no Houdini UI)."""
    try:
        import hou
        return hou.qt.mainWindow()
    except Exception:
        return None


def reduced_motion() -> bool:
    """Whether the artist asked for less motion (TumbleTrove's Reduce motion
    setting, or the OS's switch); False when it cannot be read."""
    try:
        from tumbletrove.asset_browser.ui import motion_host
        return bool(motion_host.reduced_motion())
    except Exception:
        return False


def on_main_thread() -> bool:
    """Whether this is the Qt application's own thread."""
    try:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        return app is not None and QThread.currentThread() is app.thread()
    except Exception:
        return False


def _alert(window) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.alert(window, 0)


def _hop(func) -> None:
    from tumblepipe.pipe.houdini.ui.report import run_on_main_thread
    run_on_main_thread(func)


def flash_taskbar(*, reduced=reduced_motion, window=houdini_window, alert=_alert,
                  main=on_main_thread, hop=_hop) -> str:
    """Flash Houdini's taskbar button (the dock icon bounces on macOS) until
    Houdini is activated, on the screen Houdini is on: the OS notification
    shows on the main display only, and an artist working on another screen
    saw nothing but a sound.

    Returns '' when the flash was asked for (or handed to the main thread:
    ``QApplication.alert`` is Qt, so only the main thread may call it), else
    why not: Reduce motion is on, or there is no Houdini window. Never
    raises.
    """
    try:
        if reduced():
            return "reduce motion is on"
        if not main():
            hop(lambda: flash_taskbar(reduced=reduced, window=window, alert=alert,
                                      main=main, hop=hop))
            return ""
        target = window()
        if target is None:
            return "no Houdini window"
        alert(target)
        return ""
    except Exception as error:
        log.debug("Farm jobs: the taskbar flash failed", exc_info=True)
        return f"failed: {error}"


def notify_folder(folder: str, is_dir=os.path.isdir) -> str:
    """The folder the OS notification opens on a click: ``folder`` when it
    is on disk, else '' (a click then only dismisses it)."""
    try:
        return folder if folder and is_dir(folder) else ""
    except Exception:
        return ""


def _notify_os(title: str, body: str, folder: str = "") -> None:
    from tumblepipe.util import desktop_notify
    why = desktop_notify.notify(title, body, notify_folder(folder))
    if why:
        log.info("Farm jobs: no desktop notification (%s)", why)


def announce(catalog, reports: list, *, in_front=houdini_in_front,
             notify=_notify_os, status=None,
             flash=flash_taskbar) -> finished.Announcement | None:
    """Say on the GUI thread that ``reports`` ended: the status line, the
    OS notification and the taskbar flash when Houdini is not in front, and
    a pulse request.
    Returns what was said (for tests); ``status(line, failed)`` replaces
    Houdini's status bar in tests."""
    said = finished.announcement(reports)
    if said is None:
        return None
    try:
        (status or _status_bar)(said.line, said.failed)
    except Exception:
        log.debug("Farm jobs: the status line failed", exc_info=True)
    if not in_front():
        why = flash()
        if why:
            log.debug("Farm jobs: no taskbar flash (%s)", why)
        threading.Thread(
            target=notify, args=(said.title, said.line, said.folder),
            name="tumblepipe-farm-notify", daemon=True,
        ).start()
    request = getattr(catalog, "request_quick_action_pulse", None)
    if callable(request):
        request(ACTION_ID)
    return said


def _status_bar(line: str, failed: bool) -> None:
    import hou
    severity = hou.severityType.Warning if failed else hou.severityType.Message
    hou.ui.setStatusMessage(line, severity=severity)


def announcer(catalog):
    """The farm watcher's ``on_finished``: hop to the GUI thread and
    :func:`announce` there. Nothing in hython, which has no UI."""
    def on_finished(reports: list) -> None:
        try:
            import hou
            if not hou.isUIAvailable():
                return
        except ImportError:
            return
        from tumblepipe.pipe.houdini.ui.report import run_on_main_thread
        run_on_main_thread(lambda: announce(catalog, list(reports)))
    return on_finished


def run_button(scene, session: finished.SessionWatch, row_id: str, button_id: str,
               *, preview=None) -> str:
    """An ended batch's action: *Preview in Cinedex* opens the report's frames
    in Cinedex, *Show in Jobs* opens the Farm Manager with its job selected
    (a failed batch's failed job, its error in view). Returns what was done;
    raises when Cinedex cannot open (the browser says so)."""
    report = session.report(row_id)
    if report is None:
        return ""
    if button_id == finished.PREVIEW and report.preview_folder:
        if preview is None:
            from tumblepipe.pipe import review_playlist
            preview = review_playlist.open_preview_frames
        preview(Path(report.preview_folder))
        return finished.PREVIEW
    if button_id == finished.SHOW:
        scene.open_farm_manager(select=report.select_id)
        return finished.SHOW
    return ""
