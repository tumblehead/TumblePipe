"""th::import_render: a shot's finished renders, into a comp.

The comp's ``import_shot``. It reads one render department's version of one
channel of a shot, or every channel. Its outputs, top to bottom (#67, after
Remy's mockup of 2026-11-12):

    rgba .. rgba 8   each channel's beauty, one output per channel read
    lpe              a cable of the light groups (``beauty_<tag>``), a light mix's
    aov              a cable of the other AOVs (albedo, normal, depth, uv, ...)
    matte            a cable of the mattes, distance ramps and holdouts
    cryptomaterials  a cable of the cryptomaterials rank planes, all of them }
    cryptoprimitives a cable of the cryptoprimitives rank planes            } crypto1
    geometry         the staged scene's geometry  } th::import_scene, inside the
    camera           the shot's render camera     } node, for a Rasterize Geometry

Each cable field is named after its AOV; which cable an AOV rides is
``pipe.comp.aov_cable``. The beauty is in no cable: a light mix takes it on
its own ``rgba`` input, which is wired for you, and its groups on ``lpe``.
A Cryptomatte COP reads every field of the cable on its ``crypto1`` and
needs all of one cryptomatte's rank planes (two ranks each), so each
cryptomatte th::render_vars renders has a cable of its own to wire there
whole; any other cryptomatte rides ``matte``.

A digital asset's outputs are fixed by its type (verified in 22.0.429: an
instance can neither add outputs nor relabel them), so the per-channel
beauties ride :data:`CHANNEL_OUTPUTS` fixed outputs, ``rgba`` then
``rgba2`` .. ``rgba8``; those no channel holds are empty. A one-channel
import's beauty is always ``rgba``. An all-channels import gives each
channel an output of its own and keeps it there (``channel_outputs``,
:func:`assign_channel_outputs`): a channel added later takes a free output,
one not rendered yet keeps its output for when it is, so a wire stays on
its channel through Updates. Which layer (channel) is on which output is
written under the node in the network editor, its comment (``rgba: chars``,
``rgba 2: bg``; :func:`output_names_comment`), and on the Reads label: the
outputs' own labels cannot change per node, and the Composite View's layer
menu lists them (C, the first output, then each output that holds
something, by label).

Channel ``*`` (*All channels*) reads every channel of the shot in one node:
the cables carry every channel's AOVs (``<channel>_<aov>``). A channel with
nothing rendered yet is left out and named on the Reads label. Such an
import is frozen per channel (``pipe.comp.plan_key``).

``geometry`` and ``camera`` come from an embedded th::import_scene
(:data:`SCENE`): its shot and channel are this node's (``default`` for all
channels), its department, version, camera and geometry the *Scene*
folder's. It imports when Update is pressed, and on any other run only
while ``geometry`` or ``camera`` is wired: an import_shot import composes a
whole stage, and most comps never rasterize.

*AOV Outputs* keeps a companion Cable Unpack beside the node per AOV cable
with fields (``lpe``, ``aov``, ``matte``), with an output per field (a
Cable Unpack's outputs follow its fields). A companion's wires follow their
field when its fields change, and a wire on a field that moved to another
cable (an import built before #67 split the AOVs) moves to that cable's
companion; a wire on a channel's beauty (TumblePipe 1.99 carried them in
the AOVs) to the channel's output on the node.

An import saved before #67 had three outputs: ``rgba`` (for all channels,
the channels laid over each other), ``aovs`` (every AOV) and ``beauties``
(a cable of each channel's beauty). Its ``outputs_layout`` reads 0 (a new
node's is :data:`OUTPUTS_LAYOUT`); on load and on Update
(:meth:`ImportRender.upgrade`) a wire on ``aovs`` moves to ``aov`` (a light
mix's to ``lpe``), a wire on ``rgba`` stays on ``rgba``, or for all channels
moves to an over chain of the channel outputs kept beside the node, so the
picture does not change; a wire taken from ``beauties`` through a Cable
Unpack moves to the channel's output, and the node rebuilds.

What it resolves is ``pipe.comp.resolve_render``; this module points the
File COPs inside the node's ``dive`` subnet at it. They are regenerated only
when what the node reads changes (shot, channel, department or version:
``pipe.comp.build_key``), never on a plain cook. Inside, the dive reads
left to right (:func:`dive_positions`): a row per File COP, its cache and
switch beside it, the cable packs, the outputs.

The node joins ``scene_imports.REFRESH_SPECS``: opening a workfile
re-resolves an import on ``latest``, like every other import. When it
rebuilds, the light mixes reading its cable sync their rows and the
export_comps re-check which channels the comp reads.

On a Multi's shared timeline a member shot sits on other frames than its
own (``config.multi_timeline``), and its renders are on its own. The node's
**Time Offset** (``pipe.houdini.time_offset``, From Multi by default, as on
import_shot) moves the renders there: frame F of the scene reads the
render's frame F - offset (Remy, 2026-11-11: a bookmark-timeline Multi's
comp read every member but the first on the wrong frames, so they were
black). The offset is kept in ``applied_offset``, which the File COPs read,
so a timeline edit moves them without a rebuild (:func:`sync_time_offsets`).
An import that never had an offset keeps plain ``$F4`` frames. The composite
worker sets every import to None: a farm comp writes the shot's own frames.
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
from tumblepipe.pipe.houdini.time_offset import TimeOffsetNode

TYPE_NAME = 'import_render'

# The offset the File COPs read, and the frame expression that reads it:
# frame F of the scene is the render's frame F - offset.
APPLIED_OFFSET_PARM = 'applied_offset'
PLAIN_FRAME = '$F4'
OFFSET_FRAME = f'`padzero(4, $F-ch("../../{APPLIED_OFFSET_PARM}"))`'

# The node's outputs, in order (#67), and their indices: the channel
# outputs, the AOV cables, then the scene's.
CHANNEL_OUTPUTS = 8
CHANNEL_OUTPUT_NAMES = ('rgba',) + tuple(f'rgba{i}' for i in range(2, CHANNEL_OUTPUTS + 1))
LPE, AOV, MATTE = 'lpe', 'aov', 'matte'
CRYPTO_OUTPUT_NAMES = comp.CRYPTO_CABLES
CABLE_OUTPUT_NAMES = (LPE, AOV, MATTE) + CRYPTO_OUTPUT_NAMES
OUTPUTS = CHANNEL_OUTPUT_NAMES + CABLE_OUTPUT_NAMES + ('geometry', 'camera')
RGBA_OUTPUT = OUTPUTS.index('rgba')
LPES_OUTPUT = OUTPUTS.index(LPE)
AOVS_OUTPUT = OUTPUTS.index(AOV)
MATTES_OUTPUT = OUTPUTS.index(MATTE)
GEOMETRY_OUTPUT = OUTPUTS.index('geometry')
CAMERA_OUTPUT = OUTPUTS.index('camera')
# Each AOV cable's output (the cables are named as in pipe.comp).
CABLE_OUTPUTS = {comp.LPES: LPES_OUTPUT, comp.AOVS: AOVS_OUTPUT, comp.MATTES: MATTES_OUTPUT,
                 **{cable: OUTPUTS.index(cable) for cable in comp.CRYPTO_CABLES}}
OUTPUT_CABLES = {index: cable for cable, index in CABLE_OUTPUTS.items()}


def output_label(name: str) -> str:
    """An output's label: ``rgba 2`` for ``rgba2``, else its name."""
    if name.startswith('rgba') and name[4:].isdigit():
        return f'rgba {name[4:]}'
    return name


