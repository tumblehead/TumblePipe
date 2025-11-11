from tumblehead.pipe.houdini.cops import build_comp

def create(scene_node, sequence_name, shot_name):
    cop_node = scene_node.createNode('copnet', 'composite_shot')
    comp_node = build_comp.create(cop_node, 'composite_shot')