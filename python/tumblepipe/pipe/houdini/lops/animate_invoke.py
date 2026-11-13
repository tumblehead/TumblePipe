"""th::animate's own Scene Invoke: what `anim_dive` outputs can be the scene.

A dive that ends in its Scene Animate outputs the APEX scene itself (each
character a `<name>.char` packed folder holding its `Base.rig`, shapes and
skeleton). th::animate then invokes it (`points_sops/invoke`), so the artist
never wires or sets up a Scene Invoke: the node's **Characters** list says,
per character, whether its geometry (the shapes matching **Shapes**) and its
skeleton (the rig's `Base.skel` output, for CFX) come out. A character not in
the list gives its geometry and no skeleton.

A dive whose output is already invoked (it has its own Scene Invoke, as every
dive from before this did, perhaps with SOPs after it) is used as it is; the
list then does nothing (`dive_is_scene` tells the two apart).

A skeleton is the rig's posed joints as polylines, one per bone. It goes to
`/<category>/<character>/skel/<rig>` (beside the character's `geo`), as one
BasisCurves prim, with its points only: the joints' other attributes
(transforms, names) would reach USD as primvars frozen at the first frame,
since th::animate copies only P every frame.
"""

import hou

SCENE_SUFFIX = '.char'
RIG_SUFFIX = '.rig'
SKELETON_SUFFIX = '.skel'
SKELETON_KEY = 'Base.skel'
RIG_OUTPUT = 'Base.rig/output'
SKELETON_SCOPE = 'skel'
# The invoke's Extra Outputs are a fixed number of entries, each switched on
# when there is a skeleton for it: a multiparm made longer by an expression
# gets entries without the expressions the others have.
MAX_SKELETONS = 32

MULTIPARM = 'characters'
CHARACTER = 'character'
GEOMETRY = 'geometry'
SKELETON = 'skeleton'


SHAPES = 'shapes'
SHAPE_SUFFIX = '.shp'
OUTPUT_INFIX = '_output_'


# --- which shapes come out ---------------------------------------------------
#
# The node's Shapes parm (default `Base`) is the one rule for which rig
# shapes reach the stage. The node's own Scene Invoke takes it as its Shapes
# pattern; a dive with its own Scene Invoke is held to it after the fact
# (`animate_points.placed_shapes`), so an old dive whose invoke keeps
# Houdini's default `*` does not send the rig's joint and spline viz (which
# can be spheres, not points) to /Geometry.


def shape_stem(name: str) -> str:
    """The rig shape a Scene Invoke output is: `Base` for `Base.shp` and
    for `<rig>_output_Base.shp`."""
    stem = name[:-len(SHAPE_SUFFIX)] if name.endswith(SHAPE_SUFFIX) else name
    return stem.split(OUTPUT_INFIX, 1)[1] if OUTPUT_INFIX in stem else stem


def pattern_picks(pattern: str, stem: str) -> bool:
    """Houdini's name pattern, as a Scene Invoke's Shapes reads it:
    space-separated globs, a `^glob` takes away what it matches, later
    entries win."""
    from fnmatch import fnmatchcase
    picked = False
    for token in pattern.split():
        if token.startswith('^'):
            if fnmatchcase(stem, token[1:]):
                picked = False
        elif fnmatchcase(stem, token):
            picked = True
    return picked


def unpicked_shapes(names, pattern) -> list[int]:
    """Which of one character's outputs (`names`) the Shapes `pattern` does
    not pick: shapes (`*.shp`) only; a skeleton or anything else stays.
    None for `pattern` (no th::animate to read it from) picks everything."""
    if pattern is None:
        return []
    return [i for i, name in enumerate(names)
            if name.endswith(SHAPE_SUFFIX) and not pattern_picks(pattern, shape_stem(name))]


def shapes_pattern(animate_node) -> str | None:
    """th::animate's Shapes, None when there is no such parm."""
    parm = animate_node.parm(SHAPES) if animate_node is not None and hasattr(animate_node, 'parm') else None
    return parm.evalAsString() if parm is not None else None


def _animate_node(node):
    """The th::animate holding `node` (a node inside its `points_sops`)."""
    while node is not None and not node.type().name().startswith('th::animate'):
        node = node.parent()
    return node


# --- the dive's output ----------------------------------------------------


def _name(prim) -> str:
    attrib = prim.geometry().findPrimAttrib('name')
    return prim.attribValue('name') if attrib is not None else ''


def scene_characters(geo) -> list[str]:
    """The characters of an APEX scene `geo` (its `<name>.char` folders),
    in order; empty when `geo` is not an APEX scene (`is_scene`)."""
    if not is_scene(geo):
        return []
    return [
        name[:-len(SCENE_SUFFIX)] for name in map(_name, geo.prims())
        if name.endswith(SCENE_SUFFIX)
    ]


