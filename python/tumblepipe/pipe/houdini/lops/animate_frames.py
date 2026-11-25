"""th::animate's per-character Frames: a character shows only on its frames.

The Characters list has a **Frames** column (`frames#`). Empty, a character
shows on every frame, as it always did. Filled (`1001-1100 1250-1300 1400`:
whole frames and ranges of them, separated by spaces or commas), the
character is invisible on every other frame: nothing of it renders, the
geometry th::animate animates, its skeleton (`skel/Base`) and the parts the
rig does not output that sit under the character's prim (a groom) alike.
It is still invoked and copied on those frames, so nothing about its
animation changes and the shot exports as one layer.

How: `apply` (th::animate's `apply` LOP) samples `visibility` on the
character's own prim, `/<category>/<character>`, every frame: `invisible`
outside its Frames; inside them what the prim has without th::animate (the
incoming stage's: `inherited`, or `invisible` where the stage hides it),
or, when the character's prim is itself a geometry prim th::animate
animates, the sample `apply` already wrote for it. USD's visibility is
inherited down the namespace: a prim under an invisible one is invisible
whatever its own `visibility` says (`UsdGeomImageable.ComputeVisibility`),
and a prim under a visible one is what its own says. So outside the Frames
everything under the character is hidden, and inside them each geometry
prim is exactly as `apply` animates it without Frames (the rig's
`usdvisibility`, `animate_points.author_visibility`). Every frame, for the
reason `author_visibility` samples every frame: a prim sampled only where it
is hidden reads as hidden on every frame of a saved range.

Frames are the workfile's playbar frames, the frame the dive cooks at
(`hou.frame()` in `apply`, as for every sample it writes): in a Multi's
workfile the shared timeline's, not the member shot's own. A sub-frame (a
motion blur sample) counts as the nearer whole frame (`whole_frame`).

Text that is not frames is a warning on `apply` and on the status bar,
naming the row, and the row's character shows on every frame; nothing
raises. A character in several rows shows on every frame any of them
shows it. A character with no row, or an empty Frames, gets nothing from
here: its prim is left exactly as before Frames existed.
"""

import math
import re

FRAMES = 'frames'
#: The status-bar message last shown per th::animate (`_report`), so a cook
#: every frame shows it once.
_announced: dict = {}


class FramesError(ValueError):
    """Text that is not frames; the message says which word."""


_WORD = re.compile(r'(\d+)(?:-(\d+))?')
# `1001 - 1100`: the spaces an artist types around a range's dash.
_SPACED_DASH = re.compile(r'(\d)\s*-\s*(?=\d)')


def parse_frames(text: str):
    """`text` (a Frames parm) as whole-frame ranges: None for empty (every
    frame), else a tuple of (first, last) pairs, in the order written.
    Words are separated by spaces or commas: a frame (`1400`) or a range of
    them (`1001-1100`, both ends included, first <= last; `1001 - 1100`
    reads the same). Raises FramesError naming the first word that is
    neither."""
    words = [word for word in re.split(r'[\s,]+', _SPACED_DASH.sub(r'\1-', text or '')) if word]
    if not words:
        return None
    ranges = []
    for word in words:
        match = _WORD.fullmatch(word)
        if match is None:
            raise FramesError(f'"{word}" is not a frame or a range of frames')
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        if last < first:
            raise FramesError(f'"{word}" ends before it starts')
        ranges.append((first, last))
    return tuple(ranges)


def whole_frame(frame) -> int:
    """The whole frame `frame` counts as: itself, and a sub-frame the
    nearer whole frame (half way, the later one)."""
    return math.floor(float(frame) + 0.5)


def frame_in(ranges, frame) -> bool:
    """Whether `frame` is in `ranges` (`parse_frames`; None is every frame)."""
    if ranges is None:
        return True
    at = whole_frame(frame)
    return any(first <= at <= last for first, last in ranges)


def limits(rows):
    """Which characters show on some frames only, from the Characters
    list's rows: (number, character, frames text) each, `number` the row's
    1-based index. Returns ({character: ranges}, problems): only characters
    whose every row has frames (a row with none, or with text that is not
    frames, shows its character on every frame), their rows' ranges joined;
    `problems` is (number, character, text, why) for each row whose text is
    not frames."""
    found, every, problems = {}, set(), []
    for number, character, text in rows:
        character = (character or '').strip()
        if not character:
            continue
        try:
            ranges = parse_frames(text)
        except FramesError as error:
            problems.append((number, character, text, str(error)))
            ranges = None
        if ranges is None:
            every.add(character)
        else:
            found.setdefault(character, []).extend(ranges)
    return {name: tuple(ranges) for name, ranges in found.items() if name not in every}, problems


def problem_message(number, character, text, why) -> str:
    return (
        f'th::animate: Characters row {number} ({character}): Frames "{text}" is not '
        f'frames ({why}). Write whole frames and ranges separated by spaces, '
        f'1001-1100 1250 1300-1310; {character} shows on every frame until then.'
    )


def missing_message(characters) -> str:
    shown = ', '.join(characters)
    return (
        f'th::animate: Frames are set for {shown}, which the dive\'s output does '
        'not have as a character with a stage path (its <name>.char folder), so '
        'nothing is hidden. Check the name in the Characters list.'
    )


# --- the node ----------------------------------------------------------------