# The outputs an import saved before #67 had (``rgba`` then laid the
# channels over each other, ``aovs`` carried every AOV, ``beauties`` each
# channel's beauty), and the layout a node is on: 0 before #67 (a parm such
# a node never saved reads its default), OUTPUTS_LAYOUT since (set at
# creation).
LEGACY_OUTPUTS = ('rgba', 'aovs', 'beauties')
LEGACY_MOVES = {'rgba': 'rgba', 'aovs': AOV}
OUTPUTS_LAYOUT_PARM = 'outputs_layout'
OUTPUTS_LAYOUT = 1


def outputs_layout(native) -> int:
    """The outputs layout an import_render node is on (an HDA build without
    the parm is on today's)."""
    parm = native.parm(OUTPUTS_LAYOUT_PARM)
    return OUTPUTS_LAYOUT if parm is None else int(parm.eval())


def moved_output(index: int, layout: int) -> int | None:
    """Where a plain wire on output ``index`` of a node on ``layout``
    belongs now: ``rgba`` stays, ``aovs`` is ``aov``. None for an index the
    layout never had, and for ``beauties``, which has no output now (a
    wire through a Cable Unpack moves per channel, :meth:`ImportRender.upgrade`)."""
    if layout >= OUTPUTS_LAYOUT:
        return index if 0 <= index < len(OUTPUTS) else None
    if not 0 <= index < len(LEGACY_OUTPUTS): return None
    name = LEGACY_MOVES.get(LEGACY_OUTPUTS[index])
    return None if name is None else OUTPUTS.index(name)


# Which channel is on which channel output, kept in ``channel_outputs``
# (space separated, ``-`` for an output no channel holds).
CHANNEL_OUTPUTS_PARM = 'channel_outputs'
_FREE = '-'


def format_channel_outputs(slots) -> str:
    return ' '.join(slot or _FREE for slot in slots)


def parse_channel_outputs(text: str) -> tuple:
    """The channel on each channel output (None where none), always
    :data:`CHANNEL_OUTPUTS` long."""
    slots = [None if token == _FREE else token for token in text.split()]
    slots = slots[:CHANNEL_OUTPUTS]
    return tuple(slots + [None] * (CHANNEL_OUTPUTS - len(slots)))


def assign_channel_outputs(previous, channels, all_channels: bool,
                           count: int = CHANNEL_OUTPUTS) -> tuple[tuple, list]:
    """Which channel each channel output carries for a read of ``channels``
    (in the shot's channel order), given the outputs it ``previous``-ly had:
    ``(slots, overflow)``, ``slots`` ``count`` long (None for a free output)
    and ``overflow`` the channels read that found no output.

    One channel: on the first output, always (switching the channel keeps
    the wire). All channels: a channel keeps the output it had; a new one
    takes the first free output, else the first one held by a channel not
    read now (not rendered yet, or gone from the shot); a channel not read
    keeps its output while no read channel needs it, so a wire on it stays
    on its channel until the channel renders.
    """
    if not all_channels:
        first = list(channels)[:1]
        return tuple(first + [None] * (count - len(first))), list(channels)[1:]
    previous = list(previous)[:count]
    slots = previous + [None] * (count - len(previous))
    read = list(dict.fromkeys(channels))
    overflow = []
    for channel in read:
        if channel in slots: continue
        free = next((i for i, s in enumerate(slots) if s is None), None)
        if free is None:
            free = next((i for i, s in enumerate(slots) if s not in read), None)
        if free is None:
            overflow.append(channel)
            continue
        slots[free] = channel
    return tuple(slots), overflow


def channel_outputs_label(slots, reads, overflow) -> str:
    """What the Reads label adds for an all-channels import: which channel
    is on which output (the outputs' labels cannot name them), and the
    channels read that found none."""
    held = [f'{output_label(CHANNEL_OUTPUT_NAMES[i])} {channel}'
            for i, channel in enumerate(slots) if channel in reads]
    text = '; outputs: ' + ', '.join(held) if held else ''
    if overflow:
        text += f'; no output for: {", ".join(overflow)}'
    return text


