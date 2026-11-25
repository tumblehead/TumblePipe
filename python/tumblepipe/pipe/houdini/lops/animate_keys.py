"""th::animate: a character keyed in two Scene Animates of one chain.

APEX Scene Animate keeps keys per character, at
`/animation/default.clip/_<character>.char_Base` (`CLIP`), and a layer's
keys in a folder beside them (`<clip>/<layer>/_<character>.char_Base`). What
a Scene Animate keeps of its own is its `animation` parm: what Animate saves
there, only the paths that differ from the node's input
(`apexanimate.py` `_writeAnimationToGeometryParm`, `saveToGeometry(out,
pattern, input_geo)`). Measured in hython 22.0.508 (2026-11-25, TumbleRig's
example figure, as `copy-animation.md` in TumbleRig measured on 22.0.368 and
466):

- a Scene Animate whose `animation` holds a base entry for a character
  replaces the incoming entry whole: a held entry keying one channel left
  none of the upstream channels, and so did a held entry of default values
  only, or with no channels at all;
- one with no entry for the character passes the incoming entry through
  (an unkeyed Scene Animate's `animation` is None);
- one that holds only a layer's entry for the character passes the base
  through, and a Scene Animate holding a base entry keeps the layers it is
  given, Inherit Layers on or off: only base entries replace;
- TumbleRig's Copy Animation on Base Animation merges the copied channels
  into the character's base entry; on a New Layer it writes the layer and,
  under each copied channel the base lacks, a rest key (one key at the
  first frame), so the layer has something to blend from.

So a dive that chains Scene Animates in series (one per room of a shot that
pans across rooms, each keying its own characters: issue #98) combines them
into one scene, but a character keyed in two of them keeps only the
downstream one's keys. A character's entry is one path, so what Animate
saves once a control of it is keyed can carry the keys it was given (read
from apexanimate.py; Animate itself not driven here): the shot can look
right while every edit to the character upstream does nothing. That is why
a Scene Animate is read by its own `animation`, not by comparing its output
with its input: holding an entry exactly as given, it outputs what it is
given (scripts/verify_animate_double_keys.py, case `given`).

`double_keys_warning` says so. For every Output SOP of the dive (Output
Index 0 resolved as th::animate reads it, `animate_points.dive_output`, and
every Output SOP with index 1 and up, `dive_roots`) it walks each Scene
Animate feeding it up its stream (`chains_to`: the first input, every input
of a Switch), reads per node which characters it holds or writes
(`link_of`: a Scene Animate's own `animation`, any other node's output
against its input, base entries only) and gives the plain data to
`double_keys`. The warning goes on `points_sops/placed` and the status bar
with th::animate's others (`animate_points.placed_shapes`); nothing changes
in what cooks. It is worked out again only when the dive's wiring, a bypass
flag or a Scene Animate's cook count changes (`_signature`), never per
frame: Scene Animates are not time dependent, so stepping frames leaves
their cook counts alone.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Where Scene Animate keeps the keys th::animate's rigs play.
CLIP = '/animation/default.clip'
_BASE_PREFIX = f'{CLIP}/_'
_BASE_SUFFIX = '.char_Base'
#: A layer folder's own data, beside its entries.
LAYER_DATA = 'layer.data'
#: Nodes that choose one input and write nothing: every input is a stream.
SWITCH_TYPES = ('switch', 'switchif')
#: Paths up from one Scene Animate, at most (a Switch branches the walk).
MAX_CHAINS = 64


def entry_path(character: str, layer: str = '') -> str:
    """The folder of `character`'s keys: its base entry, or on `layer`."""
    return f'{CLIP}/{layer}/_{character}{_BASE_SUFFIX}' if layer else \
        f'{_BASE_PREFIX}{character}{_BASE_SUFFIX}'


def base_characters(paths) -> list[str]:
    """The characters with a base entry among packed folder `paths`
    (`/animation/default.clip/_<character>.char_Base`), once each, in
    order. A layer's entries (`<clip>/<layer>/_<character>.char_Base`),
    `layer.data` and anything outside the clip are not base entries."""
    found = []
    for path in paths:
        if not (path.startswith(_BASE_PREFIX) and path.endswith(_BASE_SUFFIX)):
            continue
        name = path[len(_BASE_PREFIX):-len(_BASE_SUFFIX)]
        if name and '/' not in name and name not in found:
            found.append(name)
    return found


