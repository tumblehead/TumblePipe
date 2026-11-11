"""th::light_mix: a channel's light groups mixed back into its beauty.

Two inputs, RGBA out: an import_render's beauty (``rgba``) and its cable
(``aovs``), in the order the import's outputs come in, so the two wires do
not cross (Remy, 2026-11-10; the cable used to come first). A sync wires the
beauty from that import when its input is empty (the cable no longer
carries the beauty; a cable that does is read when the beauty input is
empty), and puts a mix wired the old way round back in order
(:func:`swapped_inputs`). One row per light group
(``beauty_<tag>``): Mute, Exposure (stops) and Tint. The mix is the graded
groups plus **Rest**, the beauty minus the groups (the lights in no group),
which has a row of its own. So at defaults the output is the beauty
exactly, never the beauty added to its groups (summing both doubles the
light). The alpha is always the beauty's A.

A group in the cable without a row rides in Rest, so it mixes at unity like
a row at its defaults until Sync Rows gives it one; Sync is pressed for you
when the upstream import_render changes version. Rows are never removed or
changed by a sync (``pipe.comp.sync_rows``).

The network lives in the editable ``mix`` subnet and is rebuilt by Python on
every sync: a COP block loop cannot iterate a cable's fields by name, and a
built network is one the artist can open and read. Everything stays
premultiplied: the RGB/RGBA conversions run with (un)premultiply off.
"""

import hou

from tumblepipe.pipe import comp
import tumblepipe.pipe.houdini.nodes as ns

TYPE_NAME = 'light_mix'

MIX = 'mix'
ROWS_PARM = 'groups'
# The inputs: the import's beauty, and its cable (import_render's outputs'
# order: rgba, then aovs).
BEAUTY_INPUT = 0
CABLE_INPUT = 1
# The mix subnet's inputs, in the HDA's order
MIX_IO = (('rgba', 'vector4'), ('aovs', 'cable'))


def swapped_inputs(sources: dict) -> bool:
    """Whether a mix's inputs are the old way round: ``sources`` maps an
    input index to the import output index reaching it (None when the
    input reaches no import). The cable on the beauty input, or the beauty
    on the cable input, is a mix wired before the inputs swapped."""
    from tumblepipe.pipe.houdini.cops.import_render import AOVS_OUTPUT, RGBA_OUTPUT
    beauty, cable = sources.get(BEAUTY_INPUT), sources.get(CABLE_INPUT)
    return (beauty == AOVS_OUTPUT and cable != AOVS_OUTPUT) or (
        cable == RGBA_OUTPUT and beauty != RGBA_OUTPUT
    )