def rows(animate_node):
    """(number, character, frames text) per row of the Characters list;
    empty without one, and without a Frames column (contents unlocked
    from a version before it)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    count_parm = animate_node.parm(animate_invoke.MULTIPARM) if animate_node is not None else None
    if count_parm is None:
        return []
    result = []
    for number in range(1, count_parm.eval() + 1):
        frames_parm = animate_node.parm(f'{FRAMES}{number}')
        if frames_parm is None:
            continue
        result.append((number, animate_node.evalParm(f'{animate_invoke.CHARACTER}{number}'),
                       frames_parm.evalAsString()))
    return result


_prims_cache: dict = {}


def character_prims(scene_geo) -> dict:
    """{character: `/<category>/<character>`} for each `<name>.char` folder
    of `scene_geo` (the dive's output: the APEX scene, or packed folders
    invoked from it) whose shapes carry a stage path; a character with none
    is left out (it has no prim to hide)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    found = {}
    if scene_geo is None or scene_geo.findPrimAttrib('name') is None:
        return found
    for folder in scene_geo.prims():
        name = animate_invoke._name(folder)
        if not name.endswith(animate_invoke.SCENE_SUFFIX):
            continue
        getter = getattr(folder, 'getEmbeddedGeometry', None)
        prefix = animate_invoke.shape_prefix(getter() if getter is not None else None)
        if prefix is not None:
            found.setdefault(name[:-len(animate_invoke.SCENE_SUFFIX)], prefix)
    return found


def _dive_prims(sops):
    """`character_prims` of `points_sops/dive`, worked out again only when
    the dive has cooked since (the APEX scene of a dive that ends in its
    Scene Animate does not cook every frame)."""
    dive = sops.node('dive') if sops is not None else None
    if dive is None:
        return {}
    geo = dive.geometry()
    key = (dive.sessionId(), dive.cookCount())
    cached = _prims_cache.get(key[0])
    if cached is None or cached[0] != key:
        cached = _prims_cache[key[0]] = (key, character_prims(geo))
    return cached[1]


def author_visibility(layer, paths, shown, bases, frame) -> None:
    """A `visibility` sample at `frame` on each character prim at `paths`
    (Sdf paths) in `layer`: `invisible` where `shown` (one bool per path)
    is False; else `bases` (`animate_points.visibility_bases`: what the prim
    has without th::animate), unless the prim already has a sample at
    `frame` (a geometry prim at the character's path, which `apply` samples
    itself), which is left as it is."""
    from pxr import Sdf
    with Sdf.ChangeBlock():
        for path, show, base in zip(paths, shown, bases):
            attr_path = path.AppendProperty('visibility')
            if show and layer.QueryTimeSample(attr_path, frame) is not None:
                continue
            if layer.GetAttributeAtPath(attr_path) is None:
                spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
                Sdf.AttributeSpec(spec, 'visibility', Sdf.ValueTypeNames.Token,
                                  Sdf.VariabilityVarying)
            layer.SetTimeSample(attr_path, frame, base if show else 'invisible')


def hide(layer, prims, character_limits, incoming, frame) -> list:
    """Author `frame` of the characters that show on some frames only
    (`character_limits`: `limits`) on their prims (`prims`:
    `character_prims`) in `layer`, over `incoming` (the stage coming into
    th::animate; None without one). Returns the characters with no prim."""
    from pxr import Sdf

    from tumblepipe.pipe.houdini.lops import animate_points
    shown, missing = {}, []
    for character, ranges in character_limits.items():
        path = prims.get(character)
        if path is None:
            missing.append(character)
            continue
        # Two characters on one prim: it shows where either does.
        shown[path] = shown.get(path, False) or frame_in(ranges, frame)
    if shown:
        paths = [Sdf.Path(path) for path in shown]
        bases = animate_points.visibility_bases(incoming, layer, paths, frame)
        author_visibility(layer, paths, list(shown.values()), bases, frame)
    return sorted(missing)


def apply(lop_node, layer, frame) -> None:
    """From th::animate's `apply` LOP, once it has written `frame` into
    `layer`: hide the characters outside their Frames, and say what is
    wrong with the Frames and the Segments (a warning on `apply`, once per
    change on the status bar). Nothing for a node with no Frames set and no
    Segments rows."""
    from tumblepipe.pipe.houdini.lops import animate_points, animate_segments
    animate = animate_points._animate_node(lop_node)
    if animate is None:
        return
    character_limits, problems = limits(rows(animate))
    messages = [problem_message(*problem) for problem in problems]
    if character_limits:
        sops = animate.node(animate_points.POINTS_SOPS)
        missing = hide(layer, _dive_prims(sops), character_limits,
                       animate_points._incoming_stage(lop_node), frame)
        if missing:
            messages.append(missing_message(missing))
    messages += animate_segments.problems(animate)
    _report(lop_node, animate, '\n'.join(messages))


def _report(node, animate_node, message: str) -> None:
    """`message` as a warning on `node` (warnings inside an HDA do not
    reach the node around it), and on the status bar once per change."""
    if message:
        add_warning = getattr(node, 'addWarning', None)
        if add_warning is not None:
            add_warning(message)
    path = getattr(animate_node, 'path', None)
    key = path() if callable(path) else id(animate_node)
    if _announced.get(key, '') != message:
        _announced[key] = message
        if message:
            from tumblepipe.pipe.houdini.sops import import_rig
            import_rig.announce_warning(message)
