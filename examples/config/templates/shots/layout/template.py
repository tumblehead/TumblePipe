from tumblehead.pipe.houdini.lops import (
    import_assets,
    export_shot_layer,
    playblast
)

def create(scene_node, sequence_name, shot_name):

    # Create the import node
    import_node = import_assets.create(
        scene_node,
        'import_assets'
    )
    prev_node = import_node.native()

    # Create the camera node
    camera_node = scene_node.createNode('camera', 'render_camera')
    camera_node.parm('primpath').set('/cameras/render_camera')
    camera_node.setInput(0, prev_node)
    prev_node = camera_node

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