from importlib.util import (spec_from_file_location, module_from_spec)
from importlib import reload
from pathlib import Path
import os
import json
import sys
import hou # type: ignore
from typing import Callable
import loputils # type: ignore

from tumblehead.api import default_client, fix_path, get_user_name
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.tools import utils

import radialmenu

api = default_client()

#region _load_module
def _load_module(module_path: Path):
    if not module_path.exists(): return None
    module_name = module_path.stem
    if module_name in sys.modules: 
        module = sys.modules[module_name]
        reload(module)
        return module
    spec = spec_from_file_location(module_name, module_path)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

#region _parse_radialmenu_definition
def _parse_radialmenu_definition(definition_path: Path, definition: dict | None, context: str):

    if definition is None: return None
    def _parse_script(script_type: str, script: str | dict):
        match script_type:
            case "script_submenu":
                return lambda **kwargs: _parse_radialmenu_definition(definition_path, script, context)
            case "script_action":
                action_type, action = script.split(": ", 1)
                match action_type:
                    case "expression": return action
                    case "node":
                        return lambda **kwargs: utils.place_and_create_node(
                        parent = utils.get_network_under_cursor(),
                        type = action,
                        name = utils.get_nodetype_from_names(context, action).description())
                    case "recipe":
                        return lambda **kwargs: utils.create_recipe(
                            recipe = action,
                            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor),
                            autoplace = False
                        )
                    # case "module":
                    #     module_name, func_name = action.split('.', 1) 
                    #     module_path = definition_path / f'{module_name}.py' 
                    #     module = _load_module(module_path)
                    #     assert module is not None, f'Module Path Expected: {module_path}'
                    #     return getattr(module, func_name)
                    case _:
                        assert False, f'Unknow Action Type: {action_type}'
            case _:
                assert False, f'Unknown Script Type: {script_type}'

    def _parse_item(item: dict[str, str]):
        result = dict()
        result["type"] = item["type"]
        result["label"] = item["label"]
        result["script"] = _parse_script(item["type"], item["script"])
        result["shortcut"] = item.get('shortcut', None)
        result["icon"] = item.get('icon', None)
        result["check"] = item.get('check', False)
        return result

    return radialmenu.setRadialMenu({
        location: _parse_item(item)
        for location, item in definition.items()
    })

#region load_radial_menu
def load_radial_menu(relative_path: list[str]) -> dict[str, dict[str, str]] | None:
    assert len(relative_path) > 0
    _relative_path = "/".join(map(
        lambda part: part.lower(),
        relative_path[:-1]
    ))
    radialmenu_path = api.storage.resolve(
        'pipeline:/houdini/Tumblehead/radialmenu/definitions/' 
        f'{_relative_path}/'
        f'{relative_path[-1].lower()}.json'
    )
    if not radialmenu_path.exists(): return None
    return _parse_radialmenu_definition(radialmenu_path.parent, load_json(radialmenu_path), radialmenu_path.stem)

#TODO: rotate direction based on the submenu direction

def _build_menu_item_radial(menu: hou.Parm, directions: list = ['n', 'e', 'w', 's', 'ne', 'se', 'sw', 'nw']):
    item_menu = {}
    for i, item in enumerate(menu.menuItems()):
        if i >= 8: continue
        item_menu[directions[i]] = {
            'type': 'script_action',
            'label': item,
            'script': lambda n=menu, val=item, **kwargs: n.set(val),
            'check': lambda n=menu, val=item, **kwargs: True if val == menu.eval() else False
        }
    return item_menu

#region _create_filtered_list
def _create_filtered_list(list: list, filter: list) -> list:
    filtered_list = []
    for i, item in enumerate(list):
        if item.name() in filter:
            filtered_list.append(item)
    return filtered_list

#region create_radial_from_list
def create_radial_from_list(
        items: list,
        type: str,
        directions: list[str],
        icon: str,
        script: Callable,
        check: Callable,
        **kwargs) -> dict:
    
    entries = {}
    for i, item in enumerate(items):
        entries[directions[i]] = {
            'type': f'script_{type}',
            'label': item.description(),
            'script': lambda item=item, **kwargs: script(item=item, kwargs=kwargs),
            'icon': icon,
            'check': lambda item=item, **kwargs: check(item=item),
        }

    return entries

