"""th::light_mix: a channel's light groups mixed back into its beauty.

Cable in (an import_render's ``aovs``), RGBA out. One row per light group
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

    def upstream_fields(self) -> list[str]:
        """The cable fields of the import_render this mix reads."""
        from tumblepipe.pipe.houdini.cops.import_render import upstream_import
        source = upstream_import(self.native())
        if source is None:
            raise self._error(
                "the input is not a th::import_render's aovs cable; wire the "
                "import's second output into the mix"
            )
        return source.get_fields()

    def sync_rows(self):
        """Sync Rows: a row for every light group the cable has none for,
        then rebuild the mix over the groups present."""
        fields = self.upstream_fields()
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
        inputs, outputs = mix.node('inputs'), mix.node('outputs')

        unpack = mix.createNode('cableunpack', 'unpack')
        fields = [comp.BEAUTY] + groups
        unpack.parm('fields').set(len(fields))
        for index, field in enumerate(fields, start=1):
            unpack.parm(f'fieldname{index}').set(field)
        unpack.setInput(0, inputs, 0)
        unpack.setPosition(hou.Vector2(0.0, 4.0))

        beauty_rgb = mix.createNode('rgbatorgb', 'beauty_rgb')
        beauty_rgb.parm('unpremult').set(0)
        beauty_rgb.setInput(0, unpack, 0)
        beauty_rgb.setPosition(hou.Vector2(-3.0, 2.5))
        alpha = mix.createNode('channelsplit', 'alpha')
        alpha.setInput(0, unpack, 0)
        alpha.setPosition(hou.Vector2(-6.0, 2.5))

        # Rest: the beauty minus every graded group
        rest = beauty_rgb
        for index, group in enumerate(groups):
            minus = mix.createNode('blend', f'minus_{group}')
            minus.parm('mode').set('subtract')
            minus.setInput(0, rest, 0)
            minus.setInput(1, unpack, index + 1)
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
            grade.setInput(0, unpack, index + 1)
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


def sync(kwargs):
    node = LightMix(kwargs['node'])
    with ns.node_errors_as_message('Sync Rows'):
        node.sync_rows()
