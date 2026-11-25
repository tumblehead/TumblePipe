"""th::animate's Segments: some characters from another stream of the dive, on some frames.

A shot that pans across rooms can animate each room in its own APEX Scene
Animate, side by side in one dive (`th::import_rigs` feeding all of them).
The dive's Output SOPs are its streams: **Output Index 0** is the default,
read as th::animate always has (`animate_points.dive_output`: the Output SOP
with index 0, whatever its name, else the display node); one with Output
Index 1 and up is an extra stream. The node's **Segments** rows say which
stream supplies which character on which frames:

- **Frames**: as on the Characters list (`animate_frames.parse_frames`;
  empty is every frame);
- **Output**: the extra stream, by its Output Index (`output_menu`);
- **Characters**: a pattern of the scene's characters (`*` by default; the
  Scene Invoke's own rule, `animate_invoke.pattern_picks`).

Per frame and per character, the last row whose Frames hold the frame and
whose Characters pick the character supplies it from its Output; no row:
Output 0 (`owners`). A row is ignored, with a warning naming it, when its
Frames are not frames, its Output is missing or past `MAX_STREAMS`, or the
stream is not an APEX scene still to invoke (it must end in its Scene
Animate, as the default stream does: `animate_invoke.is_scene`). A
character a row picks that its stream does not have stays where it was.
When Output 0 is itself invoked geometry (a dive from before th::animate
invoked the scene, with its own Scene Invoke) Segments do not apply, and the
node says so once.

How (no APEX clip is opened, so nothing here depends on how a Houdini build
stores keys): each stream is invoked for the characters it owns on the
frame and nothing else. `points_sops/invoke`, the default stream's invoke,
leaves out the characters another stream takes (`taken`, read by
`animate_invoke.invoke_characters` and its skeleton list); each extra stream
k has a fixed Object Merge (`stream<k>`, `stream_path`), Scene Invoke
(`stream<k>_invoke`, Characters `stream_characters`, a skeleton per Extra
Output as the default invoke has them) and Switch (`stream<k>_on`,
`stream_on`), switched to an empty Null when the stream owns nothing on the
frame, so it is not cooked at all. `streams` merges them and
`segments_join` (`join`) puts them with the default invoke's folders,
skeletons named, every character's folder in the default stream's scene
order whichever stream it came from: th::animate imports the topology once,
at the shot's first frame, and copies points after that by number, so the
order must not change with the frame. Everything lands on the same stage
prims, so a character keeps its lookdev. The `segments` Switch picks the
join only for a node with Segments rows over a scene dive (`segments_on`):
a node with none cooks exactly as before, with no more invokes and the
default invoke's own patterns.

A character's stream can change from one frame to the next. Its rig is the
same in every stream (one `th::import_rigs`), so its topology is too, and
`apply` copies its points as on any frame; a change of topology all the same
is what `apply` already handles (the frame is imported whole). Frames on the
Characters list still apply on top (`animate_frames`).
"""

import hou

MULTIPARM = 'segments'
FRAMES = 'segment_frames'
OUTPUT = 'segment_output'
CHARACTERS = 'segment_characters'
DEFAULT_CHARACTERS = '*'
#: The extra streams th::animate can invoke: Output Index 1 to this. Each is
#: a fixed `stream<k>` Object Merge, invoke and switch in `points_sops`
#: (`animate_invoke.MAX_SKELETONS` is the same kind of limit).
MAX_STREAMS = 8
STREAM = 'stream'
SCENE_SUFFIX = '.char'


# --- the rule ----------------------------------------------------------------