class LightMix(ns.Node):

    def rows(self) -> list[str]:
        """The rows' group names, in row order."""
        count = self.parm(ROWS_PARM).eval()
        return [self.parm(f'group{index}').eval() for index in range(1, count + 1)]

    def _row_index(self, group: str) -> int:
        return self.rows().index(group) + 1

    def _set_rows(self, groups: list[str]):
        """Add rows so the multiparm lists ``groups``; the existing rows are
        a prefix of it and are left as they are."""
        existing = self.rows()
        assert groups[:len(existing)] == existing
        native = self.native()
        native.parm(ROWS_PARM).set(len(groups))
        for index, group in enumerate(groups[len(existing):], start=len(existing) + 1):
            native.parm(f'group{index}').set(group)
            native.parm(f'mute{index}').set(0)
            native.parm(f'exposure{index}').set(0.0)
            native.parmTuple(f'tint{index}').set((1.0, 1.0, 1.0))

    def order_inputs(self) -> bool:
        """Swap a mix wired the old way round (cable first) into the
        HDA's order; whether it did."""
        from tumblepipe.pipe.houdini.cops.import_render import upstream_output
        native = self.native()
        sources = dict()
        for index in (BEAUTY_INPUT, CABLE_INPUT):
            found = upstream_output(native, index)
            sources[index] = None if found is None else found[1]
        if not swapped_inputs(sources): return False
        wires = {c.inputIndex(): (c.inputNode(), c.outputIndex())
                 for c in native.inputConnections()}
        for index in (BEAUTY_INPUT, CABLE_INPUT):
            native.setInput(index, None)
        for index, other in ((BEAUTY_INPUT, CABLE_INPUT), (CABLE_INPUT, BEAUTY_INPUT)):
            if other in wires:
                native.setInput(index, *wires[other])
        return True

    def cable_import(self):
        """The import_render whose cable reaches the cable input (inputs
        put in order first), None when there is none."""
        from tumblepipe.pipe.houdini.cops.import_render import upstream_import
        self.order_inputs()
        return upstream_import(self.native(), input_index=CABLE_INPUT)

    def upstream(self):
        """The import_render whose cable this mix reads; the node's error
        when there is none, or when it reads every channel (a mix grades one
        channel's light groups)."""
        source = self.cable_import()
        if source is None:
            raise self._error(
                "the aovs input is not a th::import_render's aovs cable; wire "
                "the import's aovs output into the mix's aovs input"
            )
        if source.is_all_channels():
            raise self._error(
                f'{source.path()} reads all channels; a light mix grades one '
                "channel's light groups, so read that channel with an import "
                'of its own'
            )
        return source

    def upstream_fields(self) -> list[str]:
        """The cable fields of the import_render this mix reads."""
        return self.upstream().get_fields()

    def connect_beauty(self, source=None):
        """Wire the upstream import's ``rgba`` into an empty beauty input."""
        from tumblepipe.pipe.houdini.cops.import_render import RGBA_OUTPUT
        native = self.native()
        if native.input(BEAUTY_INPUT) is not None: return
        source = source or self.upstream()
        native.setInput(BEAUTY_INPUT, source.native(), RGBA_OUTPUT)

    def sync_rows(self):
        """Sync Rows: wire the beauty when its input is empty, add a row for
        every light group the cable has none for, then rebuild the mix over
        the groups present."""
        source = self.upstream()
        fields = source.get_fields()
        self.connect_beauty(source)
        rows = comp.sync_rows(self.rows(), comp.light_groups(fields))
        self._set_rows(rows)
        self._build(comp.mixed_groups(rows, fields))

    def _build(self, groups: list[str]):
        mix = self.native().node(MIX)
        if mix is None:
            raise self._error(f'the node has no {MIX} subnet (a broken HDA)')
        for child in mix.children():
            if child.name() in ('inputs', 'outputs'): continue
            child.destroy()
        _ensure_io(mix)
        inputs, outputs = mix.node('inputs'), mix.node('outputs')

        # The beauty comes on its own input; a mix whose beauty input is
        # empty reads a cable that still carries it (built before the
        # cable dropped it, or packed by hand).
        cable_beauty = self.native().input(BEAUTY_INPUT) is None
        unpack = mix.createNode('cableunpack', 'unpack')
        fields = comp.mix_unpack_fields(groups, cable_beauty)
        unpack.parm('fields').set(len(fields))
        for index, field in enumerate(fields, start=1):
            unpack.parm(f'fieldname{index}').set(field)
        unpack.setInput(0, inputs, CABLE_INPUT)
        unpack.setPosition(hou.Vector2(0.0, 4.0))
        beauty, beauty_output = (unpack, 0) if cable_beauty else (inputs, BEAUTY_INPUT)

        beauty_rgb = mix.createNode('rgbatorgb', 'beauty_rgb')
        beauty_rgb.parm('unpremult').set(0)
        beauty_rgb.setInput(0, beauty, beauty_output)
        beauty_rgb.setPosition(hou.Vector2(-3.0, 2.5))
        alpha = mix.createNode('channelsplit', 'alpha')
        alpha.setInput(0, beauty, beauty_output)
        alpha.setPosition(hou.Vector2(-6.0, 2.5))

        # Rest: the beauty minus every graded group
        rest = beauty_rgb
        for index, group in enumerate(groups):
            minus = mix.createNode('blend', f'minus_{group}')
            minus.parm('mode').set('subtract')
            minus.setInput(0, rest, 0)
            minus.setInput(1, unpack, fields.index(group))
            minus.setPosition(hou.Vector2(-3.0, 1.5 - index))
            rest = minus
        rest_grade = mix.createNode('bright', 'rest')
        rest_grade.setInput(0, rest, 0)
        _drive(rest_grade, 'rest_mute', 'rest_exposure', 'rest_tint')
        rest_grade.setPosition(hou.Vector2(-3.0, 0.5 - len(groups)))

        total = rest_grade
        for index, group in enumerate(groups):
            row = self._row_index(group)
            grade = mix.createNode('bright', group)
            grade.setInput(0, unpack, fields.index(group))
            _drive(grade, f'mute{row}', f'exposure{row}', f'tint{row}')
            grade.setPosition(hou.Vector2(index * 2.5, 2.5))
            plus = mix.createNode('blend', f'plus_{group}')
            plus.parm('mode').set('add')
            plus.setInput(0, total, 0)
            plus.setInput(1, grade, 0)
            plus.setPosition(hou.Vector2(index * 2.5, -0.5 - len(groups)))
            total = plus

        rgba = mix.createNode('rgbtorgba', 'rgba')
        rgba.parm('premult').set(0)
        rgba.setInput(0, total, 0)
        rgba.setInput(1, alpha, 3)
        rgba.setPosition(hou.Vector2(0.0, -2.0 - len(groups)))
        outputs.setInput(0, rgba, 0)
        outputs.setPosition(hou.Vector2(0.0, -3.5 - len(groups)))

        self.parm('mix_label').set(
            f'{len(groups)} light group{"" if len(groups) == 1 else "s"} + Rest'
            if groups else 'no light groups: Rest is the whole beauty'
        )


