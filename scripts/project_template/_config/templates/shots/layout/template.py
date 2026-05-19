from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.lops import (
    import_shot,
    export_layer
)

def _create_entity(scene_node, entity_uri: Uri, department_name: str):

    # Create the import node
    import_node = import_shot.create(scene_node, 'import_shot')
    prev_node = import_node.native()

    # Create the edit node for layout assets
    edit_node = scene_node.createNode('edit', 'layout_assets')
    edit_node.setInput(0, prev_node)
    prev_node = edit_node

    # Create the camera node
    camera_node = scene_node.createNode('camera', 'render_camera')
    camera_node.parm('primpath').set('/cameras/render_camera')
    camera_node.setInput(0, prev_node)
    prev_node = camera_node

    # Create the export node
    export_node = export_layer.create(scene_node, 'export_layout')
    export_node.setInput(0, prev_node)

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    # Create per-member nodes
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])

        # Create the import node
        import_node = import_shot.create(scene_node, f'import_shot_{member_name}')
        import_node.set_shot_uri(member_uri)

        prev_node = import_node.native()

        # Create the edit node for layout assets
        edit_node = scene_node.createNode('edit', f'layout_assets_{member_name}')
        edit_node.setInput(0, prev_node)
        prev_node = edit_node

        # Create the camera node
        camera_node = scene_node.createNode('camera', f'render_camera_{member_name}')
        camera_node.parm('primpath').set('/cameras/render_camera')
        camera_node.setInput(0, prev_node)
        prev_node = camera_node

        # Create the export node
        export_node = export_layer.create(scene_node, f'export_layout_{member_name}')
        export_node.setInput(0, prev_node)
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
