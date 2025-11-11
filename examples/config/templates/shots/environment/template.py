from tumblehead.pipe.houdini.lops import (
    build_shot,
    export_shot_layer,
    playblast
)

def create(scene_node, sequence_name, shot_name):

    # Create the import node
    import_node = build_shot.create(scene_node, 'import_shot')
    prev_node = import_node.native()

    # Create the stage out null node
    stage_out_node = scene_node.createNode('null', 'OUT_stage')
    stage_out_node.setInput(0, prev_node)
    stage_out_node.setDisplayFlag(True)
    prev_node = stage_out_node

    # Create the playblast node
    playblast_node = playblast.create(scene_node, 'playblast')
    playblast_node.setInput(0, prev_node)
    
    # Create the export node
    export_node = export_shot_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, prev_node)