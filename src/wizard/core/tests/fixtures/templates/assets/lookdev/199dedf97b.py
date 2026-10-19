
from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.variants import list_variants
from tumblepipe.pipe.houdini.lops import (
    import_layer,
    export_layer
)

def _pin_entity(node, entity_uri: Uri):
    """Pin an entity-aware th:: HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. Routing
    through the HDA's _apply_entity keeps the visible Entity label in step
    with the parm. Single-entity workfiles deliberately leave the parm at
    'from_context' so the node follows the workfile it lives in.
    """
    node.hdaModule()._apply_entity(node, str(entity_uri))

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    variant_names = list_variants(entity_uri)

    # Import the model department (entity resolves from context)
    import_node = import_layer.create(scene_node, 'IMPORT_MODEL')
    import_node.set_department_name('model')

    # Create lookdev variant system via HDA (entity + prim path from context)
    lookdev_node = scene_node.createNode('th::create_asset_lookdev::1.0', 'LOOKDEV')
    lookdev_node.setInput(0, import_node.native())

    # Populate variants from config and sync internal nodes
    lookdev_node.parm('variants').set(len(variant_names))
    for i, name in enumerate(variant_names):
        lookdev_node.parm(f'variant_name{i+1}').set(name)
    lookdev_node.hdaModule()._sync_variants(lookdev_node)

    # Create the export layer node (entity + department resolve from context)
    export_node = export_layer.create(scene_node, 'export_lookdev')
    export_node.setInput(0, lookdev_node)

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])
        variant_names = list_variants(member_uri)

        # Import the model department
        import_node = import_layer.create(scene_node, f'IMPORT_MODEL_{member_name}')
        import_node.set_entity_uri(member_uri)
        import_node.set_department_name('model')

        # Create lookdev variant system via HDA
        lookdev_node = scene_node.createNode('th::create_asset_lookdev::1.0', f'LOOKDEV_{member_name}')
        lookdev_node.setInput(0, import_node.native())
        _pin_entity(lookdev_node, member_uri)

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
