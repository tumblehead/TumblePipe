import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.lops import import_shot, export_layer

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
IMPORT_POS = hou.Vector2(0.0, 0.63)
ENVIRONMENT_LIGHT_POS = hou.Vector2(0.02, -0.89)
KEY_LIGHT_POS = hou.Vector2(0.02, -2.53)
LIGHT_LINKER_POS = hou.Vector2(0.0, -4.01)
EXPORT_POS = hou.Vector2(0.0, -5.5)
COLUMN_STRIDE = 4.0

KEY_LIGHT_TYPE = 'UsdLuxRectLight'

def _hide_descriptive_name(node):
    """Department convention: node names read on their own, no suffix."""
    node.setGenericFlag(hou.nodeFlag.DisplayDescriptiveName, False)

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one shot's import -> lights -> linker -> export column."""
    # Create the import node (shot resolves from context)
    import_node = import_shot.create(scene_node, f'import_shot{suffix}')
    if pin:
        import_node.set_shot_uri(entity_uri)
        import_node.set_department_name(department_name)

    import_native = import_node.native()
    import_native.setPosition(IMPORT_POS + offset)
    _hide_descriptive_name(import_native)
    prev_node = import_native

    # Dome light for ambient/environment fill
    environment_light_node = scene_node.createNode(
        'domelight::3.0', f'environment_light{suffix}'
    )
    environment_light_node.setInput(0, prev_node)
    environment_light_node.setPosition(ENVIRONMENT_LIGHT_POS + offset)
    _hide_descriptive_name(environment_light_node)
    prev_node = environment_light_node

    # Rect key light to get started
    key_light_node = scene_node.createNode('light::2.0', f'key_light{suffix}')
    key_light_node.parm('lighttype').set(KEY_LIGHT_TYPE)
    key_light_node.setInput(0, prev_node)
    key_light_node.setPosition(KEY_LIGHT_POS + offset)
    _hide_descriptive_name(key_light_node)
    prev_node = key_light_node

    # Light linker for per-light include/exclude sets
    light_linker_node = scene_node.createNode(
        'lightlinker', f'light_linker{suffix}'
    )
    light_linker_node.setInput(0, prev_node)
    light_linker_node.setPosition(LIGHT_LINKER_POS + offset)
    _hide_descriptive_name(light_linker_node)
    prev_node = light_linker_node

    # Create the export node
    export_node = export_layer.create(scene_node, f'export_shot{suffix}')
    export_node.setInput(0, prev_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_native = export_node.native()
    export_native.setPosition(EXPORT_POS + offset)
    _hide_descriptive_name(export_native)
    export_native.setDisplayFlag(True)

    return export_node

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    _build(scene_node, entity_uri, department_name)

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for i, member_uri in enumerate(group.members):
        member_name = '_'.join(member_uri.segments[1:])
        _build(
            scene_node, member_uri, department_name,
            suffix=f'_{member_name}',
            offset=hou.Vector2(i * COLUMN_STRIDE, 0.0),
            pin=True,
        )

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
