import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.channels import list_channels
from tumblepipe.pipe.houdini.lops import import_layer, export_layer

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
IMPORT_POS = hou.Vector2(0.0, 0.13)
LOOKDEV_POS = hou.Vector2(0.0, -1.13)
EXPORT_POS = hou.Vector2(0.0, -2.44)
COLUMN_STRIDE = 4.0

STAGE_NOTE = ('Create Lookdev In here -->',
              hou.Vector2(-3.36, -1.28), hou.Vector2(3.03, 0.52))

# --- lookdev_variant_subnet layout ---------------------------------------
LOOKDEV_SUBNET_POS = hou.Vector2(8.02, 10.24)
VARIANT_OUT_POS = hou.Vector2(8.02, 8.35)
VARIANT_STRIDE = 2.5

# --- lookdev_subnet layout -----------------------------------------------
MATERIAL_LIBRARY_POS = hou.Vector2(-14.5, 12.85)
MATERIAL_ASSIGNER_POS = hou.Vector2(-14.5, 11.42)
VARIANT_OUTPUT_POS = hou.Vector2(-14.5, 9.95)

# The LOOKDEV HDA's primpath, seen from inside the material library.
MATPATHPREFIX = '`chs("../../../primpath")`/mtl/'

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))
NOTES = [
    ('Create materials inside the material library -->',
     hou.Vector2(-17.49, 12.52), hou.Vector2(2.5, 0.96)),
    ('Assign materials to scene primitives -->',
     hou.Vector2(-17.44, 11.1), hou.Vector2(2.19, 0.96)),
]

INPUTS_BOX_TITLE = 'INPUTS'
INPUTS_BOX_POS = hou.Vector2(-15.25, 15.73)
INPUTS_BOX_SIZE = hou.Vector2(2.5, 0.0)

# --- material library layout ---------------------------------------------
DEFAULT_MTL_NAME = 'default_mtl'
DEFAULT_MTL_POS = hou.Vector2(4.33, 3.35)
DEFAULT_MTL_BASE_COLOR = (1.0, 0.0, 0.0)

# This one is a filled-in note rather than the plain grey labels above.
MTL_NOTE_TEXT = ('To create new materials, copy the default_mtl or create '
                 'new "Karma Material Builder" nodes using the tabmenu')
MTL_NOTE_POS = hou.Vector2(7.12, 2.42)
MTL_NOTE_SIZE = hou.Vector2(4.42, 1.26)
MTL_NOTE_TEXT_COLOR = hou.Color((0.0, 0.0, 0.0))
MTL_NOTE_COLOR = hou.Color((1.0, 0.73, 0.0))

def _pin_entity(node, entity_uri: Uri):
    """Pin an entity-aware th:: HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. Single-entity
    workfiles deliberately leave the parm at 'from_context' so the node
    follows the workfile it lives in.
    """
    node.hdaModule()._apply_entity(node, str(entity_uri))

def _sticky_note(parent, text: str, position: hou.Vector2, size: hou.Vector2,
                 text_color=NOTE_TEXT_COLOR, color=NOTE_COLOR,
                 draw_background: bool = False):
    note = parent.createStickyNote()
    note.setText(text)
    note.setTextColor(text_color)
    note.setColor(color)
    note.setDrawBackground(draw_background)
    note.setSize(size)
    note.setPosition(position)
    return note

def _build_material_library(material_library_node):
    """Label the material library and make sure it has a material to copy.

    The HDA ships this library already carrying a built 'default_mtl' Karma
    material (and its matpathprefix), so the usual case only adds the note.
    The builder is recreated only if a future HDA revision drops it.
    """
    default_mtl = material_library_node.node(DEFAULT_MTL_NAME)
    if default_mtl is None:
        import voptoolutils

        material_library_node.parm('matpathprefix').set(MATPATHPREFIX)

        # The same call the tab menu's 'Karma Material Builder' entry makes,
        # minus the kwargs plumbing that needs a live network editor.
        default_mtl = voptoolutils._setupMtlXBuilderSubnet(
            subnet_node=None,
            destination_node=material_library_node,
            name=DEFAULT_MTL_NAME,
            mask=voptoolutils.KARMAMTLX_TAB_MASK,
            folder_label='Karma Material Builder',
            render_context='kma',
        )
        default_mtl.setPosition(DEFAULT_MTL_POS)

        surface_node = default_mtl.node('mtlxstandard_surface')
        if surface_node is not None:
            surface_node.parmTuple('base_color').set(DEFAULT_MTL_BASE_COLOR)

    _sticky_note(
        material_library_node, MTL_NOTE_TEXT, MTL_NOTE_POS, MTL_NOTE_SIZE,
        text_color=MTL_NOTE_TEXT_COLOR, color=MTL_NOTE_COLOR,
        draw_background=True,
    )

    return default_mtl

