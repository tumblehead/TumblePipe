"""The department jump (Alt+D): which asset it opens, what happens to the
open scene's unsaved work first, and the icons its slices show.

Discord, Remy 2026-11-20: the jump radial should show the Asset Browser's
department icons, open on the same key everywhere in an asset department
(not only over a Solaris viewport with a stamped prim selected), and
optionally save without asking.

Everything here is pure: the Houdini side
(``pipe.houdini.selection_entities``, ``pipe.houdini.radial``) reads the
selection, the open workfile and the prefs, and asks these functions what
to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

#: What happens to the open scene before the jump loads another workfile.
LOAD = "load"   # nothing unsaved: load straight away
SAVE = "save"   # write the next workfile version, then load
ASK = "ask"     # leave it to Houdini's own save prompt

#: The version note a save before a jump writes starts with this, the note
#: the Save button's Autosave writes (``asset_browser.save_timer``).
AUTOSAVE_NOTE = "Autosave"


def is_asset(uri) -> bool:
    """Whether *uri* is an asset entity (``entity:/assets/...``)."""
    if uri is None:
        return False
    segments = tuple(uri.segments)
    return uri.purpose == "entity" and len(segments) >= 2 and segments[0] == "assets"


def jump_target(selected: Iterable, workfile):
    """The entity the jump opens a department of, or None.

    *selected* are the distinct entities of the LOP selection, *workfile*
    the entity of the open workfile (None when it is no pipeline workfile).

    A selection of one entity names it; a selection of several names none
    (which one is meant cannot be told). With nothing that names an entity
    selected, the open workfile's own asset is the target, so the jump works
    in a SOP model or rig workfile, where there is no stamped prim to
    select. A shot workfile is no fallback: its departments are not the
    asset departments the menu offers.
    """
    selected = list(selected)
    if selected:
        return selected[0] if len(selected) == 1 else None
    return workfile if is_asset(workfile) else None


def before_jump(unsaved: bool, in_pipeline: bool, save_without_asking: bool) -> str:
    """What to do with the open scene before the jump: :data:`LOAD`,
    :data:`SAVE` or :data:`ASK`.

    Only a pipeline workfile can be saved as its next version; an untitled
    or off-pipeline scene always gets Houdini's prompt, whatever the
    setting says, rather than a guess at where to write it.
    """
    if not unsaved:
        return LOAD
    if in_pipeline and save_without_asking:
        return SAVE
    return ASK


def jump_note(label: str) -> str:
    """The version note of a save before a jump to the department *label*
    (its slice label, MDL)."""
    label = str(label or "").strip()
    return f"{AUTOSAVE_NOTE} before opening {label}" if label else AUTOSAVE_NOTE


def radial_icon(spec: str, resolve: Callable[[str], "Path | None"]) -> str:
    """The Radial icon id for an Asset Browser icon *spec*.

    *spec* is what the Asset Browser's department table holds: a Lucide
    name (``shapes``) or the absolute path of an SVG this package ships
    (``package_icon``). *resolve* finds a name's SVG among the icons
    TumbleTrove bundles, None when it has none.

    Radial looks a ``lucide:<name>`` id up as ``<pool>/<name>.svg``, so an
    absolute path without its ``.svg`` names that file wherever the pools
    are, and it is tinted like any Lucide icon. A name nothing bundles is
    left as a plain Lucide name for Radial to find (or fetch) itself.
    """
    spec = str(spec or "").strip()
    if not spec:
        return ""
    path = Path(spec)
    if path.suffix.lower() == ".svg" and path.is_absolute():
        return "lucide:" + path.with_suffix("").as_posix()
    found = resolve(spec)
    if found is not None:
        return "lucide:" + Path(found).with_suffix("").as_posix()
    return "lucide:" + spec


def active_departments(
    departments: Iterable[str],
    has_work: Callable[[str], bool],
    turned_off: Iterable[str] = (),
    current: str | None = None,
) -> list[str]:
    """The departments of *departments* the jump offers, in the order
    given: those with a workfile (``has_work(name)``) that are not turned
    off on the entity (*turned_off*, #64).

    Discord, Remy 2026-11-22: the jump "should only show active
    departments", the ones the Asset Browser lights up on the entity's
    card. *current* is the open workfile's department when it is this
    entity's: it always stays, turned off or not, so the ring never hides
    the department you are in.
    """
    off = set(turned_off)
    return [
        name for name in departments
        if name == current or (name not in off and has_work(name))
    ]


def ring_departments(pool: Iterable[str], active: Iterable[str], slots: int) -> list[str]:
    """The departments the jump's ring shows, in pool order, at most
    *slots*: the active ones, or the whole pool (greyed by the ring) when
    the jump has no entity or its entity has no work yet, so the ring
    still says what it is for."""
    pool = list(pool)
    wanted = set(active)
    shown = [name for name in pool if name in wanted]
    return (shown or pool)[:max(slots, 0)]
