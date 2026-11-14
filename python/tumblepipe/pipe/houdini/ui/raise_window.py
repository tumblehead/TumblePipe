"""Bring Houdini's main window to the front, from another program's ask.

Cinedex's Open in Houdini loads the workfile in the Houdini that started it
(``pipe.houdini_link``); that Houdini comes to the front first, so its
unsaved-changes prompt is seen, not hidden behind the editor. On Windows a
background process may take the foreground only when the foreground one
lets it: the editor does (``AllowSetForegroundWindow``) just before it asks.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def raise_main_window() -> None:
    """Show Houdini's main window (restored, if it was minimised) in front
    and give it the keyboard. On the main thread. Never raises: a window
    left behind is no reason to fail the open."""
    try:
        import hou
        from PySide6.QtCore import Qt

        window = hou.qt.mainWindow()
        if window is None:
            return
        if window.isMinimized():
            window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
            window.show()
        window.raise_()
        window.activateWindow()
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(window.winId()))
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("Houdini's window could not come to the front")
