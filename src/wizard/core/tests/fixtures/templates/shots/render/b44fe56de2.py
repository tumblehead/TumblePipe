from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.lops import (
    import_shot,
    puzzle_mattes,
    render_vars,
    export_layer
)

def _create_entity(scene_node, entity_uri: Uri, department_name: str):

    # Create the import node
    import_node = import_shot.create(scene_node, 'import_shot')
    prev_node = import_node.native()

    # Create the puzzle mattes node
    puzzle_mattes_node = puzzle_mattes.create(scene_node, 'puzzle_mattes')
    puzzle_mattes_node.setInput(0, prev_node)
    prev_node = puzzle_mattes_node.native()

    # Create the render vars node
    render_vars_node = render_vars.create(scene_node, 'render_vars')
    render_vars_node.setInput(0, prev_node)
    prev_node = render_vars_node.native()

    # Create the export node
    export_node = export_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, prev_node)

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])

        # Create the import node
        import_node = import_shot.create(scene_node, f'import_shot_{member_name}')
        import_node.set_shot_uri(member_uri)
        import_node.set_department_name(department_name)
        prev_node = import_node.native()

        # Create the puzzle mattes node
        puzzle_mattes_node = puzzle_mattes.create(scene_node, f'puzzle_mattes_{member_name}')
        puzzle_mattes_node.setInput(0, prev_node)
        prev_node = puzzle_mattes_node.native()

        # Create the render vars node
        render_vars_node = render_vars.create(scene_node, f'render_vars_{member_name}')
        render_vars_node.setInput(0, prev_node)
        prev_node = render_vars_node.native()

        # Create the export node
        export_node = export_layer.create(scene_node, f'export_shot_{member_name}')
        export_node.setInput(0, prev_node)
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
