import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini.lops import (
    import_shot,
    render_vars,
    lpe_tags,
    puzzle_mattes,
    export_layer
)

# --- /stage layout -------------------------------------------------------
# Explicit positions: this department owns its layout, so nothing here calls
# layoutChildren(). Group workfiles repeat the arrangement per member,
# shifted along x by COLUMN_STRIDE.
IMPORT_POS = hou.Vector2(0.0, 2.62)
GEO_RENDER_SETTINGS_POS = hou.Vector2(0.0, 1.39)
RENDER_VARS_POS = hou.Vector2(0.0, 0.27)
LPE_TAGS_POS = hou.Vector2(0.0, -0.89)
PUZZLE_MATTES_POS = hou.Vector2(0.0, -2.06)
EXPORT_POS = hou.Vector2(0.0, -3.38)
COLUMN_STRIDE = 5.0

# The render vars the shot always ships with; everything else is opt-in.
RENDER_VARS_ENABLED = ('beauty', 'alpha')

# Author the per-prim render visibility primvar so the shot can hide geometry
# from specific ray types. 'set' switches the property on; it defaults to
# 'none', which leaves it unauthored.
RENDER_VISIBILITY_CONTROL = 'xn__primvarskarmaobjectrendervisibility_control_5bcfg'

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))
# One label per editable stage of the chain, sitting left of the node it
# points at. Listed in __stickynote<N> order, not top-to-bottom order: the
# render-visibility note was added last but sits highest.
NOTES = [
    ('Add utility AOVs here -->',
     hou.Vector2(-4.03, 0.16), hou.Vector2(2.96, 0.52)),
    ('Add custom light passes here -->',
     hou.Vector2(-4.01, -1.1), hou.Vector2(3.65, 0.61)),
    ('Add RGB masks here -->',
     hou.Vector2(-4.02, -2.21), hou.Vector2(3.65, 0.61)),
    ('Set custom render visibility on scene geometry here -->',
     hou.Vector2(-4.0, 1.09), hou.Vector2(2.96, 0.91)),
]

def _sticky_note(parent, text: str, position: hou.Vector2, size: hou.Vector2):
    note = parent.createStickyNote()
    note.setText(text)
    note.setTextColor(NOTE_TEXT_COLOR)
    note.setColor(NOTE_COLOR)
    note.setDrawBackground(False)
    note.setSize(size)
    note.setPosition(position)
    return note

def _build(scene_node, entity_uri: Uri, department_name: str, suffix: str = '',
           offset: hou.Vector2 = hou.Vector2(0.0, 0.0), pin: bool = False):
    """Build one shot's import -> render vars -> lpe -> mattes -> export."""
    # Create the import node (shot resolves from context)
    import_node = import_shot.create(scene_node, f'import_shot{suffix}')
    if pin:
        import_node.set_shot_uri(entity_uri)
        import_node.set_department_name(department_name)

    import_native = import_node.native()
    import_native.setPosition(IMPORT_POS + offset)
    prev_node = import_native

    # Per-prim render geometry settings (ray visibility, motion blur, limits)
    geo_render_settings_node = scene_node.createNode(
        'rendergeometrysettings', f'geo_render_settings{suffix}'
    )
    geo_render_settings_node.setInput(0, prev_node)
    geo_render_settings_node.parm(RENDER_VISIBILITY_CONTROL).set('set')
    geo_render_settings_node.setPosition(GEO_RENDER_SETTINGS_POS + offset)
    prev_node = geo_render_settings_node

    # Declare the render vars (utility AOVs get added onto this node)
    render_vars_node = render_vars.create(scene_node, f'render_vars{suffix}')
    render_vars_node.setInput(0, prev_node)
    for parm_name in RENDER_VARS_ENABLED:
        render_vars_node.parm(parm_name).set(True)

    render_vars_native = render_vars_node.native()
    render_vars_native.setPosition(RENDER_VARS_POS + offset)
    prev_node = render_vars_native

    # Light path expression tags (custom light passes get added here)
    lpe_tags_node = lpe_tags.create(scene_node, f'lpe_tags{suffix}')
    lpe_tags_node.setInput(0, prev_node)
    lpe_tags_node.native().setPosition(LPE_TAGS_POS + offset)
    prev_node = lpe_tags_node.native()

    # Puzzle mattes (RGB masks get added here)
    puzzle_mattes_node = puzzle_mattes.create(
        scene_node, f'puzzle_mattes{suffix}'
    )
    puzzle_mattes_node.setInput(0, prev_node)
    puzzle_mattes_node.native().setPosition(PUZZLE_MATTES_POS + offset)
    prev_node = puzzle_mattes_node.native()

    # Create the export node
    export_node = export_layer.create(scene_node, f'export_shot{suffix}')
    export_node.setInput(0, prev_node)
    if pin:
        export_node.set_entity_uri(entity_uri)
        export_node.set_department_name(department_name)

    export_node.native().setPosition(EXPORT_POS + offset)

    # The geometry settings node holds the display flag rather than the
    # export at the end of the chain.
    geo_render_settings_node.setDisplayFlag(True)

    for text, position, size in NOTES:
        _sticky_note(scene_node, text, position + offset, size)

    return export_node

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
