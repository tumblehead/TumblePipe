import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.sops import cache
from tumblepipe.pipe.houdini.lops import import_layer, export_layer
from tumblepipe.pipe.houdini.util import disable_layer_save_path, uri_to_prim_path

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
IMPORT_POS = hou.Vector2(0.0, 1.55)
CREATE_POS = hou.Vector2(0.0, 0.42)
EXPORT_POS = hou.Vector2(0.0, -0.71)
COLUMN_STRIDE = 4.0

STAGE_NOTE = ('Create Blendshapes in here -->',
              hou.Vector2(-4.17, 0.38), hou.Vector2(3.39, 0.52))

# --- sopnet/create layout ------------------------------------------------
NODE_POS = {
    'IMPORT_LOP_MODEL': hou.Vector2(0.0, 3.23),
    'UNPACK_USD_MODEL': hou.Vector2(0.0, 1.71),
    'sculpt': hou.Vector2(0.0, -0.74),
    'cache': hou.Vector2(0.0, -2.49),
    'name': hou.Vector2(0.0, -4.22),
    'merge': hou.Vector2(0.0, -6.19),
    'output': hou.Vector2(0.0, -8.58),
}

# The frame the model is sampled at. Static import: a blendshape is sculpted
# against one pose, not an animated one.
STATIC_IMPORT_FRAME = 1001.0

# The sopcreate's LOP input, seen from inside sopnet/create.
LOP_INPUT_PATH = '`opinputpath("../../..", 0)`'

# Import only renderable geometry — skip scopes, xforms and other non-geo
# prims that would otherwise come through as empty packed entries.
LOP_PRIM_PATTERN = '%type:Boundable'

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))
NOTES = [
    ('Sculpt or edit your mesh into new blend shape',
     hou.Vector2(-3.22, -1.03), hou.Vector2(2.5, 0.88)),
    ("Sculpt nodes can break if model changes happen  upstream. "
     "It's therefore nice to cache the shape to disk",
     hou.Vector2(-3.19, -3.19), hou.Vector2(2.5, 1.69)),
    ('Name the blendshape',
     hou.Vector2(-3.19, -4.22), hou.Vector2(2.5, 0.53)),
    ('Merge all blendshapes ',
     hou.Vector2(-3.2, -6.34), hou.Vector2(2.5, 0.59)),
]

BOX_TITLE = 'BLENDSHAPE 1'
BOX_MEMBERS = ('sculpt', 'cache', 'name')
BOX_POS = hou.Vector2(-0.4, -4.6)
BOX_SIZE = hou.Vector2(2.55, 4.62)

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
    """Populate the sopcreate's inner network: import -> sculpt -> output.

    The model arrives as USD through the sopcreate's own LOP input, so the
    import pair reads back up the chain with opinputpath rather than going
    through a second pipeline import node.
    """
    # Pull the model off the sopcreate's LOP input and unpack it to polygons
    lop_import_node = sop_dive_node.createNode('lopimport::2.0', 'IMPORT_LOP_MODEL')
    lop_import_node.parm('loppath').set(LOP_INPUT_PATH)
    lop_import_node.parm('primpattern').set(LOP_PRIM_PATTERN)
    lop_import_node.parm('timesample').set('static')
    lop_import_node.parm('staticimportframe').set(STATIC_IMPORT_FRAME)

    unpack_node = sop_dive_node.createNode('unpackusd::2.0', 'UNPACK_USD_MODEL')
    unpack_node.parm('output').set('polygons')
    unpack_node.setInput(0, lop_import_node)

    # The blendshape itself: sculpt, then cache so the shape survives an
    # upstream model change that would otherwise invalidate the sculpt.
    sculpt_node = sop_dive_node.createNode('sculpt::2.0', 'sculpt')
    sculpt_node.setInput(0, unpack_node)

    cache_node = cache.create(sop_dive_node, 'cache')
    cache_node.setInput(0, sculpt_node)

    name_node = sop_dive_node.createNode('name', 'name')
    name_node.parm('name1').set('$OS')
    name_node.setInput(0, cache_node.native())

    # Merge collects every blendshape branch; the output is the sopcreate sink
    merge_node = sop_dive_node.createNode('merge', 'merge')
    merge_node.setInput(0, name_node)

    output_node = sop_dive_node.createNode('output', 'output')
    output_node.parm('outputidx').set(0)
    output_node.setInput(0, merge_node)

    nodes = {
        'IMPORT_LOP_MODEL': lop_import_node,
        'UNPACK_USD_MODEL': unpack_node,
        'sculpt': sculpt_node,
        'cache': cache_node.native(),
        'name': name_node,
        'merge': merge_node,
        'output': output_node,
    }
    for name, node in nodes.items():
        node.setPosition(NODE_POS[name])

    for text, position, size in NOTES:
        _sticky_note(sop_dive_node, text, position, size)

    # Group the one seeded blendshape branch, so adding the next shape is a
    # box copy rather than a node-by-node rebuild.
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
    # Order matters here in the opposite direction to the box above: addItem
    # autofits the box over its items, so the position has to be applied
    # afterwards. That drags the inputs along, which is fine — nothing else
    # depends on where they sit.
    inputs_box = sop_dive_node.createNetworkBox()
    inputs_box.setComment(INPUTS_BOX_TITLE)
    inputs_box.setColor(NOTE_COLOR)
    for indirect_input in sop_dive_node.indirectInputs():
        inputs_box.addItem(indirect_input)
    inputs_box.setMinimized(True)
    inputs_box.setPosition(INPUTS_BOX_POS)
    inputs_box.setSize(INPUTS_BOX_SIZE)

    # Display the unpacked model, not the sculpt result: createNode() left
    # the flags on the last node made.
    unpack_node.setDisplayFlag(True)
    unpack_node.setRenderFlag(True)

    return nodes

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one entity's IMPORT_MODEL -> CREATE_BLENDSHAPES -> EXPORT column."""
    prim_path = uri_to_prim_path(entity_uri)

    # Import the model department (entity resolves from context)
    import_node = import_layer.create(scene_node, f'IMPORT_MODEL{suffix}')
    import_node.set_department_name('model')
    if pin: _pin_entity(import_node, entity_uri)

    # Press Import on create, so the node comes up resolved (entity/version
    # labels filled, layer paths wired) instead of inert until clicked.
    import_node.execute()

    import_native = import_node.native()
    import_native.setPosition(IMPORT_POS + offset)
    import_native.setDisplayFlag(True)

    # Create the SOP create node holding the blendshape network
    sop_node = scene_node.createNode('sopcreate', f'BLENDSHAPES{suffix}')
    disable_layer_save_path(sop_node)
    sop_node.parm('pathprefix').set(f'{prim_path}/blshp/')
    sop_node.setInput(0, import_native)
    sop_node.setPosition(CREATE_POS + offset)

    _build_sop_dive(sop_node.node('sopnet/create'))

    text, position, size = STAGE_NOTE
    _sticky_note(scene_node, text, position + offset, size)

    # Create the export node (entity + department resolve from context)
    export_node = export_layer.create(scene_node, f'EXPORT_BLENDSHAPES{suffix}')
    export_node.setInput(0, sop_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_node.native().setPosition(EXPORT_POS + offset)

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