def is_scene(geo) -> bool:
    """True when `geo` is an APEX scene still to be invoked: packed
    folders, a `<name>.char` one holding a rig. An invoked scene in packed
    folders has the same `.char` folders, but only shapes in them.

    A scene also holds folders that are not characters: once its Scene
    Animate has keys, the clip is a root `animation` folder
    (`/animation/default.clip/...`). Reading those as "invoked already"
    let every keyed scene through un-invoked, and the stage got the rigs'
    control shapes instead of the characters (Remy, tumblepipe__bugs
    2026-11-13); a fresh, unkeyed scene has no such folder."""
    if geo is None:
        return False
    prims = geo.prims()
    if len(prims) == 0:
        return False
    has_rig = False
    for prim in prims:
        if prim.type() != hou.primType.PackedGeometry:
            return False
        if has_rig or not _name(prim).endswith(SCENE_SUFFIX):
            continue
        inner = prim.getEmbeddedGeometry()
        if inner is None or inner.findPrimAttrib('name') is None:
            continue
        has_rig = any(name.endswith(RIG_SUFFIX) for name in inner.primStringAttribValues('name'))
    return has_rig


def dive_is_scene(sop_node) -> int:
    """The `points_sops/scene` Switch: 1 when the dive outputs an APEX scene
    (th::animate invokes it), 0 when it is invoked already."""
    dive = sop_node.node('../dive')
    return int(dive is not None and is_scene(dive.geometry()))


# --- the Characters list ----------------------------------------------------


def rows(animate_node) -> list[tuple[str, bool, bool]]:
    """(character, geometry, skeleton) for each row of the Characters list
    with a character named, in order."""
    count_parm = animate_node.parm(MULTIPARM)
    if count_parm is None:
        return []
    result = []
    for index in range(1, count_parm.eval() + 1):
        name = animate_node.evalParm(f'{CHARACTER}{index}').strip()
        if not name:
            continue
        result.append((
            name,
            bool(animate_node.evalParm(f'{GEOMETRY}{index}')),
            bool(animate_node.evalParm(f'{SKELETON}{index}')),
        ))
    return result


def geometry_pattern(table) -> str:
    """The invoke's Characters pattern: every character but those whose row
    turns Geometry off."""
    off = [name for name, geometry, _skeleton in table if not geometry]
    return ' '.join(['*'] + [f'^{name}' for name in off])


def skeleton_characters(table, characters) -> list[str]:
    """The characters of the scene (`characters`, in scene order) whose row
    turns Skeleton on."""
    wanted = {name for name, _geometry, skeleton in table if skeleton}
    return [name for name in characters if name in wanted][:MAX_SKELETONS]


def skeleton_output_path(character: str) -> str:
    return f'/{character}{SCENE_SUFFIX}/{RIG_OUTPUT}'


def _dive_characters(sop_node) -> list[str]:
    dive = sop_node.node('../dive')
    return scene_characters(dive.geometry()) if dive is not None else []


def _skeletons(sop_node) -> list[str]:
    animate = _animate_node(sop_node)
    if animate is None:
        return []
    return skeleton_characters(rows(animate), _dive_characters(sop_node))


# Expressions on points_sops/invoke and points_sops/skeletons.

def invoke_characters(sop_node) -> str:
    animate = _animate_node(sop_node)
    return geometry_pattern(rows(animate)) if animate is not None else '*'


def skeleton_count(sop_node) -> int:
    return len(_skeletons(sop_node))


def skeleton_enabled(sop_node, index: int) -> int:
    return int(index < len(_skeletons(sop_node)))


def skeleton_path(sop_node, index: int) -> str:
    skeletons = _skeletons(sop_node)
    return skeleton_output_path(skeletons[index]) if index < len(skeletons) else ''


# --- skeleton paths ----------------------------------------------------------


def character_prefix(folder_geo, character: str) -> str:
    """Where a character's prims go: `/<category>/<character>`, read from
    the first path on its shapes (`/<category>/<character>/geo/...`), else
    `/<character>`."""
    if folder_geo is not None and folder_geo.findPrimAttrib('name') is not None:
        for shape in folder_geo.prims():
            if shape.type() != hou.primType.PackedGeometry:
                continue
            if not _name(shape).endswith('.shp'):
                continue
            shape_geo = shape.getEmbeddedGeometry()
            if shape_geo is None or shape_geo.findPrimAttrib('path') is None:
                continue
            for path in shape_geo.primStringAttribValues('path'):
                parts = [part for part in path.split('/') if part]
                if len(parts) >= 2:
                    return f'/{parts[0]}/{parts[1]}'
    return f'/{character}'


