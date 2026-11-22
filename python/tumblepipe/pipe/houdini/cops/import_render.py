"""th::import_render: a shot's finished renders, into a comp.

The comp's ``import_shot``. It reads one render department's version of one
channel of a shot, or every channel. A th::import_render::2.0 (today's;
Remy, 2026-11-22: "a cleaner, working import render node") has ten
outputs, top to bottom:

    beauty           the beauty (output name ``rgba``); for all channels the
                     channels' beauties laid over each other, last on top
    lpe              a cable of the light groups (``beauty_<tag>``), a light
                     mix's, and the LPE splits (diffuse_key, specular_dome,
                     diffuse_direct, ...: ``pipe.comp.LPE_AOV_NAMES``)
    aov              a cable of the geometry and camera data (albedo, normal,
                     depth (Mono), uv, position)
    matte            a cable of the mattes, distance ramps and holdouts
    cryptomaterials  a cable of the cryptomaterials rank planes, all of them }
    cryptoprimitives a cable of the cryptoprimitives rank planes            } crypto1
    layers           a cable of each channel's beauty, named after its channel
    geometry         the staged scene's geometry  } th::import_scene, inside the
    camera           the shot's render camera     } node, for a Rasterize Geometry
    extra            a cable of every other AOV (transmission, sss, emission,
                     shadow, ...; Antoine, 2026-11-22), last so the wires on
                     the outputs above stay put

Each cable field is named after its AOV (``layers``: its channel); which
cable an AOV rides is ``pipe.comp.aov_cable``. The beauty is in no AOV
cable: a light mix takes it on its own ``rgba`` input, which is wired for
you, and its groups on ``lpe``. A Cryptomatte COP reads every field of the
cable on its ``crypto1`` and needs all of one cryptomatte's rank planes (two
ranks each), so each cryptomatte th::render_vars renders has a cable of its
own to wire there whole; any other cryptomatte rides ``matte``. *AOV
Outputs* gives each pass an output named after it, on a Cable Unpack beside
the node per cable.

A digital asset's outputs are fixed by its type (verified in 22.0.429: an
instance can neither add outputs nor relabel them, and an asset whose
DialogScript names no outputs has none, whatever its subnet parms say), so
outputs named after the passes a render has can only be a Cable Unpack's.
Under the node, its comment says what each channel reads (``chars: denoise
v0004``) or that it has nothing yet (:func:`status_comment`).

The node reads nothing when nothing resolves; its outputs are then empty,
never an earlier read. A channel with no render yet says so under the node
and is no error (a comp is built before its renders land); any other
refusal is the node's error too, an Error COP in the dive, so the node and
everything reading it turn red with the reason (:func:`failure_state`).
Dropped by hand, it resolves at once: the latest render of the scene's shot.

A th::import_render::1.0 (scenes saved before 2.0 keep theirs, and it keeps
working; its outputs layout is :data:`LAYOUT_V1`) has a beauty output per
channel, ``rgba`` then ``rgba2`` .. ``rgba8`` (:data:`CHANNEL_OUTPUTS`;
those no channel holds are empty), then the cables but ``layers``, then
``geometry`` and ``camera``. A one-channel import's beauty is always
``rgba``. An all-channels import gives each channel an output of its own
and keeps it there (``channel_outputs``, :func:`assign_channel_outputs`): a
channel added later takes a free output, one not rendered yet keeps its
output for when it is, so a wire stays on its channel through Updates.
Which layer (channel) is on which output is written under the node in the
network editor, its comment (``rgba: chars``, ``rgba 2: bg``;
:func:`output_names_comment`), and on the Reads label. Code finds an
output by name on either (:meth:`ImportRender.output`); the module's
``*_OUTPUT`` indices are today's (2.0's), what :func:`create` makes.

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
the AOVs) to the channel's output on the node (1.0).

A 1.0 import saved before #67 had three outputs: ``rgba`` (for all channels,
the channels laid over each other), ``aovs`` (every AOV) and ``beauties``
(a cable of each channel's beauty). Its ``outputs_layout`` reads 0 (a new
1.0 node's is :data:`LAYOUT_V1`); on load and on Update
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

A frame the render does not have reads as empty, so a pre-roll cooks.
**Missing Frames** (``pipe.hold_frames``; Remy, 2026-11-18) can hold the
nearest rendered frame instead, and does by default for a check render
(Farm Submit's Step: every Nth frame). The Version menu offers check
renders by hand, and ``latest`` reads the newest one when the channel has
no complete render to read (a complete render wins over a newer check
render; ``pipe.comp.resolve_render``). The frames a version has are those its
``context.json`` records, so the File COPs' file names carry the nearest
frame as HScript arithmetic (:func:`frame_token`): no cook lists a
directory. What was built is kept in ``held``; changing the setting
rebuilds the dive. A submit refuses an import reading a check render
(``comp.check_render_refusal``).
"""

import hou

from tumblepipe.api import path_str, api
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.util.uri import Uri
from tumblepipe.pipe import comp, hold_frames
from tumblepipe.pipe.paths import get_render_context
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.time_offset import TimeOffsetNode

TYPE_NAME = 'import_render'

# The offset the File COPs read, and the frame expression that reads it:
# frame F of the scene is the render's frame F - offset.
APPLIED_OFFSET_PARM = 'applied_offset'
PLAIN_FRAME = '$F4'
OFFSET_REF = f'ch("../../{APPLIED_OFFSET_PARM}")'
OFFSET_FRAME = f'`padzero(4, $F-{OFFSET_REF})`'
# Missing Frames (pipe.hold_frames): what a frame the render does not have
# reads as. HELD_PARM keeps what the dive's File COPs were built to hold.
MISSING_PARM = 'missing_frames'
MISSING_LABEL_PARM = 'missing_frames_label'
HELD_PARM = 'held'