# Which layer (channel) each beauty output carries, written on the node in
# the network editor (Remy, 2026-11-13: "the outputs should be named by the
# layer name, not rgba"). A digital asset's output labels are its type's
# (verified in 22.0.429: an instance has no parm or call that relabels them,
# and the Composite View's layer menu lists those labels), so the names go
# in the node's comment, shown under it. The comment is the node's own only
# while it still reads as it was last written (user data
# OUTPUT_NAMES_COMMENT_KEY): a comment someone typed is never replaced.
OUTPUT_NAMES_COMMENT_KEY = 'th_import_render_output_names'


def channel_output_names(slots, reads=None) -> tuple:
    """What each beauty output carries, by name, in output order: the layer
    (channel) it holds, else its own label (``rgba 4``) when it holds none
    or holds a channel not read now (``reads``; None reads every held
    channel). Always :data:`CHANNEL_OUTPUTS` long, and no two names alike:
    a channel name that is another output's label (a channel called
    ``rgba 2``) keeps its output's label beside it."""
    slots = parse_channel_outputs(format_channel_outputs(slots))
    labels = [output_label(name) for name in CHANNEL_OUTPUT_NAMES]
    names = []
    for label, channel in zip(labels, slots):
        held = channel is not None and (reads is None or channel in reads)
        names.append(channel if held else label)
    counts = {name: names.count(name) for name in names}
    return tuple(
        f'{name} ({label})' if counts[name] > 1 and name != label else name
        for name, label in zip(names, labels)
    )


def output_names_comment(slots, reads=None, overflow=()) -> str:
    """The node's comment: one line per beauty output that holds a layer
    read now, ``<output label>: <layer>``, in output order; then the layers
    read that found no output. Empty when no output holds one."""
    names = channel_output_names(slots, reads)
    lines = [f'{output_label(CHANNEL_OUTPUT_NAMES[i])}: {name}'
             for i, name in enumerate(names)
             if name != output_label(CHANNEL_OUTPUT_NAMES[i])]
    if overflow:
        lines.append(f'no output: {", ".join(overflow)}')
    return '\n'.join(lines)


def next_comment(current: str, last_written: str | None, wanted: str) -> str | None:
    """The comment to set, None to leave the node's alone: the node's own
    comment (empty, or as last written) follows ``wanted``; one someone
    typed stays."""
    if current == wanted: return None
    if current and current != (last_written or ''): return None
    return wanted


# Inside the dive, left to right (#67, Remy 2026-11-12: the File COPs sat in
# one row under their long file name labels, the packs and outputs stacked
# on each other): a row per File COP, the beauties first, then the AOVs by
# cable in output order (lpe, aov, matte, the cryptomattes); its Cache COP and switch on its
# row; then the cable packs, each on its first read's row; then the
# outputs. A File COP draws its file name above its name, a line more than
# comp.NODE_FOOTPRINT allows for.
DIVE_ROW = comp.NODE_FOOTPRINT[1] + 0.9
DIVE_COLUMNS = {'file': 0, 'cache': 1, 'switch': 2, 'pack': 3, 'outputs': 4}


def _CABLES_IN_OUTPUT_ORDER() -> list[str]:
    return [OUTPUT_CABLES[OUTPUTS.index(name)] for name in CABLE_OUTPUT_NAMES]


def dive_rows(plan) -> list[str]:
    """The dive's File COPs, top to bottom (:data:`DIVE_ROW` apart)."""
    rows = list(plan.beauties)
    for cable in _CABLES_IN_OUTPUT_ORDER():
        rows += [f for f in plan.cable_fields(cable) if f not in rows]
    rows += [read.name for read in plan.files if read.name not in rows]
    return rows


def dive_positions(plan) -> dict[str, tuple[float, float]]:
    """Where each node of the dive goes, by name: every File COP, its cache
    and switch, the cable packs and ``outputs``."""
    stride = comp.COLUMN_STRIDE

    def at(column: str, row: float) -> tuple[float, float]:
        return (DIVE_COLUMNS[column] * stride, -row * DIVE_ROW)

    positions = {}
    rows = dive_rows(plan)
    for row, name in enumerate(rows):
        positions[name] = at('file', row)
        positions[cache_node_name(name)] = at('cache', row)
        positions[cache_switch_name(name)] = at('switch', row)
    # A pack beside its first read, or lower when an empty cable's pack
    # took that row: never on another pack.
    next_free = len(plan.beauties)
    for cable in _CABLES_IN_OUTPUT_ORDER():
        fields = plan.cable_fields(cable)
        row = max(rows.index(fields[0]), next_free) if fields else next_free
        positions[CABLE_PACKS[cable]] = at('pack', row)
        next_free = row + 1
    positions['outputs'] = at('outputs', 0)
    return positions


# The editable subnet the File COPs live in, its cable packs (named before
# #67 renamed the outputs), and the embedded th::import_scene that gives
# geometry and camera.
DIVE = 'dive'
PACK = 'pack'
BEAUTIES_PACK = 'beauties'  # a dive built before #67 had it
CABLE_PACKS = {comp.AOVS: PACK, comp.LPES: 'lpes', comp.MATTES: 'mattes',
               **{cable: cable for cable in comp.CRYPTO_CABLES}}
SCENE = 'scene'
# Whether the scene imported, and its camera cooks: the geometry and camera
# outputs read the embedded import_scene only then (Copernicus cooks every
# output of the node together, so a camera that cannot cook would fail the
# renders as well), and are empty before.
SCENE_READY_PARM = 'scene_ready'
CAMERA_READY_PARM = 'camera_ready'
SCENE_LABEL_PARM = 'scene_label'

