from tumblehead.pipe.houdini.lops import (
    import_asset_layer,
    export_asset_layer,
    lookdev_studio
)

import loptoolutils

def create(scene_node, category_name, asset_name):

    # Create the import model node
    import_node = import_asset_layer.create(scene_node, 'import_model')
    import_node.set_department_name('model')
    prev_node = import_node.native()

    # Create the preview material
    preview_material = scene_node.createNode('editmaterialproperties', 'preview_MAT')
    loptoolutils.createQuickSurfaceMaterial(preview_material)
    preview_material.parm('primpath').set(
        f'/{category_name}/{asset_name}/mat/preview_MAT'
    )
    preview_material.setInput(0, prev_node)
    prev_node = preview_material

    # Create the material linker node
    assign_node = scene_node.createNode('materiallinker', 'material_linker')
    assign_node.parm('num_links').set(1)
    assign_node.parm('link_prim_1').set(
        f'/{category_name}/{asset_name}/mat/preview_MAT'
    )
    assign_node.parm('link_includes_1').set(' '.join([
        f'/{category_name}/{asset_name}/geo/proxy',
        f'/{category_name}/{asset_name}/geo/render'
    ]))
    assign_node.setInput(0, prev_node)
    prev_node = assign_node

    # Create the lookdev studio node
    studio_node = lookdev_studio.create(scene_node, 'lookdev_studio')
    studio_node.parm('primpattern').set(f'/{category_name}/{asset_name}')
    studio_node.setInput(0, prev_node)
    
    # Create the export node
    export_node = export_asset_layer.create(scene_node, 'export_lookdev')
    export_node.setInput(0, prev_node)