def read_rows(raw_rows, outputs, scene_of):
    """The Segments rows that apply, and what is wrong with the others.

    `raw_rows`: (number, frames text, output text, characters pattern) per
    row, `number` its 1-based index. `outputs`: {Output Index: Output SOP
    name} of the dive's Output SOPs; None to tell a missing one only by
    `scene_of`. `scene_of(index)`: the characters of stream `index`'s APEX
    scene, None when it is not one (or there is no such stream); asked
    only for the indices rows name.

    Returns (rules, problems): rules are (ranges, index, pattern) in row
    order (`ranges` as `animate_frames.parse_frames`, None for every frame);
    problems are (number, why) for each row left out."""
    from tumblepipe.pipe.houdini.lops import animate_frames
    rules, problems = [], []
    for number, frames_text, output_text, pattern in raw_rows:
        try:
            ranges = animate_frames.parse_frames(frames_text)
        except animate_frames.FramesError as error:
            problems.append((number, f'Frames "{frames_text}" are not frames ({error})'))
            continue
        output_text = (output_text or '').strip()
        if not output_text:
            problems.append((number, 'it names no Output'))
            continue
        try:
            index = int(output_text)
        except ValueError:
            index = None
        if index is None or index < 1:
            problems.append((number, f'Output "{output_text}" is not an Output Index of 1 or more '
                                     '(0 is the default stream, used where no row is)'))
            continue
        if index > MAX_STREAMS:
            problems.append((number, f'Output {index} is past the {MAX_STREAMS} extra outputs '
                                     f'Segments read (Output Index 1 to {MAX_STREAMS})'))
            continue
        if outputs is not None and index not in outputs:
            problems.append((number, f'anim_dive has no Output SOP with Output Index {index}'))
            continue
        if scene_of(index) is None:
            name = f' ({outputs[index]})' if outputs is not None else ''
            problems.append((number, f'Output {index}{name} is not an APEX scene to invoke: '
                                     'end it in its Scene Animate, with no Scene Invoke'))
            continue
        rules.append((ranges, index, pattern if pattern is not None else DEFAULT_CHARACTERS))
    return rules, problems


def owners(rules, characters, scenes, frame) -> dict:
    """{character: the Output Index supplying it at `frame`} for each of
    `characters` (the default stream's): the last of `rules` (`read_rows`)
    whose Frames hold `frame`, whose pattern picks the character and whose
    stream (`scenes`: {index: its characters}) has it; 0 when none does."""
    from tumblepipe.pipe.houdini.lops import animate_frames, animate_invoke
    live = [(index, pattern) for ranges, index, pattern in rules
            if animate_frames.frame_in(ranges, frame)]
    result = {}
    for character in characters:
        owner = 0
        for index, pattern in live:
            if character in (scenes.get(index) or ()) and animate_invoke.pattern_picks(pattern, character):
                owner = index
        result[character] = owner
    return result


def problem_message(number, why) -> str:
    return f'th::animate: Segments row {number} is ignored: {why}.'


INVOKED_MESSAGE = (
    "th::animate: Segments do not apply: anim_dive's Output 0 is invoked geometry "
    '(it has its own Scene Invoke), not the APEX scene. Wire its Scene Animate into '
    'the output and delete the Scene Invoke to use them.'
)


# --- the node ----------------------------------------------------------------


def _row_count(animate_node) -> int:
    parm = animate_node.parm(MULTIPARM) if animate_node is not None else None
    return parm.eval() if parm is not None else 0


def raw_rows(animate_node) -> list:
    """(number, frames, output, characters) per Segments row."""
    result = []
    for number in range(1, _row_count(animate_node) + 1):
        result.append((
            number,
            animate_node.evalParm(f'{FRAMES}{number}'),
            animate_node.evalParm(f'{OUTPUT}{number}'),
            animate_node.evalParm(f'{CHARACTERS}{number}'),
        ))
    return result


def dive_outputs(dive_node) -> dict:
    """{Output Index: Output SOP} of the dive's Output SOPs, the first of
    each index. Found by node type, as `animate_points.dive_output` finds
    Output 0, so placing a node in the dive does not recook what reads it."""
    from tumblepipe.pipe.houdini.lops import animate_points
    found = {}
    output_type = animate_points._output_type()
    if dive_node is None or output_type is None:
        return found
    for node in output_type.instances():
        if node.parent() != dive_node:
            continue
        parm = node.parm('outputidx')
        if parm is None:
            continue
        found.setdefault(parm.eval(), node)
    return found


def _sops_of(animate_node):
    return animate_node.node('points_sops') if animate_node is not None else None


