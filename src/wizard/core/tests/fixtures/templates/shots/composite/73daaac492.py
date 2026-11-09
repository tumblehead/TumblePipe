import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.config.channels import list_channels
from tumblepipe.pipe import comp
from tumblepipe.pipe.houdini.cops import import_render, light_mix, export_comp

# The comp is built from the shot's channels: per channel an import_render
# (its finished render) into a light_mix, the columns over-merged back to
# front into one export_comp (the merged comp). Explicit positions from
# pipe.comp.template_layout: nothing here calls layoutChildren(). A Multi
# repeats the set per member, along x, each import pinned to its member.

NOTE_TEXT_COLOR = hou.Color((0.8, 0.8, 0.8))
NOTE_COLOR = hou.Color((0.0, 0.0, 0.0))


def _sticky_note(parent, note):
    sticky = parent.createStickyNote()
    sticky.setText(note.text)
    sticky.setTextColor(NOTE_TEXT_COLOR)
    sticky.setColor(NOTE_COLOR)
    sticky.setDrawBackground(False)
    sticky.setSize(hou.Vector2(*note.size))
    sticky.setPosition(hou.Vector2(*note.position))
    return sticky


def _build(copnet, shot_uri: Uri, suffix: str = '', x: float = 0.0, pin: bool = False):
    """One shot's comp; returns the layout's width."""
    layout = comp.template_layout(list_channels(shot_uri), suffix, x)
    nodes = dict()
    for name, channel in layout.imports.items():
        node = import_render.create(copnet, name)
        node.set_channel_name(channel)
        if pin:
            node.set_entity_uri(shot_uri)
        nodes[name] = node.native()
    for name, import_name in layout.mixes.items():
        node = light_mix.create(copnet, name)
        node.native().setInput(0, nodes[import_name], import_render.AOVS_OUTPUT)
        nodes[name] = node.native()
    for over_name, back, front in layout.overs:
        over = copnet.createNode('blend', over_name)
        over.parm('mode').set('over')
        # The blend's first input is the background, its second the
        # foreground laid over it.
        over.setInput(0, nodes[back], 0)
        over.setInput(1, nodes[front], 0)
        nodes[over_name] = over
    export_node = export_comp.create(copnet, layout.export)
    if pin:
        export_node.set_entity_uri(shot_uri)
    last = layout.overs[-1][0] if layout.overs else next(iter(layout.mixes), None)
    if last is not None:
        export_node.native().setInput(0, nodes[last], 0)
    nodes[layout.export] = export_node.native()
    for name, (px, py) in layout.positions.items():
        nodes[name].setPosition(hou.Vector2(px, py))
    for note in layout.notes:
        _sticky_note(copnet, note)
    export_node.native().setDisplayFlag(True)
    # Resolve what is rendered already; a channel not rendered yet says so
    # on its import's Reads label.
    for name in layout.imports:
        try:
            import_render.ImportRender(nodes[name]).execute()
        except Exception:
            pass
    export_node.refresh_warning()
    return layout.width


def _create_entity(scene_node, entity_uri: Uri, department_name: str):
    copnet = scene_node.createNode('copnet', 'comp')
    _build(copnet, entity_uri)


def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return
    copnet = scene_node.createNode('copnet', 'comp')
    x = 0.0
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])
        width = _build(copnet, member_uri, suffix=f'_{member_name}', x=x, pin=True)
        x += width + comp.MEMBER_GAP


def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
