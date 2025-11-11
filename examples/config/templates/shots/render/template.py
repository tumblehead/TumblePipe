from tumblehead.api import default_client
from tumblehead.pipe.houdini.lops import (
    build_shot,
    puzzle_mattes,
    render_vars,
    render_settings,
    export_shot_layer,
    export_render_layer
)

api = default_client()

def create(scene_node, sequence_name, shot_name):

    # Create the import node
    import_node = build_shot.create(scene_node, 'import_shot')
    prev_node = import_node.native()

    # Create the puzzle mattes node
    puzzle_mattes_node = puzzle_mattes.create(scene_node, 'puzzle_mattes')
    puzzle_mattes_node.setInput(0, prev_node)
    prev_node = puzzle_mattes_node.native()

    # Create the render vars node
    render_vars_node = render_vars.create(scene_node, 'render_vars')
    render_vars_node.setInput(0, prev_node)
    prev_node = render_vars_node.native()

    # Create the render settings node
    render_settings_node = render_settings.create(scene_node, 'render_settings')
    render_settings_node.setInput(0, prev_node)
    prev_node = render_settings_node.native()

    # Create the export node
    export_node = export_shot_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, prev_node)
    prev_node = export_node.native()
    
    # Create the render layer nodes
    render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
    for render_layer_name in render_layer_names:
        render_layer_node = export_render_layer.create(scene_node, f'export_{render_layer_name}')
        render_layer_node.setInput(0, prev_node)