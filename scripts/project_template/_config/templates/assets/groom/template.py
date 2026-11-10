import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.lops import import_layer, export_layer
from tumblepipe.pipe.houdini.nodes import disable_layer_save_path
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
IMPORT_POS = hou.Vector2(0.0, 1.55)
CREATE_POS = hou.Vector2(0.0, 0.42)
SCOPE_POS = hou.Vector2(0.0, -0.71)
EXPORT_POS = hou.Vector2(0.0, -1.84)
COLUMN_STRIDE = 4.0

STAGE_NOTE = ('Groom in here -->',
              hou.Vector2(-3.4, 0.38), hou.Vector2(2.6, 0.52))

# --- sopnet/create layout ------------------------------------------------
NODE_POS = {
    'IMPORT_LOP_MODEL': hou.Vector2(0.0, 3.23),
    'UNPACK_USD_MODEL': hou.Vector2(0.0, 1.71),
    'GUIDES': hou.Vector2(0.0, 0.21),
    'groom': hou.Vector2(0.0, -1.29),
    'HAIR': hou.Vector2(0.0, -2.79),
    'name': hou.Vector2(0.0, -4.29),
    'output': hou.Vector2(0.0, -5.79),
}

# The frame the model is sampled at. Static import: a groom is planted and
# combed on the rest skin; the rig and cfx deform it per shot.
STATIC_IMPORT_FRAME = 1001.0

# The sopcreate's LOP input, seen from inside sopnet/create.
LOP_INPUT_PATH = '`opinputpath("../../..", 0)`'

# Import only renderable geometry — skip scopes, xforms and other non-geo
# prims that would otherwise come through as empty packed entries.
LOP_PRIM_PATTERN = '%type:Boundable'

# Fixed counts rather than Hair Generate's per-area density, so the seeded
# groom looks the same whatever units the model was built in.
GUIDE_COUNT = 500
HAIR_COUNT = 20000

# The prim the hair lands on: <asset>/groom/hair
HAIR_NAME = 'hair'

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))
NOTES = [
    ('Seed guides on the skin. Group or mask the skin to choose where hair grows',
     hou.Vector2(-3.22, 0.0), hou.Vector2(2.5, 0.88)),
    ('Comb, cut and plant guides here',
     hou.Vector2(-3.22, -1.29), hou.Vector2(2.5, 0.53)),
    ('Interpolate the final hair from the guides',
     hou.Vector2(-3.22, -2.94), hou.Vector2(2.5, 0.68)),
    ('One name per groom; add a branch and merge for more',
     hou.Vector2(-3.22, -4.44), hou.Vector2(2.5, 0.68)),
]

BOX_TITLE = 'GROOM 1'
BOX_MEMBERS = ('GUIDES', 'groom', 'HAIR', 'name')
BOX_POS = hou.Vector2(-0.4, -4.75)
BOX_SIZE = hou.Vector2(2.55, 5.4)

INPUTS_BOX_TITLE = 'INPUTS'
INPUTS_BOX_POS = hou.Vector2(-0.75, 5.09)
INPUTS_BOX_SIZE = hou.Vector2(2.5, 0.0)

def _pin_entity(node, entity_uri: Uri):
    """Pin an entity-aware th:: HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. Single-entity
    workfiles deliberately leave the parm at 'from_context' so the node
    follows the workfile it lives in.
    """
    node.set_entity_uri(entity_uri)

def _sticky_note(parent, text: str, position: hou.Vector2, size: hou.Vector2):
    note = parent.createStickyNote()
    note.setText(text)
    note.setTextColor(NOTE_TEXT_COLOR)
    note.setColor(NOTE_COLOR)
    note.setDrawBackground(False)
    note.setSize(size)
    note.setPosition(position)
    return note