# The frame cache (#73, Remy): a File COP read is wire-bound (~0.5 s a 1080p
# frame from the server), so every read in the dive passes through a Cache
# COP that keeps the frames played. Cache (on by default) caches the beauty
# reads, so the channel outputs; Cache AOVs Too (off: they are many, and
# memory is bounded only by Max Frames) adds the AOV reads, so lpe, aov
# and matte. Each read is a Switch between its File COP (0) and its
# Cache COP (1), so an uncached read never pulls its cache. The caches sit
# on the reads, not on the node's outputs: Copernicus cooks every output
# of a subnet together, so a cache on rgba alone was read past whenever the
# raw aovs output cooked the dive (verify_comp counts the File COP cooks).
# geometry and camera are not cached.
CACHE_PARM = 'cache'
CACHE_AOVS_PARM = 'cache_aovs'
CACHE_FRAMES_PARM = 'cache_frames'
CACHE_FRAMES_DEFAULT = 250
CACHE_CLEAR_PARM = 'clear_cache'
# The toggles a read's cache needs on, all of them, by what it reads.
BEAUTY_CACHE_TOGGLES = (CACHE_PARM,)
AOV_CACHE_TOGGLES = (CACHE_PARM, CACHE_AOVS_PARM)
# So, the outputs served from cache by the toggles they need.
CACHE_TOGGLES = {
    **{name: BEAUTY_CACHE_TOGGLES for name in CHANNEL_OUTPUT_NAMES},
    **{name: AOV_CACHE_TOGGLES for name in CABLE_OUTPUT_NAMES},
}


def cache_toggles(beauty: bool) -> tuple:
    """The toggles a read's cache needs on: a beauty read Cache, an AOV
    read Cache and Cache AOVs Too."""
    return BEAUTY_CACHE_TOGGLES if beauty else AOV_CACHE_TOGGLES


def cache_switch_expression(toggles) -> str:
    """The dive Switch's input: 1 (the cache) while every toggle is on."""
    return ' * '.join(f'ch("../../{toggle}")' for toggle in toggles)


def cache_node_name(read: str) -> str:
    """The Cache COP in the dive that keeps File COP ``read``'s frames. A
    read is named after its AOV (letters, digits, underscores), so a name
    with a period never collides with another read's."""
    return f'{read}.cache'


def cache_switch_name(read: str) -> str:
    """The Switch in the dive that picks File COP ``read`` or its cache."""
    return f'{read}.read'


def cached_outputs(toggles) -> frozenset:
    """The outputs served from cache, given the toggles on (a set of parm
    names): those whose CACHE_TOGGLES are all on."""
    on = set(toggles)
    return frozenset(name for name, needs in CACHE_TOGGLES.items() if set(needs) <= on)


# The dive's outputs, as (label, COP subnet output type), only ever
# appended to: an import saved before ``beauties`` existed has a dive with
# the first two, one saved before #67 the first three; a build gives it
# all (``beauties`` is no longer wired). The HDA wires each node output to
# the dive output of DIVE_SOURCES.
DIVE_OUTPUTS = ((('rgba', 'vector4'), ('aovs', 'cable'), ('beauties', 'cable'),
                 ('lpes', 'cable'), ('mattes', 'cable'))
                + tuple((name, 'vector4') for name in CHANNEL_OUTPUT_NAMES[1:])
                + tuple((name, 'cable') for name in CRYPTO_OUTPUT_NAMES))
DIVE_INDEX = {label: index for index, (label, _) in enumerate(DIVE_OUTPUTS)}
DIVE_SOURCES = {
    **{name: name for name in CHANNEL_OUTPUT_NAMES},
    LPE: 'lpes', AOV: 'aovs', MATTE: 'mattes',
    **{name: name for name in CRYPTO_OUTPUT_NAMES},
}

# Parm values the node stores what it last resolved in, so a refresh that
# resolves the same thing touches nothing.
RESOLVED_PARM = 'resolved'      # "<department> <version>" (one channel)
FIELDS_PARM = 'fields'          # the cable's fields, space separated
BUILD_KEY_PARM = 'build_key'    # pipe.comp.build_key of the last build
PINS_PARM = 'pins'              # an all-channels import's farm pins
UNPACK_PARM = 'unpack_outputs'

# The Channel menu's label for comp.ALL_CHANNELS.
ALL_CHANNELS_LABEL = 'All channels'
# User data marking the Cable Unpacks an import keeps for AOV Outputs, the
# Channel Outputs unpack an import made before #67 kept (its wires move to
# the channel outputs), and the over chain an all-channels import made
# before #67 gets for the wires on its old rgba.
UNPACK_MARK = 'th_import_render_outputs'
UNPACK_SUFFIX = '_aov'
LPES_UNPACK_MARK = 'th_import_render_lpes'
MATTES_UNPACK_MARK = 'th_import_render_mattes'
BEAUTY_UNPACK_MARK = 'th_import_render_beauties'
OVER_MARK = 'th_import_render_over'

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


