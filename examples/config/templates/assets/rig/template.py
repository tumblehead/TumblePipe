from tumblehead.pipe.houdini.lops import (
    import_asset_layer
)
from tumblehead.pipe.houdini.sops import (
    export_rig
)

def create(scene_node, category_name, asset_name):

    # Create the model import node
    import_model_node = import_asset_layer.create(scene_node, 'import_model')
    import_model_node.set_department_name('model')
    
    # Create the blendshape import node
    import_blendshapes_node = import_asset_layer.create(scene_node, 'import_blendshapes')
    import_blendshapes_node.set_department_name('blendshape')

    # Create the SOP modify node
    sop_node = scene_node.createNode('sopmodify', 'rigging')
    sop_node.parm('primpattern').set(f'/{category_name}/{asset_name}/geo/render')
    sop_node.parm('unpacktopolygons').set(1)
    sop_node.parm('purpose').set(' '.join(['render', 'proxy']))
    sop_node.setInput(0, import_model_node.native())

    # Create the export rig node
    dive_node = sop_node.node('modify/modify')
    dive_input_node = dive_node.indirectInputs()[0]
    export_node = export_rig.create(dive_node, 'export_rig')
    export_node.setInput(0, dive_input_node)
    dive_node.layoutChildren()