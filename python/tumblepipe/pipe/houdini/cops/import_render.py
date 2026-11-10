"""th::import_render: a shot's finished renders, into a comp.

The comp's ``import_shot``. It reads one render department's version of one
channel of a shot and outputs the beauty (``rgba``) and a cable of the
channel's other AOVs the comp reads (``aovs``), each field named after its
AOV. The beauty is not in the cable a second time: a light mix takes it on
its own ``rgba`` input, which is wired for you.

Channel ``*`` (*All channels*) reads every channel of the shot in one node:
``rgba`` is the channels laid over each other back to front in the shot's
channel order (the last on top, as the template stacks them), and the cable
carries every channel's AOVs (``<channel>_<aov>``). A channel with nothing
rendered yet is left out and named on the Reads label. Such an import is
frozen per channel (``pipe.comp.plan_key``).

The third output, ``beauties``, is a cable of each channel's beauty named
after the channel (one field for a one-channel import). The beauties are
not in ``aovs``: a simple comp takes them without unpacking the AOVs (Remy,
2026-11-10).

A digital asset's output count is fixed by its type, so per-field outputs
live on companion Cable Unpacks beside the node (a Cable Unpack's outputs
follow its fields), each kept in step with its cable:

* *Channel Outputs* (on by default): an all-channels import keeps one,
  wired to ``beauties``, with an output per channel.
* *AOV Outputs*: one wired to ``aovs``, with an output per AOV.

A companion's wires follow their field when its fields change, and a wire
from an AOV Outputs field that moved to ``beauties`` (an import built before
the beauties left the cable) moves to the Channel Outputs node.

What it resolves is ``pipe.comp.resolve_render``; this module points the
File COPs inside the node's ``dive`` subnet at it. They are regenerated only
when what the node reads changes (shot, channel, department or version:
``pipe.comp.build_key``), never on a plain cook.

The node joins ``scene_imports.REFRESH_SPECS``: opening a workfile
re-resolves an import on ``latest``, like every other import. When it
rebuilds, the light mixes reading its cable sync their rows and the
export_comps re-check which channels the comp reads.
"""

import hou

from tumblepipe.api import path_str, api
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.util.uri import Uri
from tumblepipe.pipe import comp
from tumblepipe.pipe.paths import get_render_context
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

TYPE_NAME = 'import_render'

# The editable subnet the File COPs live in, its cable packs, and the
# node's outputs (in order).
DIVE = 'dive'
PACK = 'pack'
BEAUTIES_PACK = 'beauties'
RGBA_OUTPUT = 0
AOVS_OUTPUT = 1
BEAUTIES_OUTPUT = 2
# The dive's outputs, as (label, COP subnet output type). An import saved
# before ``beauties`` existed has a dive with the first two; a build gives
# it all three.
DIVE_OUTPUTS = (('rgba', 'vector4'), ('aovs', 'cable'), ('beauties', 'cable'))

# Parm values the node stores what it last resolved in, so a refresh that
# resolves the same thing touches nothing.
RESOLVED_PARM = 'resolved'      # "<department> <version>" (one channel)
FIELDS_PARM = 'fields'          # the cable's fields, space separated
BUILD_KEY_PARM = 'build_key'    # pipe.comp.build_key of the last build
PINS_PARM = 'pins'              # an all-channels import's farm pins
UNPACK_PARM = 'unpack_outputs'
BEAUTY_UNPACK_PARM = 'beauty_outputs'

# The Channel menu's label for comp.ALL_CHANNELS.
ALL_CHANNELS_LABEL = 'All channels'
# User data marking the Cable Unpacks an import keeps for AOV Outputs and
# Channel Outputs.
UNPACK_MARK = 'th_import_render_outputs'
UNPACK_SUFFIX = '_aovs'
BEAUTY_UNPACK_MARK = 'th_import_render_beauties'
BEAUTY_UNPACK_SUFFIX = '_beauties'

# A File COP's 'missingdata' token for a missing frame: output empty (zero),
# so a frame outside the render's range (a pre-roll) does not fail the cook.
MISSING_EMPTY = 1
# File COP 'colorspace' tokens: the scene's colour space for the beauty and
# its light groups, raw for data passes.
COLORSPACE_AUTO = 0
COLORSPACE_RAW = 1


def render_department_names() -> list[str]:
    return [d.name for d in list_departments('render')]