# The node's outputs. A digital asset's outputs are its type's, so each
# outputs layout is a type (version):
#
#   th::import_render::2.0 (LAYOUT_V2, today's; Remy 2026-11-22, "a
#   cleaner import render node"): rgba (labelled ``beauty``), the AOV
#   cables, ``layers`` (each channel's beauty, by channel name), the
#   scene's, then ``extra`` (every other AOV; Antoine 2026-11-22, appended
#   so a wire saved on the nine before it stays on its output).
#   th::import_render::1.0 on LAYOUT_V1 (#67): rgba .. rgba8, a beauty per
#   channel read (the rest empty), the AOV cables, the scene's. Scenes saved
#   with it keep it, and it keeps working.
#   th::import_render::1.0 on LAYOUT_LEGACY (before #67): rgba, aovs,
#   beauties. Moved onto LAYOUT_V1 on load (:meth:`ImportRender.upgrade`).
#
# The beauty output is named ``rgba`` in every layout (wires, a light mix's
# input and the code find it by that name); only 2.0 labels it ``beauty``.
LAYOUT_LEGACY, LAYOUT_V1, LAYOUT_V2 = 0, 1, 2
OUTPUTS_LAYOUT = LAYOUT_V2
CHANNEL_OUTPUTS = 8  # 1.0's beauty outputs
CHANNEL_OUTPUT_NAMES = ('rgba',) + tuple(f'rgba{i}' for i in range(2, CHANNEL_OUTPUTS + 1))
LPE, AOV, MATTE, LAYERS = 'lpe', 'aov', 'matte', 'layers'
CRYPTO_OUTPUT_NAMES = comp.CRYPTO_CABLES
CABLE_OUTPUT_NAMES = (LPE, AOV, MATTE) + CRYPTO_OUTPUT_NAMES
SCENE_OUTPUT_NAMES = ('geometry', 'camera')
EXTRA = 'extra'  # 2.0's last output: the AOVs no other cable takes
OUTPUTS_V1 = CHANNEL_OUTPUT_NAMES + CABLE_OUTPUT_NAMES + SCENE_OUTPUT_NAMES
OUTPUTS_V2 = ('rgba',) + CABLE_OUTPUT_NAMES + (LAYERS,) + SCENE_OUTPUT_NAMES + (EXTRA,)
# The outputs an import saved before #67 had (``rgba`` then laid the
# channels over each other, ``aovs`` carried every AOV, ``beauties`` each
# channel's beauty).
LEGACY_OUTPUTS = ('rgba', 'aovs', 'beauties')
LAYOUT_OUTPUTS = {LAYOUT_LEGACY: LEGACY_OUTPUTS, LAYOUT_V1: OUTPUTS_V1, LAYOUT_V2: OUTPUTS_V2}
# The type version each layout's node is (the legacy layout is a 1.0 node
# saved before #67).
LAYOUT_VERSIONS = {LAYOUT_LEGACY: '1.0', LAYOUT_V1: '1.0', LAYOUT_V2: '2.0'}

# Today's outputs (2.0) and their indices; output_index() gives another
# layout's. A workfile template wires import_render.AOVS_OUTPUT on the node
# import_render.create() makes, which is today's type.
OUTPUTS = OUTPUTS_V2
RGBA_OUTPUT = OUTPUTS.index('rgba')
LPES_OUTPUT = OUTPUTS.index(LPE)
AOVS_OUTPUT = OUTPUTS.index(AOV)
MATTES_OUTPUT = OUTPUTS.index(MATTE)
EXTRA_OUTPUT = OUTPUTS.index(EXTRA)
LAYERS_OUTPUT = OUTPUTS.index(LAYERS)
GEOMETRY_OUTPUT = OUTPUTS.index('geometry')
CAMERA_OUTPUT = OUTPUTS.index('camera')
# Each cable's output name (the AOV cables are named as in pipe.comp; the
# beauties cable is 2.0's ``layers``).
CABLE_OUTPUT_OF = {comp.LPES: LPE, comp.AOVS: AOV, comp.MATTES: MATTE,
                   **{cable: cable for cable in comp.CRYPTO_CABLES},
                   comp.EXTRAS: EXTRA}
BEAUTIES_CABLE = 'beauties'
# Each AOV cable's output on today's node.
CABLE_OUTPUTS = {cable: OUTPUTS.index(name) for cable, name in CABLE_OUTPUT_OF.items()}
OUTPUT_CABLES = {index: cable for cable, index in CABLE_OUTPUTS.items()}


def output_index(layout: int, name: str) -> int | None:
    """Output ``name``'s index on a node of ``layout``, None when that
    layout has no such output."""
    outputs = LAYOUT_OUTPUTS.get(layout, ())
    return outputs.index(name) if name in outputs else None


def output_name(layout: int, index: int) -> str | None:
    """The name of output ``index`` of a node of ``layout``, None when it
    has no such output."""
    outputs = LAYOUT_OUTPUTS.get(layout, ())
    return outputs[index] if 0 <= index < len(outputs) else None


def output_label(name: str, layout: int = LAYOUT_V1) -> str:
    """An output's label: 2.0's ``rgba`` is ``beauty``; 1.0's ``rgba2`` is
    ``rgba 2``; any other its name."""
    if name == 'rgba' and layout >= LAYOUT_V2:
        return comp.BEAUTY
    if name.startswith('rgba') and name[4:].isdigit():
        return f'rgba {name[4:]}'
    return name


def output_labels(layout: int) -> tuple:
    """Every output's label on a node of ``layout``, in order."""
    return tuple(output_label(name, layout) for name in LAYOUT_OUTPUTS[layout])


# The layout a 1.0 node is on: 0 before #67 (a parm such a node never saved
# reads its default), LAYOUT_V1 since (set at creation). A 2.0 node has no
# such parm: its type is its layout.
LEGACY_MOVES = {'rgba': 'rgba', 'aovs': AOV}
OUTPUTS_LAYOUT_PARM = 'outputs_layout'