def _ensure_io(mix):
    """Give the mix subnet the HDA's two inputs in the HDA's order: one
    saved before the beauty input existed has only the cable, one saved
    before the inputs swapped has them cable first."""
    current = [
        (mix.parm(f'inputlabel{index}').eval(), mix.parm(f'inputtype{index}').eval())
        for index in range(1, mix.parm('inputs').eval() + 1)
    ]
    if current == list(MIX_IO): return
    mix.parm('inputs').set(len(MIX_IO))
    for index, (label, kind) in enumerate(MIX_IO, start=1):
        mix.parm(f'inputlabel{index}').set(label)
        mix.parm(f'inputtype{index}').set(kind)
    root_inputs = mix.parent().node('inputs')
    mix.setInput(BEAUTY_INPUT, root_inputs, BEAUTY_INPUT)
    mix.setInput(CABLE_INPUT, root_inputs, CABLE_INPUT)


def _drive(bright, mute: str, exposure: str, tint: str):
    """Point a Bright COP at a row's parms on the light_mix (two levels up:
    the mix subnet, then the HDA)."""
    bright.parm('bright').setExpression(
        f'pow(2,ch("../../{exposure}"))*(1-ch("../../{mute}"))'
    )
    for component in 'rgb':
        bright.parm(f'brighttint{component}').setExpression(
            f'ch("../../{tint}{component}")'
        )


def find_mixes() -> list[LightMix]:
    return [LightMix(native) for native in ns.list_by_node_type(TYPE_NAME, 'Cop')]


def create(scene, name):
    return ns.create_node(scene, name, LightMix, TYPE_NAME, 'Cop')


def on_created(raw_node):
    ns.set_node_style(raw_node)


def on_loaded(raw_node):
    """A mix saved before the inputs swapped: its wires come back cable
    first and its mix network reads them that way, so put the wires in
    order and rebuild the network. A mix with nothing to read keeps its
    own error until it is wired.

    The rebuild destroys nodes, so it waits until the scene has loaded:
    done inside the load it crashed Houdini (Remy, 2026-11-11;
    ``pipe.houdini.after_load``)."""
    from tumblepipe.pipe.houdini import after_load
    session_id = raw_node.sessionId()

    def repair():
        native = hou.nodeBySessionId(session_id)
        if native is not None:
            repair_loaded(native)

    after_load.run(repair)


def repair_loaded(raw_node):
    """:func:`on_loaded`'s work, on a scene that has finished loading."""
    node = LightMix(raw_node)
    mix = raw_node.node(MIX)
    old_io = mix is not None and mix.parm('inputlabel1').eval() != MIX_IO[0][0]
    if not node.order_inputs() and not old_io: return
    try:
        node.sync_rows()
    except Exception:
        if mix is not None:
            _ensure_io(mix)


def sync(kwargs):
    node = LightMix(kwargs['node'])
    with ns.node_errors_as_message('Sync Rows'):
        node.sync_rows()
