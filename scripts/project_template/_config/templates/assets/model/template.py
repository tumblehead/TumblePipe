
from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.variants import list_variants
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from tumblepipe.pipe.houdini.lops import export_layer

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    prim_path = uri_to_prim_path(entity_uri)
    variant_names = list_variants(entity_uri)

    # Create asset prim via HDA
    asset_node = scene_node.createNode('th::create_asset::1.0', 'ASSET')
    asset_node.parm('primpath').set(prim_path)

    # Create model variant system via HDA
    model_node = scene_node.createNode('th::create_asset_model::1.0', 'MODEL')
    model_node.setInput(0, asset_node)

    # Populate variants from config and sync internal nodes
    model_node.parm('variants').set(len(variant_names))
    for i, name in enumerate(variant_names):
        model_node.parm(f'variant_name{i+1}').set(name)
    model_node.hdaModule()._sync_variants(model_node)

    # Asset payload (display color)
    payload_node = scene_node.createNode('th::asset_payload::1.0', 'PAYLOAD')
    payload_node.setInput(0, model_node)

    # Create the export layer node
    export_node = export_layer.create(scene_node, 'export_model')
    export_node.setInput(0, payload_node)

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])
        prim_path = uri_to_prim_path(member_uri)
        variant_names = list_variants(member_uri)

        # Create asset prim via HDA
        asset_node = scene_node.createNode('th::create_asset::1.0', f'ASSET_{member_name}')
        asset_node.parm('primpath').set(prim_path)

        # Create model variant system via HDA
        model_node = scene_node.createNode('th::create_asset_model::1.0', f'MODEL_{member_name}')
        model_node.setInput(0, asset_node)

        # Populate variants from config and sync internal nodes
        model_node.parm('variants').set(len(variant_names))
        for i, name in enumerate(variant_names):
            model_node.parm(f'variant_name{i+1}').set(name)
        model_node.hdaModule()._sync_variants(model_node)

        # Asset payload (display color)
        payload_node = scene_node.createNode('th::asset_payload::1.0', f'PAYLOAD_{member_name}')
        payload_node.setInput(0, model_node)

        # Create the export layer node
        export_node = export_layer.create(scene_node, f'export_model_{member_name}')
        export_node.setInput(0, payload_node)
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
