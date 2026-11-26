"""Rig Sync: a character's keys move to its new name (X -> X0).

A character is named after its stage copies: one copy is ``X``, several
are ``X0``, ``X1`` .. (``sops.rig_sync``). When a layout duplicates an
asset that was animated alone, the character ``X`` becomes ``X0``, and the
keys a Scene Animate keeps for ``X`` would drive nothing. Rig Sync moves
them (``rig_sync.plan_stage_sync`` plans it as ``moved_keys``;
``animate_sync.move_keys`` applies it).

What a Scene Animate keeps of a character, in its ``animation`` parm
(packed folders, as ``lops.animate_keys`` reads them; measured in hython
22.0.508, 2026-11-26, and TumbleRig's ``copy-animation.md``):

- its keys: ``/animation/<clip>.clip/_X.char_Base``, one channel prim per
  rig parm, with a detail ``rig_path`` (``/X.char/Base.rig``);
- its keys on a layer: ``/animation/<clip>.clip/<layer>/_X.char_Base``
  (the layer's ``layer.data`` names no character);
- what Animate saved of the character's own scene data, when it changed
  it (a constraint tool, a full save): ``/X.char/...``;
- the detail ``removed_paths`` of the stored geometry: scene paths the
  artist removed in Animate (``/X.char/...`` among them).

A move takes all of these from ``X`` to ``X0``: each packed file goes to
its renamed path as it is (the channels untouched, bit for bit), the
entry's ``rig_path`` and the ``removed_paths`` entries are renamed, and the
old folders go. Nothing of another character changes. A Scene Animate
holding anything of ``X0`` already is never written (``blocked``): keys
are never overwritten, Rig Sync keeps ``X`` beside ``X0`` instead.

The planning (``rename_plan``) is plain values; ``rename_character``
applies it to any geometry with Houdini's packed-folder methods
(``extractPackedPaths``, ``packedFolderProperties``, ``unpackFromFolder``,
``packToFolder``, ``removeFromFolder``), which the tests stand in for.
"""

from __future__ import annotations

from dataclasses import dataclass

ANIMATION = '/animation'
#: The detail on a key entry naming its character's rig.
RIG_PATH = 'rig_path'
#: The detail on a Scene Animate's stored geometry: scene paths removed.
REMOVED_PATHS = 'removed_paths'
#: ``rename_character`` outcomes.
MOVED, NOTHING, BLOCKED = 'moved', 'nothing', 'blocked'


def _char_root(character: str) -> str:
    return f'/{character}.char'


def _entry_name(character: str) -> str:
    return f'_{character}.char_Base'


def _is_clip(path: str) -> bool:
    """``/animation/<name>.clip``."""
    if not path.startswith(ANIMATION + '/'):
        return False
    name = path[len(ANIMATION) + 1:]
    return bool(name) and '/' not in name and name.endswith('.clip')


def renamed_path(path: str, old: str, new: str) -> str | None:
    """``path`` as character ``new``'s when it is ``old``'s, else None.

    ``old``'s paths: ``/old.char`` and everything under it, and its key
    entry ``_old.char_Base`` straight in a clip or in a clip's layer
    folder (``/animation/<clip>.clip[/<layer>]/_old.char_Base``)."""
    root = _char_root(old)
    if path == root or path.startswith(root + '/'):
        return _char_root(new) + path[len(root):]
    folder, _, name = path.rpartition('/')
    if name != _entry_name(old):
        return None
    if _is_clip(folder) or _is_clip(folder.rpartition('/')[0]):
        return f'{folder}/{_entry_name(new)}'
    return None


def renamed_scene_path(value: str, old: str, new: str) -> str:
    """A scene path (a ``rig_path``, a ``removed_paths`` entry) under
    ``/old.char`` renamed to ``/new.char``; any other value as it is."""
    root = _char_root(old)
    if value == root or value.startswith(root + '/'):
        return _char_root(new) + value[len(root):]
    return value


@dataclass(frozen=True)
class RenamePlan:
    """How to move ``old``'s paths to ``new``: ``moves`` ``(src, dst,
    is_folder)``, each packed file (and each folder with nothing in it)
    to its renamed path; ``removes`` the topmost old paths, gone once
    moved. ``blocked``: ``new`` has paths here already, nothing moves."""
    moves: tuple = ()
    removes: tuple = ()
    blocked: bool = False

    @property
    def empty(self) -> bool:
        return not self.moves and not self.removes


