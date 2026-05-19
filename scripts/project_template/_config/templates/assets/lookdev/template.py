import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.variants import list_variants
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from tumblepipe.pipe.houdini.lops import (
    import_layer,
    export_layer
)

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    prim_path = uri_to_prim_path(entity_uri)
    variant_names = list_variants(entity_uri)

    # Import the model department
    import_node = import_layer.create(scene_node, 'IMPORT_MODEL')
    import_node.set_entity_uri(entity_uri)
    import_node.set_department_name('model')

    # Create lookdev variant system via HDA
    lookdev_node = scene_node.createNode('th::create_asset_lookdev::1.0', 'LOOKDEV')
    lookdev_node.setInput(0, import_node.native())
    lookdev_node.parm('primpath').set(prim_path)

    # Populate variants from config and sync internal nodes
    lookdev_node.parm('variants').set(len(variant_names))
    for i, name in enumerate(variant_names):
        lookdev_node.parm(f'variant_name{i+1}').set(name)
    lookdev_node.hdaModule()._sync_variants(lookdev_node)

    # Create the export layer node
    export_node = export_layer.create(scene_node, 'export_lookdev')
    export_node.setInput(0, lookdev_node)

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])
        prim_path = uri_to_prim_path(member_uri)
        variant_names = list_variants(member_uri)

        # Import the model department
        import_node = import_layer.create(scene_node, f'IMPORT_MODEL_{member_name}')
        import_node.set_entity_uri(member_uri)
        import_node.set_department_name('model')

        # Create lookdev variant system via HDA
        lookdev_node = scene_node.createNode('th::create_asset_lookdev::1.0', f'LOOKDEV_{member_name}')
        lookdev_node.setInput(0, import_node.native())
        lookdev_node.parm('primpath').set(prim_path)

        # Populate variants from config and sync internal nodes
        lookdev_node.parm('variants').set(len(variant_names))
        for i, name in enumerate(variant_names):
            lookdev_node.parm(f'variant_name{i+1}').set(name)
        lookdev_node.hdaModule()._sync_variants(lookdev_node)

        # Create the export layer node
        export_node = export_layer.create(scene_node, f'export_lookdev_{member_name}')
        export_node.setInput(0, lookdev_node)
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