def skeleton_prim_path(prefix: str, shape_name: str) -> str:
    """`<prefix>/skel/<rig>` for the invoke's skeleton shape
    `<rig>_output_<rig>.skel` (or `<rig>.skel`)."""
    stem = shape_name[:-len(SKELETON_SUFFIX)] if shape_name.endswith(SKELETON_SUFFIX) else shape_name
    rig = stem.split('_output_', 1)[0] or 'Base'
    return f'{prefix}/{SKELETON_SCOPE}/{rig}'


def _skeleton_geometry(source, path: str):
    """A skeleton with its points and polylines only, every prim on `path`."""
    geo = hou.Geometry()
    geo.merge(source)
    keep = {'P'}
    for attrib in list(geo.pointAttribs()):
        if attrib.name() not in keep:
            attrib.destroy()
    for attribs in (geo.primAttribs(), geo.vertexAttribs(), geo.globalAttribs()):
        for attrib in list(attribs):
            attrib.destroy()
    for group in list(geo.primGroups()) + list(geo.pointGroups()):
        group.destroy()
    geo.addAttrib(hou.attribType.Prim, 'path', '')
    prim_count = len(geo.prims())
    if prim_count:
        geo.setPrimStringAttribValues('path', [path] * prim_count)
    return geo


def skeleton_paths(sop_node) -> None:
    """The `skeleton_paths` Python SOP: give each invoked skeleton its prim
    path (`skeleton_prim_path`) and nothing but its points."""
    geo = sop_node.geometry()
    dive = sop_node.node('../dive')
    scene = dive.geometry() if dive is not None else None
    scene_folders = {}
    if scene is not None and scene.findPrimAttrib('name') is not None:
        for prim in scene.prims():
            scene_folders[_name(prim)] = prim
    for folder in geo.prims():
        if folder.type() != hou.primType.PackedGeometry:
            continue
        folder_name = _name(folder)
        inner = folder.getEmbeddedGeometry()
        if inner is None or inner.findPrimAttrib('name') is None:
            continue
        if not any(name.endswith(SKELETON_SUFFIX) for name in inner.primStringAttribValues('name')):
            continue
        character = folder_name[:-len(SCENE_SUFFIX)] if folder_name.endswith(SCENE_SUFFIX) else folder_name
        scene_folder = scene_folders.get(folder_name)
        prefix = character_prefix(
            scene_folder.getEmbeddedGeometry() if scene_folder is not None else None,
            character,
        )
        inner = inner.freeze()
        for shape in inner.prims():
            name = _name(shape)
            if not name.endswith(SKELETON_SUFFIX):
                continue
            if shape.type() != hou.primType.PackedGeometry:
                continue
            source = shape.getEmbeddedGeometry()
            if source is None:
                continue
            shape.setEmbeddedGeometry(_skeleton_geometry(source, skeleton_prim_path(prefix, name)))
        folder.setEmbeddedGeometry(inner)


# --- parameter callbacks -----------------------------------------------------


def _scene_characters_of(animate_node) -> list[str]:
    dive = animate_node.node('points_sops/dive')
    return scene_characters(dive.geometry()) if dive is not None else []


def character_menu(animate_node) -> list[str]:
    """The Character menu: the scene's characters (token, label pairs)."""
    menu = []
    for name in _scene_characters_of(animate_node):
        menu.extend([name, name])
    return menu


def discover(animate_node) -> int:
    """The Discover button: a row for each of the scene's characters not
    listed yet (Geometry on, Skeleton off). Returns how many were added."""
    listed = {name for name, _g, _s in rows(animate_node)}
    added = 0
    count_parm = animate_node.parm(MULTIPARM)
    for name in _scene_characters_of(animate_node):
        if name in listed:
            continue
        index = count_parm.eval() + 1
        count_parm.set(index)
        animate_node.parm(f'{CHARACTER}{index}').set(name)
        animate_node.parm(f'{GEOMETRY}{index}').set(1)
        animate_node.parm(f'{SKELETON}{index}').set(0)
        added += 1
    return added


# --- a new node's dive -------------------------------------------------------


def build_default_dive(animate_node) -> None:
    """A th::animate placed by hand starts with what every animation needs:
    th::import_rigs -> Scene Animate -> the dive's output. Nothing is
    invoked in the dive; th::animate does that (see the module docstring).
    Workfile templates build their own dive and skip this
    (`nodes.creating_from_code`)."""
    from tumblepipe.pipe.houdini.sops import import_rigs
    dive = animate_node.node('anim_dive')
    if dive is None:
        return
    output = None
    for child in dive.children():
        if child.type().name() == 'output':
            output = child
        else:
            return  # not empty: leave it alone
    rigs = import_rigs.create(dive, 'import_rigs')
    animate = dive.createNode('apex::sceneanimate', 'scene_animate')
    animate.setInput(0, rigs.native())
    if output is not None:
        output.setInput(0, animate)
    dive.layoutChildren()