def type_layout(type_name: str, layout_parm: int | None) -> int:
    """The outputs layout of a node of type ``type_name``
    (``th::import_render::<version>``) whose ``outputs_layout`` parm reads
    ``layout_parm`` (None when the node has none): 2.0 is LAYOUT_V2; a 1.0
    node is on its parm's layout, LAYOUT_V1 for an HDA build without it."""
    version = type_name.split('::')[2] if type_name.count('::') >= 2 else '1.0'
    if version != '1.0':
        return LAYOUT_V2
    if layout_parm is None:
        return LAYOUT_V1
    return min(int(layout_parm), LAYOUT_V1)


def outputs_layout(native) -> int:
    """The outputs layout an import_render node is on (:func:`type_layout`)."""
    parm = native.parm(OUTPUTS_LAYOUT_PARM)
    return type_layout(native.type().name(), None if parm is None else int(parm.eval()))


def moved_output(index: int, layout: int) -> int | None:
    """Where a plain wire on output ``index`` of a 1.0 node on ``layout``
    belongs on 1.0's outputs now: ``rgba`` stays, ``aovs`` is ``aov``. None
    for an index the layout never had, and for ``beauties``, which has no
    output now (a wire through a Cable Unpack moves per channel,
    :meth:`ImportRender.upgrade`)."""
    if layout >= LAYOUT_V1:
        outputs = LAYOUT_OUTPUTS[min(layout, LAYOUT_V2)]
        return index if 0 <= index < len(outputs) else None
    if not 0 <= index < len(LEGACY_OUTPUTS): return None
    name = LEGACY_MOVES.get(LEGACY_OUTPUTS[index])
    return output_index(LAYOUT_V1, name) if name else None


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


# What a 2.0 node writes under itself in the network editor (its comment,
# kept the same way as 1.0's output names): what each channel reads, so a
# comp's rows say which layer and version each import brings without
# opening one, and a channel with nothing to read says so where the empty
# thumbnail is.
NOT_RENDERED = 'not rendered yet'
CANNOT_COMP = 'cannot comp it (see the node error)'


def status_comment(reads, skipped=(), problem: str | None = None,
                   channel: str | None = None) -> str:
    """The 2.0 node's comment: one line per channel read, ``<channel>:
    <department> <version>`` (``reads``: ``{channel: (department,
    version)}``, in the shot's channel order), then one per channel left
    out, ``<channel>: not rendered yet``. When nothing could be read,
    ``problem`` says why for ``channel`` (``*`` for all channels):
    :data:`NOT_RENDERED`, or :data:`CANNOT_COMP` for any other refusal."""
    if problem is not None:
        return f'{channel}: {problem}' if channel else problem
    lines = [f'{c}: {d} {v}' for c, (d, v) in reads.items()]
    lines += [f'{c}: {NOT_RENDERED}' for c in skipped]
    return '\n'.join(lines)


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
    cable_of = {name: cable for cable, name in CABLE_OUTPUT_OF.items()}
    return [cable_of[name] for name in CABLE_OUTPUT_NAMES + (EXTRA,)]


def dive_rows(plan) -> list[str]:
    """The dive's File COPs, top to bottom (:data:`DIVE_ROW` apart)."""
    rows = list(plan.beauties)
    for cable in _CABLES_IN_OUTPUT_ORDER():
        rows += [f for f in plan.cable_fields(cable) if f not in rows]
    rows += [read.name for read in plan.files if read.name not in rows]
    return rows


def dive_positions(plan, layout: int = OUTPUTS_LAYOUT) -> dict[str, tuple[float, float]]:
    """Where each node of the dive goes, by name: every File COP, its cache
    and switch, the cable packs and ``outputs``; on 2.0 also the
    ``beauties`` pack (on the first beauty's row) and the over blends (each
    on its foreground beauty's row), in the pack column, whose beauty rows
    the AOV packs leave free."""
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
    if layout >= LAYOUT_V2:
        positions[BEAUTIES_PACK] = at('pack', 0)
        for row, (name, _, _) in enumerate(over_chain(plan.beauties), start=1):
            positions[name] = at('pack', row)
    positions['outputs'] = at('outputs', 0)
    return positions


