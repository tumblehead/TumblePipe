from tumblehead.pipe.houdini.lops import export_kit_layer

def create(scene_node, category_name, kit_name):
    export_node = export_kit_layer.create(scene_node, 'export_general')