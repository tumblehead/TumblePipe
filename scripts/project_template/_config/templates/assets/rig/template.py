import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.sops import export_rig
from tumblepipe.pipe.houdini.util import disable_layer_save_path

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
RIGGING_POS = hou.Vector2(0.12, -0.35)
COLUMN_STRIDE = 4.0

STAGE_NOTE = ('Create Rig in here -->',
              hou.Vector2(-2.81, -0.4), hou.Vector2(2.5, 0.46))

# --- sopnet/create layout ------------------------------------------------
IMPORT_MODEL_POS = hou.Vector2(0.0, 0.0)
IMPORT_BLENDSHAPES_POS = hou.Vector2(3.94, 0.0)
EXPORT_RIG_POS = hou.Vector2(0.0, -3.19)

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))

INPUTS_BOX_TITLE = 'INPUTS'
INPUTS_BOX_POS = hou.Vector2(-0.75, 1.65)
INPUTS_BOX_SIZE = hou.Vector2(2.5, 0.0)

def _run_import(sop_import_node):
    """Press Import on a freshly created th::import_model.

    Nothing imports at creation on its own: import_model's OnCreated only
    sets colour, shape and the internal department, so the node sits there
    holding its factory 'from_context: none' entity label and a blank
    version until someone clicks the button. execute() is the same code path
    that button runs — it resolves the entity and version labels, wires up
    the layer file paths, and bypasses the node with a reason when the
    department has nothing staged yet. It takes an explicit node so callers
    outside the parm-callback context can drive it.
    """
    sop_import_node.hdaModule().execute(sop_import_node)

def _pin_entity(sop_import_node, entity_uri: Uri):
    """Pin a th::import_model HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. The entity
    parm channel-feeds the embedded import_layer LOP, so the label refresh
    has to happen after the parm is written.
    """
    sop_import_node.parm('entity').set(str(entity_uri))

def _sticky_note(parent, text: str, position: hou.Vector2, size: hou.Vector2):
    note = parent.createStickyNote()
    note.setText(text)
    note.setTextColor(NOTE_TEXT_COLOR)
    note.setColor(NOTE_COLOR)
    note.setDrawBackground(False)
    note.setSize(size)
    note.setPosition(position)
    return note

def _build_sop_dive(sop_dive_node, entity_uri: Uri, pin: bool):
    """Populate the sopcreate's inner network: imports -> rig -> export."""
    # Create the import model HDA (entity defaults to from_context)
    import_model_node = sop_dive_node.createNode(
        'th::import_model::1.0', 'import_model'
    )
    if pin: _pin_entity(import_model_node, entity_uri)
    _run_import(import_model_node)
    import_model_node.setPosition(IMPORT_MODEL_POS)

    # Create the import blendshapes HDA, left unwired for the rigger to use
    import_blendshapes_node = sop_dive_node.createNode(
        'th::import_model::1.0', 'import_blendshapes'
    )
    if pin: _pin_entity(import_blendshapes_node, entity_uri)
    import_blendshapes_node.parm('department').set('blendshape')
    _run_import(import_blendshapes_node)
    import_blendshapes_node.setPosition(IMPORT_BLENDSHAPES_POS)

    # Create the export rig node (terminal sink, no output connector)
    export_node = export_rig.create(sop_dive_node, 'export_rig')
    export_node.setInput(0, import_model_node)
    export_node.native().setPosition(EXPORT_RIG_POS)

    # Tuck the subnet's indirect inputs into a minimized box, out of the way.
    # addItem autofits the box over its items, so position comes afterwards.
    inputs_box = sop_dive_node.createNetworkBox()
    inputs_box.setComment(INPUTS_BOX_TITLE)
    inputs_box.setColor(NOTE_COLOR)
    for indirect_input in sop_dive_node.indirectInputs():
        inputs_box.addItem(indirect_input)
    inputs_box.setMinimized(True)
    inputs_box.setPosition(INPUTS_BOX_POS)
    inputs_box.setSize(INPUTS_BOX_SIZE)

    # The model is what the rigger works against: createNode() left the
    # flags on the last node made.
    import_model_node.setDisplayFlag(True)
    import_model_node.setRenderFlag(True)

    return import_model_node

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one entity's rigging sopcreate."""
    # Create the sopcreate node for the rigging network
    sop_node = scene_node.createNode('sopcreate', f'rigging{suffix}')
    disable_layer_save_path(sop_node)
    sop_node.setPosition(RIGGING_POS + offset)
    sop_node.setDisplayFlag(True)

    _build_sop_dive(sop_node.node('sopnet/create'), entity_uri, pin)

    text, position, size = STAGE_NOTE
    _sticky_note(scene_node, text, position + offset, size)

    return sop_node

def _dive_network_editor(sop_node):
    """Navigate the network editor into the sopcreate subnet."""
    if not hou.isUIAvailable(): return
    network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
    if network_editor is not None:
        network_editor.cd(sop_node.path())

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    sop_node = _build(scene_node, entity_uri, department_name)
    _dive_network_editor(sop_node)

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    sop_node = None
    for i, member_uri in enumerate(group.members):
        member_name = '_'.join(member_uri.segments[1:])
        sop_node = _build(
            scene_node, member_uri, department_name,
            suffix=f'_{member_name}',
            offset=hou.Vector2(i * COLUMN_STRIDE, 0.0),
            pin=True,
        )

    if sop_node is not None: _dive_network_editor(sop_node)

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