def _build_lookdev_subnet(lookdev_subnet, variant_names: list):
    """Wire the material library -> assigner -> per-variant outputs.

    The HDA already ships the material_library wired off the subnet's first
    indirect input, and _sync_variants has made an OUT_<name> per variant.
    """
    material_library_node = lookdev_subnet.node('material_library')
    if material_library_node is None: return

    material_library_node.setPosition(MATERIAL_LIBRARY_POS)
    # Display only: LOP nodes have no setRenderFlag, their render flag
    # follows the display flag.
    material_library_node.setDisplayFlag(True)
    _build_material_library(material_library_node)

    # Assign the library's materials onto the incoming scene prims
    assigner_node = lookdev_subnet.createNode(
        'th::material_assigner::1.0', 'material_assigner'
    )
    assigner_node.setInput(0, material_library_node)
    assigner_node.parm('assignments').set(1)
    assigner_node.parm('assignment_1').set(1)
    assigner_node.setPosition(MATERIAL_ASSIGNER_POS)

    # Every variant reads the same assigned stage; a variant that needs its
    # own materials gets its chain branched off by hand later.
    for i, variant_name in enumerate(variant_names):
        output_node = lookdev_subnet.node(f'OUT_{variant_name}')
        if output_node is None: continue
        output_node.setInput(0, assigner_node)
        output_node.setPosition(
            VARIANT_OUTPUT_POS + hou.Vector2(i * VARIANT_STRIDE, 0.0)
        )

    for text, position, size in NOTES:
        _sticky_note(lookdev_subnet, text, position, size)

    # The unused indirect inputs are already boxed and minimized by the HDA;
    # rebuilding that here would steal them out of its box into a duplicate.
    # Only build one if a future HDA revision stops shipping it.
    if not any(box.comment() == INPUTS_BOX_TITLE
               for box in lookdev_subnet.networkBoxes()):
        inputs_box = lookdev_subnet.createNetworkBox()
        inputs_box.setComment(INPUTS_BOX_TITLE)
        inputs_box.setColor(NOTE_COLOR)
        # The first indirect input feeds the library, so it stays out.
        # addItem autofits the box over its items, so position comes after.
        for indirect_input in lookdev_subnet.indirectInputs()[1:]:
            inputs_box.addItem(indirect_input)
        inputs_box.setMinimized(True)
        inputs_box.setPosition(INPUTS_BOX_POS)
        inputs_box.setSize(INPUTS_BOX_SIZE)

def _build_lookdev(lookdev_node, variant_names: list):
    """Lay out the HDA's two nested subnets and populate the inner one."""
    variant_subnet = lookdev_node.node('lookdev_variant_subnet')
    if variant_subnet is None: return

    lookdev_subnet = variant_subnet.node('lookdev_subnet')
    if lookdev_subnet is None: return

    lookdev_subnet.setPosition(LOOKDEV_SUBNET_POS)
    lookdev_subnet.setDisplayFlag(True)

    _build_lookdev_subnet(lookdev_subnet, variant_names)

    # _sync_variants leaves the per-variant nulls at the origin
    for i in range(len(variant_names)):
        null_node = variant_subnet.node(f'VARIANT{i+1}_OUT')
        if null_node is None: continue
        null_node.setPosition(
            VARIANT_OUT_POS + hou.Vector2(i * VARIANT_STRIDE, 0.0)
        )

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one entity's IMPORT_MODEL -> LOOKDEV -> EXPORT_LOOKDEV column."""
    channel_names = list_channels(entity_uri)

    # Import the model department (entity resolves from context)
    import_node = import_layer.create(scene_node, f'IMPORT_MODEL{suffix}')
    import_node.set_department_name('model')
    if pin: import_node.set_entity_uri(entity_uri)

    # Press Import on create, so the node comes up resolved (entity/version
    # labels filled, layer paths wired) instead of inert until clicked. This
    # also sets import_enable1/2 from what actually resolved on disk — the
    # dump's False values are that outcome, not a setting to force.
    import_node.execute()

    import_native = import_node.native()
    import_native.setPosition(IMPORT_POS + offset)
    import_native.setGenericFlag(hou.nodeFlag.DisplayDescriptiveName, False)

    # Create the lookdev variant system via HDA (entity + prim path from context)
    lookdev_node = scene_node.createNode(
        'th::create_asset_lookdev::1.0', f'LOOKDEV{suffix}'
    )
    lookdev_node.setInput(0, import_native)
    lookdev_node.setPosition(LOOKDEV_POS + offset)
    lookdev_node.setGenericFlag(hou.nodeFlag.DisplayDescriptiveName, False)
    lookdev_node.setUserData('wirestyle', 'rounded')
    if pin: _pin_entity(lookdev_node, entity_uri)

    # NOTE: the HDA's `variants` multiparm authors a *native USD variantSet*
    # on the asset prim, but it is seeded here from the entity's publish-
    # channel list - one property, two meanings. Until assets get their own
    # look list (designs/native-usd-variants.md), a channel and a USD variant
    # name are the same string for an asset.
    # Populate variants from config and sync internal nodes. An entity with
    # no configured channels keeps the HDA's own 'default' from OnCreated.
    if channel_names:
        lookdev_node.parm('variants').set(len(channel_names))
        for i, name in enumerate(channel_names):
            lookdev_node.parm(f'variant_name{i+1}').set(name)
        lookdev_node.hdaModule()._sync_variants(lookdev_node)
    else:
        channel_names = ['default']

    _build_lookdev(lookdev_node, channel_names)
    lookdev_node.setDisplayFlag(True)

    # Create the export layer node (entity + department resolve from context)
    export_node = export_layer.create(scene_node, f'EXPORT_LOOKDEV{suffix}')
    export_node.setInput(0, lookdev_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_node.native().setPosition(EXPORT_POS + offset)

    text, position, size = STAGE_NOTE
    _sticky_note(scene_node, text, position + offset, size)

    return lookdev_node

def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    _build(scene_node, entity_uri, department_name)

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    for i, member_uri in enumerate(group.members):
        member_name = '_'.join(member_uri.segments[1:])
        _build(
            scene_node, member_uri, department_name,
            suffix=f'_{member_name}',
            offset=hou.Vector2(i * COLUMN_STRIDE, 0.0),
            pin=True,
        )

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
