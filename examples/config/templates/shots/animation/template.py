from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import (
    build_shot,
    export_shot_layer,
    animate,
    playblast
)
from tumblehead.pipe.houdini.sops import (
    import_rigs
)
import hou

def create(scene_node, sequence_name, shot_name):

    def _add(rig_imports, category_name, asset_name):
        if category_name not in rig_imports:
            rig_imports[category_name] = {}
        if asset_name not in rig_imports[category_name]:
            rig_imports[category_name][asset_name] = 0
        rig_imports[category_name][asset_name] += 1
    
    # Import the assets
    import_node = build_shot.create(scene_node, 'import_shot')
    import_node.execute()
    
    # Scrape the assets
    root = import_node.native().stage().GetPseudoRoot()
    rig_imports = dict()
    for asset_info in util.list_assets(root):
        _add(
            rig_imports,
            asset_info['category'],
            asset_info['asset']
        )

    # Create the animation node
    animate_node = animate.create(scene_node, 'animate_shot')
    animate_node.setInput(0, import_node.native())
    inner_animate_node = animate_node.native().node('anim_dive')
    output_animate_node = inner_animate_node.node('output0')

    # Create animation camera
    anim_camera = hou.node('/obj').createNode('lopimportcam', 'anim_camera')
    anim_camera.parm('loppath').set('/stage/import_shot')
    anim_camera.parm('primpath').set('/cameras/render_camera')

    # Import the rigs
    rigs_node = import_rigs.create(
        inner_animate_node,
        'import_rigs'
    )
    rigs_node.set_rig_imports(rig_imports)
    rigs_node.execute()

    # Create the scene animate nodes
    scene_animate_node = inner_animate_node.createNode(
        'apex::sceneanimate',
        'scene_animate'
    )
    scene_animate_node.setInput(0, rigs_node.native())
    invoke_scene_node = inner_animate_node.createNode(
        'apex::sceneinvoke',
        'scene_invoke'
    )
    invoke_scene_node.setInput(0, scene_animate_node)
    output_animate_node.setInput(0, invoke_scene_node)

    # Create the playblast node
    playblast_node = playblast.create(
        inner_animate_node,
        'playblast'
    )
    playblast_node.setInput(0, invoke_scene_node)

    # Layout the dive nodes
    inner_animate_node.layoutChildren()

    # Create the stage out null node
    stage_out_node = scene_node.createNode('null', 'OUT_stage')
    stage_out_node.setInput(0, animate_node.native())
    stage_out_node.setDisplayFlag(True)

    # Create the playblast node
    playblast_node = playblast.create(scene_node, 'playblast')
    playblast_node.setInput(0, stage_out_node)

    # Create the export node
    export_node = export_shot_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, stage_out_node)