def _scene_reader(sops):
    """`read_rows`' `scene_of` for a node: stream `index`'s characters,
    read once each through `points_sops/stream<index>` (an Object Merge of
    the dive's Output SOP with that index, nothing without one), None
    when what it gives is not an APEX scene. `.scenes` keeps what was
    read."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    scenes = {}

    def scene_of(index):
        if index not in scenes:
            merge = sops.node(f'{STREAM}{index}') if sops is not None else None
            geo = merge.geometry() if merge is not None else None
            scenes[index] = (set(animate_invoke.scene_characters(geo))
                             if animate_invoke.is_scene(geo) else None)
        return scenes[index]

    scene_of.scenes = scenes
    return scene_of


def _state(animate_node, outputs=None):
    """(rules, problems, characters, scenes) of a node's Segments: the rows
    that apply, what is wrong with the rest (`outputs`, {index: name} of the
    dive's Output SOPs, words the problems; without it a missing output is
    told only by its stream), the default stream's characters in scene
    order and each named extra stream's (`_scene_reader`)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    sops = _sops_of(animate_node)
    dive = sops.node('dive') if sops is not None else None
    characters = animate_invoke.scene_characters(dive.geometry()) if dive is not None else []
    scene_of = _scene_reader(sops)
    rules, problems = read_rows(raw_rows(animate_node), outputs, scene_of)
    scenes = {index: names for index, names in scene_of.scenes.items() if names is not None}
    return rules, problems, characters, scenes


def _active(animate_node) -> bool:
    """The node has Segments rows and a dive that outputs the APEX scene."""
    if not _row_count(animate_node):
        return False
    from tumblepipe.pipe.houdini.lops import animate_invoke
    sops = _sops_of(animate_node)
    dive = sops.node('dive') if sops is not None else None
    return dive is not None and animate_invoke.is_scene(dive.geometry())


