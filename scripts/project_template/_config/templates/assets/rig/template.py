import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.sops import export_rig
from tumblepipe.pipe.houdini.util import disable_layer_save_path

def _pin_entity(sop_import_node, entity_uri: Uri):
    """Pin a th::import_model HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. The entity
    parm channel-feeds the embedded import_layer LOP; _update_labels pulls
    the resolved label back out so the node doesn't keep reading
    'from_context: none' while pointing at a real asset.
    """
    sop_import_node.parm('entity').set(str(entity_uri))
    sop_import_node.hdaModule()._update_labels(sop_import_node)

def _create_entity(scene_node, entity_uri: Uri, department_name: str):

    # Create the sopcreate node for the rigging network
    sop_node = scene_node.createNode('sopcreate', 'rigging')
    disable_layer_save_path(sop_node)
    sop_dive_node = sop_node.node('sopnet/create')

    # Create the import model HDA (entity defaults to from_context)
    sop_import_model = sop_dive_node.createNode('th::import_model::1.0', 'import_model')

    # Create the import blendshapes HDA
    sop_import_blendshapes = sop_dive_node.createNode('th::import_model::1.0', 'import_blendshapes')
    sop_import_blendshapes.parm('department').set('blendshape')

    # Create the export rig node (terminal sink, no output connector)
    export_node = export_rig.create(sop_dive_node, 'export_rig')
    export_node.setInput(0, sop_import_model)

    # Layout the dive nodes
    sop_dive_node.layoutChildren()

    # Navigate the network editor into the sopcreate subnet
    network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
    if network_editor is not None:
        network_editor.cd(sop_node.path())

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    # Create per-member nodes
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])

        # Create the sopcreate node for this member's rigging network
        sop_node = scene_node.createNode('sopcreate', f'rigging_{member_name}')
        disable_layer_save_path(sop_node)
        sop_dive_node = sop_node.node('sopnet/create')

        # Create the import model HDA (entity set explicitly for group members)
        sop_import_model = sop_dive_node.createNode('th::import_model::1.0', 'import_model')
        _pin_entity(sop_import_model, member_uri)

        # Create the import blendshapes HDA
        sop_import_blendshapes = sop_dive_node.createNode('th::import_model::1.0', 'import_blendshapes')
        _pin_entity(sop_import_blendshapes, member_uri)
        sop_import_blendshapes.parm('department').set('blendshape')

        # Create the export rig node
        export_node = export_rig.create(sop_dive_node, 'export_rig')
        export_node.setInput(0, sop_import_model)
        export_node.set_entity_uri(member_uri)

        # Layout the dive nodes
        sop_dive_node.layoutChildren()

        # Navigate the network editor into this member's sopcreate subnet
        network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
        if network_editor is not None:
            network_editor.cd(sop_node.path())

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