def resolve(shot_uri: Uri, channel: str, department: str, version: str) -> comp.Resolved:
    """``pipe.comp.resolve_render`` on the shot's renders on disk."""
    renders = get_render_context(shot_uri, purpose='render').renders
    return comp.resolve_render(
        renders, render_department_names(), channel, department, version
    )


def version_menu(shot_uri: Uri | None, department: str, channel: str) -> list[str]:
    """The Version menu: ``latest``, then a concrete department's complete
    versions newest first. All channels read ``latest`` only (each channel
    has versions of its own)."""
    if department == comp.BEST or shot_uri is None: return [comp.LATEST]
    if channel == comp.ALL_CHANNELS: return [comp.LATEST]
    renders = get_render_context(shot_uri, purpose='render').renders
    return [comp.LATEST] + comp.list_versions(renders, department, channel)


class ImportRender(EntityNode):

    ENTITY_CONTEXTS = ('shots',)

    # Menus (read-only; Houdini evaluates them on every redraw)

    def list_channel_names(self) -> list[str]:
        shot_uri = self.get_entity_uri()
        if shot_uri is None: return []
        return list_channels(shot_uri)

    def channel_menu(self) -> list[str]:
        """The Channel menu as token/label pairs: the shot's channels, then
        All channels."""
        items = []
        for channel in self.list_channel_names():
            items += [channel, channel]
        return items + [comp.ALL_CHANNELS, ALL_CHANNELS_LABEL]

    def list_department_names(self) -> list[str]:
        return [comp.BEST] + comp.readable_departments(render_department_names())

    def list_version_names(self) -> list[str]:
        return version_menu(
            self.get_entity_uri(),
            self.get_department_setting(),
            self.get_channel_name(),
        )

    # Parms

    def get_channel_name(self) -> str:
        return self.parm('channel').eval()

    def set_channel_name(self, channel: str):
        self.parm('channel').set(channel)

    def is_all_channels(self) -> bool:
        return self.get_channel_name() == comp.ALL_CHANNELS

    def get_department_setting(self) -> str:
        return self.parm('department').eval()

    def get_version_setting(self) -> str:
        return self.parm('version').eval()

    def pin(self, department: str, version: str):
        """Set the Department and Version parms (the farm's freeze of a
        one-channel import)."""
        self.parm('department').set(department)
        self.parm('version').set(version)

    def get_pins(self) -> dict[str, tuple[str, str]]:
        """An all-channels import's farm pins: ``{channel: (department,
        version)}``; empty in a session."""
        return comp.parse_pins(self.parm(PINS_PARM).eval())

    def pin_channels(self, pins: dict[str, tuple[str, str]]):
        """The farm's freeze of an all-channels import: each channel read at
        the version the submit froze."""
        self.parm(PINS_PARM).set(comp.format_pins(pins))

    def get_resolved(self) -> tuple[str, str] | None:
        """``(department, version)`` a one-channel import last resolved,
        None if none (or for all channels)."""
        raw = self.parm(RESOLVED_PARM).eval()
        if not raw or ' ' not in raw: return None
        department, version = raw.split(' ', 1)
        return department, version

    def get_fields(self) -> list[str]:
        """The fields of the ``aovs`` cable as last built."""
        return [field for field in self.parm(FIELDS_PARM).eval().split() if field]

    def get_build_key(self) -> str:
        return self.parm(BUILD_KEY_PARM).eval()

    def read_channels(self) -> list[str]:
        """The channels the node reads: its channel, or for all channels
        those its last build read (a channel not rendered yet is not read)."""
        if not self.is_all_channels():
            return [self.get_channel_name()]
        return comp.build_key_channels(self.get_build_key())

    def get_unpack_outputs(self) -> bool:
        return bool(self.parm(UNPACK_PARM).eval())

    def get_beauty_outputs(self) -> bool:
        """Channel Outputs: on, and the node reads all channels (a
        one-channel import's beauty is its ``rgba``)."""
        parm = self.parm(BEAUTY_UNPACK_PARM)
        return parm is not None and bool(parm.eval()) and self.is_all_channels()

    # Resolution

    def resolve(self) -> comp.Resolved:
        """What a one-channel import's parms resolve to now; raises the
        node's error with the reason when nothing does."""
        shot_uri = self.require_entity_uri()
        channel = self.get_channel_name()
        if channel not in list_channels(shot_uri):
            raise self._error(f'"{channel}" is not a channel of {shot_uri}')
        return self._resolve_channel(
            shot_uri, channel,
            self.get_department_setting(), self.get_version_setting(),
        )

    def _resolve_channel(self, shot_uri, channel, department, version) -> comp.Resolved:
        try:
            resolved = resolve(shot_uri, channel, department, version)
        except comp.CompError as error:
            raise self._error(str(error)) from None
        problems = comp.render_problems({channel: resolved.aovs})
        if problems:
            raise self._error(_unbuildable_message(shot_uri, resolved, problems))
        return resolved

    def resolve_all(self) -> tuple[dict[str, comp.Resolved], list[str]]:
        """What the node reads now: ``({channel: Resolved}, channels left
        out)``, in the shot's channel order.

        One channel: just it, or the node's error. All channels: every
        channel of the shot; one with no complete render yet is left out
        (unless the farm pinned it), and it is an error only when none is
        rendered. A pinned version needs a channel of its own.
        """
        if not self.is_all_channels():
            resolved = self.resolve()
            return {resolved.channel: resolved}, []
        shot_uri = self.require_entity_uri()
        renders = get_render_context(shot_uri, purpose='render').renders
        try:
            reads, skipped = comp.resolve_channels(
                renders, render_department_names(), list_channels(shot_uri),
                self.get_department_setting(), self.get_version_setting(),
                self.get_pins(),
            )
        except comp.CompError as error:
            raise self._error(str(error)) from None
        for resolved in reads.values():
            problems = comp.render_problems({resolved.channel: resolved.aovs})
            if problems:
                raise self._error(_unbuildable_message(shot_uri, resolved, problems))
        return reads, skipped

    def _update_labels(self, text: str | None = None):
        self.parm('entity_label').set(self._entity_label())
        if text is not None:
            self.parm('version_label').set(text)

    def execute(self) -> dict[str, comp.Resolved]:
        """Resolve, and point the File COPs at it when what the node reads
        changed. Returns ``{channel: Resolved}``.

        A rebuild syncs the downstream light mixes' rows; the Channel and
        AOV Outputs unpacks follow what it reads; every export_comp
        re-checks the channels it reads.
        """
        previous = self.get_resolved()
        previous_key = self.get_build_key()
        try:
            reads, skipped = self.resolve_all()
        except ns.NodeError as error:
            self._update_labels(f'none: {str(error).split(": ", 1)[-1]}')
            raise
        shot_uri = self.require_entity_uri()
        key = comp.build_key(
            shot_uri, {c: (r.department, r.version) for c, r in reads.items()}
        )
        plan = comp.dive_plan(
            {c: r.aovs for c, r in reads.items()}, self.is_all_channels()
        )
        rebuilt = key != previous_key or not self._dive_built(plan)
        if rebuilt:
            self._build(reads, plan, key)
        self._update_labels(self._label(reads, skipped, previous, previous_key))
        if rebuilt:
            _sync_downstream_mixes(self.native())
        self.sync_unpacks(plan)
        _refresh_exports()
        return reads

    def import_states(self) -> list[comp.ImportState]:
        """Execute, and say what a submit freezes: one state per channel
        read, keyed by ``pipe.comp.plan_key``."""
        reads = self.execute()
        all_channels = self.is_all_channels()
        return [
            comp.ImportState(
                node_path = comp.plan_key(self.path(), channel, all_channels),
                channel = channel,
                department = self.get_department_setting(),
                version = self.get_version_setting(),
                resolved_department = resolved.department,
                resolved_version = resolved.version,
            )
            for channel, resolved in reads.items()
        ]

    def _label(self, reads, skipped, previous, previous_key) -> str:
        if not self.is_all_channels():
            resolved = next(iter(reads.values()))
            label = resolved.label
            current = (resolved.department, resolved.version)
            previous_reads = previous_key.split()[1:]
            same_channel = bool(previous_reads) and \
                previous_reads[0].split(':', 1)[0] == resolved.channel
            if previous is not None and previous != current and same_channel:
                label += f' (was {previous[1] if previous[0] == current[0] else " ".join(previous)})'
            return label
        versions = sorted({f'{r.department} {r.version}' for r in reads.values()})
        noun = 'channel' if len(reads) == 1 else 'channels'
        label = f'{len(reads)} {noun}: ' + ', '.join(versions)
        if skipped:
            label += f'; not rendered: {", ".join(skipped)}'
        return label

    def _dive_built(self, plan: comp.DivePlan) -> bool:
        dive = self.native().node(DIVE)
        if dive is None or self.get_fields() != list(plan.fields): return False
        # An import saved before the beauties cable has no beauties pack.
        if dive.node(BEAUTIES_PACK) is None: return False
        return all(dive.node(read.name) is not None for read in plan.files)

    def _build(self, reads: dict[str, comp.Resolved], plan: comp.DivePlan, key: str):
        """Rebuild the dive: one File COP per AOV read, the cable's into
        the pack, each beauty into the beauties pack, the beauty (or the
        channels laid over each other) to ``rgba``. Nothing outside the dive
        changes."""
        dive = self.native().node(DIVE)
        if dive is None:
            raise self._error(f'the node has no {DIVE} subnet (a broken HDA)')
        for child in dive.children():
            if child.name() in ('inputs', 'outputs'): continue
            child.destroy()
        _ensure_dive_outputs(dive)
        outputs = dive.node('outputs')
        pack = dive.createNode('cablepack', PACK)
        files: dict[str, hou.Node] = dict()
        for column, read in enumerate(plan.files):
            file_node = dive.createNode('file', read.name)
            _configure_file(file_node, reads[read.channel].aovs[read.aov], read.aov)
            file_node.setPosition(hou.Vector2(column * 2.5, 2.0))
            files[read.name] = file_node
        beauties = [files[name] for name in plan.beauties]
        fields = list(plan.fields)
        column = len(plan.files)
        for index, field in enumerate(fields):
            pack.setInput(index, files[field], 0)
        # Name the fields once every input is wired: wiring an input resizes
        # the pack's field list to the inputs.
        pack.parm('fields').set(len(fields))
        for index, field in enumerate(fields):
            pack.parm(f'fieldname{index + 1}').set(field)
        width = max(column - 1, 0) * 2.5
        pack.setPosition(hou.Vector2(width / 2, 0.0))
        beauty_pack = _pack(dive, BEAUTIES_PACK, beauties, list(plan.beauty_fields))
        beauty_pack.setPosition(hou.Vector2(width / 2 + 2.5, 0.0))
        rgba = _over_chain(dive, beauties, hou.Vector2(width / 2 - 2.5, 0.0))
        if rgba is not None:
            outputs.setInput(RGBA_OUTPUT, rgba, 0)
        outputs.setInput(AOVS_OUTPUT, pack, 0)
        outputs.setInput(BEAUTIES_OUTPUT, beauty_pack, 0)
        outputs.setPosition(hou.Vector2(width / 2, -1.5 - max(len(beauties) - 1, 0)))
        single = None if self.is_all_channels() else next(iter(reads.values()))
        self.parm(RESOLVED_PARM).set(
            f'{single.department} {single.version}' if single else ''
        )
        self.parm(FIELDS_PARM).set(' '.join(fields))
        self.parm(BUILD_KEY_PARM).set(key)

    # Channel Outputs and AOV Outputs

    def find_unpack(self, output_index: int = AOVS_OUTPUT):
        """The Cable Unpack this import keeps on ``output_index`` (AOV
        Outputs on ``aovs``, Channel Outputs on ``beauties``), None if none."""
        mark = _UNPACKS[output_index][0]
        for connection in self.native().outputConnections():
            if connection.outputIndex() != output_index: continue
            node = connection.outputNode()
            if node.userData(mark) is not None:
                return node
        return None

    def sync_unpacks(self, plan: comp.DivePlan | None):
        """Keep both companions in step with ``plan`` (what the node just
        resolved; None when it is not known, which only removes). Channel
        Outputs first, so a wire AOV Outputs drops for a beauty can move to
        it."""
        beauties = self.sync_unpack(plan, BEAUTIES_OUTPUT)
        self.sync_unpack(plan, AOVS_OUTPUT, rehome=beauties)

    def sync_unpack(self, plan: comp.DivePlan | None,
                    output_index: int = AOVS_OUTPUT, rehome=None):
        """Keep the companion unpack on ``output_index`` in step with
        ``plan``: create it beside the node when its toggle is on, give it
        one typed output per cable field, and remove it when the toggle is
        off and nothing reads it. Wires follow their field; a wire whose
        field is gone moves to the same-named output of ``rehome`` (another
        unpack) when it has one. Returns the unpack, None when there is
        none."""
        wanted = (self.get_beauty_outputs() if output_index == BEAUTIES_OUTPUT
                  else self.get_unpack_outputs())
        mark, suffix, offset = _UNPACKS[output_index]
        unpack = self.find_unpack(output_index)
        if not wanted:
            if unpack is not None and not unpack.outputConnections():
                unpack.destroy()
                return None
            return unpack
        if plan is None:
            raise self._error('the outputs need what the node reads: press Update')
        if output_index == BEAUTIES_OUTPUT:
            fields, signatures = list(plan.beauty_fields), plan.beauty_signatures
        else:
            fields, signatures = list(plan.fields), plan.signatures
        native = self.native()
        if unpack is None:
            unpack = native.parent().createNode(
                'cableunpack', f'{native.name()}{suffix}'
            )
            unpack.setUserData(mark, native.name())
            unpack.setInput(0, native, output_index)
            unpack.setPosition(native.position() + hou.Vector2(*offset))
            unpack.setColor(native.color())
        if _unpack_fields(unpack) != fields:
            _set_unpack_fields(unpack, fields, signatures, rehome)
        return unpack

    def open_location(self):
        resolved = self.get_resolved()
        shot_uri = self.require_entity_uri()
        root = api.storage.resolve(
            Uri.parse('render:/render') / shot_uri.segments
        )
        path = root
        if resolved is not None and not self.is_all_channels():
            path = root / resolved[0] / self.get_channel_name() / resolved[1]
        if not path.exists():
            raise self._error(f'{path_str(path)} does not exist yet')
        hou.ui.showInFileBrowser(path_str(path))


