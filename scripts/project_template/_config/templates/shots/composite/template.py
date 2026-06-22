from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.cops import build_comp

def _create_entity(scene_node, department_name: str):
    cop_node = scene_node.createNode('copnet', 'composite_shot')
    comp_node = build_comp.create(cop_node, 'composite_shot')

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    # Create per-member nodes
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])

        cop_node = scene_node.createNode('copnet', f'composite_shot_{member_name}')
        comp_node = build_comp.create(cop_node, f'composite_shot_{member_name}')
        comp_node.set_shot_uri(member_uri)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