def layer_names(paths) -> list[str]:
    """The layer folders among the clip's children `paths`: neither a base
    entry nor the clip's own `layer.data`."""
    names = []
    for path in paths:
        if not path.startswith(CLIP + '/'):
            continue
        name = path[len(CLIP) + 1:]
        if not name or '/' in name or name.startswith('_') or name == LAYER_DATA:
            continue
        if name not in names:
            names.append(name)
    return names


def base_writes(before: dict, after: dict, layers_before: dict, layers_after: dict) -> set:
    """The channels a node writes on a character's base entry: the ones
    whose curve `after` (channel -> its keys, values, slopes and default)
    has and `before` (the node's input) does not have the same, less the
    channels the node also added or changed on a layer of the character
    (`layers_*`: (layer, channel) -> the same): those are the rest keys a
    New Layer puts under its channels, not keys of their own."""
    written = {name for name, curve in after.items() if before.get(name) != curve}
    layered = {name for (layer, name), curve in layers_after.items()
               if layers_before.get((layer, name)) != curve}
    return written - layered


# --- the rule, over plain data ------------------------------------------------


@dataclass(frozen=True)
class Link:
    """One node of a chain as `double_keys` reads it.

    `holds`: the characters whose base entry the node replaces whole (a
    Scene Animate's own entries). `writes`: the characters whose base entry
    it changes and keeps the rest of (Copy Animation on Base Animation; at
    the head of a chain, whatever keys arrive from elsewhere). `keyed`: of
    those, the ones whose entry as the node holds it, or channels as the
    node writes them, carry keys (a channel with only a default drives
    nothing)."""
    name: str
    holds: frozenset = frozenset()
    writes: frozenset = frozenset()
    keyed: frozenset = frozenset()


@dataclass(frozen=True)
class DoubleKey:
    """`node` holds its own keys for `character`, so the keys of `lost`
    (nodes above it, nearest first) never reach the shot."""
    character: str
    node: str
    lost: tuple


def keys_reaching(upstream, character: str) -> list[str]:
    """The nodes of `upstream` (a chain, upstream first) whose keys for
    `character` reach its end, nearest first: walking up, every node that
    holds or writes the character with keys, up to the nearest that holds
    it (it replaced whatever was above it)."""
    lost = []
    for link in reversed(upstream):
        holds = character in link.holds
        if not holds and character not in link.writes:
            continue
        if character in link.keyed:
            lost.append(link.name)
        if holds:
            break
    return lost


def double_keys(chain) -> list[DoubleKey]:
    """Each node of `chain` (`Link`s, upstream first) that holds an entry
    for a character whose keys from above reach it (`keys_reaching`): its
    entry replaces them whole. A character held with nothing keyed above,
    a node that only writes (merges), and a node that holds nothing are no
    double key."""
    found = []
    for index, link in enumerate(chain):
        for character in sorted(link.holds):
            lost = keys_reaching(chain[:index], character)
            if lost:
                found.append(DoubleKey(character, link.name, tuple(lost)))
    return found


def _listed(names) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def double_keys_message(found) -> str:
    """The warning for `found` (`DoubleKey`s): a line per node and the
    nodes it replaces keys of (in the order first given), naming the
    characters and the fix; '' for none."""
    groups: dict = {}
    for item in found:
        lost, characters = groups.setdefault((item.node, frozenset(item.lost)), (item.lost, []))
        if item.character not in characters:
            characters.append(item.character)
    lines = []
    for (node, _), (lost, characters) in groups.items():
        whose = f"{characters[0]}'s" if len(characters) == 1 else 'their'
        lines.append(
            f'th::animate: in anim_dive, {node} holds its own keys for '
            f'{_listed(characters)}, so {whose} keys from {_listed(lost)} above '
            'it do nothing (a Scene Animate\'s keys for a character replace '
            'the ones it is given). Key each character in one Scene Animate '
            'and move keys between them with Copy Animation, or have Copy '
            'Animation put the keys above on a New Layer, which a Scene '
            'Animate below keeps.')
    return '\n'.join(lines)


# --- reading the dive -----------------------------------------------------------


def _type_name(node) -> str:
    return node.type().name()


def is_scene_animate(node) -> bool:
    return _type_name(node).startswith('apex::sceneanimate')


