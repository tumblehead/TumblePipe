import shutil
import json
import os
from tumblehead.api import default_client, fix_path
from tumblehead.util.uri import Uri

from pathlib import Path

import nodegraphutils # type: ignore
import nodeutils # type: ignore
import hou # type: ignore

api = default_client()

RECENT_NODES_PATH = fix_path(api.storage.resolve(Uri.parse_unsafe("temp:/")) /"recent_nodes.json")
MAX_RECENTS = 8

#region make_name_valid
def make_name_valid(name: str) -> str:
    valid_name = name.lower()
    valid_name.replace(" ", "_")
    return valid_name

#region get_icon_from_type
def get_icon_from_type(category_name: str, type_name: str ) -> str:
    
    category_name = category_name.lower()
    category_map = {
        "sop": hou.sopNodeTypeCategory(),
        "lop": hou.lopNodeTypeCategory(),
        "object": hou.objNodeTypeCategory(),
        "cop": hou.copNodeTypeCategory(),
        "vop": hou.vopNodeTypeCategory(),
    }

    category = category_map.get(category_name)
    if not category: raise ValueError(f"Unknown category: {category_name}")
    
    node_type = hou.nodeType(category, type_name)
    if node_type is None: raise ValueError(f"Node type '{type_name}' not found in category '{category_name}'")
    
    return node_type.icon()

#region get_node_from_names
def get_nodetype_from_names(category_name: str, type_name: str ) -> hou.nodeType:
    
    if category_name == None: return
    if type_name == None: return
    
    category_name = category_name.lower()
    category_map = {
        "sop": hou.sopNodeTypeCategory(),
        "lop": hou.lopNodeTypeCategory(),
        "object": hou.objNodeTypeCategory(),
        "cop": hou.copNodeTypeCategory(),
        "vop": hou.vopNodeTypeCategory(),
    }

    category = category_map.get(category_name)
    node_type = hou.nodeType(category, type_name)

    return node_type

#region get_recent_node_types
def get_recent_node_types(network_category: str) -> list[str]:
    if os.path.exists(RECENT_NODES_PATH):
        with open(RECENT_NODES_PATH, "r") as f:
            data = json.load(f)
    else:
        data = []
    return [entry["type"] for entry in data if entry["category"] == network_category]
 
