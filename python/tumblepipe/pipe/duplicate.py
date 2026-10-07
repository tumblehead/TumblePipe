"""Duplicate an asset under a new name: what to copy, and how paths change.

Asked for by an external user who made variations of an asset by copying
its folder in Explorer: the copy's workfiles kept a ``context.json`` naming
the original, and their nodes kept typing its prim path (``/CHAR/Pengo``),
so publishing from the copy overwrote the original.

The Asset Browser's Duplicate creates the new entity and hands the latest
finished workfile of each department to a hython worker
(``pipe.houdini.duplicate_workfiles``), which saves it as the new entity's
first version, points its nodes at the new workfile, and rewrites the old
prim path where the artist typed it. Nothing is published: the copy is
re-published from its own workfiles.

The parts here need no Houdini.
"""

import re
from pathlib import Path

from tumblepipe.util.uri import Uri

# A prim path segment is word characters, and the pipeline's names add '-'
# and '.'. The old prefix only matches where it is a whole path: not inside
# a longer name (/CHAR/Pengo_02) or below another prim (/X/CHAR/Pengo).
_NAME = r'[\w.\-]'


def prim_path_for(entity_uri: Uri) -> str:
    """The entity's root prim path: the URI without its context segment."""
    return '/' + '/'.join(entity_uri.segments[1:])


def replace_prim_prefix(text: str, old: str, new: str) -> str:
    """``text`` with every whole-path occurrence of ``old`` turned into ``new``.

    ``/CHAR/Pengo``, ``/CHAR/Pengo/geo`` and ``%/CHAR/Pengo/geo/*`` change;
    ``/CHAR/Pengo_02``, ``/X/CHAR/Pengo`` and ``CHAR/Pengo`` do not.
    """
    if not old or old == new:
        return text
    pattern = re.compile(
        rf'(?<!{_NAME})(?<!/){re.escape(old)}(?!{_NAME})'
    )
    return pattern.sub(lambda _m: new, text)


def plan_duplicate(source_uri: Uri, department_names) -> list[tuple[str, Path]]:
    """``(department, workfile)`` for each department the copy starts from.

    The latest finished workfile of each department the source has its own
    workfile in. A department whose workfile belongs to a Multi is skipped:
    that scene is shared with the Multi's other members, not the asset's.
    """
    from tumblepipe.pipe.paths import (
        latest_complete_hip_file_path,
        resolve_workfile_uri,
    )

    plan = []
    for department_name in department_names:
        if resolve_workfile_uri(source_uri, department_name) != source_uri:
            continue
        hip_path = latest_complete_hip_file_path(source_uri, department_name)
        if hip_path is not None:
            plan.append((department_name, hip_path))
    return plan