def is_switch(node) -> bool:
    return _type_name(node).split('::')[0] in SWITCH_TYPES


def stream_inputs(node) -> list:
    """The inputs `node`'s scene comes from: every wired input of a
    Switch (any one may be chosen), else its first (a Scene Animate's
    scene, a Copy Animation's input 1, a Scene Add Character's scene)."""
    inputs = list(node.inputs())
    if is_switch(node):
        return [n for n in inputs if n is not None]
    return [inputs[0]] if inputs and inputs[0] is not None else []


def chains_to(node, limit: int = MAX_CHAINS) -> list[list]:
    """Every path up `stream_inputs` from `node` to a node with none, each
    upstream first and ending at `node`; at most `limit`."""
    chains, todo = [], [[node]]
    while todo and len(chains) < limit:
        path = todo.pop()
        seen = {n.sessionId() for n in path}
        ups = [n for n in stream_inputs(path[0]) if n.sessionId() not in seen]
        if not ups:
            chains.append(path)
            continue
        todo += [[up] + path for up in reversed(ups)]
    return chains


def dive_roots(dive_node) -> list:
    """The dive's outputs: its Output SOP with Output Index 0 (else its
    display node, `animate_points.dive_output`) and every Output SOP in it
    with index 1 and up. Found by node type, not by listing the dive's
    children, for `dive_output`'s reason."""
    from tumblepipe.pipe.houdini.lops import animate_points
    roots = []
    first = animate_points.dive_output(dive_node)
    if first is not None:
        roots.append(first)
    output_type = animate_points._output_type()
    for node in (output_type.instances() if output_type is not None else ()):
        if node.parent() != dive_node or node in roots:
            continue
        parm = node.parm('outputidx')
        if parm is not None and parm.eval() >= 1:
            roots.append(node)
    return roots


def upstream_nodes(roots) -> list:
    """`roots` and every node wired above them, through every input."""
    found, seen, todo = [], set(), list(roots)
    while todo:
        node = todo.pop()
        if node is None or node.sessionId() in seen:
            continue
        seen.add(node.sessionId())
        found.append(node)
        todo += [n for n in node.inputs() if n is not None]
    return found


def _geometry(node):
    import hou
    try:
        return node.geometry()
    except hou.Error:
        return None


def _stored(node):
    """A Scene Animate's own `animation`, None when it holds nothing."""
    import hou
    parm = node.parm('animation')
    try:
        return parm.evalAsGeometry() if parm is not None else None
    except hou.Error:
        return None


def _paths(geo, folder: str) -> tuple:
    import hou
    if geo is None:
        return ()
    try:
        return tuple(geo.extractPackedPaths(f'{folder}/*'))
    except (ValueError, hou.Error):
        return ()


def _unpack(geo, path: str):
    import hou
    try:
        return geo.unpackFromFolder(path)
    except (ValueError, hou.Error):
        return None


def _curve(prim):
    """What a channel plays: its keys, their values, slopes and
    accelerations, and its default."""
    frames = tuple(prim.keyFrames())
    shape = ()
    if frames:
        shape = tuple(prim.keyValues())
        for name in ('keySlopes', 'keyAccels'):
            read = getattr(prim, name, None)
            shape += (tuple(read()),) if read is not None else ()
    return (frames, shape, prim.defaultValue())


def _channels(geo, path: str, children: tuple) -> dict | None:
    """Channel name -> `_curve` of the entry at `path`; None without one."""
    if path not in children:
        return None
    entry = _unpack(geo, path)
    if entry is None:
        return None
    named = entry.findPrimAttrib('name') is not None
    curves = {}
    for prim in entry.prims():
        if not hasattr(prim, 'keyFrames'):
            continue
        curves[prim.attribValue('name') if named else str(prim.number())] = _curve(prim)
    return curves


def _has_keys(curves) -> bool:
    return any(curve[0] for curve in (curves or {}).values())


def _layer_channels(geo, character: str, clip_children: tuple) -> dict:
    """(layer, channel) -> `_curve` of `character`'s entries on layers."""
    found = {}
    for layer in layer_names(clip_children):
        path = entry_path(character, layer)
        for name, curve in (_channels(geo, path, _paths(geo, f'{CLIP}/{layer}')) or {}).items():
            found[(layer, name)] = curve
    return found


