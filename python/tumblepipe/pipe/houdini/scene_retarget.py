"""Point a scene's pipeline nodes at the workfile it was just saved as.

New from Current saves the open scene as another entity's or department's
workfile. Nodes whose entity/department parm holds the 'from_context'
sentinel follow the new workfile on their own. A node with a *concrete*
value, one picked by hand or baked into an older scene, keeps addressing the
old workfile. An export then publishes over the entity or department the
scene came from, and an import keeps loading the old entity.

A concrete value equal to the old workfile's own entity (or department)
meant "this workfile", so it becomes 'from_context'. Anything else is a
deliberate reference to another entity and is left alone. A department is
only retargeted on a node that addresses the workfile's own entity: an
import of another asset's 'lookdev' stays on 'lookdev'.

Only parms whose HDA default is the sentinel are touched, so a parm that
cannot hold it (the LOP playblast's department, import_model's fixed
'model') is never set to it. scripts/verify_entity_from_context.py keeps
those defaults honest.
"""

from __future__ import annotations

import importlib
import logging
from typing import Mapping, Optional

from tumblepipe.pipe.houdini import scene_imports

logger = logging.getLogger(__name__)

SENTINEL = "from_context"
ENTITY_PARMS = ("entity", "asset", "shot")
DEPARTMENT_PARM = "department"

# (node type, category) -> "module:Class" whose no-argument
# _update_labels() rewrites the node's 'from_context: <uri>' labels. Import
# nodes re-executed after a retarget refresh their own labels.
LABELLED = {
    ("export_layer", "Lop"):
        "tumblepipe.pipe.houdini.lops.export_layer:ExportLayer",
    ("import_layer", "Lop"):
        "tumblepipe.pipe.houdini.lops.import_layer:ImportLayer",
    ("import_asset", "Lop"):
        "tumblepipe.pipe.houdini.lops.import_asset:ImportAsset",
    ("import_shot", "Lop"):
        "tumblepipe.pipe.houdini.lops.import_shot:ImportShot",
    ("layer_split", "Lop"):
        "tumblepipe.pipe.houdini.lops.layer_split:LayerSplit",
    ("cache", "Lop"):
        "tumblepipe.pipe.houdini.lops.cache:Cache",
    ("create_model", "Lop"):
        "tumblepipe.pipe.houdini.lops.create_model:CreateModel",
    ("cache", "Sop"):
        "tumblepipe.pipe.houdini.sops.cache:Cache",
    ("import_rig", "Sop"):
        "tumblepipe.pipe.houdini.sops.import_rig:ImportRig",
    ("playblast", "Sop"):
        "tumblepipe.pipe.houdini.sops.playblast:Playblast",
    ("import_lop_camera", "Cop"):
        "tumblepipe.pipe.houdini.cops.import_lop_camera:ImportLopCamera",
}


def retarget_parms(
    parms: Mapping[str, tuple[str, str]],
    old_entity: str, old_department: str,
    new_entity: str, new_department: str,
) -> dict[str, str]:
    """The parm changes for one node, as ``{name: new value}``.

    ``parms`` maps each of the node's entity/department parm names to
    ``(value, default)``.
    """
    changes = {}
    addresses_own_entity = True
    for name in ENTITY_PARMS:
        if name not in parms:
            continue
        value, default = parms[name]
        if value == SENTINEL:
            continue
        if (default == SENTINEL and value == old_entity
                and old_entity != new_entity):
            changes[name] = SENTINEL
        elif value != new_entity:
            addresses_own_entity = False

    if DEPARTMENT_PARM in parms and addresses_own_entity:
        value, default = parms[DEPARTMENT_PARM]
        if (default == SENTINEL and value == old_department
                and old_department != new_department):
            changes[DEPARTMENT_PARM] = SENTINEL
    return changes


def _type_key(native) -> Optional[tuple[str, str]]:
    parts = native.type().name().lower().split("::")
    if len(parts) < 2 or parts[0] != "th":
        return None
    return (parts[1], native.type().category().name())


def _parm_state(native) -> dict[str, tuple[str, str]]:
    state = {}
    for name in ENTITY_PARMS + (DEPARTMENT_PARM,):
        parm = native.parm(name)
        if parm is None:
            continue
        template = parm.parmTemplate()
        # Menu string parms only; an expression or a key is somebody's
        # deliberate setup, not a baked value.
        if template.type().name() != "String" or parm.keyframes():
            continue
        defaults = template.defaultValue()
        state[name] = (parm.unexpandedString(), defaults[0] if defaults else "")
    return state


def _update_labels(native, key) -> None:
    target = LABELLED.get(key)
    if target is None:
        return
    module_name, class_name = target.split(":")
    wrapper = getattr(importlib.import_module(module_name), class_name)
    wrapper(native)._update_labels()


def retarget_scene(old_context, new_context) -> list[str]:
    """Retarget the open scene from ``old_context`` to ``new_context``
    (``tumblepipe.pipe.paths.Context``) and return the changed node paths.

    Retargeted import nodes are run again so the stage shows the new
    entity, and every labelled node's labels are rewritten, since a
    'from_context' label still names the old workfile. One failing node is
    logged and skipped.
    """
    import hou
    from tumblepipe import resolver

    old_entity, new_entity = (
        str(old_context.entity_uri), str(new_context.entity_uri))
    old_dept, new_dept = (
        old_context.department_name, new_context.department_name)

    changed = []
    labelled = []
    for native in hou.node("/").allSubChildren():
        key = _type_key(native)
        if key is None or native.isInsideLockedHDA():
            continue
        try:
            changes = retarget_parms(
                _parm_state(native), old_entity, old_dept,
                new_entity, new_dept,
            )
            for name, value in changes.items():
                native.parm(name).set(value)
        except Exception:
            logger.exception("Retarget: updating %s failed", native.path())
            continue
        if changes:
            changed.append(native.path())
            logger.info("Retarget: %s %s", native.path(), sorted(changes))
        if key in LABELLED:
            labelled.append((native, key))

    for native, key in labelled:
        try:
            _update_labels(native, key)
        except Exception:
            logger.exception("Retarget: labels of %s failed", native.path())

    if not changed:
        return changed
    # A retargeted outer HDA (SOP import_asset) imports through a node
    # inside it, so run the import nodes at or below each changed node.
    with resolver.deferred_refresh():
        for native, spec in scene_imports.find_import_nodes():
            if not any(within(native.path(), path) for path in changed):
                continue
            try:
                scene_imports.execute(native, spec)
            except Exception:
                logger.exception(
                    "Retarget: re-importing %s failed", native.path())
    return changed


def within(path: str, root: str) -> bool:
    """Whether node ``path`` is ``root`` or inside it."""
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")