def _build_sop_dive(sop_dive_node):
    """Populate the sopcreate's inner network:
    skin -> guides -> groom -> hair -> output.

    The model arrives as USD through the sopcreate's own LOP input, so the
    import pair reads back up the chain with opinputpath rather than going
    through a second pipeline import node.
    """
    # Pull the model off the sopcreate's LOP input and unpack it to polygons:
    # this is the skin every groom node reads.
    lop_import_node = sop_dive_node.createNode('lopimport::2.0', 'IMPORT_LOP_MODEL')
    lop_import_node.parm('loppath').set(LOP_INPUT_PATH)
    lop_import_node.parm('primpattern').set(LOP_PRIM_PATTERN)
    lop_import_node.parm('timesample').set('static')
    lop_import_node.parm('staticimportframe').set(STATIC_IMPORT_FRAME)

    skin_node = sop_dive_node.createNode('unpackusd::2.0', 'UNPACK_USD_MODEL')
    skin_node.parm('output').set('polygons')
    skin_node.setInput(0, lop_import_node)

    # Sparse guide curves scattered over the skin, as a starting point
    guides_node = sop_dive_node.createNode('hairgen::2.0', 'GUIDES')
    guides_node.parm('forcecount').set(True)
    guides_node.parm('count').set(GUIDE_COUNT)
    guides_node.setInput(0, skin_node)

    # The artist's node: the Guide Groom brushes edit the guides on the skin
    groom_node = sop_dive_node.createNode('guidegroom::2.0', 'groom')
    groom_node.setInput(0, guides_node)
    groom_node.setInput(1, skin_node)

    # The final hair, interpolated from the groomed guides
    hair_node = sop_dive_node.createNode('hairgen::2.0', 'HAIR')
    hair_node.parm('forcecount').set(True)
    hair_node.parm('count').set(HAIR_COUNT)
    hair_node.setInput(0, skin_node)
    hair_node.setInput(1, groom_node)

    # The name becomes the prim under <asset>/groom/
    name_node = sop_dive_node.createNode('name', 'name')
    name_node.parm('name1').set(HAIR_NAME)
    name_node.setInput(0, hair_node)

    output_node = sop_dive_node.createNode('output', 'output')
    output_node.parm('outputidx').set(0)
    output_node.setInput(0, name_node)

    nodes = {
        'IMPORT_LOP_MODEL': lop_import_node,
        'UNPACK_USD_MODEL': skin_node,
        'GUIDES': guides_node,
        'groom': groom_node,
        'HAIR': hair_node,
        'name': name_node,
        'output': output_node,
    }
    for name, node in nodes.items():
        node.setPosition(NODE_POS[name])

    for text, position, size in NOTES:
        _sticky_note(sop_dive_node, text, position, size)

    # Group the one seeded groom branch, so adding the next groom (brows,
    # lashes) is a box copy rather than a node-by-node rebuild.
    network_box = sop_dive_node.createNetworkBox()
    network_box.setComment(BOX_TITLE)
    network_box.setColor(NOTE_COLOR)
    # Place and size the box before adopting anything: setPosition() drags a
    # box's contents along with it, so adding first would shift the nodes.
    network_box.setPosition(BOX_POS)
    network_box.setSize(BOX_SIZE)
    for name in BOX_MEMBERS:
        network_box.addItem(nodes[name])

    # Tuck the subnet's indirect inputs into a minimized box, out of the way.
    # addItem autofits the box over its items, so the position is applied
    # afterwards.
    inputs_box = sop_dive_node.createNetworkBox()
    inputs_box.setComment(INPUTS_BOX_TITLE)
    inputs_box.setColor(NOTE_COLOR)
    for indirect_input in sop_dive_node.indirectInputs():
        inputs_box.addItem(indirect_input)
    inputs_box.setMinimized(True)
    inputs_box.setPosition(INPUTS_BOX_POS)
    inputs_box.setSize(INPUTS_BOX_SIZE)

    # Display the groom node, where the artist works: createNode() left the
    # flags on the last node made.
    groom_node.setDisplayFlag(True)
    groom_node.setRenderFlag(True)

    return nodes

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one entity's IMPORT_MODEL -> GROOM -> GROOM_SCOPE -> EXPORT
    column."""
    prim_path = uri_to_prim_path(entity_uri)

    # Import the model department (entity resolves from context)
    import_node = import_layer.create(scene_node, f'IMPORT_MODEL{suffix}')
    # Pin first: in a Multi's workfile 'from_context' resolves no entity,
    # and until it has one the import offers no department but
    # 'from_context', so setting 'model' first raised.
    if pin: _pin_entity(import_node, entity_uri)
    import_node.set_department_name('model')

    # Press Import on create, so the node comes up resolved (entity/version
    # labels filled, layer paths wired) instead of inert until clicked.
    import_node.execute()

    import_native = import_node.native()
    import_native.setPosition(IMPORT_POS + offset)
    import_native.setDisplayFlag(True)

    # Create the SOP create node holding the groom network
    sop_node = scene_node.createNode('sopcreate', f'GROOM{suffix}')
    disable_layer_save_path(sop_node)
    sop_node.parm('pathprefix').set(f'{prim_path}/groom/')
    sop_node.setInput(0, import_native)
    sop_node.setPosition(CREATE_POS + offset)

    _build_sop_dive(sop_node.node('sopnet/create'))

    # The sopcreate authors the prims above its geometry as Xforms; the
    # asset structure check wants <asset>/groom to be a Scope, as it does
    # geo, blshp and mtl.
    scope_node = scene_node.createNode('configureprimitive', f'GROOM_SCOPE{suffix}')
    scope_node.parm('primpattern').set(f'{prim_path}/groom')
    scope_node.parm('settype').set(1)
    scope_node.parm('type').set('UsdGeomScope')
    scope_node.setInput(0, sop_node)
    scope_node.setPosition(SCOPE_POS + offset)

    text, position, size = STAGE_NOTE
    _sticky_note(scene_node, text, position + offset, size)

    # Create the export node (entity + department resolve from context)
    export_node = export_layer.create(scene_node, f'EXPORT_GROOM{suffix}')
    export_node.setInput(0, scope_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_node.native().setPosition(EXPORT_POS + offset)

    return sop_node

def _dive_network_editor(sop_node):
    """Navigate the network editor into the sopcreate's SOP network.

    cd() does not follow an HDA's dive target the way double-clicking does:
    pointed at the sopcreate itself it opens the LOP internals instead of
    sopnet/create, where the work happens.
    """
    if not hou.isUIAvailable(): return
    network_editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
    if network_editor is not None:
        network_editor.cd(sop_node.node('sopnet/create').path())

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