class ImportRender(TimeOffsetNode, EntityNode):

    ENTITY_CONTEXTS = ('shots',)

    # Time offset (a shared-timeline Multi)

    def get_applied_offset(self) -> int:
        parm = self.parm(APPLIED_OFFSET_PARM)
        return 0 if parm is None else int(parm.eval())

    def files_follow_offset(self) -> bool:
        """Whether the dive's File COPs read ``applied_offset`` (an import
        that never had an offset reads plain ``$F4``)."""
        dive = self.native().node(DIVE)
        if dive is None: return False
        files = [c for c in dive.children() if c.type().name() == 'file']
        return bool(files) and all(
            OFFSET_FRAME in f.parm('filename').unexpandedString() for f in files
        )

    def sync_time_offset(self) -> bool:
        """Store the node's time offset where its File COPs read it;
        whether the dive needs a rebuild to read it (an offset on an import
        whose File COPs read plain frames)."""
        try:
            offset = self.get_time_offset()
        except ns.NodeError:
            offset = 0
        parm = self.native().parm(APPLIED_OFFSET_PARM)
        if parm is None: return False  # an HDA build without the parm
        if int(parm.eval()) != offset:
            parm.set(offset)
        return offset != 0 and not self.files_follow_offset()

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

    # The channel outputs (#67)

    def get_channel_outputs(self) -> tuple:
        """The channel on each channel output (None where none), as last
        built."""
        parm = self.parm(CHANNEL_OUTPUTS_PARM)
        return parse_channel_outputs('' if parm is None else parm.eval())

    def set_channel_outputs(self, slots):
        parm = self.parm(CHANNEL_OUTPUTS_PARM)
        if parm is not None:
            parm.set(format_channel_outputs(slots))

    def channel_output(self, channel: str) -> int | None:
        """The output ``channel``'s beauty is on, None when it has none."""
        slots = self.get_channel_outputs()
        return slots.index(channel) if channel in slots else None

    def show_output_names(self, slots, reads, overflow=()):
        """Write which layer each beauty output carries in the node's
        comment, shown under it in the network editor
        (:func:`output_names_comment`); a comment someone typed stays."""
        native = self.native()
        wanted = output_names_comment(slots, reads, overflow)
        comment = next_comment(native.comment(), native.userData(OUTPUT_NAMES_COMMENT_KEY), wanted)
        if comment is None: return
        native.setComment(comment)
        native.setUserData(OUTPUT_NAMES_COMMENT_KEY, comment)
        native.setGenericFlag(hou.nodeFlag.DisplayComment, bool(comment))

    def _drop_moved_wires(self, previous, slots):
        """Unwire each channel output an all-channels read gave to another
        channel (it ran out of outputs): its wires were on the channel that
        held it, and must not read another channel's beauty unseen."""
        if not self.is_all_channels(): return
        moved = {i for i, (old, new) in enumerate(zip(previous, slots))
                 if old is not None and old != new}
        if not moved: return
        for connection in self.native().outputConnections():
            if connection.outputIndex() in moved:
                connection.outputNode().setInput(connection.inputIndex(), None)

    # The outputs' layout (#67)

    def get_outputs_layout(self) -> int:
        return outputs_layout(self.native())

    def upgrade(self) -> bool:
        """Move an import saved before #67 onto today's outputs (its
        channels, from its last build, each on an output of its own): a
        wire on ``aovs`` to ``aov`` (a light mix's on to ``lpe``), on
        ``rgba`` to ``rgba``, or for an import of several channels, whose
        ``rgba`` laid them over each other, to an over chain of the channel
        outputs beside the node; a wire taken from ``beauties`` through a
        Cable Unpack to its channel's output. Whether it moved anything (the
        dive then needs a rebuild, which :meth:`execute` sees)."""
        layout = self.get_outputs_layout()
        if layout >= OUTPUTS_LAYOUT: return False
        native = self.native()
        all_channels = self.is_all_channels()
        slots, _ = assign_channel_outputs((), self.read_channels(), all_channels)
        self.set_channel_outputs(slots)
        wires = [
            (c.outputIndex(), c.outputNode(), c.inputIndex())
            for c in native.outputConnections()
        ]
        for _, node, input_index in wires:
            node.setInput(input_index, None)
        layered = all_channels and sum(1 for s in slots if s) > 1
        over_wires = []
        for output, node, input_index in wires:
            name = LEGACY_OUTPUTS[output] if 0 <= output < len(LEGACY_OUTPUTS) else None
            if name == 'beauties':
                self._move_beauty_unpack(node, slots)
                continue
            if name == 'rgba' and layered:
                over_wires.append((node, input_index))
                continue
            target = moved_output(output, layout)
            if target is not None:
                node.setInput(input_index, native, target)
        if over_wires:
            over = self._over_companion(slots)
            for node, input_index in over_wires:
                node.setInput(input_index, over, 0)
        native.parm(OUTPUTS_LAYOUT_PARM).set(OUTPUTS_LAYOUT)
        from tumblepipe.pipe.houdini.cops import light_mix
        for mix in light_mix.find_mixes():
            mix.rewire_cable()
        return True

    def _move_beauty_unpack(self, unpack, slots):
        """A Cable Unpack that read the old ``beauties`` cable: each wire on
        a channel's field moves to that channel's output on the node; the
        unpack goes when it was the node's Channel Outputs, or nothing reads
        it any more. Anything else on ``beauties`` loses its wire."""
        if unpack.type().name() != 'cableunpack': return
        native = self.native()
        fields = _unpack_fields(unpack)
        wires = [(c.outputIndex(), c.outputNode(), c.inputIndex())
                 for c in unpack.outputConnections()]
        for output, node, input_index in wires:
            field = fields[output] if output < len(fields) else None
            if field not in slots: continue
            node.setInput(input_index, None)
            node.setInput(input_index, native, slots.index(field))
        if unpack.userData(BEAUTY_UNPACK_MARK) is not None or not unpack.outputConnections():
            unpack.destroy()

    def _over_companion(self, slots):
        """The channel outputs laid over each other in output order (the
        channels' order at an upgrade), the first at the back, as an import
        made before #67 gave on ``rgba``: blends beside the node, marked
        :data:`OVER_MARK`. Returns the last."""
        native = self.native()
        back, back_output = native, 0
        held = [i for i, s in enumerate(slots) if s]
        for step, index in enumerate(held[1:], start=1):
            over = native.parent().createNode('blend', f'{native.name()}_over{step}')
            over.parm('mode').set('over')
            over.setUserData(OVER_MARK, native.name())
            # The blend's first input is the background, its second the
            # foreground laid over it.
            over.setInput(0, back, back_output)
            over.setInput(1, native, index)
            over.setPosition(native.position() + hou.Vector2(
                comp.COLUMN_STRIDE * step, 0.0))
            back, back_output = over, 0
        return back

    # The frame cache (#73)

    def cached_outputs(self) -> frozenset:
        """The outputs served from their Cache COP now."""
        return cached_outputs(
            name for name in (CACHE_PARM, CACHE_AOVS_PARM)
            if self.parm(name) is not None and self.parm(name).eval()
        )

    def clear_cache(self) -> int:
        """Clear Cache: empty every Cache COP in the dive; how many."""
        dive = self.native().node(DIVE)
        if dive is None: return 0
        caches = [c for c in dive.children() if c.type().name() == 'cache']
        for cache in caches:
            cache.parm('clearcache').pressButton()
        return len(caches)

    # The scene (camera and scene outputs)

    def scene(self):
        """The embedded th::import_scene, None in an HDA built without it."""
        from tumblepipe.pipe.houdini.cops.import_scene import ImportScene
        inner = self.native().node(SCENE)
        return None if inner is None else ImportScene(inner)

    def scene_wired(self) -> bool:
        """Whether anything reads ``geometry`` or ``camera``."""
        return any(
            c.outputIndex() in (GEOMETRY_OUTPUT, CAMERA_OUTPUT)
            for c in self.native().outputConnections()
        )

    def import_scene(self):
        """Run the embedded th::import_scene and open the camera and scene
        outputs to it: scene once it imported, camera once its Camera
        Import cooks too. Scene Reads says what it read, or why an output
        stays empty; raises the import's error after saying so."""
        scene = self.scene()
        if scene is None: return
        try:
            scene.execute()
        except Exception as error:
            self._set_scene_state(False, False, f'none: {error}')
            raise
        self.open_scene_outputs()

    def open_scene_outputs(self):
        """Open ``scene`` to the imported scene, and ``camera`` when its
        Camera Import cooks (checked here, once, not on every cook)."""
        scene = self.scene()
        if scene is None: return
        label = scene.parm('version_label').eval()
        problems = _cook_problems(scene.native().node('camera'))
        if problems:
            label = f'{label}; no camera: {problems[0]}'
        self._set_scene_state(True, not problems, label)

    def _set_scene_state(self, scene_ready: bool, camera_ready: bool, label: str):
        for name, value in ((SCENE_READY_PARM, int(scene_ready)),
                            (CAMERA_READY_PARM, int(camera_ready))):
            parm = self.parm(name)
            if parm is not None and parm.eval() != value:
                parm.set(value)
        if self.parm(SCENE_LABEL_PARM) is not None:
            self.parm(SCENE_LABEL_PARM).set(label)

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

    def execute(self, scene: bool | None = None) -> dict[str, comp.Resolved]:
        """Resolve, and point the File COPs at it when what the node reads
        changed. Returns ``{channel: Resolved}``.

        An import saved before #67 moves onto today's outputs first
        (:meth:`upgrade`). A rebuild syncs the downstream light mixes' rows;
        the AOV Outputs unpacks follow what it reads; every
        export_comp re-checks the channels it reads. The embedded scene
        imports when ``scene`` is True (Update), or when None and
        ``geometry`` or ``camera`` is wired; a scene that fails says so on
        Scene Reads and never stops the renders.
        """
        self.upgrade()
        if scene or (scene is None and self.scene_wired()):
            try:
                self.import_scene()
            except Exception:
                pass  # said on Scene Reads; the renders do not wait on it
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
        previous_slots = self.get_channel_outputs()
        slots, overflow = assign_channel_outputs(
            previous_slots, list(reads), self.is_all_channels()
        )
        offset_unread = self.sync_time_offset()
        rebuilt = (key != previous_key or not self._dive_built(plan, slots)
                   or offset_unread)
        if rebuilt:
            self._drop_moved_wires(previous_slots, slots)
            follow = self.get_applied_offset() != 0 or self.files_follow_offset()
            self._build(reads, plan, key, slots, follow_offset=follow)
        label = self._label(reads, skipped, previous, previous_key)
        if self.is_all_channels():
            label += channel_outputs_label(slots, reads, overflow)
        self._update_labels(label)
        self.show_output_names(slots, list(reads), overflow)
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

    def _dive_built(self, plan: comp.DivePlan, slots) -> bool:
        dive = self.native().node(DIVE)
        if dive is None or self.get_fields() != list(plan.fields): return False
        if not self.has_packs(): return False
        if self.get_channel_outputs() != tuple(slots): return False
        # A dive built before the frame cache (#73) has no Cache COPs.
        return all(dive.node(name) is not None for read in plan.files
                   for name in (read.name, cache_node_name(read.name)))

    def has_packs(self) -> bool:
        """Whether the dive has every cable pack: an import saved before #67
        has no lpes or mattes pack."""
        dive = self.native().node(DIVE)
        if dive is None: return False
        return all(dive.node(name) is not None for name in CABLE_PACKS.values())

    def _build(self, reads: dict[str, comp.Resolved], plan: comp.DivePlan, key: str,
               slots, follow_offset: bool = False):
        """Rebuild the dive: one File COP per AOV read (behind its frame
        cache), each AOV into the pack of its cable (``pipe.comp.aov_cable``),
        each channel's beauty to the channel output ``slots`` gives it. Laid
        out by :func:`dive_positions`. Nothing outside the dive changes.
        ``follow_offset``: the File COPs read ``applied_offset``."""
        dive = self.native().node(DIVE)
        if dive is None:
            raise self._error(f'the node has no {DIVE} subnet (a broken HDA)')
        for child in dive.children():
            if child.name() in ('inputs', 'outputs'): continue
            child.destroy()
        _ensure_dive_outputs(dive)
        outputs = dive.node('outputs')
        for index in range(len(DIVE_OUTPUTS)):
            outputs.setInput(index, None)
        positions = dive_positions(plan)
        files: dict[str, hou.Node] = dict()
        beauty_reads = set(plan.beauties)
        for read in plan.files:
            file_node = dive.createNode('file', read.name)
            _configure_file(file_node, reads[read.channel].aovs[read.aov], read.aov,
                            follow_offset)
            files[read.name] = _cached_read(
                dive, file_node, cache_toggles(read.name in beauty_reads)
            )
        for cable in comp.AOV_CABLES:
            cable_fields = list(plan.cable_fields(cable))
            pack = _pack(dive, CABLE_PACKS[cable], [files[f] for f in cable_fields], cable_fields)
            outputs.setInput(DIVE_INDEX[DIVE_SOURCES[OUTPUTS[CABLE_OUTPUTS[cable]]]], pack, 0)
        beauty_of = dict(zip(plan.beauty_fields, plan.beauties))
        for index, channel in enumerate(slots):
            if channel not in beauty_of: continue
            outputs.setInput(DIVE_INDEX[DIVE_SOURCES[CHANNEL_OUTPUT_NAMES[index]]],
                             files[beauty_of[channel]], 0)
        for child in dive.children():
            if child.name() in positions:
                child.setPosition(hou.Vector2(*positions[child.name()]))
        inputs = dive.node('inputs')
        if inputs is not None:
            inputs.setPosition(hou.Vector2(-comp.COLUMN_STRIDE, 0.0))
        single = None if self.is_all_channels() else next(iter(reads.values()))
        self.parm(RESOLVED_PARM).set(
            f'{single.department} {single.version}' if single else ''
        )
        self.parm(FIELDS_PARM).set(' '.join(plan.fields))
        self.parm(BUILD_KEY_PARM).set(key)
        self.set_channel_outputs(slots)

    # AOV Outputs

    def find_unpack(self, output_index: int = AOVS_OUTPUT):
        """The Cable Unpack AOV Outputs keeps on ``output_index`` (``lpe``,
        ``aov`` or ``matte``), None if none."""
        mark = _UNPACKS[output_index][0]
        for connection in self.native().outputConnections():
            if connection.outputIndex() != output_index: continue
            node = connection.outputNode()
            if node.userData(mark) is not None:
                return node
        return None

    def sync_unpacks(self, plan: comp.DivePlan | None):
        """Keep every companion in step with ``plan`` (what the node just
        resolved; None when it is not known, which only removes). lpe,
        matte and the cryptomattes first, so a wire the ``aov`` unpack drops
        for a field that moved to one of them can move there; a wire on a
        channel's beauty (TumblePipe 1.99 carried them in the AOVs) moves to
        the channel's output on the node."""
        firsts = [self.sync_unpack(plan, index) for index in _UNPACKS if index != AOVS_OUTPUT]
        rehomes = [(u, _unpack_fields(u)) for u in firsts if u is not None]
        rehomes.append((self.native(), list(self.get_channel_outputs())))
        self.sync_unpack(plan, AOVS_OUTPUT, rehome=rehomes)

    def sync_unpack(self, plan: comp.DivePlan | None,
                    output_index: int = AOVS_OUTPUT, rehome=()):
        """Keep the companion unpack on ``output_index`` in step with
        ``plan``: create it beside the node when its toggle is on and its
        cable has fields, give it one typed output per cable field, and
        remove it when the toggle is off (or the cable empty) and nothing
        reads it. Wires follow their field; a wire whose field is gone moves
        to the same-named output of the first ``rehome`` (node, fields) that
        has it. Returns the unpack, None when there is none."""
        wanted = self.get_unpack_outputs()
        mark, suffix, offset = _UNPACKS[output_index]
        unpack = self.find_unpack(output_index)
        if wanted and plan is None:
            raise self._error('the outputs need what the node reads: press Update')
        cable = OUTPUT_CABLES[output_index]
        fields = list(plan.cable_fields(cable)) if plan else []
        signatures = plan.signatures if plan else {}
        if not wanted or not fields:
            if unpack is not None and not unpack.outputConnections():
                unpack.destroy()
                return None
            # Kept while read; an empty cable still moves its wires on.
            if unpack is None or not wanted: return unpack
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

    def render_seed(self) -> comp.RenderSeed:
        """What **Render** opens Farm Submit on: this shot, its channel and,
        when not ``best``, its department as the cut (#68)."""
        return comp.render_seed(
            self.require_entity_uri(),
            self.get_channel_name(),
            self.get_department_setting(),
        )

    def open_farm_submit(self):
        from tumblepipe.pipe.houdini.ui import farm_submit
        farm_submit.open_grid(self.render_seed())


