
import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.channels import list_channels
from tumblepipe.pipe.houdini.lops import export_layer

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE (wide enough to clear the sticky note,
# which reaches 3.11 units left of its MODEL node).
MODEL_POS = hou.Vector2(0.0, -1.98)
EXPORT_POS = hou.Vector2(0.0, -3.39)
COLUMN_STRIDE = 6.0

NOTE_POS = hou.Vector2(-3.11, -2.33)
NOTE_SIZE = hou.Vector2(2.76, 0.78)
NOTE_TEXT = 'Create model in here -->'
NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))

# --- create_variants layout ----------------------------------------------
# Inside the HDA, next to the 'INPUTS' network box it ships with. One column
# per variant, so a multi-variant asset doesn't stack its boxes on top of
# each other.
BOX_POS = hou.Vector2(-126.03, 74.79)
OUT_POS = hou.Vector2(-126.03, 73.03)
VARIANT_STRIDE = 3.0

def _pin_entity(node, entity_uri: Uri):
    """Pin an entity-aware th:: HDA to one specific entity.

    Only group workfiles need this: they hold several entities at once, so
    the 'from_context' default cannot resolve to a single one. Routing
    through the HDA's _apply_entity keeps the visible Entity label in step
    with the parm. Single-entity workfiles deliberately leave the parm at
    'from_context' so the node follows the workfile it lives in.
    """
    node.hdaModule()._apply_entity(node, str(entity_uri))

def _sticky_note(scene_node, offset: hou.Vector2):
    """The 'Create model in here -->' pointer sitting left of a MODEL node."""
    note = scene_node.createStickyNote()
    note.setText(NOTE_TEXT)
    note.setTextColor(NOTE_TEXT_COLOR)
    note.setColor(NOTE_COLOR)
    note.setDrawBackground(False)
    note.setSize(NOTE_SIZE)
    note.setPosition(NOTE_POS + offset)
    return note

def _seed_variant_geo(model_node, variant_names: list):
    """Give every variant a starter box inside create_variants.

    _sync_variants has already made the per-variant OUT_<name> output (and
    its paired null in variant_sopnet); all that is missing is geometry
    feeding it. It places those outputs with moveToGoodPosition(), so the
    positions are restated here to keep each variant in its own column.
    """
    variant_sopnet = model_node.node('variant_sopnet')
    if variant_sopnet is None: return

    create_variants = variant_sopnet.node('create_variants')
    if create_variants is None: return

    for i, variant_name in enumerate(variant_names):
        out_node = create_variants.node(f'OUT_{variant_name}')
        if out_node is None: continue

        column = hou.Vector2(i * VARIANT_STRIDE, 0.0)

        box_node = create_variants.createNode('box', 'box')
        box_node.parm('type').set('polymesh')
        box_node.parmTuple('divrate').set((2, 2, 2))
        box_node.setPosition(BOX_POS + column)

        out_node.setInput(0, box_node)
        out_node.setPosition(OUT_POS + column)

        # createNode() moved the display/render flags onto the box; the
        # output is what the variant foreach reads, so hand them back.
        out_node.setDisplayFlag(True)
        out_node.setRenderFlag(True)

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one entity's MODEL -> EXPORT_MODEL column."""
    channel_names = list_channels(entity_uri)

    # Create the model variant system via HDA. The HDA builds the asset prim
    # itself, so there is no separate create_asset node. Entity and prim path
    # resolve from context unless this is a group workfile.
    model_node = scene_node.createNode(
        'th::create_asset_model::1.0', f'MODEL{suffix}'
    )
    model_node.setPosition(MODEL_POS + offset)
    model_node.setGenericFlag(hou.nodeFlag.DisplayDescriptiveName, False)
    if pin: _pin_entity(model_node, entity_uri)

    # NOTE: the HDA's `variants` multiparm authors a *native USD variantSet*
    # on the asset prim, but it is seeded here from the entity's publish-
    # channel list - one property, two meanings. Until assets get their own
    # look list (designs/native-usd-variants.md), a channel and a USD variant
    # name are the same string for an asset.
    # Populate variants from config and sync internal nodes. An entity with
    # no configured channels keeps the HDA's own 'default' from OnCreated.
    if channel_names:
        model_node.parm('variants').set(len(channel_names))
        for i, name in enumerate(channel_names):
            model_node.parm(f'variant_name{i+1}').set(name)
        model_node.hdaModule()._sync_variants(model_node)
    else:
        channel_names = ['default']

    _seed_variant_geo(model_node, channel_names)

    # Create the export layer node (entity + department resolve from context)
    export_node = export_layer.create(scene_node, f'EXPORT_MODEL{suffix}')
    export_node.setInput(0, model_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_native = export_node.native()
    export_native.setPosition(EXPORT_POS + offset)
    export_native.setGenericFlag(hou.nodeFlag.DisplayDescriptiveName, False)
    # Display only: LOP nodes have no setRenderFlag (their render flag
    # follows the display flag), unlike the SOP outputs in _seed_variant_geo.
    export_native.setDisplayFlag(True)

    _sticky_note(scene_node, offset)

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