# Each companion unpack, by the output it reads: (user data mark, name
# suffix, offset from the import). Plain tuples: the module imports
# without Houdini (the tests' hou stub has no Vector2).
_UNPACKS = {
    AOVS_OUTPUT: (UNPACK_MARK, UNPACK_SUFFIX, (2.0, -0.75)),
    BEAUTIES_OUTPUT: (BEAUTY_UNPACK_MARK, BEAUTY_UNPACK_SUFFIX, (-2.5, -0.75)),
}


def _unpack_fields(unpack) -> list[str]:
    return [
        unpack.parm(f'fieldname{index}').eval()
        for index in range(1, unpack.parm('fields').eval() + 1)
    ]


def _set_unpack_fields(unpack, fields: list[str], signatures, rehome=None):
    """Give ``unpack`` these fields, its wires following their field: a
    Cable Unpack's outputs are by position, so renaming the fields in place
    would leave a wire on whatever field took its slot."""
    current = _unpack_fields(unpack)
    rehome_fields = _unpack_fields(rehome) if rehome is not None else []
    targets = comp.follow_fields(current, fields, rehome_fields)
    # (old output, downstream node, its input); a connection's outputNode()
    # is the downstream node, its inputNode() this unpack.
    wires = [
        (c.outputIndex(), c.outputNode(), c.inputIndex())
        for c in unpack.outputConnections()
        if c.outputIndex() < len(current)
    ]
    for _, node, input_index in wires:
        node.setInput(input_index, None)
    unpack.parm('fields').set(len(fields))
    for index, field in enumerate(fields, start=1):
        unpack.parm(f'fieldname{index}').set(field)
        unpack.parm(f'fieldtype{index}').set(signatures[field])
    for output, node, input_index in wires:
        target = targets[output]
        if target is None: continue
        moved, index = target
        node.setInput(input_index, rehome if moved else unpack, index)


