"""Whether the open scene is untitled: a scene with no saved file behind it.

Every "is this a saved workfile?" check asked ``hou.hipFile.isNewFile()``,
but that flag lies for a scene Houdini opened from its command line when
the load ended with errors (missing node types): the scene IS the workfile
on disk, ``hou.hipFile.path()`` names it, yet ``isNewFile()`` stays True,
through a reload of the same file too, until another file is loaded. A
clean startup load, or the same file loaded from an untitled scene,
reports False (Houdini 22.0.508, 2026-11-25). The Asset Browser's Shift
open and the department jump's Shift+click open workfiles exactly that
way (``launch.launch_houdini``: ``houdini <hip>``), so such a workfile
read as untitled: no department jump list, no autosave, no Save badge.

So the flag counts only when the path backs it up: a scene is untitled
when Houdini says it is new AND its path names no saved file (empty,
missing, or Houdini's own ``untitled`` name; TT Desktop keeps a real
``untitled.hip`` in its folder, so existing on disk alone is not enough).
"""

from __future__ import annotations

import os
import re
from pathlib import PurePath

_UNTITLED = re.compile(r"untitled\d*", re.IGNORECASE)


def untitled(path: str | None, is_new: bool, exists: bool) -> bool:
    """Whether a scene at *path* is untitled, given Houdini's
    ``isNewFile()`` (*is_new*) and whether *path* is a file on disk
    (*exists*). Pure."""
    if not path:
        return True
    if not is_new:
        return False
    return not exists or bool(_UNTITLED.fullmatch(PurePath(path).stem))


def scene_is_untitled() -> bool:
    """Whether the scene Houdini has open is untitled (:func:`untitled`):
    use it wherever ``hou.hipFile.isNewFile()`` stood for "no saved
    workfile"."""
    import hou

    path = hou.hipFile.path()
    is_new = hou.hipFile.isNewFile()
    return untitled(path, is_new, bool(path) and is_new and os.path.isfile(path))
