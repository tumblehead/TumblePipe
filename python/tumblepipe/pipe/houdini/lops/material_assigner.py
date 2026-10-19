"""th::material_assigner: bind the stage's materials to prim patterns.

Each Material Assignments row names a material path (``matspecpath#``),
picked from a menu of the materials on the node's stage. The node has no
stage while nothing is wired in, or while its input fails to cook: the menu
is then empty, and the buttons that read materials refuse with an error.
"""

import hou
from pxr import UsdShade


def stage_materials(stage):
    """The Material prims on ``stage``, in traversal order; none without a stage."""
    if stage is None:
        return []
    return [prim for prim in stage.Traverse() if prim.IsA(UsdShade.Material)]


def material_menu(stage):
    """The ``matspecpath#`` menu: each material's path as token, name as label."""
    menu = []
    for material in stage_materials(stage):
        menu.append(str(material.GetPath()))
        menu.append(material.GetName())
    return menu


def _stage_or_error(node):
    stage = node.stage()
    if stage is None:
        raise hou.NodeError(
            f'{node.path()} has no stage to read materials from: '
            'wire in a stage that cooks')
    return stage


def init_materials(kwargs):
    """One assignment row per stage material; ctrl-click to choose which."""
    node = kwargs['node']
    materials = stage_materials(_stage_or_error(node))
    if kwargs.get('ctrlclick'):
        selection = hou.ui.selectFromList([material.GetName() for material in materials])
        materials = [materials[index] for index in selection]
    node.parm('assignments').set(len(materials))
    for index, material in enumerate(materials, 1):
        node.parm(f'matspecpath{index}').set(str(material.GetPath()))


def update_material_paths(kwargs):
    """Repoint each row at the stage material with the same name."""
    node = kwargs['node']
    by_name = {
        material.GetName(): str(material.GetPath())
        for material in stage_materials(_stage_or_error(node))
    }
    for index in range(1, node.evalParm('assignments') + 1):
        entry = node.parm(f'matspecpath{index}')
        name = entry.eval().split('/')[-1]
        if name in by_name:
            entry.set(by_name[name])
        else:
            print(f"[update_material_paths] Warning: '{name}' not found in stage materials")
