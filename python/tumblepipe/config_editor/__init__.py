from .database_window import DatabaseWindow

__all__ = ['DatabaseWindow', 'open_database_editor']

# Singleton editor window, shared across every entry point (the shelf
# launcher and the asset-browser per-asset action) so we never spawn a
# second copy.
_window = None


def open_database_editor(uri=None, parent=None):
    """Open (or raise) the singleton Database Editor window.

    This is the single launch point for the editor — both the shelf
    tool and the asset-browser "Open in Database Editor…" action call
    here. ``uri`` optionally pre-selects an entity. Returns the window,
    or ``None`` if it could not be opened (the failure is reported to the
    artist).

    The window edits the *active* project: it is rebuilt when the project
    has been switched since it opened, because its deletes and renames go
    through the pipeline layer, which always works on the active project.
    """
    global _window
    import hou
    from tumblepipe.api import default_client
    from tumblepipe.pipe.houdini.ui.report import report_failure

    if parent is None:
        parent = hou.qt.mainWindow()

    try:
        client = default_client()
        w = _window
        # Reuse the window only if its underlying C++ object is still
        # alive and it edits the active project. A closed/destroyed
        # QMainWindow leaves a dangling Python wrapper whose method calls
        # raise RuntimeError — recreate then.
        alive = False
        if w is not None and w.client is client:
            try:
                w.isVisible()
                alive = True
            except RuntimeError:
                alive = False
        if not alive:
            if w is not None:
                try:
                    w.close()
                except RuntimeError:
                    pass  # its C++ object is already gone
            w = DatabaseWindow(client, parent=parent)
            _window = w

        if uri is not None:
            w.select_entity(uri)
        w.show()
        w.raise_()
        w.activateWindow()
        return w
    except Exception as exc:
        report_failure("Opening the Database Editor", exc)
        return None