def held(node) -> dict:
    """Character -> whether its entry carries keys, for the base entries
    Scene Animate `node` holds of its own."""
    stored = _stored(node)
    children = _paths(stored, CLIP)
    return {character: _has_keys(_channels(stored, entry_path(character), children))
            for character in base_characters(children)}


def link_of(node, upstream, characters) -> Link:
    """`node` as a `Link`, below `upstream` (None at the head of a chain),
    for `characters`. A bypassed node and a Switch pass their input on; a
    Scene Animate holds its own base entries (`held`); any other node
    writes a character whose base entry it changes (`base_writes` of its
    output against `upstream`'s; at the head, against nothing)."""
    name = node.name()
    if node.isBypassed() or is_switch(node):
        return Link(name)
    if is_scene_animate(node):
        own = held(node)
        return Link(name, holds=frozenset(own),
                    keyed=frozenset(c for c, keyed in own.items() if keyed))
    after_geo = _geometry(node)
    before_geo = _geometry(upstream) if upstream is not None else None
    after_children, before_children = _paths(after_geo, CLIP), _paths(before_geo, CLIP)
    writes, keyed = set(), set()
    for character in characters:
        after = _channels(after_geo, entry_path(character), after_children) or {}
        before = _channels(before_geo, entry_path(character), before_children) or {}
        if after == before:
            continue
        written = base_writes(before, after,
                              _layer_channels(before_geo, character, before_children),
                              _layer_channels(after_geo, character, after_children))
        if written:
            writes.add(character)
            if any(after[channel][0] for channel in written):
                keyed.add(character)
    return Link(name, writes=frozenset(writes), keyed=frozenset(keyed))


def find_double_keys(dive_node) -> list[DoubleKey]:
    """`double_keys` of every chain up from every Scene Animate feeding the
    dive's outputs (`dive_roots`), each node with what it holds once."""
    animates = [n for n in upstream_nodes(dive_roots(dive_node))
                if is_scene_animate(n) and not n.isBypassed()]
    found: dict = {}
    links: dict = {}
    holds: dict = {}

    def holds_of(node):
        key = node.sessionId()
        if key not in holds:
            holds[key] = frozenset(held(node)) if is_scene_animate(node) and not node.isBypassed() \
                else frozenset()
        return holds[key]

    for animate in animates:
        if not holds_of(animate):
            continue
        for chain in chains_to(animate):
            characters = frozenset().union(*(holds_of(n) for n in chain))
            row = []
            for index, node in enumerate(chain):
                upstream = chain[index - 1] if index else None
                key = (node.sessionId(), upstream.sessionId() if upstream else None, characters)
                if key not in links:
                    links[key] = link_of(node, upstream, characters)
                row.append(links[key])
            for item in double_keys(row):
                if item.node != animate.name():
                    continue  # that Scene Animate's own chains say it
                lost = found.setdefault((item.node, item.character), [])
                lost += [name for name in item.lost if name not in lost]
    return [DoubleKey(character, node, tuple(lost)) for (node, character), lost in found.items()]


# What was last worked out per th::animate: (signature, message).
_cache: dict = {}


def _signature(nodes) -> tuple:
    """What `find_double_keys` reads changes only with this: the nodes,
    their wiring and bypass flags, and the Scene Animates' cook counts (a
    key edit recooks the Scene Animate and everything below it; nothing
    else does, frame changes included)."""
    return tuple(
        (node.sessionId(), node.isBypassed(),
         tuple(n.sessionId() if n is not None else None for n in node.inputs()),
         node.cookCount() if is_scene_animate(node) else None)
        for node in nodes)


def double_keys_warning(animate_node) -> str:
    """The warning for th::animate `animate_node`'s dive
    (`double_keys_message` of `find_double_keys`), '' for none or outside
    a th::animate. Worked out again only when `_signature` changes. A
    warning only: reading the dive never fails the cook."""
    try:
        find = getattr(animate_node, 'node', None)
        dive = find('anim_dive') if callable(find) else None
        if dive is None:
            return ''
        signature = _signature(upstream_nodes(dive_roots(dive)))
        key = animate_node.path()
        cached = _cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        message = double_keys_message(find_double_keys(dive))
        _cache[key] = (signature, message)
        return message
    except Exception:
        return ''