#region build_recent_menu
def build_recent_menu(network: hou.Node):
    network_category = network.childTypeCategory()
    
    recent_nodes = utils.get_recent_node_types(network_category.name())
    directions = ['n', 'e', 's', 'w', 'ne', 'se', 'sw', 'nw']

    entries = {}
    for i, node in enumerate(recent_nodes):
        icon = utils.get_icon_from_type(network_category.name(), node)
        entries[directions[i]] = {
            'type': 'script_action',
            'label': hou.nodeType(network_category, node).description(),
            'shortcut': str(i+1),
            'icon': icon,
            'script': lambda n=node, **kwargs: utils.place_and_create_node(network, n, utils.make_name_valid(n))
        }

    radialmenu.setRadialMenu(entries)

#region build_menu_radial
def build_menu_radial(node: hou.Node) -> dict:

    menu_parm_filter = ["category", "asset", "department", "sequence", "shot"]
    button_parm_filter = ["import", "export", "open_location", "latest"]
    toggle_parm_filter = ["include_procedurals", "include_downstream_departments", "include_layerbreak"]
    path_parm_filter = ["pathprefix", "primpattern"]
    
    menu_directions = ['nw', 'n', 'ne', 'e', 'ne', 'se', 'sw', 'nw']
    button_directions = ['e', 'se', 's', 'sw', 'ne', 'se', 'sw', 'nw']
    path_directions = ["s", "sw", 'w', 'nw', 'n', 'ne', 'e', 'se']
    toggle_directions = ["sw", "w", 'nw', 'nw', 'n', 'ne', 'e', 'se']

    menu_parms = _create_filtered_list(node.parms(), menu_parm_filter)
    button_parms = _create_filtered_list(node.parms(), button_parm_filter)
    toggle_parms = _create_filtered_list(node.parms(), toggle_parm_filter)
    path_parms = _create_filtered_list(node.parms(), path_parm_filter)

    menu_entries = create_radial_from_list(
        menu_parms,
        'submenu', 
        menu_directions,
        "", 
        lambda item, **kwargs: radialmenu.setRadialMenu(_build_menu_item_radial(item)),
        lambda: False
    )
    button_entries = create_radial_from_list(
        button_parms,
        'action',
        button_directions,
        'DATATYPES_button',
        lambda item, **kwargs: item.pressButton(),
        lambda item: False
    )
    toggle_entries = create_radial_from_list(
        toggle_parms,
        'action',
        toggle_directions,
        'DATATYPES_boolean',
        lambda item, **kwargs: item.set(1-item.eval()),
        lambda item, **kwargs: item.eval()
    )
    path_entries = create_radial_from_list(
        path_parms,
        'action',
        path_directions,
        'BUTTONS_reselect',
        lambda item, kwargs: loputils.selectPrimsInParm(
            kwargs = {'parmtuple': (item,)}, 
            multisel=True, 
            forcepickerwindow=True),
        lambda item: False
    )

    combined_entries = {**menu_entries, **button_entries, **path_entries, **toggle_entries}

    radialmenu.setRadialMenu(combined_entries)

#region get_favorite_path
def get_favorites_path() -> Path:
    user = get_user_name()
    FAVORITES_PATH = fix_path(api.storage.resolve(Uri.parse_unsafe("temp:/")) /f"{user}_faves.json")
    return FAVORITES_PATH

#region clear favorits
def clear_category_favorites(category: str):
    if not hou.ui.displayConfirmation(f"Clear {category} favorites?"): return

    favorites_path = get_favorites_path()

    if os.path.exists(favorites_path):
        with open(favorites_path, "r") as f:
            data = json.load(f)
    else:
        data = []

    # Clear Current Network Category Favorites
    data = [entry for entry in data if entry["category"] != category]

    with open(favorites_path, "w") as f:
            json.dump(data, f, indent=4)

    hou.ui.setStatusMessage(f"Cleared {category} favorites")

#region add_node_to_favorites
def favorite_node(node: hou.Node, direction: str):
    if node == None: return
    favorites_path = get_favorites_path()
    try:
        node_data = {
            "category": node.type().category().name(),
            "type": node.type().name(),
            "direction": direction
        }
         
        if os.path.exists(favorites_path):
            with open(favorites_path, "r") as f:
                data = json.load(f)
        else:
            data = []

        data = [entry for entry in data if entry["type"] != node_data["type"]]
            
        data.append(node_data)

        with open(favorites_path, "w") as f:
            json.dump(data, f, indent=4)

        hou.ui.setStatusMessage(f"Added {node.type().description()} to favorites")

    except Exception as e:
        print(f"Error Adding node to favorites: {e}")

