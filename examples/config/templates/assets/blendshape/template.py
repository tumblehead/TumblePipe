from tumblehead.pipe.houdini.sops import cache
from tumblehead.pipe.houdini.lops import (
    import_asset_layer,
    export_asset_layer
)

def create(scene_node, category_name, asset_name):
    
    # Create the import model node
    import_model_node = import_asset_layer.create(scene_node, 'import_model')
    import_model_node.set_department_name('model')

    # Create the SOP create node
    sop_node = scene_node.createNode('sopcreate', 'create_blendshapes')
    sop_node.parm('pathprefix').set(f'/{category_name}/{asset_name}/blshp/')
    sop_dive_node = sop_node.node('sopnet/create')

    # Create the SOP import model node
    sop_import_model_node = sop_dive_node.createNode('lopimport', 'import_model')
    sop_import_model_node.parm('loppath').set(sop_import_model_node.relativePathTo(import_model_node))
    sop_import_model_node.parm('primpattern').set(f'/{category_name}/{asset_name}/geo')
    sop_import_model_node.parm('timesample').set(0)

    # Create the SOP unpackusd node
    sop_unpack_model_node = sop_dive_node.createNode('unpackusd', 'unpack_model')
    sop_unpack_model_node.setInput(0, sop_import_model_node)

    # Create the SOP model anchor
    sop_model_anchor_node = sop_dive_node.createNode('null', 'IN_model')
    sop_model_anchor_node.setInput(0, sop_unpack_model_node)

    # Create the SOP GOZ import node
    sop_goz_import_node = sop_dive_node.createNode('goz_import', 'goz_import')

    # Create the SOP cache node
    sop_cache_node = cache.create(sop_dive_node, 'cache')
    sop_cache_node.setInput(0, sop_goz_import_node)

    # Create the SOP name node
    sop_name_node = sop_dive_node.createNode('name', 'name')
    sop_name_node.parm('name1').set('$OS')
    sop_name_node.setInput(0, sop_cache_node.native())

    # Create the SOP merge node
    sop_merge_node = sop_dive_node.createNode('merge', 'merge')
    sop_merge_node.setInput(0, sop_name_node)

    # Create the SOP output node
    sop_output_node = sop_dive_node.createNode('output', 'output')
    sop_output_node.setInput(0, sop_merge_node)

    # Layout the dive nodes
    sop_dive_node.layoutChildren()

    # Create the export node
    export_node = export_asset_layer.create(scene_node, 'export_blendshapes')
    export_node.setInput(0, sop_node)