# The editable subnet the File COPs live in, its cable packs (named before
# #67 renamed the outputs), and the embedded th::import_scene that gives
# geometry and camera.
DIVE = 'dive'
PACK = 'pack'
BEAUTIES_PACK = 'beauties'  # a dive built before #67 had it
CABLE_PACKS = {comp.AOVS: PACK, comp.LPES: 'lpes', comp.MATTES: 'mattes',
               **{cable: cable for cable in comp.CRYPTO_CABLES},
               comp.EXTRAS: comp.EXTRAS}
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
# So, the outputs served from cache by the toggles they need (1.0's
# beauty outputs and 2.0's rgba and layers carry beauties).
CACHE_TOGGLES = {
    **{name: BEAUTY_CACHE_TOGGLES for name in CHANNEL_OUTPUT_NAMES + (LAYERS,)},
    **{name: AOV_CACHE_TOGGLES for name in CABLE_OUTPUT_NAMES + (EXTRA,)},
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


def cached_outputs(toggles, layout: int = OUTPUTS_LAYOUT) -> frozenset:
    """The outputs of a node of ``layout`` served from cache, given the
    toggles on (a set of parm names): those whose CACHE_TOGGLES are all on."""
    on = set(toggles)
    return frozenset(name for name in LAYOUT_OUTPUTS[layout]
                     if name in CACHE_TOGGLES and set(CACHE_TOGGLES[name]) <= on)


# The dive's outputs, as (label, COP subnet output type), only ever
# appended to: an import saved before ``beauties`` existed has a dive with
# the first two, one saved before #67 the first three; a build gives it
# all. The HDA wires each node output to the dive output its layout's
# DIVE_SOURCES names: 1.0 a beauty output per channel (``rgba`` ..
# ``rgba8``), 2.0 ``over`` (the beauty, or for all channels the beauties
# laid over each other) on rgba and ``beauties`` (a cable of each channel's
# beauty) on layers. A dive is built for its layout: a 1.0 node's leaves
# ``over`` and ``beauties`` unwired (Copernicus cooks every output of a
# subnet together, so an unused one would still cook).
DIVE_OUTPUTS = ((('rgba', 'vector4'), ('aovs', 'cable'), ('beauties', 'cable'),
                 ('lpes', 'cable'), ('mattes', 'cable'))
                + tuple((name, 'vector4') for name in CHANNEL_OUTPUT_NAMES[1:])
                + tuple((name, 'cable') for name in CRYPTO_OUTPUT_NAMES)
                + (('over', 'vector4'), (comp.EXTRAS, 'cable')))
DIVE_INDEX = {label: index for index, (label, _) in enumerate(DIVE_OUTPUTS)}
_CABLE_SOURCES = {LPE: 'lpes', AOV: 'aovs', MATTE: 'mattes',
                  **{name: name for name in CRYPTO_OUTPUT_NAMES}}
DIVE_SOURCES_V1 = {**{name: name for name in CHANNEL_OUTPUT_NAMES}, **_CABLE_SOURCES}
DIVE_SOURCES_V2 = {'rgba': 'over', **_CABLE_SOURCES, LAYERS: 'beauties', EXTRA: comp.EXTRAS}
LAYOUT_DIVE_SOURCES = {LAYOUT_V1: DIVE_SOURCES_V1, LAYOUT_V2: DIVE_SOURCES_V2}
DIVE_SOURCES = DIVE_SOURCES_V2


def dive_sources(layout: int) -> dict:
    """Which dive output each node output of ``layout`` reads (geometry and
    camera read the embedded scene, not the dive)."""
    return LAYOUT_DIVE_SOURCES[max(layout, LAYOUT_V1)]


def over_chain(beauties) -> list[tuple[str, str, str]]:
    """2.0's beauty for several channels: the beauties (File COP reads,
    back to front: the shot's channel order, the last on top, as the comp
    template stacks its rows) laid over each other, as ``[(blend name,
    background, foreground)]``, each background the first read or the blend
    before it. Empty for one beauty or none (it is the beauty itself, or
    nothing)."""
    beauties = list(beauties)
    chain = []
    back = beauties[0] if beauties else None
    for step, front in enumerate(beauties[1:], start=1):
        name = f'over.{step}'  # a period: never a read's name
        chain.append((name, back, front))
        back = name
    return chain

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

# When the node reads nothing, its dive is emptied (an empty output never
# shows an earlier read as if it were this one): a channel with nothing
# rendered yet (comp.NotRendered) leaves the outputs empty and says so
# under the node; any other refusal (no such channel, a pin that is not a
# complete render, a render that cannot be comped) is also the node's
# error, an Error COP in the dive on the beauty's path, so the node and
# every node reading it turn red with the reason (a warning there would
# not reach the node: Houdini 22.0.429 lifts only errors out of a subnet).
ERROR_NODE = 'error'
ERROR_SEVERITY = 2  # the Error COP's 'severity1' token: error


def default_channel(current: str, channels, rendered) -> str:
    """The channel a node dropped by hand reads: its Channel when that has
    a complete render (``rendered``, in the shot's ``channels``) or is All
    channels, else the first of the shot's channels that has one (a shot
    rendered as chars and bg, its implicit ``default`` never rendered);
    unchanged when none has."""
    rendered = set(rendered)
    if current == comp.ALL_CHANNELS or current in rendered:
        return current
    return next((channel for channel in channels if channel in rendered), current)


def failure_state(error: Exception) -> tuple[str, bool]:
    """What a node that read nothing says under itself, and whether it is
    the node's error: a channel not rendered yet is no error (a comp is
    built before its renders land), anything else is."""
    if isinstance(error, comp.NotRendered):
        return NOT_RENDERED, False
    return CANNOT_COMP, True


def render_department_names() -> list[str]:
    return [d.name for d in list_departments('render')]


def resolve(shot_uri: Uri, channel: str, department: str, version: str) -> comp.Resolved:
    """``pipe.comp.resolve_render`` on the shot's renders on disk."""
    renders = get_render_context(shot_uri, purpose='render').renders
    return comp.resolve_render(
        renders, render_department_names(), channel, department, version
    )


def version_items(shot_uri: Uri | None, department: str, channel: str) -> list[tuple[str, str]]:
    """The Version menu as ``(token, label)``: ``latest``, then a concrete
    department's versions newest first, the complete ones and its check
    renders (labelled, ``v0014 (check, every 5th)``: read only when picked,
    their frames held over the gaps). All channels read ``latest`` only
    (each channel has versions of its own)."""
    items = [(comp.LATEST, comp.LATEST)]
    if department == comp.BEST or shot_uri is None: return items
    if channel == comp.ALL_CHANNELS: return items
    renders = get_render_context(shot_uri, purpose='render').renders
    return items + comp.version_items(
        comp.list_versions(renders, department, channel),
        comp.list_check_versions(renders, department, channel),
    )


def version_menu(shot_uri: Uri | None, department: str, channel: str) -> list[str]:
    """The Version menu's tokens (:func:`version_items`)."""
    return [token for token, _ in version_items(shot_uri, department, channel)]


def format_held(held: dict) -> str:
    """``{channel: (first, last, step)}`` as :data:`HELD_PARM` keeps it."""
    import json
    return json.dumps({c: list(r) for c, r in sorted(held.items())}) if held else ''


def parse_held(text: str) -> dict:
    """:func:`format_held` read back; anything else reads as nothing held."""
    import json
    try:
        raw = json.loads(text) if text else {}
        return {str(c): (int(r[0]), int(r[1]), int(r[2])) for c, r in raw.items()}
    except (ValueError, TypeError, IndexError, AttributeError, KeyError):
        return {}


def held_ranges(setting: str, reads: dict) -> dict:
    """What each channel read holds: ``{channel: (first, last, step)}`` for
    the channels whose missing frames hold the nearest rendered one
    (``hold_frames.holds``), from the range the version records. A version
    with no record holds nothing (its frames are not known)."""
    held = dict()
    for channel, resolved in reads.items():
        if not hold_frames.holds(setting, resolved.check_step): continue
        frame_range = next(
            (aov.get_frame_range() for aov in resolved.aovs.values()), None)
        if frame_range is None: continue
        held[channel] = (frame_range.first_frame, frame_range.last_frame, frame_range.step_size)
    return held


def frame_token(follow_offset: bool, hold: tuple | None = None) -> str:
    """The frame number in a File COP's file name: the scene's frame
    (``$F4``), less the node's time offset when it follows one, and, when
    the read holds (``hold``: its first, last and step), the nearest
    rendered frame to that (``hold_frames.hold_expression``)."""
    if hold is None:
        return OFFSET_FRAME if follow_offset else PLAIN_FRAME
    frame = f'$F-{OFFSET_REF}' if follow_offset else '$F'
    return f'`padzero(4, {hold_frames.hold_expression(*hold, frame=frame)})`'


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
            OFFSET_REF in f.parm('filename').unexpandedString() for f in files
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

    def rendered_channels(self) -> tuple[list[str], list[str]]:
        """``(the shot's channels, those with a complete render on the
        node's Department)``; both empty when the shot is not known."""
        try:
            shot_uri = self.get_entity_uri()
            if shot_uri is None: return [], []
            channels = list_channels(shot_uri)
            renders = get_render_context(shot_uri, purpose='render').renders
        except Exception:
            return [], []
        rendered = []
        for channel in channels:
            try:
                comp.resolve_render(renders, render_department_names(), channel,
                                    self.get_department_setting(), comp.LATEST)
            except comp.CompError:
                continue
            rendered.append(channel)
        return channels, rendered

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

    def version_menu(self) -> list[str]:
        """The Version menu as token/label pairs (:func:`version_items`)."""
        items = []
        for token, label in version_items(
                self.get_entity_uri(), self.get_department_setting(),
                self.get_channel_name()):
            items += [token, label]
        return items

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

    # Missing frames (pipe.hold_frames)

    def get_missing_setting(self) -> str:
        """The Missing Frames parm; Automatic on an HDA build without it."""
        parm = self.native().parm(MISSING_PARM)
        return hold_frames.AUTO if parm is None else parm.eval()

    def get_held(self) -> dict:
        """What the dive's File COPs hold, as last built
        (:func:`held_ranges`)."""
        parm = self.native().parm(HELD_PARM)
        return {} if parm is None else parse_held(parm.eval())

    def _wanted_held(self, reads) -> dict:
        if self.native().parm(HELD_PARM) is None: return {}  # an old HDA build
        return held_ranges(self.get_missing_setting(), reads)

    def _update_missing_label(self, reads):
        parm = self.native().parm(MISSING_LABEL_PARM)
        if parm is None: return
        parm.set(hold_frames.missing_label(
            self.get_missing_setting(), [r.check_step for r in reads.values()]))

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
        self._write_comment(output_names_comment(slots, reads, overflow))

    def _write_comment(self, wanted: str):
        """Set the node's own comment to ``wanted`` and show it under the
        node; a comment someone typed stays (:func:`next_comment`)."""
        native = self.native()
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

    def is_v2(self) -> bool:
        """Whether the node is a 2.0 (one beauty output, a layers cable)."""
        return self.get_outputs_layout() >= LAYOUT_V2

    def output(self, name: str) -> int | None:
        """Output ``name``'s index on this node (its type's layout), None
        when it has no such output."""
        return output_index(self.get_outputs_layout(), name)

    def upgrade(self) -> bool:
        """Move a 1.0 import saved before #67 onto 1.0's outputs (its
        channels, from its last build, each on an output of its own): a
        wire on ``aovs`` to ``aov`` (a light mix's on to ``lpe``), on
        ``rgba`` to ``rgba``, or for an import of several channels, whose
        ``rgba`` laid them over each other, to an over chain of the channel
        outputs beside the node; a wire taken from ``beauties`` through a
        Cable Unpack to its channel's output. Whether it moved anything (the
        dive then needs a rebuild, which :meth:`execute` sees)."""
        layout = self.get_outputs_layout()
        if layout >= LAYOUT_V1: return False
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
        native.parm(OUTPUTS_LAYOUT_PARM).set(LAYOUT_V1)
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
        on = [name for name in (CACHE_PARM, CACHE_AOVS_PARM)
              if self.parm(name) is not None and self.parm(name).eval()]
        return cached_outputs(on, self.get_outputs_layout())

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
            c.outputIndex() in (self.output('geometry'), self.output('camera'))
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
            raise self._error(str(error)) from error  # failure_state reads it
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
            raise self._error(str(error)) from error  # failure_state reads it
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
            reason = str(error).split(': ', 1)[-1]
            self._update_labels(f'none: {reason}')
            self._read_nothing(error, reason)
            raise
        shot_uri = self.require_entity_uri()
        key = comp.build_key(
            shot_uri, {c: (r.department, r.version) for c, r in reads.items()}
        )
        v2 = self.is_v2()
        # A 1.0 node has no extra output, so reads none of its AOVs.
        plan = comp.dive_plan(
            {c: r.aovs for c, r in reads.items()}, self.is_all_channels(), extras=v2
        )
        previous_slots = self.get_channel_outputs()
        if v2:
            slots, overflow = None, []
        else:
            slots, overflow = assign_channel_outputs(
                previous_slots, list(reads), self.is_all_channels()
            )
        offset_unread = self.sync_time_offset()
        held = self._wanted_held(reads)
        rebuilt = (key != previous_key or not self._dive_built(plan, slots)
                   or offset_unread or held != self.get_held())
        if rebuilt:
            if not v2:
                self._drop_moved_wires(previous_slots, slots)
            follow = self.get_applied_offset() != 0 or self.files_follow_offset()
            self._build(reads, plan, key, slots, follow_offset=follow, held=held)
        self._update_missing_label(reads)
        label = self._label(reads, skipped, previous, previous_key)
        if self.is_all_channels() and not v2:
            label += channel_outputs_label(slots, reads, overflow)
        self._update_labels(label)
        if v2:
            self._write_comment(status_comment(
                {c: (r.department, r.version if r.check_step is None
                     else comp.check_version_label(r.version, r.check_step))
                 for c, r in reads.items()}, skipped))
        else:
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
        refusal = comp.check_render_refusal(reads.values())
        if refusal:
            raise self._error(refusal)
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
        versions = sorted({
            f'{r.department} {r.version}' if r.check_step is None
            else f'{r.department} {comp.check_version_label(r.version, r.check_step)}'
            for r in reads.values()
        })
        noun = 'channel' if len(reads) == 1 else 'channels'
        label = f'{len(reads)} {noun}: ' + ', '.join(versions)
        if skipped:
            label += f'; not rendered: {", ".join(skipped)}'
        return label

    def _read_nothing(self, error: Exception, reason: str):
        """The node reads nothing (``error``, the node's, says why): empty
        the dive so no output shows an earlier read, say so under the node,
        and for anything but a channel not rendered yet make it the node's
        error, with ``reason`` (:func:`failure_state`)."""
        cause = error.__cause__ if isinstance(error.__cause__, comp.CompError) else error
        problem, is_error = failure_state(cause)
        dive = self.native().node(DIVE)
        if dive is not None:
            self._clear_dive(dive)
            if is_error:
                _error_read(dive, f'{self.path()}: {reason}')
        for name in (RESOLVED_PARM, FIELDS_PARM, BUILD_KEY_PARM):
            self.parm(name).set('')
        held_parm = self.native().parm(HELD_PARM)
        if held_parm is not None:
            held_parm.set('')
        if self.is_v2():
            self._write_comment(status_comment({}, problem=problem,
                                               channel=self.get_channel_name()))
        else:
            self.show_output_names((), [])

    def _clear_dive(self, dive):
        """Remove everything the dive was built with and unwire its outputs."""
        for child in dive.children():
            if child.name() in ('inputs', 'outputs'): continue
            child.destroy()
        _ensure_dive_outputs(dive)
        outputs = dive.node('outputs')
        for index in range(len(DIVE_OUTPUTS)):
            outputs.setInput(index, None)

    def _dive_built(self, plan: comp.DivePlan, slots) -> bool:
        dive = self.native().node(DIVE)
        if dive is None or self.get_fields() != list(plan.fields): return False
        if not self.has_packs(): return False
        if slots is None:  # 2.0: its beauties pack, wired to layers
            if dive.node(BEAUTIES_PACK) is None: return False
        elif self.get_channel_outputs() != tuple(slots): return False
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
               slots, follow_offset: bool = False, held: dict | None = None):
        """Rebuild the dive: one File COP per AOV read (behind its frame
        cache), each AOV into the pack of its cable (``pipe.comp.aov_cable``).
        A 1.0 node: each channel's beauty to the channel output ``slots``
        gives it. A 2.0 node (``slots`` None): the beauties laid over each
        other (:func:`over_chain`) to ``over`` and packed by channel name
        to ``beauties``. Laid out by :func:`dive_positions`. Nothing outside
        the dive changes. ``follow_offset``: the File COPs read
        ``applied_offset``. ``held``: the channels whose File COPs hold the
        nearest rendered frame (:func:`held_ranges`)."""
        held = held or {}
        layout = self.get_outputs_layout()
        sources = dive_sources(layout)
        dive = self.native().node(DIVE)
        if dive is None:
            raise self._error(f'the node has no {DIVE} subnet (a broken HDA)')
        self._clear_dive(dive)
        outputs = dive.node('outputs')
        positions = dive_positions(plan, layout)
        files: dict[str, hou.Node] = dict()
        beauty_reads = set(plan.beauties)
        for read in plan.files:
            file_node = dive.createNode('file', read.name)
            _configure_file(file_node, reads[read.channel].aovs[read.aov], read.aov,
                            follow_offset, held.get(read.channel))
            files[read.name] = _cached_read(
                dive, file_node, cache_toggles(read.name in beauty_reads)
            )
        for cable in comp.AOV_CABLES:
            cable_fields = list(plan.cable_fields(cable))
            pack = _pack(dive, CABLE_PACKS[cable], [files[f] for f in cable_fields], cable_fields)
            # Every pack is built (has_packs), but 1.0 has no extra output.
            if CABLE_OUTPUT_OF[cable] in sources:
                outputs.setInput(DIVE_INDEX[sources[CABLE_OUTPUT_OF[cable]]], pack, 0)
        beauty_of = dict(zip(plan.beauty_fields, plan.beauties))
        if slots is None:
            pack = _pack(dive, BEAUTIES_PACK, [files[b] for b in plan.beauties],
                         list(plan.beauty_fields))
            outputs.setInput(DIVE_INDEX[sources[LAYERS]], pack, 0)
            front = files[plan.beauties[0]] if plan.beauties else None
            for name, back, fore in over_chain(plan.beauties):
                over = dive.createNode('blend', name)
                over.parm('mode').set('over')
                # The blend's first input is the background, its second
                # the foreground laid over it.
                over.setInput(0, files.get(back) or dive.node(back), 0)
                over.setInput(1, files[fore], 0)
                front = over
            if front is not None:
                outputs.setInput(DIVE_INDEX[sources['rgba']], front, 0)
        else:
            for index, channel in enumerate(slots):
                if channel not in beauty_of: continue
                outputs.setInput(DIVE_INDEX[sources[CHANNEL_OUTPUT_NAMES[index]]],
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
        held_parm = self.native().parm(HELD_PARM)
        if held_parm is not None:
            held_parm.set(format_held(held))
        if slots is not None:
            self.set_channel_outputs(slots)

    # AOV Outputs

    def _output_name(self, output) -> str | None:
        """``output`` as a name: a name stays, an index is this node's."""
        if isinstance(output, str): return output
        return output_name(self.get_outputs_layout(), output)

    def find_unpack(self, output=AOV):
        """The Cable Unpack AOV Outputs keeps on ``output`` (a name, or
        this node's output index: ``lpe``, ``aov``, ``matte``, a
        cryptomatte's, 2.0's ``layers``), None if none."""
        name = self._output_name(output)
        index = self.output(name) if name in _UNPACKS else None
        if index is None: return None
        mark = _UNPACKS[name][0]
        for connection in self.native().outputConnections():
            if connection.outputIndex() != index: continue
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
        the channel's output on the node (on 2.0, to the layers unpack)."""
        names = unpack_outputs(self.get_outputs_layout())
        firsts = [self.sync_unpack(plan, name) for name in names if name != AOV]
        rehomes = [(u, _unpack_fields(u)) for u in firsts if u is not None]
        if not self.is_v2():
            rehomes.append((self.native(), list(self.get_channel_outputs())))
        self.sync_unpack(plan, AOV, rehome=rehomes)

    def sync_unpack(self, plan: comp.DivePlan | None, output=AOV, rehome=()):
        """Keep the companion unpack on ``output_index`` in step with
        ``plan``: create it beside the node when its toggle is on and its
        cable has fields, give it one typed output per cable field, and
        remove it when the toggle is off (or the cable empty) and nothing
        reads it. Wires follow their field; a wire whose field is gone moves
        to the same-named output of the first ``rehome`` (node, fields) that
        has it. Returns the unpack, None when there is none."""
        name = self._output_name(output)
        output_index = self.output(name)
        wanted = self.get_unpack_outputs()
        mark, suffix, offset = _UNPACKS[name]
        unpack = self.find_unpack(name)
        if wanted and plan is None:
            raise self._error('the outputs need what the node reads: press Update')
        if name == LAYERS:
            fields = list(plan.beauty_fields) if plan else []
            signatures = plan.beauty_signatures if plan else {}
        else:
            cable = {n: c for c, n in CABLE_OUTPUT_OF.items()}[name]
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
    LPE: (LPES_UNPACK_MARK, '_lpe', (comp.COLUMN_STRIDE, 0.0)),
    AOV: (UNPACK_MARK, UNPACK_SUFFIX, (comp.COLUMN_STRIDE, -comp.ROW)),
    MATTE: (MATTES_UNPACK_MARK, '_matte', (comp.COLUMN_STRIDE, -2 * comp.ROW)),
    **{cable: (f'th_import_render_{cable}', f'_{cable}',
               (comp.COLUMN_STRIDE, -(3 + step) * comp.ROW))
       for step, cable in enumerate(comp.CRYPTO_CABLES)},
    # 2.0 only: each channel's beauty, by channel name; every other AOV.
    LAYERS: ('th_import_render_layers', '_layers',
             (comp.COLUMN_STRIDE, -(3 + len(comp.CRYPTO_CABLES)) * comp.ROW)),
    EXTRA: ('th_import_render_extra', '_extra',
            (comp.COLUMN_STRIDE, -(4 + len(comp.CRYPTO_CABLES)) * comp.ROW)),
}


