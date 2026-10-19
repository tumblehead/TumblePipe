import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.sops import export_rig
from tumblepipe.pipe.houdini.util import uri_to_prim_path

def _create_entity(scene_node, entity_uri: Uri, department_name: str):

    # Create the sopcreate node for the rigging network
    sop_node = scene_node.createNode('sopcreate', 'rigging')
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
        sop_dive_node = sop_node.node('sopnet/create')

        # Create the import model HDA (entity set explicitly for group members)
        sop_import_model = sop_dive_node.createNode('th::import_model::1.0', 'import_model')
        sop_import_model.parm('entity').set(str(member_uri))

        # Create the import blendshapes HDA
        sop_import_blendshapes = sop_dive_node.createNode('th::import_model::1.0', 'import_blendshapes')
        sop_import_blendshapes.parm('entity').set(str(member_uri))
        sop_import_blendshapes.parm('department').set('blendshape')

        # Create the export rig node
        export_node = export_rig.create(sop_dive_node, 'export_rig')
        export_node.setInput(0, sop_import_model)
        export_node.set_asset_uri(member_uri)

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