def _ensure_dive_outputs(dive):
    """Give the dive every output in DIVE_OUTPUTS (an import saved before
    ``beauties`` existed has two)."""
    if dive.parm('outputs') is None: return
    if dive.parm('outputs').eval() >= len(DIVE_OUTPUTS): return
    dive.parm('outputs').set(len(DIVE_OUTPUTS))
    for index, (label, kind) in enumerate(DIVE_OUTPUTS, start=1):
        dive.parm(f'outputlabel{index}').set(label)
        dive.parm(f'outputtype{index}').set(kind)


def _pack(dive, name: str, sources: list, fields: list[str]):
    """A Cable Pack in ``dive`` with ``sources`` as ``fields``, in order."""
    pack = dive.createNode('cablepack', name)
    for index, source in enumerate(sources):
        pack.setInput(index, source, 0)
    # Name the fields once every input is wired: wiring an input resizes
    # the pack's field list to the inputs.
    pack.parm('fields').set(len(fields))
    for index, field in enumerate(fields):
        pack.parm(f'fieldname{index + 1}').set(field)
    return pack


def _over_chain(dive, beauties: list, position):
    """The beauties laid over each other, the first at the back: the node
    to wire to ``rgba``, None when there is no beauty."""
    if not beauties: return None
    back = beauties[0]
    for index, front in enumerate(beauties[1:]):
        over = dive.createNode('blend', f'over{index + 1}')
        over.parm('mode').set('over')
        # The blend's first input is the background, its second the
        # foreground laid over it.
        over.setInput(0, back, 0)
        over.setInput(1, front, 0)
        over.setPosition(position + hou.Vector2(0.0, -1.0 * index))
        back = over
    return back