def unpack_outputs(layout: int) -> list[str]:
    """The outputs AOV Outputs keeps a Cable Unpack on for a node of
    ``layout``: the AOV cables, and on 2.0 ``layers``."""
    return [name for name in _UNPACKS if output_index(layout, name) is not None]


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
    # Wiring a Cache COP gives it an output (its 'outputs' multiparm) by a
    # script Houdini does not run inside an OnCreated (hython 22.0.429): a
    # node dropped by hand, which builds in its OnCreated, had caches with
    # no output, and every cached output empty.
    outputs = cache.parm('outputs')
    if outputs is not None and outputs.eval() < 1:
        outputs.set(1)
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


def _error_read(dive, message: str):
    """An Error COP in the emptied dive saying ``message``, on the path of
    the beauty outputs (1.0's ``rgba``, 2.0's ``over``): Copernicus cooks
    every output of the node together, so the node, and whatever reads any
    of its outputs, fails the cook with the message."""
    empty = dive.createNode('constant', f'{ERROR_NODE}.empty')
    empty.setPosition(hou.Vector2(-comp.COLUMN_STRIDE, 0.0))
    error = dive.createNode('error', ERROR_NODE)
    error.setInput(0, empty, 0)
    error.parm('numerror').set(1)
    error.parm('errormsg1').set(message)
    error.parm('severity1').set(ERROR_SEVERITY)
    error.parm('enable1').set(1)
    for label in ('rgba', 'over'):
        dive.node('outputs').setInput(DIVE_INDEX[label], error, 0)
    error.setPosition(hou.Vector2(0.0, 0.0))
    return error


