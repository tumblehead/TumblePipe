import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.sops import cache
from tumblepipe.pipe.houdini.lops import export_layer
from tumblepipe.pipe.houdini.util import disable_layer_save_path, uri_to_prim_path

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    prim_path = uri_to_prim_path(entity_uri)

    # Create the SOP create node
    sop_node = scene_node.createNode('sopcreate', 'create_blendshapes')
    disable_layer_save_path(sop_node)
    sop_node.parm('pathprefix').set(f'{prim_path}/blshp/')
    sop_dive_node = sop_node.node('sopnet/create')

    # Create the import model HDA (encapsulates LOP import + lopimport + unpack)
    sop_import_node = sop_dive_node.createNode('th::import_model::1.0', 'import_model')

    # Create the sculpt node
    sop_sculpt_node = sop_dive_node.createNode('sculpt', 'sculpt')
    sop_sculpt_node.setInput(0, sop_import_node)

    # Create the SOP cache node
    sop_cache_node = cache.create(sop_dive_node, 'cache')
    sop_cache_node.setInput(0, sop_sculpt_node)

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

    # Navigate the network editor into the sopcreate subnet
    network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
    if network_editor is not None:
        network_editor.cd(sop_node.path())

    # Create the export node
    export_node = export_layer.create(scene_node, 'export_blendshapes')
    export_node.setInput(0, sop_node)

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    # Create per-member nodes
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])
        prim_path = uri_to_prim_path(member_uri)

        # Create the SOP create node
        sop_node = scene_node.createNode('sopcreate', f'create_blendshapes_{member_name}')
        disable_layer_save_path(sop_node)
        sop_node.parm('pathprefix').set(f'{prim_path}/blshp/')
        sop_dive_node = sop_node.node('sopnet/create')

        # Create the import model HDA (entity set explicitly for group members)
        sop_import_node = sop_dive_node.createNode('th::import_model::1.0', 'import_model')
        sop_import_node.parm('entity').set(str(member_uri))

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

        # Navigate the network editor into this member's sopcreate subnet
        network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
        if network_editor is not None:
            network_editor.cd(sop_node.path())

        # Create the export node
        export_node = export_layer.create(scene_node, f'export_blendshapes_{member_name}')
        export_node.setInput(0, sop_node)
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