#region unfavorite_node
def unfavorite_node(node: hou.Node | str, direction: str = None):
    if node == None: return
    favorites_path = get_favorites_path()
    try:
        if isinstance(node, hou.Node):
            node_data = {
                "type": node.type().name(),
                "direction": direction
            }
        else:
            node_data = {
                "type": node,
                "direction": direction
            }

        if os.path.exists(favorites_path):
            with open(favorites_path, "r") as f:
                data = json.load(f)
        else:
            data = []

        if direction is not None:
            data = [
                entry for entry in data
                if not (entry["type"] == node_data["type"] and entry.get("direction") == direction)
            ]
        else:
            data = [
                entry for entry in data
                if entry["type"] != node_data["type"]
            ]

        with open(favorites_path, "w") as f:
            json.dump(data, f, indent=4)

        node_type = node_data["type"]
        hou.ui.setStatusMessage(f"Removed {node_type} from favorites")
        
    except Exception as e:
        print(f"Error Removing node from favorites: {e}")

#region get_favorite_node_types
def get_favorite_node_types(category: str) -> list[str]:
    favorites_path = get_favorites_path()
    if os.path.exists(favorites_path):
        with open(favorites_path, "r") as f:
            data = json.load(f)
    else:
        data = []
    return [(entry["type"], entry.get("direction"))
        for entry in data if entry["category"] == category
    ]

#region build_unfavorite_menu
def build_unfavorite_menu(network: hou.Node):
    network_category = network.childTypeCategory()
    
    favorites = get_favorite_node_types(network_category.name())

    entries = {}
    for node, dir in favorites:
        if dir:
            icon = utils.get_icon_from_type(network_category.name(), node)
            entries[dir] = {
                'type': 'script_action',
                'label': "Unfavorite " + hou.nodeType(network_category, node).description(),
                'icon': icon,
                'script': lambda d=dir, n=node, **kwargs: unfavorite_node(n, d)
        }

    radialmenu.setRadialMenu(entries)

#region build_favorites_menu
def build_favorites_menu(network: hou.Node):
    network_category = network.childTypeCategory()
    
    favorites = get_favorite_node_types(network_category.name())
    directions = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw']

    entries = {}
    for node, dir in favorites:
        if dir:
            icon = utils.get_icon_from_type(network_category.name(), node)
            entries[dir] = {
                'type': 'script_action',
                'label': hou.nodeType(network_category, node).description(),
                'icon': icon,
                'script': lambda n=node, **kwargs: utils.place_and_create_node(
                    network,
                    n,
                    hou.nodeType(network_category, n).nameComponents()[2]
            )
        }

    favorite_node_types = [node[0] for node in favorites]
    selected_node_types = [node.type().name() for node in hou.selectedNodes()]
    
    # Show Add Favorites if selected nodes don't contain a favorite already
    if hou.selectedNodes() and not any(node in selected_node_types for node in favorite_node_types):
        free_directions = [d for d in directions if d not in entries]
        for dir in free_directions:
            entries[dir] = {
                'type': 'script_action',
                'label': '',
                'icon': "BUTTONS_list_add",
                'script': lambda d=dir, **kwargs: 
                    favorite_node(hou.selectedNodes()[-1], d)
                    if hou.selectedNodes() else None
            }

    radialmenu.setRadialMenu(entries)

#
def node_is_favorite(node: hou.Node, network_category: hou.NodeTypeCategory) -> bool:
    favorites_node_types = get_favorite_node_types(network_category.name())
    return True if node.type().name() in favorites_node_types else False
 
#region build_category_menu
def build_department_menu(
        department: str,
        network_category: str) -> dict[str, dict[str, str]]:

    return load_radial_menu([network_category, department])

#region build_context_menu
def build_context_menu(network_category: str):
    # if len(hou.selectedNodes()) > 0:
        # return build_menu_radial(hou.selectedNodes()[-1])
    load_radial_menu([network_category])