def _configure_file(file_node, aov, field: str, follow_offset: bool = False,
                    hold: tuple | None = None):
    render_range = aov.get_frame_range()
    frame_path = aov.get_aov_frame_path(frame_token(follow_offset, hold))
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
    found = _upstream_native(native, input_index)
    return None if found is None else (ImportRender(found[0]), found[1])


def upstream_output_name(native, input_index: int = 0) -> str | None:
    """The name of the import_render output (on its type's layout) that
    reaches ``native``'s input ``input_index``; None when none does."""
    found = _upstream_native(native, input_index)
    return None if found is None else output_name(outputs_layout(found[0]), found[1])


def _upstream_native(native, input_index: int):
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
            return source, connection.outputIndex()
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


def create(scene, name, version: str | None = None):
    """The import named ``name`` under ``scene``, made from code (it waits
    for its channel to be set before it resolves): today's type (2.0), or
    the type ``version`` names (``'1.0'``, as old scenes hold)."""
    if version is None:
        return ns.create_node(scene, name, ImportRender, TYPE_NAME, 'Cop')
    native = scene.node(name)
    if native is not None: return ImportRender(native)
    outer, ns._from_code = ns._from_code, True
    try:
        return ImportRender(scene.createNode(f'th::{TYPE_NAME}::{version}', name))
    finally:
        ns._from_code = outer


