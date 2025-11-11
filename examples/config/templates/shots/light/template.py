from tumblehead.api import default_client
from tumblehead.pipe.houdini.lops import (
    build_shot,
    lpe_tags,
    render_settings,
    export_shot_layer
)

api = default_client()

def create(scene_node, sequence_name, shot_name):

    # Create the import node
    import_node = build_shot.create(scene_node, 'import_shot')
    prev_node = import_node.native()

    # Create the LPE tags node
    lpe_tags_node = lpe_tags.create(scene_node, 'lpe_tags')
    lpe_tags_node.setInput(0, prev_node)
    prev_node = lpe_tags_node.native()

    # Create the render settings node
    render_settings_node = render_settings.create(scene_node, 'render_settings')
    render_settings_node.setInput(0, prev_node)
    prev_node = render_settings_node.native()

    # Create the export node
    export_node = export_shot_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, prev_node)
    prev_node = export_node.native()