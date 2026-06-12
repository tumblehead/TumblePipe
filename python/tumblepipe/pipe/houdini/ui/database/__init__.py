import logging
import traceback

from .database_window import DatabaseWindow

__all__ = ['DatabaseWindow', 'open_database_editor']

log = logging.getLogger(__name__)

# Singleton editor window, shared across every entry point (the shelf
# launcher and the asset-browser per-asset action) so we never spawn a
# second copy.
_window = None


def open_database_editor(uri=None, parent=None):
    """Open (or raise) the singleton Database Editor window.

    This is the single launch point for the editor — both the shelf
    tool and the asset-browser "Open in Database Editor…" action call
    here. ``uri`` optionally pre-selects an entity. Returns the window,
    or ``None`` if it could not be opened (the error is logged and shown
    to the user rather than swallowed silently).
    """
    global _window
    import hou

    if parent is None:
        parent = hou.qt.mainWindow()

    try:
        from tumblepipe.api import default_client

        w = _window
        # Reuse the window only if its underlying C++ object is still
        # alive. A closed/destroyed QMainWindow leaves a dangling Python
        # wrapper whose method calls raise RuntimeError — recreate then.
        alive = False
        if w is not None:
            try:
                w.isVisible()
                alive = True
            except RuntimeError:
                alive = False
        if not alive:
            w = DatabaseWindow(default_client(), parent=parent)
            _window = w

        if uri is not None:
            w.select_entity(uri)
        w.show()
        w.raise_()
        w.activateWindow()
        return w
    except Exception:
        log.exception("Failed to open Database Editor")
        try:
            hou.ui.displayMessage(
                "Failed to open the Database Editor:\n\n"
                + traceback.format_exc(),
                severity=hou.severityType.Error,
            )
        except Exception:
            pass
        return None