def version_moves(from_layout: int, to_layout: int, slots, all_channels: bool) -> dict:
    """Where a wire on each output of a node on ``from_layout`` lands when
    the node becomes ``to_layout``'s type: ``{output index: (output name,
    layers field)}``, the field None for a plain output. An output the
    target has by name keeps its wire there, but for 1.0's beauty outputs
    going to 2.0: each carries the channel ``slots`` gives it, and lands on
    ``layers`` at that channel's field, except a one-channel import's
    ``rgba``, which stays (2.0's rgba of all channels is them laid over each
    other, not one of them). The rest (a 1.0 beauty output holding no
    channel, 2.0's layers on a 1.0) are dropped."""
    moves = {}
    for index, name in enumerate(LAYOUT_OUTPUTS[from_layout]):
        beauty = (name in CHANNEL_OUTPUT_NAMES and from_layout < LAYOUT_V2
                  and (name != 'rgba' or all_channels))
        if beauty and to_layout >= LAYOUT_V2:
            slot = CHANNEL_OUTPUT_NAMES.index(name)
            channel = slots[slot] if slots and slot < len(slots) else None
            if channel:
                moves[index] = (LAYERS, channel)
            continue
        if output_index(to_layout, name) is not None:
            moves[index] = (name, None)
    return moves


