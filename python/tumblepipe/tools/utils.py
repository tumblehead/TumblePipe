"""Network-editor helpers for the TumblePipe HDAs and startup menus.

Two entry points, and they are the whole surface:

- ``create_cop_reference`` — ``vop_th.triplanar_projection``'s
  ``script_action`` parm tag;
- ``create_recipe`` — ``tumblepipe.startup``.

``create_floating_network_editor`` and ``randomize_color`` are not called
from outside this module; they exist because ``create_cop_reference`` calls
them.

The rest of this module used to be reachable only from ``toolbar/*.shelf``,
which was never in the package allowlist — so those seven helpers shipped to
every artist with no way to invoke them. The shelves and the helpers went
together; see the commit that removed them for what was in there. The COP
helpers they duplicated survive in ``tools.coputils``, which the startup COP
context menu drives.
"""

import hou # type: ignore

#region create_cop_reference
def create_cop_reference(parm: str, kwargs):
    node = kwargs.get("node")
    stage = hou.node("/stage")

    cop_node = stage.node("material_maps")
    if cop_node is None:
        cop_node = stage.createNode("copnet", "material_maps")

    null = cop_node.node(f"OUT_{node.name()}")
    if null is None:
        null = cop_node.createNode("null", f"OUT_{node.name()}")

    cop_reference_path = f"op:{null.path()}"

    node.parm(parm).set(cop_reference_path)
    randomize_color([node, null], unify_items=True)

    create_floating_network_editor(cop_node.path())
    null.setSelected(True, clear_all_selected=True)
    null.moveToGoodPosition()

def create_floating_network_editor(path: str):
    network = hou.node(path)
    if not network: return
    desktop = hou.ui.curDesktop()

    panel = desktop.createFloatingPanel(hou.paneTabType.NetworkEditor, (400, 300))
    panel.setName(f"{path}")

    pane = panel.panes()[0]
    pane.showPaneTabsStow(False)

    tab = pane.tabs()[0]
    tab.setPwd(network)
    tab.setShowNetworkControls(False)
    tab.setPin(True)

#region randomize_color
def randomize_color(items: list[hou.NetworkMovableItem], unify_items: bool) -> None:
    import random
    if len(items) == 0: return
    if unify_items:
        r = random.random()
        g = random.random()
        b = random.random()
        for item in items:
            item.setColor(hou.Color(r, g, b))
        return
    for item in items:
       r = random.random()
       g = random.random()
       b = random.random()
       item.setColor(hou.Color(r, g, b))

#region create recipe
def create_recipe(recipe: str, **kwargs):
    selection = hou.selectedItems()
    node_data = hou.data.applyTabToolRecipe(name = recipe, kwargs = kwargs)
    if not node_data: return
    central_node = node_data["objects"]["central_node"]
    if len(selection) > 0 and central_node:
         selection = hou.selectedItems()[0]
         central_node.setInput(0, selection)