def _owners(sop_node):
    """{character: owner} on this frame for the th::animate `sop_node` is
    in; None when its Segments do not apply (no rows: nothing more is
    read)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    animate = animate_invoke._animate_node(sop_node)
    if animate is None or not _active(animate):
        return None
    rules, _problems, characters, scenes = _state(animate)
    return owners(rules, characters, scenes, hou.frame())


def _owned(sop_node, index) -> list:
    owner_of = _owners(sop_node)
    return [] if owner_of is None else [name for name, owner in owner_of.items() if owner == index]


# Expressions in points_sops.

def segments_on(sop_node) -> int:
    """The `segments` Switch: 1 when the node has Segments rows and its dive
    outputs the APEX scene (the streams are joined), else 0 (the default
    invoke's output as it is)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    return int(_active(animate_invoke._animate_node(sop_node)))


def taken(sop_node) -> list:
    """The characters another stream supplies on this frame, which the
    default stream's invoke leaves out; empty for a node with no Segments
    rows (read without cooking anything)."""
    owner_of = _owners(sop_node)
    return [] if owner_of is None else [name for name, owner in owner_of.items() if owner != 0]


def stream_path(sop_node, index: int) -> str:
    """`stream<index>`'s Object Merge path: the dive's Output SOP with
    Output Index `index`, '' without one."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    animate = animate_invoke._animate_node(sop_node)
    if animate is None or not _row_count(animate):
        return ''
    node = dive_outputs(animate.node('anim_dive')).get(index)
    return node.path() if node is not None else ''


def stream_on(sop_node, index: int) -> int:
    """`stream<index>_on`: 1 when stream `index` supplies a character on
    this frame (its invoke cooks), else 0 (an empty Null)."""
    return int(bool(_owned(sop_node, index)))


def stream_characters(sop_node, index: int) -> str:
    """`stream<index>_invoke`'s Characters: the characters the stream
    supplies on this frame but those whose row on the Characters list
    turns Geometry off (their skeletons still come, as Extra Outputs)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    animate = animate_invoke._animate_node(sop_node)
    off = {name for name, geometry, _s in animate_invoke.rows(animate) if not geometry} if animate else set()
    return ' '.join(name for name in _owned(sop_node, index) if name not in off)


def _stream_skeletons(sop_node, index: int) -> list:
    from tumblepipe.pipe.houdini.lops import animate_invoke
    animate = animate_invoke._animate_node(sop_node)
    if animate is None:
        return []
    return animate_invoke.skeleton_characters(animate_invoke.rows(animate), _owned(sop_node, index))


def stream_skeleton_enabled(sop_node, index: int, entry: int) -> int:
    return int(entry < len(_stream_skeletons(sop_node, index)))


def stream_skeleton_path(sop_node, index: int, entry: int) -> str:
    from tumblepipe.pipe.houdini.lops import animate_invoke
    skeletons = _stream_skeletons(sop_node, index)
    return animate_invoke.skeleton_output_path(skeletons[entry]) if entry < len(skeletons) else ''


# --- joining the streams -----------------------------------------------------


def join_order(main_names, stream_names, characters):
    """Where each folder of the joined streams comes from: a list of
    (source, number), source 0 for the default invoke's folders
    (`main_names`, in its order) and 1 for the extra streams' (`stream_names`).

    The default invoke's folders that are not characters (no `.char`) come
    first, as they are. Then one folder per character, in the default
    stream's scene order (`characters`), names it does not have after, in
    name order: the same order on every frame, whichever stream a character
    comes from. A character in both (never, while the default invoke leaves
    out what the streams take) is the stream's; extra streams' folders that
    are not characters are left out (each stream's scene has the same rigs:
    only its characters are its own)."""
    rank = {name: i for i, name in enumerate(characters)}
    picked, order = {}, []
    for number, name in enumerate(main_names):
        if not name.endswith(SCENE_SUFFIX):
            order.append((0, number))
        else:
            picked.setdefault(name, (0, number))
    for number, name in enumerate(stream_names):
        if name.endswith(SCENE_SUFFIX):
            if picked.get(name, (0,))[0] == 0:
                picked[name] = (1, number)
    big = len(characters)

    def key(name):
        return (rank.get(name[:-len(SCENE_SUFFIX)], big), name)

    order += [picked[name] for name in sorted(picked, key=key)]
    return order


_TAKE = '__th_segments_take'


def _folder_names(geo) -> list:
    from tumblepipe.pipe.houdini.lops import animate_invoke
    if geo is None or geo.findPrimAttrib('name') is None:
        return ['' for _ in (geo.prims() if geo is not None else ())]
    return [animate_invoke._name(prim) for prim in geo.prims()]


def join(sop_node) -> None:
    """The `segments_join` Python SOP: the default invoke's packed folders
    (input 1, skeletons named) with the extra streams' (input 2, merged),
    their skeletons named, in `join_order`."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    geo = sop_node.geometry()
    inputs = sop_node.inputs()
    second = inputs[1] if len(inputs) > 1 else None
    streams = second.geometry() if second is not None else None
    dive = sop_node.node('../dive')
    scene = dive.geometry() if dive is not None else None
    characters = animate_invoke.scene_characters(scene)
    main = geo.freeze()
    extra = streams.freeze() if streams is not None and len(streams.prims()) else None
    if extra is not None:
        animate_invoke.name_skeletons(extra, scene)
    order = join_order(_folder_names(main), _folder_names(extra), characters)
    if extra is None and order == [(0, n) for n in range(len(main.prims()))]:
        return  # nothing from the streams, and already in order
    sources = (main, extra)
    geo.deletePrims(geo.prims())
    for source, number in order:
        src = sources[source]
        group = src.findPrimGroup(_TAKE) or src.createPrimGroup(_TAKE)
        group.clear()
        group.add(src.prim(number))
        geo.mergePrims(src, group)
    leftover = geo.findPrimGroup(_TAKE)
    if leftover is not None:
        leftover.destroy()


# --- parameter menus and warnings --------------------------------------------


def output_menu(animate_node) -> list:
    """The Output menu: the dive's Output SOPs with Output Index 1 and up,
    `1  kitchen_out` (token, label pairs, by index)."""
    menu = []
    outputs = dive_outputs(animate_node.node('anim_dive'))
    for index in sorted(outputs):
        if index >= 1:
            menu.extend([str(index), f'{index}  {outputs[index].name()}'])
    return menu


def problems(animate_node) -> list:
    """The warnings for a node's Segments: Output 0 not a scene (they do
    not apply), or each row left out and why; none without rows."""
    if not _row_count(animate_node):
        return []
    from tumblepipe.pipe.houdini.lops import animate_invoke
    sops = _sops_of(animate_node)
    dive = sops.node('dive') if sops is not None else None
    if dive is None or not animate_invoke.is_scene(dive.geometry()):
        return [INVOKED_MESSAGE]
    outputs = {index: node.name() for index, node in
               dive_outputs(animate_node.node('anim_dive')).items()}
    _rules, found, _characters, _scenes = _state(animate_node, outputs)
    return [problem_message(number, why) for number, why in found]