#region log_recent_node
def log_recent_node_type(node: hou.Node):
    try:
        node_data = {
            "category": node.type().category().name(),
            "type": node.type().name()
        }
        
        if os.path.exists(RECENT_NODES_PATH):
            with open(RECENT_NODES_PATH, "r") as f:
                data = json.load(f)
        else:
            data = [] 

        data = [entry for entry in data if entry["type"] != node_data["type"]]
            
        data.insert(0, node_data)
        data = data[:MAX_RECENTS]  # keep only latest N

        with open(RECENT_NODES_PATH, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error logging node: {e}")

def OnChildCreated(**kwargs):
    node = kwargs.get("child_node")
    if not node: return
    log_recent_node_type(node)

#region OnHipeFileEvent
def OnHipFileEvent(event_type):
    if event_type == hou.hipFileEventType.AfterSave:
        print("hipfile saved")

#region copy pane
def copy_tab_under_cursor():
    pane = hou.ui.paneTabUnderCursor()
    pane_copy = pane.clone()
    pane_copy.setShowNetworkControls(False)
    pane_copy.floatingPanel().panes()[0].showPaneTabsStow(False)
    pane_copy.setPin(True)
    
#region create_parmeter_panel
def create_parameter_panel(nodes: hou.Node):
    for node in nodes:
        hou.ui.showFloatingParameterEditor(node, False)

#region network under cursor
def get_network_under_cursor() -> hou.Node:
    pane = hou.ui.paneTabUnderCursor()
    network = pane.pwd()
    return network

#region get_recipes
def get_recipes() -> list[hou.HDADefinition]:
    recipe_file =  hou.expandString("$TH_PIPELINE_PATH/houdini/Tumblehead/otls/Recipes.hda")
    recipe_definitions = hou.hda.definitionsInFile(recipe_file)
    return recipe_definitions

#region jump_up
def jump_up(pane: hou.PaneTab):
    if not isinstance(pane, hou.NetworkEditor): return
    current = pane.pwd()
    parent = current.parent()
    if parent and parent.path() == '/': return
    while current.path() != '/':
        parent = current.parent()
        if parent is None:
            break
        if parent.isEditable():
            break
        else:
            current = parent
    pane.setPwd(parent)

#region get_editable_children
def get_editable_children(node: hou.Node, include_root: bool) -> list[hou.Node] | None:
    
    #if node is editable (for example a network)
    editable_children = []
    if include_root and node.isEditable():
        editable_children.append(node)
    
    subchildren = node.allSubChildren()
    for child in subchildren:
        if child.isEditable():
            editable_children.append(child)

    return editable_children if editable_children else None
            
#region jump_down
def jump_down(node: hou.Node, pane: hou.PaneTab):
    if not isinstance(pane, hou.NetworkEditor): return
    editable_children = get_editable_children(node, True)

    if editable_children:
        pane.setPwd(editable_children[0])

#region move_item_to_cursor
def move_item_to_cursor(item: hou.NetworkMovableItem):
    pane = hou.ui.paneTabUnderCursor()
    cursor_position = pane.cursorPosition()
    offset = hou.Vector2(.5, .2)
    item.setPosition(cursor_position - offset)

#region get_nodes_in_order
def get_items_in_order(nodes: list[hou.NetworkMovableItem], axis: str = "x", reverse: bool = False):
    if axis.lower() == "x":
        keyfunc = lambda n: n.position().x()
    elif axis.lower() == "y":
        keyfunc = lambda n: n.position().y()
    else:
        raise ValueError("Axis must be 'x' or 'y'.")
    return sorted(nodes, key=keyfunc, reverse=reverse)

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

#region merge_items
def merge_items(items: list[hou.NetworkMovableItem]):

    # Place merge node
    network_editor = hou.ui.paneTabUnderCursor()
    pos = network_editor.selectPosition()
    merge_node = items[0].parent().createNode('merge', 'merge') if items else get_network_under_cursor().createNode('merge', 'merge')
    merge_node.setPosition(pos)

    if not items:
        hou.ui.setStatusMessage("   No items selected to merge", severity=hou.severityType.Message)
        return

    # If context is LOPS, set merge style to separate layers
    if network_editor.pwd().childTypeCategory() == hou.lopNodeTypeCategory():
        merge_node.parm("mergestyle").set("seperate")

    # Merge nodes from left to right, skipping nodes without outputs
    ordered_items = get_items_in_order(items, axis="x")
    for i, item in enumerate(ordered_items):
        # Skip non-connectable items
        if not isinstance(item, (hou.Node, hou.OpNetworkDot)): continue
        if isinstance(item, hou.Node) and item.type().maxNumOutputs() == 0: continue
        
        merge_node.setInput(i, item)


# region expand_inputs
def expand_inputs(nodes: list[hou.node]) -> list[hou.Node]:
    if not nodes:
        hou.ui.setStatusMessage("   No items selected to expand", severity=hou.severityType.Message)
        return

    network = nodes[0].parent()
    null_nodes = list()

    for node in nodes:
        # Layout created Nulls together with the current node
        layout_nodes = list()
        layout_nodes.append(node)

        # Check if there is already a connection on this input port
        existing_connections = node.inputConnections()

        for i, input in enumerate(node.inputNames()):
            null = network.createNode("null", f"OUT_{input}")
            null.setColor(hou.Color(0,0,0))

            # Rewire each existing connection to go through the null
            for conn in existing_connections:
                target_node = conn.inputNode()
                target_output_index = conn.inputIndex()
                null.setInput(0, target_node, target_output_index)
            
            node.setInput(i, null, 0)

            null_nodes.append(null)
            layout_nodes.append(null)

        network.layoutChildren(items = layout_nodes)

    hou.clearAllSelected()
    for null_node in null_nodes:
        null_node.setSelected(True, clear_all_selected=False)

    return null_nodes

#region expand_outputs
def expand_outputs(nodes: list[hou.node]) -> list[hou.Node]:
    if not nodes: 
        hou.ui.setStatusMessage("   No items selected to expand", severity=hou.severityType.Message)
        return

    network = nodes[0].parent()
    null_nodes = list()

    for node in nodes:
        layout_nodes = list()
        layout_nodes.append(node)

        # Check if there is already a connection on this output port
        existing_connections = node.outputConnections()

        for i, output in enumerate(node.outputNames()):
            null = network.createNode("null", f"OUT_{output}")
            null.setColor(hou.Color(0,0,0))

            # Rewire each existing connection to go through the null
            for conn in existing_connections:
                target_node = conn.outputNode()
                target_input_index = conn.inputIndex()
                target_node.setInput(target_input_index, null)

            null.setInput(0, node, i)

            null_nodes.append(null)
            layout_nodes.append(null)

        network.layoutChildren(items = layout_nodes)

    hou.clearAllSelected()
    for null_node in null_nodes:
        null_node.setSelected(True, clear_all_selected=False)
        
    return null_nodes

#region set_vop_detail_level
def set_vop_detail_level(nodes: list[hou.Node], level: str):
    for node in nodes:
        if node.type().category().name().lower() != "vop": continue
        match level:
            case "high": node.setGenericFlag(hou.nodeFlag.InOutDetailHigh, True)
            case "med": node.setGenericFlag(hou.nodeFlag.InOutDetailMedium, True)
            case "low": node.setGenericFlag(hou.nodeFlag.InOutDetailLow, True)

#region network_box
def network_box(
        nodes: list[hou.Node], 
        color: hou.Color = None, 
        comment: str = None) -> list[hou.NetworkMovableItem]:
    
    if not nodes:
        pane = hou.ui.paneTabUnderCursor()
        network = pane.pwd()
        network_box = network.createNetworkBox()
        network_box.setColor(color)
        move_item_to_cursor(network_box)
        return
    
    network = nodes[0].parent()
    network_box_list = list()
    network_box = network.createNetworkBox()
    for node in nodes:
        network_box.addItem(node)
    network_box.fitAroundContents()
    network_box.setColor(color)
    network_box.setComment(comment)
    network_box_list.append(network_box)
    return network_box_list

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

#region add_note
def add_note(items: list[hou.NetworkMovableItem]) -> None:
    pane = hou.ui.paneTabUnderCursor()
    network = pane.pwd()
    
    # if no selectection: create stickynote at cursor
    if not items: 
        sticky = network.createStickyNote()
        move_item_to_cursor(sticky)
        return None

    # if selection: add note next to selected items
    for item in items:
        note_offset = hou.Vector2(-4, -.5)
        note_color = hou.Color(0,0,0)
        note_size = hou.Vector2(3, 1)
        text_color = hou.Color(.839,.839,.839) # This is the almost white swatch
        text_size = .3
        
        note = network.createStickyNote()
        
        note.setPosition(item.position() + note_offset)
        note.setTextColor(text_color)
        note.setSize(note_size)
        note.setColor(note_color)
        note.setTextSize(text_size)
        note.setSelected(True)
        note.setDrawBackground(False)
        hou.ui.waitUntil(lambda: False)  # UI refresh trick       

#region copy_image
def copy_image(image_path: str) -> str:
    poster_folder = Path(hou.hipFile.path()).parent / "posters"
    poster_folder.mkdir(parents=True, exist_ok=True)
    image_path = Path(image_path)
    destination = poster_folder / image_path.name
    if destination.exists():
        return str(destination)
    shutil.copy(image_path, destination)
    return str(destination)

# region thumbnail
def thumbnail(node: hou.Node):
    if not node:
        hou.ui.displayMessage("No Network Item selected")
        #TODO: add thumbnail under cursor 
        return
    
    desktop = hou.ui.curDesktop()
    editor = desktop.paneTabOfType(hou.paneTabType.NetworkEditor)
    scene_viewer = desktop.paneTabOfType(hou.paneTabType.SceneViewer)
    thumbnail_path = f"$HIP/thumbnails/{node.name()}.png"
    
    width, height = scene_viewer.curViewport().resolutionInPixels()
    #aspect_ratio = width / height

    thumbnail_resolution = (256, 256)
    aspect_ratio = 1

    # Get camera aspect ratio
    camera_path = scene_viewer.curViewport().cameraPath()
    if camera_path:
        camera_prim = node.stage().GetPrimAtPath(camera_path)
        horizontal_aperture = camera_prim.GetAttribute("horizontalAperture").Get()
        vertical_aperture = camera_prim.GetAttribute("verticalAperture").Get()
        aspect_ratio = horizontal_aperture / vertical_aperture

        # Get normalized image size
        image_size = hou.Vector2(aspect_ratio, 1) if aspect_ratio > 1 else hou.Vector2(1, aspect_ratio)

        thumbnail_resolution = image_size * 256

    # Set flipbook settings 
    flipbook_options = scene_viewer.flipbookSettings()
    flipbook_options.output(thumbnail_path)  # Empty means send to MPlay
    flipbook_options.frameRange((hou.frame(), hou.frame()))
    flipbook_options.resolution((int(thumbnail_resolution[0]), int(thumbnail_resolution[1])))
    scene_viewer.flipbook(scene_viewer.curViewport(), flipbook_options)

    # Add background image
    images = list(editor.backgroundImages())
    for image in images:
        if image.path() == thumbnail_path:
            return
        
    image = hou.NetworkImage(thumbnail_path, hou.BoundingRect(-5, -1.25, 1, 1))
    image.setRelativeToPath(node.path())
    images.append(image)

    editor.setBackgroundImages(images)
    nodegraphutils.saveBackgroundImages(editor.pwd(), editor.backgroundImages())
    nodegraphutils.loadBackgroundImages(editor.pwd())

    editor.redraw()

#region add_comment
def add_comment(nodes: list[hou.Node], comment: str) -> None:
    for node in nodes:
        node.setComment(comment)
        node.setGenericFlag(hou.nodeFlag.DisplayComment, True)

#region create_node_under_cursor
def place_and_create_node(parent: hou.Node, type: str, name: str) -> hou.Node | None:
    network_editor = hou.ui.paneTabUnderCursor()
    selected_node = hou.selectedNodes()[-1] if hou.selectedNodes() else None
    created_node = hou.nodeType(parent.childTypeCategory(), type)

    if selected_node and selected_node.type().maxNumInputs()  and created_node.maxNumInputs() > 0:
        pos = network_editor.selectPosition(selected_node, 0)
    else:
        pos = network_editor.selectPosition()
    
    created_node = parent.createNode(type, name, force_valid_node_name=True)
    created_node.setSelected(True, clear_all_selected=True)
    created_node.setPosition(pos)
    if not isinstance(created_node, hou.OpNode):
        created_node.setDisplayFlag(True)

    if selected_node and selected_node.type().maxNumInputs() > 0 and created_node.type().maxNumInputs() > 0:
        created_node.setInput(0, selected_node)

    return created_node

#region edit_comment
def edit_comment(node: hou.Node):
    nodeutils.show_comment_editor(node)
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    
#region create recipe
def create_recipe(recipe: str, **kwargs):
    selection = hou.selectedItems() # selection prior to recipe
    node_data = hou.data.applyTabToolRecipe(name = recipe, kwargs = kwargs)
    if not node_data: return
    central_node = node_data["objects"]["central_node"]
    if len(selection) > 0 and central_node:
         selection = hou.selectedItems()[0]
         central_node.setInput(0, selection)

#region snap_to_nearest_axis()
def snap_camera_to_nearest_axis(kwargs):
    scene_viewer = kwargs.get("pane")
    viewport = scene_viewer.curViewport()

    # Ensure we're in perspective (don't return early)
    if viewport.type() != hou.geometryViewportType.Perspective:
        viewport.changeType(hou.geometryViewportType.Perspective)
        return

    view_transform = viewport.viewTransform()
    rotation = view_transform.extractRotationMatrix3()

    # Normalized forward vector (-Z axis in view space)
    forward = -hou.Vector3(
        rotation.at(0, 2),
        rotation.at(1, 2),
        rotation.at(2, 2)
    ).normalized()

    axis_definitions = [
        ("front",  hou.Vector3(0, 0, -1), hou.geometryViewportType.Front),
        ("back",   hou.Vector3(0, 0, 1),  hou.geometryViewportType.Back),
        ("left",   hou.Vector3(-1, 0, 0), hou.geometryViewportType.Left),
        ("right",  hou.Vector3(1, 0, 0),  hou.geometryViewportType.Right),
        ("top",    hou.Vector3(0, 1, 0),  hou.geometryViewportType.Top),
        ("bottom", hou.Vector3(0, -1, 0), hou.geometryViewportType.Bottom),
    ]

    best_match = max(
        axis_definitions,
        key=lambda item: forward.dot(item[1])
    )

    direction_name, axis_vector, view_type = best_match
    
    if forward.dot(-hou.Vector3(0,0,1)) < 0:
        if view_type == hou.geometryViewportType.Bottom: view_type = hou.geometryViewportType.Top
        elif view_type == hou.geometryViewportType.Top: view_type = hou.geometryViewportType.Bottom

    viewport.changeType(view_type)
    hou.ui.setStatusMessage(f"Snapped to {direction_name.upper()} view")

#region snap_viewport_in_direction
def step_viewport_in_direction(direction: str, kwargs):
    scene_viewer = kwargs.get("pane")
    
    viewport = scene_viewer.curViewport()
    current_view = viewport.type()

    if current_view == hou.geometryViewportType.Perspective:
        snap_camera_to_nearest_axis(kwargs)

    # Define the navigation graph
    transition_map = {
        hou.geometryViewportType.Perspective: {},
        hou.geometryViewportType.Front: {
            "left": hou.geometryViewportType.Left,
            "right": hou.geometryViewportType.Right,
            "up": hou.geometryViewportType.Top,
            "down": hou.geometryViewportType.Bottom
        }, 
        hou.geometryViewportType.Back: {
            "left": hou.geometryViewportType.Right,
            "right": hou.geometryViewportType.Left,
            "up": hou.geometryViewportType.Top,
            "down": hou.geometryViewportType.Bottom
        },
        hou.geometryViewportType.Left: {
            "left": hou.geometryViewportType.Back,
            "right": hou.geometryViewportType.Front,
            "up": hou.geometryViewportType.Top,
            "down": hou.geometryViewportType.Bottom
        },
        hou.geometryViewportType.Right: {
            "left": hou.geometryViewportType.Front,
            "right": hou.geometryViewportType.Back,
            "up": hou.geometryViewportType.Top,
            "down": hou.geometryViewportType.Bottom
        },
        hou.geometryViewportType.Top: {
            "left": hou.geometryViewportType.Left,
            "right": hou.geometryViewportType.Right,
            "up": hou.geometryViewportType.Back,
            "down": hou.geometryViewportType.Front
        },
        hou.geometryViewportType.Bottom: {
            "left": hou.geometryViewportType.Left,
            "right": hou.geometryViewportType.Right,
            "up": hou.geometryViewportType.Front,
            "down": hou.geometryViewportType.Back
        }
    }

    # Find the next view in the given direction
    next_view = transition_map[current_view].get(direction)

    if next_view is not None:
        viewport.changeType(next_view)

#region create_fetch_node
def fetch_node(node: hou.Node) -> hou.Node:
    if node is None:
        hou.ui.setStatusMessage("   No node selected to fetch", severity=hou.severityType.Message)
        return
    
    # Fetch node for lops, cops, vops
    node_type = node.type().category()

    #TODO: Fetch node from clipboard

    # Create appropriate fetch node based on context
    if node_type in [hou.lopNodeTypeCategory(), hou.copNodeTypeCategory()]:
        fetch = place_and_create_node(node.parent(), "fetch", f"fetch_{node.name()}")
        fetch_parm_name = f"{node_type.name().lower()}path"
        fetch.parm(fetch_parm_name).set(node.path())
        fetch.setSelected(True, clear_all_selected=True)
        fetch.setColor(node.color())
        return fetch
    if node_type == hou.sopNodeTypeCategory():
        objmerge = place_and_create_node(node.parent(), "object_merge", f"objmerge_{node.name()}")
        objmerge.parm("objpath1").set(node.path())
        objmerge.setSelected(True, clear_all_selected=True)
        fetch.setColor(node.color())
        return objmerge
    
#region frame_selection
def frame_scene_selection(frame_all: bool = False):
    # Get the scene viewer under cursor. Return if not found
    sceneviewer = hou.ui.paneTabUnderCursor()
    if not isinstance(sceneviewer, hou.SceneViewer): return

    viewport_context = sceneviewer.pwd().childTypeCategory().name()

    if frame_all:
        sceneviewer.curViewport().frameAll()

    if viewport_context == "Lop":
        sceneviewer.curViewport().frameSelected()
    if viewport_context == "Sop":
        sceneviewer.curViewport().frameNonTemplated()