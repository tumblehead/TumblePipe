"""Cinedex's Open in Houdini, answered by this Houdini session.

``pipe.houdini_link`` listens for the editors this session starts; this is
the opener it is registered with (``tumblepipe.startup``): it opens the
workfile the way the Asset Browser's Open does
(``WorkfileManager.open_for_editor``), with the browser's own catalog when
one is open, else one made for it.
"""

from __future__ import annotations

import weakref
from pathlib import Path

_live = None  # weakref to the catalog the browser made last
_own = None   # the catalog made here when the browser had none


def remember(catalog) -> None:
    """The browser made ``catalog`` (``factory.create_catalog``): opens
    from the editor go through it, so its cards show what they do."""
    global _live
    _live = weakref.ref(catalog)


def _catalog():
    global _own
    catalog = _live() if _live is not None else None
    if catalog is not None:
        return catalog
    if _own is None:
        from .factory import create_catalog
        _own = create_catalog()
    if _own is None:
        from tumblepipe.pipe.houdini_link import NotHere
        raise NotHere('this Houdini has no TumblePipe project')
    return _own


def open_workfile(path: str, entity: str, department: str) -> str:
    """Open ``entity``'s ``department`` workfile at ``path`` here, for the
    editor. On the main thread; answers ``pipe.houdini_link``'s outcome."""
    # The catalog's own WorkfileManager, read privately: a public attribute
    # on the catalog would be a hook TumbleTrove does not declare
    # (test_catalog_hook_contract).
    return _catalog()._workfiles.open_for_editor(Path(path), entity, department)


def register() -> None:
    """Answer the editors this session starts (graphical sessions only)."""
    from tumblepipe.pipe import houdini_link
    from tumblepipe.pipe.houdini.ui.report import run_on_main_thread
    houdini_link.register(open_workfile, run_on_main_thread)