def _configure_file(file_node, aov, field: str):
    render_range = aov.get_frame_range()
    frame_path = aov.get_aov_frame_path('$F4')
    file_node.parm('filename').set(path_str(frame_path))
    file_node.parm('videoframestart').deleteAllKeyframes()
    if render_range is not None:
        file_node.parm('videoframestart').set(render_range.first_frame)
    file_node.parm('missingdata').set(MISSING_EMPTY)
    file_node.parm('missingcolora').set(0)
    file_node.parm('colorspace').set(
        COLORSPACE_AUTO if comp.is_colour_aov(field) else COLORSPACE_RAW
    )
    file_node.parm('aovs').set(1)
    file_node.parm('aov1').set(comp.aov_plane_name(aov))
    file_node.parm('type1').set(comp.aov_signature(field))


def _unbuildable_message(shot_uri, resolved: comp.Resolved, problems) -> str:
    render_root = path_str(api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments
    ))
    return (
        f'cannot comp {resolved.department} {resolved.version} of channel '
        f'"{resolved.channel}": ' + '; '.join(problems) + '. Renders are read '
        f'from {render_root}/<department>/<channel>/v####/<aov>/.'
    )


def find_imports(entity_uri: Uri | None = None) -> list[ImportRender]:
    """Every import_render in the scene; only those addressing
    ``entity_uri`` when given (a Multi's workfile holds several shots')."""
    found = []
    for native in ns.list_by_node_type(TYPE_NAME, 'Cop'):
        node = ImportRender(native)
        if entity_uri is not None:
            try:
                if node.get_entity_uri() != entity_uri: continue
            except ns.NodeError:
                continue
        found.append(node)
    return found


