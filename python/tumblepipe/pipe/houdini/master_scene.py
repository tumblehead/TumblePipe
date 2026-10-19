"""An entity's master scene: every department's latest workfile in one hip.

``<entity>/master/<name>_master.hip`` merges each department's latest
workfile (the one Open would land on — a Multi's, where one covers the
department) in pipeline order, boxes each department's nodes in a labelled
network box and lays the boxes out left to right. A review scene: nothing
publishes from it.

Works on the active project, and on this session's scene: building one
replaces whatever is open.
"""

import logging
from pathlib import Path

import hou

from tumblepipe.config.department import list_departments
from tumblepipe.pipe.houdini.workfiles import latest_workfile
from tumblepipe.pipe.paths import entity_root_path
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)

# The networks a department's nodes can merge into.
_CONTAINERS = ("/obj", "/stage", "/out", "/shop", "/ch", "/tasks")
_BOX_GAP = 3.0


def master_scene_path(entity_uri: Uri) -> Path:
    """Where ``entity_uri``'s master scene is saved."""
    name = entity_uri.segments[-1]
    return entity_root_path(entity_uri) / "master" / f"{name}_master.hip"


def department_workfiles(entity_uri: Uri) -> list[tuple[str, Path]]:
    """``(department, latest workfile)`` in pipeline order, for every
    department of the entity's context that has one."""
    context = entity_uri.segments[0]
    workfiles = []
    for department in list_departments(context, include_generated=False):
        path = latest_workfile(entity_uri, department.name)
        if path is not None:
            workfiles.append((department.name, path))
    return workfiles


def _children() -> set[str]:
    paths = set()
    for container in _CONTAINERS:
        parent = hou.node(container)
        if parent is not None:
            paths.update(n.path() for n in parent.children())
    return paths


def _new_nodes(before: set[str]) -> dict[str, list]:
    """``{container: [nodes]}`` added since ``before``."""
    grouped: dict[str, list] = {}
    for container in _CONTAINERS:
        parent = hou.node(container)
        if parent is None:
            continue
        for node in parent.children():
            if node.path() not in before:
                grouped.setdefault(container, []).append(node)
    return grouped


def build_master_scene(
    master_hip: Path, workfiles: list[tuple[str, Path]], label: str,
) -> list[str]:
    """Replace this session's scene with the master scene and save it.

    Returns the departments whose workfile would not merge (an
    ``OperationFailed``): one department that will not merge must not cost
    the others, so they are skipped and named for the caller to report.
    """
    master_hip.parent.mkdir(parents=True, exist_ok=True)
    # Save a blank hip to the master path, then load it so Houdini fully
    # switches context (title bar, $HIP, ...).
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.hipFile.save(str(master_hip))
    hou.hipFile.load(
        str(master_hip), suppress_save_prompt=True, ignore_load_warnings=True,
    )

    failed: list[str] = []
    x_offset = 0.0
    with hou.undos.group(f"Generate master: {label}"):
        for department, hip_path in workfiles:
            before = _children()
            try:
                hou.hipFile.merge(
                    str(hip_path), node_pattern="*",
                    overwrite_on_conflict=False, ignore_load_warnings=True,
                )
            except hou.LoadWarning:
                # Merged, with warnings (a missing HDA, say): the
                # department's nodes are in, so it is boxed like the rest.
                pass
            except hou.OperationFailed:
                logger.exception("Failed to merge %s", hip_path)
                failed.append(department)
                continue
            grouped = _new_nodes(before)
            if not grouped:
                continue
            right = x_offset
            for container, nodes in grouped.items():
                parent = hou.node(container)
                min_x = min(n.position()[0] for n in nodes)
                min_y = min(n.position()[1] for n in nodes)
                offset = hou.Vector2(x_offset - min_x, -min_y)
                for node in nodes:
                    node.setPosition(node.position() + offset)
                box = parent.createNetworkBox()
                box.setComment(department.upper())
                box.setColor(hou.Color(0, 0, 0))
                for node in nodes:
                    box.addItem(node)
                box.fitAroundContents()
                right = max(right, box.position()[0] + box.size()[0])
            x_offset = right + _BOX_GAP

    hou.hipFile.save()
    for pane in hou.ui.paneTabs():
        if isinstance(pane, hou.NetworkEditor):
            pane.homeToSelection()
            break
    return failed