def rename_plan(paths: dict, old: str, new: str) -> RenamePlan:
    """``RenamePlan`` for packed ``paths`` (path -> is it a folder).

    A plan with ``old == new``, or with no path of ``old``, is empty. Any
    path of ``new`` already there blocks it (nothing is overwritten)."""
    if old == new:
        return RenamePlan()
    mine = {path: renamed_path(path, old, new) for path in paths}
    mine = {path: dst for path, dst in mine.items() if dst is not None}
    if not mine:
        return RenamePlan()
    if any(renamed_path(path, new, new) is not None for path in paths):
        return RenamePlan(blocked=True)
    moves, removes = [], []
    for path in sorted(mine):
        parent = path.rpartition('/')[0]
        if parent not in mine:
            removes.append(path)
        if paths[path]:
            inside = [p for p in mine if p.startswith(path + '/')]
            if inside:
                continue  # its contents move, which makes the folder
        moves.append((path, mine[path], bool(paths[path])))
    return RenamePlan(moves=tuple(moves), removes=tuple(removes))


def renamed_list(values, old: str, new: str) -> tuple:
    """``renamed_scene_path`` of each of ``values``."""
    return tuple(renamed_scene_path(v, old, new) for v in values)


# --- applying it to a geometry --------------------------------------------------


def packed_paths(geo) -> dict:
    """Path -> is it a folder, for every packed path of ``geo`` but the
    root."""
    found = {}
    for path in geo.extractPackedPaths('**'):
        if path == '/':
            continue
        found[path] = bool(geo.packedFolderProperties(path).get('treat_as_folder'))
    return found


def _lists(geo, name: str, character: str) -> bool:
    """Whether ``geo``'s detail ``name`` names a path of ``character``."""
    if geo.findGlobalAttrib(name) is None:
        return False
    value = geo.attribValue(name)
    root = _char_root(character)
    values = (value,) if isinstance(value, str) else tuple(value)
    return any(v == root or v.startswith(root + '/') for v in values)


def _rename_detail(geo, name: str, old: str, new: str) -> bool:
    """Rename ``/old.char`` paths in ``geo``'s detail ``name`` (a string
    or a string array); whether it changed."""
    if geo.findGlobalAttrib(name) is None:
        return False
    value = geo.attribValue(name)
    if isinstance(value, str):
        renamed = renamed_scene_path(value, old, new)
    else:
        renamed = renamed_list(value, old, new)
        value = tuple(value)
    if renamed == value:
        return False
    geo.setGlobalAttribValue(name, list(renamed) if isinstance(renamed, tuple) else renamed)
    return True


def rename_character(geo, old: str, new: str) -> str:
    """Move everything ``geo`` (a Scene Animate's stored animation,
    editable) keeps of character ``old`` to ``new``, in place: ``MOVED``,
    ``NOTHING`` (it keeps nothing of ``old``) or ``BLOCKED`` (it keeps
    something of ``new``: nothing changed)."""
    if geo is None or old == new:
        return NOTHING
    paths = packed_paths(geo)
    plan = rename_plan(paths, old, new)
    if plan.blocked:
        return BLOCKED
    if plan.empty and _lists(geo, REMOVED_PATHS, old) and any(
            renamed_path(path, new, new) is not None for path in paths):
        return BLOCKED
    changed = False
    for src, dst, is_folder in plan.moves:
        visible = bool(geo.packedFolderProperties(src).get('visible', True))
        content = geo.unpackFromFolder(src)
        if not is_folder and _lists(content, RIG_PATH, old):
            # unpackFromFolder's geometry is read-only: an editable copy
            content = content.freeze()
            _rename_detail(content, RIG_PATH, old, new)
        geo.packToFolder(dst, content, is_folder, visible)
        changed = True
    for path in plan.removes:
        geo.removeFromFolder(path)
        changed = True
    if _rename_detail(geo, REMOVED_PATHS, old, new):
        changed = True
    return MOVED if changed else NOTHING


def holds_character(geo, character: str) -> bool:
    """Whether ``geo`` keeps any path of ``character``."""
    if geo is None:
        return False
    return any(renamed_path(path, character, character) is not None
               for path in packed_paths(geo))


# --- the rows that name a character ---------------------------------------------


def renamed_row(names, old: str, new: str) -> int | None:
    """The index (into ``names``, a list's characters in order) of the
    row to rename from ``old`` to ``new``: the first naming ``old``, when
    none names ``new`` already (a second row for one character is never
    made); None otherwise."""
    names = list(names)
    if new in names or old not in names:
        return None
    return names.index(old)