def upstream_output(native, input_index: int = 0):
    """``(import_render, its output index)`` that reaches ``native``'s input
    ``input_index``, following first inputs upstream; None when none does."""
    seen = set()
    current, index = native, input_index
    while current is not None and current.path() not in seen:
        seen.add(current.path())
        connections = current.inputConnections()
        connection = next(
            (c for c in connections if c.inputIndex() == index), None
        )
        if connection is None: return None
        source = connection.inputNode()
        if _is_import_render(source):
            return ImportRender(source), connection.outputIndex()
        current, index = source, 0
    return None


def upstream_import(native, input_index: int = 0):
    """The import_render that reaches ``native``'s input ``input_index``
    (its first), following first inputs upstream; None when there is none."""
    found = upstream_output(native, input_index)
    return None if found is None else found[0]


def _is_import_render(native) -> bool:
    if native is None: return False
    parts = native.type().name().split('::')
    return len(parts) >= 2 and parts[0] == 'th' and parts[1] == TYPE_NAME


def _sync_downstream_mixes(native):
    """Sync the rows of every light_mix that reads this import's cable."""
    from tumblepipe.pipe.houdini.cops import light_mix
    for mix in light_mix.find_mixes():
        source = mix.cable_import()
        if source is None or source.path() != native.path(): continue
        try:
            mix.sync_rows()
        except ns.NodeError:
            pass


def _refresh_exports():
    from tumblepipe.pipe.houdini.cops import export_comp
    export_comp.refresh_warnings()


def create(scene, name):
    return ns.create_node(scene, name, ImportRender, TYPE_NAME, 'Cop')


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    node = ImportRender(raw_node)
    node._update_labels()


def execute(kwargs):
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Import Render'):
        node.execute()


def changed(kwargs):
    """Channel, Department or Version changed: resolve again. A render that
    does not resolve shows on the Reads label, without a dialog."""
    node = ImportRender(kwargs['node'])
    try:
        node.execute()
    except ns.NodeError:
        pass


def unpack_changed(kwargs):
    """AOV Outputs or Channel Outputs toggled: add or remove the Cable
    Unpacks beside the node."""
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Outputs'):
        if node.get_unpack_outputs() or node.get_beauty_outputs():
            node.execute()  # resolves (rebuilding nothing unchanged) and syncs them
        else:
            node.sync_unpacks(None)


def open_location(kwargs):
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Open Location'):
        node.open_location()


def select_entity(kwargs):
    node = ImportRender(kwargs['node'])
    node.pick_entity('Select Shot', contexts=('shots',))