def change_version(native, version: str):
    """Make import ``native`` a ``th::import_render::<version>`` in place
    (its name, parms and position kept), rebuilt, each wire on the output
    of the same name (:func:`version_moves`): 1.0 -> 2.0 tidies an old comp
    (a beauty taken from rgba 2 .. rgba 8 lands on a Cable Unpack of
    ``layers`` beside the node, at its channel). Returns the new node."""
    old = ImportRender(native)
    from_layout = old.get_outputs_layout()
    all_channels = old.is_all_channels()
    slots = old.get_channel_outputs() if from_layout < LAYOUT_V2 else None
    wires = [(c.outputIndex(), c.outputNode(), c.inputIndex())
             for c in native.outputConnections()]
    for _, node, input_index in wires:
        node.setInput(input_index, None)
    outer, ns._from_code = ns._from_code, True
    try:
        native = native.changeNodeType(
            f'th::{TYPE_NAME}::{version}', keep_name=True, keep_parms=True,
            keep_network_contents=False)
    finally:
        ns._from_code = outer
    node = ImportRender(native)
    to_layout = node.get_outputs_layout()
    if native.parm(OUTPUTS_LAYOUT_PARM) is not None:
        native.parm(OUTPUTS_LAYOUT_PARM).set(LAYOUT_V1)
    node.parm(BUILD_KEY_PARM).set('')
    try:
        node.execute()
    except ns.NodeError:
        pass  # said on Reads, under the node, and as its error
    moves = version_moves(from_layout, to_layout, slots, all_channels)
    layers_unpack = None
    for output, reader, input_index in wires:
        move = moves.get(output)
        if move is None: continue
        name, field = move
        if field is None:
            reader.setInput(input_index, native, output_index(to_layout, name))
            continue
        if layers_unpack is None:
            layers_unpack = native.parent().createNode(
                'cableunpack', f'{native.name()}_layers')
            layers_unpack.setInput(0, native, output_index(to_layout, LAYERS))
            layers_unpack.setPosition(native.position() + hou.Vector2(comp.COLUMN_STRIDE, 0.0))
            channels = [s for s in slots if s]
            layers_unpack.parm('fields').set(len(channels))
            for index, channel in enumerate(channels, start=1):
                layers_unpack.parm(f'fieldname{index}').set(channel)
                layers_unpack.parm(f'fieldtype{index}').set(comp.aov_signature(comp.BEAUTY))
        fields = _unpack_fields(layers_unpack)
        if field in fields:
            reader.setInput(input_index, layers_unpack, fields.index(field))
    return native


def on_created(raw_node):
    """A new node: styled, and, when an artist drops it (not a workfile
    template, which sets the channel first and resolves itself), resolved
    straight away, so it shows the shot's latest render, or says why there
    is none, without an Update."""
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    # A new 1.0 node is on 1.0's outputs; one saved before #67 never stored
    # the parm, so it loads at the default, 0. A 2.0 node has no such parm.
    layout = raw_node.parm(OUTPUTS_LAYOUT_PARM)
    if layout is not None:
        layout.set(LAYOUT_V1)
    node = ImportRender(raw_node)
    node._update_labels()
    if ns.creating_from_code(): return
    channel = default_channel(node.get_channel_name(), *node.rendered_channels())
    if channel != node.get_channel_name():
        node.set_channel_name(channel)
    try:
        node.execute()
    except Exception:
        pass  # said on Reads, under the node, and as its error


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
