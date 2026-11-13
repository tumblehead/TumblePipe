"""The Farm Manager quick action's Jobs popover, dot, pulse and announcement.

When a batch the artist submitted in this Houdini session ends
(``farm.manager.finished``, fed by the farm watcher's thread):

- one line goes to Houdini's status bar, ``SQ010/SH020 render done: 48
  frames, 3 passes · 12 min on the farm`` (a warning when something failed);
- when Houdini is not the application in front, the same line goes to the
  OS as a desktop notification (``util.desktop_notify``: a line only, it
  has no buttons);
- the Farm Manager button asks TumbleTrove for one pulse, which TumbleTrove
  plays or refuses (hidden browser, reduced motion, Houdini cooking or
  playing back), and shows a dot at its foot, accent for finished, danger
  for failed, until the artist opens the popover.

A click on the button opens the popover, "Finished since you looked": the
session's ended batches, failures first, with *Preview in Cinedex* and
*Show in Jobs*. A double-click, or *Open Jobs* in its header, opens the
Farm Manager as the click used to.

The dot, the popover and the pulse need TumbleTrove 0.60.0
(``Catalog.get_quick_action_dot`` / ``get_quick_action_popover`` /
``request_quick_action_pulse``). With an older TumbleTrove the hooks are
never asked, the click opens the Farm Manager, and the status line and the
OS notification still go out.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from tumblepipe.farm.manager import finished

log = logging.getLogger(__name__)

#: The quick action the popover hangs on.
ACTION_ID = "farm_manager"


def popover_api():
    """TumbleTrove's popover values ``(QuickActionPopover, PopoverRow,
    PopoverButton)``, or None for a TumbleTrove without them (before 0.60.0)."""
    try:
        from tumbletrove.asset_browser.api.types import (
            PopoverButton, PopoverRow, QuickActionPopover,
        )
    except ImportError:
        return None
    return QuickActionPopover, PopoverRow, PopoverButton


def build_popover(spec: finished.PopoverSpec, api):
    """TumbleTrove's ``QuickActionPopover`` for ``spec``."""
    popover_cls, row_cls, button_cls = api
    rows = tuple(
        row_cls(
            row.id, row.title, icon=row.icon, tone=row.tone, caption=row.caption,
            line=row.line,
            buttons=tuple(button_cls(bid, label, primary=primary)
                          for bid, label, primary in row.buttons),
        )
        for row in spec.rows
    )
    return popover_cls(
        spec.title, link_label=spec.link_label, rows=rows,
        empty_text=spec.empty_text, footer=spec.footer,
    )


def popover(session: finished.SessionWatch):
    """The Jobs popover for the session's ended batches; None when this
    TumbleTrove has no popovers (the click then opens the Farm Manager)."""
    api = popover_api()
    if api is None:
        return None
    return build_popover(finished.popover_spec(session.reports()), api)


def houdini_in_front() -> bool:
    """Whether Houdini is the application in front (its window active)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        return app is not None and app.applicationState() == Qt.ApplicationActive
    except Exception:
        return True


def _notify_os(title: str, body: str) -> None:
    from tumblepipe.util import desktop_notify
    why = desktop_notify.notify(title, body)
    if why:
        log.info("Farm jobs: no desktop notification (%s)", why)


def announce(catalog, reports: list, *, in_front=houdini_in_front,
             notify=_notify_os, status=None) -> finished.Announcement | None:
    """Say on the GUI thread that ``reports`` ended: the status line, the
    OS notification when Houdini is not in front, and a pulse request.
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
        threading.Thread(
            target=notify, args=(said.title, said.line),
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
    """A popover row's button: *Preview in Cinedex* opens the report's frames
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