# Each companion unpack, by the output it reads: (user data mark, name
# suffix, offset from the import). Plain tuples: the module imports
# without Houdini (the tests' hou stub has no Vector2).
_UNPACKS = {
    LPES_OUTPUT: (LPES_UNPACK_MARK, '_lpe', (comp.COLUMN_STRIDE, 0.0)),
    AOVS_OUTPUT: (UNPACK_MARK, UNPACK_SUFFIX, (comp.COLUMN_STRIDE, -comp.ROW)),
    MATTES_OUTPUT: (MATTES_UNPACK_MARK, '_matte', (comp.COLUMN_STRIDE, -2 * comp.ROW)),
    **{CABLE_OUTPUTS[cable]: (f'th_import_render_{cable}', f'_{cable}',
                              (comp.COLUMN_STRIDE, -(3 + step) * comp.ROW))
       for step, cable in enumerate(comp.CRYPTO_CABLES)},
}


def _unpack_fields(unpack) -> list[str]:
    return [
        unpack.parm(f'fieldname{index}').eval()
        for index in range(1, unpack.parm('fields').eval() + 1)
    ]


def _set_unpack_fields(unpack, fields: list[str], signatures, rehome=()):
    """Give ``unpack`` these fields, its wires following their field: a
    Cable Unpack's outputs are by position, so renaming the fields in place
    would leave a wire on whatever field took its slot. A wire whose field
    is gone moves to output ``i`` of the first ``rehome`` that has it as
    its ``i``-th field: each a Cable Unpack, or a ``(node, fields)`` pair
    (the import itself, its channel outputs)."""
    current = _unpack_fields(unpack)
    rehomes = [r if isinstance(r, tuple) else (r, _unpack_fields(r)) for r in rehome]
    targets = comp.follow_fields_among(current, fields, [f for _, f in rehomes])
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
        home, index = target
        node.setInput(input_index, unpack if home is None else rehomes[home][0], index)


def _ensure_dive_outputs(dive):
    """Give the dive every output in DIVE_OUTPUTS (an import saved before
    ``beauties`` existed has two, one saved before #67 three; the channel
    outputs came after)."""
    if dive.parm('outputs') is None: return
    if dive.parm('outputs').eval() >= len(DIVE_OUTPUTS): return
    dive.parm('outputs').set(len(DIVE_OUTPUTS))
    for index, (label, kind) in enumerate(DIVE_OUTPUTS, start=1):
        dive.parm(f'outputlabel{index}').set(label)
        dive.parm(f'outputtype{index}').set(kind)


def _cached_read(dive, file_node, toggles):
    """File COP ``file_node`` behind its frame cache (#73): a Cache COP
    (Max Frames from the node) and a Switch on the node's ``toggles``
    between the read (0) and the cache (1). Returns the Switch, what the
    packs and the channel outputs read."""
    name = file_node.name()
    cache = dive.createNode('cache', cache_node_name(name))
    cache.setInput(0, file_node, 0)
    cache.parm('maxframes').setExpression(
        f'ch("../../{CACHE_FRAMES_PARM}")', hou.exprLanguage.Hscript
    )
    switch = dive.createNode('switch', cache_switch_name(name))
    switch.setInput(0, file_node, 0)
    switch.setInput(1, cache, 0)
    switch.parm('input').setExpression(
        cache_switch_expression(toggles), hou.exprLanguage.Hscript
    )
    return switch


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


def _configure_file(file_node, aov, field: str, follow_offset: bool = False):
    render_range = aov.get_frame_range()
    frame_path = aov.get_aov_frame_path(OFFSET_FRAME if follow_offset else PLAIN_FRAME)
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


def _cook_problems(native) -> list[str]:
    """Why ``native`` does not cook (its errors), empty when it does."""
    if native is None: return ['the node is missing (a broken HDA)']
    try:
        native.cook(force=True)
    except hou.OperationFailed as error:
        return list(native.errors()) or [str(error)]
    return list(native.errors())


def _unbuildable_message(shot_uri, resolved: comp.Resolved, problems) -> str:
    render_root = path_str(api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments
    ))
    return (
        f'cannot comp {resolved.department} {resolved.version} of channel '
        f'"{resolved.channel}": ' + '; '.join(problems) + '. Renders are read '
        f'from {render_root}/<department>/<channel>/v####/<aov>/.'
    )


def sync_time_offsets() -> int:
    """Give every import its current time offset (the open Multi's shared
    timeline was laid out or edited); an import whose File COPs did not
    read an offset yet rebuilds. The number that changed what they read."""
    changed = 0
    for node in find_imports():
        before = node.get_applied_offset()
        if node.sync_time_offset():
            try:
                node.execute()
            except ns.NodeError:
                continue
        if node.get_applied_offset() != before:
            changed += 1
    return changed


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
    # A new node is on today's outputs; one saved before #67 never stored
    # the parm, so it loads at the default, 0.
    layout = raw_node.parm(OUTPUTS_LAYOUT_PARM)
    if layout is not None:
        layout.set(OUTPUTS_LAYOUT)
    node = ImportRender(raw_node)
    node._update_labels()


def on_loaded(raw_node):
    """An import saved before #67: move its wires onto today's outputs and
    rebuild it (:meth:`ImportRender.upgrade`, then :meth:`execute`), once
    the scene has loaded: rewiring and rebuilding inside the load is what
    crashed Houdini for th::light_mix (``pipe.houdini.after_load``). An
    import on today's outputs is left to the scene refresh."""
    from tumblepipe.pipe.houdini import after_load
    parm = raw_node.parm(OUTPUTS_LAYOUT_PARM)
    if parm is None or int(parm.eval()) >= OUTPUTS_LAYOUT: return
    session_id = raw_node.sessionId()

    def upgrade():
        native = hou.nodeBySessionId(session_id)
        if native is not None:
            upgrade_loaded(native)

    after_load.run(upgrade)


def upgrade_loaded(raw_node):
    """:func:`on_loaded`'s work, on a scene that has finished loading. A
    render that does not resolve keeps its message on the Reads label."""
    node = ImportRender(raw_node)
    node.upgrade()  # a light mix's repair may have done it already
    if node.has_packs(): return
    try:
        node.execute()
    except Exception:
        pass


def execute(kwargs):
    """Update: resolve again, and import the scene."""
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Import Render'):
        node.execute(scene=True)


def clear_cache(kwargs):
    """Clear Cache: drop the frames the node's Cache COPs keep (the next
    play reads them from disk again)."""
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Clear Cache'):
        node.clear_cache()


def scene_changed(kwargs):
    """A Scene setting changed: import the scene again (its Reads say how
    it went, without a dialog)."""
    node = ImportRender(kwargs['node'])
    try:
        node.import_scene()
    except Exception:
        pass


def changed(kwargs):
    """Channel, Department or Version changed: resolve again. A render that
    does not resolve shows on the Reads label, without a dialog."""
    node = ImportRender(kwargs['node'])
    try:
        node.execute()
    except ns.NodeError:
        pass


def unpack_changed(kwargs):
    """AOV Outputs toggled: add or remove the Cable Unpacks beside the
    node."""
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Outputs'):
        if node.get_unpack_outputs():
            node.execute()  # resolves (rebuilding nothing unchanged) and syncs them
        else:
            node.sync_unpacks(None)


def open_location(kwargs):
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Open Location'):
        node.open_location()


def render(kwargs):
    """Render: open Farm Submit with this shot's Render ticked and this
    import's channel checked, to render what it reads again."""
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Render'):
        node.open_farm_submit()


def select_entity(kwargs):
    node = ImportRender(kwargs['node'])
    node.pick_entity('Select Shot', contexts=('shots',))
