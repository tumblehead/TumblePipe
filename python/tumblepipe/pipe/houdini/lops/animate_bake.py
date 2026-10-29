"""th::animate's Fast mode: the rigs run by Solaris' APEX bake.

Classic mode imports what the dive's APEX Scene Invoke outputs, with a SOP
Import, every frame. The rigs run in SOPs and then every point they output is
converted to USD, which is most of the cost.

Fast mode reads the APEX scene itself, from the dive's second output (before
the Scene Invoke), and lets Houdini 22's Bake APEX Scene LOP run the rigs:

- `apex_sops/classify` picks the characters whose Base shape is a TumblePipe
  mesh: polygons, every one with a `path` under one asset prim.
- `fast_rest` imports those Base shapes once, static: the same part meshes
  Classic mode makes.
- On a side branch, `fast_proxy` imports each character's Base shape once
  more, merged into one mesh, because an APEX shape binds to exactly one USD
  mesh. `fast_configure` binds the proxies to the scene's characters, as
  outputs only, and records which proxy points make up which part.
  `fast_bake` runs the rigs on their own Base shapes, as the Scene Invoke
  does, and writes the deformed points onto the proxies.
- `fast_apply` copies the baked points, normals and extents onto the parts.
- `fast_remainder` invokes every other character the classic way, an APEX
  camera say. With none, it costs nothing.
- `mode_switch` picks the classic `import` or `fast_remainder` by the Mode
  parm.

The proxies and the APEX scene prim stay on the side branch, so the output has
the prims and attributes Classic mode makes; only the points are now time
samples over a static topology.

Fast mode outputs each character's Base shape, like the animation template's
Scene Invoke. SOPs placed after the dive's Scene Invoke are not seen by it.
"""

import hou
import numpy as np
from pxr import Sdf, UsdGeom, Vt

MODE_CLASSIC = 0
MODE_FAST = 1

SHAPE = 'Base'
SCENE_OUTPUT = 1
SCENE_PRIM = '/scene'
PROXY_NAME = '__th_bake_proxy'

ATTR_SHAPES = 'th_bake_shapes'
ATTR_REMAINDER = 'th_bake_remainder'
ATTR_PART = '__th_part'
ATTR_PROXY = '__th_proxy'
ATTR_SHARED = '__th_shared'
ATTR_PARTS = 'th:bake:parts'
ATTR_COUNTS = 'th:bake:counts'
ATTR_POINTS = 'th:bake:points'


class BakeError(Exception):
    pass


def asset_prim_path(part_paths) -> str | None:
    """The asset prim (`/<category>/<asset>`) every part path is under, or
    None when they are not all under one, or a path is not that deep."""
    asset_path = None
    for part_path in part_paths:
        segments = part_path.split('/')
        if len(segments) < 4 or segments[0] != '' or '' in segments[1:]:
            return None
        part_asset_path = '/' + segments[1] + '/' + segments[2]
        if asset_path is None:
            asset_path = part_asset_path
        elif part_asset_path != asset_path:
            return None
    return asset_path


def scene_file_name(node_path: str) -> str:
    """The name `fast_configure` locks the APEX scene under: the node's own
    path, so the layer it becomes is absolute and no other node's."""
    return node_path + '_apex_scene'


def patterns(characters) -> tuple[str, str]:
    """The packed folder pattern of the Base shapes Fast mode bakes, and the
    character pattern of the Scene Invoke that runs all the others.

    `characters` is (name, bakeable) per character in the scene.
    """
    baked = [name for name, bakeable in characters if bakeable]
    shapes = ' '.join(f'/{name}.char/{SHAPE}.shp' for name in baked)
    remainder = ' '.join(['*'] + [f'^{name}' for name in baked])
    return shapes, remainder


def point_map(proxy_of_point, part_of_point) -> dict:
    """Per proxy: its parts, their point counts, and the proxy point numbers
    of every part, part after part (None when that is just 0..n-1).

    Point i of the unpacked Base shapes lands in proxy `proxy_of_point[i]`
    and part `part_of_point[i]` (any ids; the SOPs use a prim number). A SOP
    Import keeps point order when it splits geometry by path, so a proxy's
    points are its unpacked points in ascending order, and so are a part's.
    Parts are listed by their first point, so a rig whose parts each hold a
    run of points needs no map: each part is a slice of the proxy.
    """
    proxy_of_point = np.asarray(proxy_of_point)
    part_of_point = np.asarray(part_of_point)
    result = {}
    for proxy in np.unique(proxy_of_point):
        members = np.flatnonzero(proxy_of_point == proxy)
        parts, part_index = np.unique(
            part_of_point[members], return_inverse=True
        )
        part_index = part_index.reshape(-1)
        # Renumber the parts by first point, so a part in run order sorts first.
        _, first = np.unique(part_index, return_index=True)
        rank = np.empty(len(parts), dtype=np.int64)
        rank[np.argsort(first, kind='stable')] = np.arange(len(parts))
        part_index = rank[part_index]
        parts = parts[np.argsort(rank)]
        counts = np.bincount(part_index, minlength=len(parts))
        points = np.argsort(part_index, kind='stable').astype(np.int32)
        if np.array_equal(points, np.arange(len(points))):
            points = None
        result[proxy.item()] = (
            [part.item() for part in parts],
            counts.astype(np.int32),
            points,
        )
    return result


def _attribute(layer, prim_spec, name, type_name):
    attr = layer.GetAttributeAtPath(prim_spec.path.AppendProperty(name))
    if attr is None:
        attr = Sdf.AttributeSpec(prim_spec, name, type_name)
    return attr


def apply_bake(layer, baked, frame: float) -> None:
    """Author every baked proxy's points, normals and extent at `frame`, from
    the `baked` stage onto its parts, as time samples in `layer`."""
    with Sdf.ChangeBlock():
        for proxy in baked.Traverse():
            parts_attr = proxy.GetAttribute(ATTR_PARTS)
            if not parts_attr or not parts_attr.HasAuthoredValue():
                continue
            part_paths = parts_attr.Get()
            counts = np.asarray(proxy.GetAttribute(ATTR_COUNTS).Get())
            points = proxy.GetAttribute(ATTR_POINTS).Get()
            points = None if points is None or len(points) == 0 else np.asarray(points)
            proxy_mesh = UsdGeom.Mesh(proxy)
            points_attr = proxy_mesh.GetPointsAttr()
            # A scene file the bake cannot read is no error to it: it leaves
            # the proxy unbaked, which would pass for the rest pose.
            if points_attr.GetNumTimeSamples() == 0:
                raise BakeError(
                    f'{proxy.GetPath().GetParentPath()}: Bake APEX Scene did '
                    'not deform it. Check fast_bake inside th::animate, or '
                    'use Classic mode.'
                )
            baked_points = points_attr.Get(frame)
            if baked_points is None:
                continue
            if len(baked_points) != int(counts.sum()):
                raise BakeError(
                    f'{proxy.GetPath().GetParentPath()}: the rig outputs '
                    f'{len(baked_points)} points from a {SHAPE} shape of '
                    f'{int(counts.sum())}. Fast mode cannot bake a rig that '
                    'changes the topology; use Classic mode.'
                )
            baked_normals = proxy_mesh.GetNormalsAttr().Get(frame)
            if baked_normals is not None and len(baked_normals) != len(baked_points):
                baked_normals = None
            if points is not None:
                baked_points = Vt.Vec3fArray.FromNumpy(
                    np.take(np.asarray(baked_points), points, axis=0)
                )
                if baked_normals is not None:
                    baked_normals = Vt.Vec3fArray.FromNumpy(
                        np.take(np.asarray(baked_normals), points, axis=0)
                    )
            start = 0
            for part_path, count in zip(part_paths, counts.tolist()):
                end = start + count
                spec = layer.GetPrimAtPath(part_path)
                if spec is None:
                    spec = Sdf.CreatePrimInLayer(layer, part_path)
                posed = baked_points[start:end]
                layer.SetTimeSample(
                    _attribute(layer, spec, 'points', Sdf.ValueTypeNames.Point3fArray).path,
                    frame, posed,
                )
                layer.SetTimeSample(
                    _attribute(layer, spec, 'extent', Sdf.ValueTypeNames.Float3Array).path,
                    frame, UsdGeom.PointBased.ComputeExtent(posed),
                )
                if baked_normals is not None:
                    layer.SetTimeSample(
                        _attribute(layer, spec, 'normals', Sdf.ValueTypeNames.Normal3fArray).path,
                        frame, baked_normals[start:end],
                    )
                start = end


# Houdini side: the nodes inside th::animate call these.


def _shape_geometry(folder_prim):
    """The Base shape in a character folder, or None."""
    if not isinstance(folder_prim, hou.PackedGeometry):
        return None
    folder = folder_prim.getEmbeddedGeometry()
    if folder is None or folder.findPrimAttrib('name') is None:
        return None
    for prim in folder.prims():
        if prim.attribValue('name') != f'{SHAPE}.shp':
            continue
        if not isinstance(prim, hou.PackedGeometry):
            return None
        return prim.getEmbeddedGeometry()
    return None


def _bakeable(shape) -> bool:
    if shape is None or shape.findPrimAttrib('path') is None:
        return False
    prim_count = shape.intrinsicValue('primitivecount')
    if prim_count == 0:
        return False
    if shape.countPrimType(hou.primType.Polygon) != prim_count:
        return False
    return asset_prim_path(set(shape.primStringAttribValues('path'))) is not None


def classify(sop_node) -> None:
    """Python SOP `apex_sops/classify`: on the APEX scene, record which
    Base shapes Fast mode bakes and which characters the remainder runs."""
    geo = sop_node.geometry()
    characters = []
    if geo.findPrimAttrib('name') is not None:
        for prim in geo.prims():
            name = prim.attribValue('name')
            if not name.endswith('.char'):
                continue
            characters.append((
                name[:-len('.char')],
                _bakeable(_shape_geometry(prim)),
            ))
    shapes, remainder = patterns(characters)
    geo.addAttrib(hou.attribType.Global, ATTR_SHAPES, '')
    geo.setGlobalAttribValue(ATTR_SHAPES, shapes)
    geo.addAttrib(hou.attribType.Global, ATTR_REMAINDER, '')
    geo.setGlobalAttribValue(ATTR_REMAINDER, remainder)


def _point_ints(geo, name):
    return np.frombuffer(
        geo.pointIntAttribValuesAsString(name, int_type=hou.numericData.Int32),
        dtype=np.int32,
    )


def configure(lop_node) -> None:
    """Python LOP `fast_configure`, on the proxies: the APEX scene prim, its
    character and shape bindings, and each proxy's point map."""
    from husd import UsdHoudini

    sops = lop_node.node('../apex_sops')
    shapes = sops.node('SHAPES').geometry()
    if len(shapes.prims()) == 0:
        return
    parts = sops.node('POINT_PARTS').geometry()
    shared = _point_ints(parts, ATTR_SHARED)
    if shared.any():
        point = parts.point(int(np.flatnonzero(shared)[0]))
        raise hou.NodeError(
            f'{int(shared.sum())} points of the {SHAPE} shapes are shared '
            f'between parts, e.g. point {point.number()} of '
            f'{point.prims()[0].attribValue("path")}. Fast mode needs every '
            'part to have points of its own; use Classic mode.'
        )
    stage = lop_node.editableStage()
    scene = UsdHoudini.HoudiniApexScene.Define(stage, SCENE_PRIM)
    # The name becomes the layer identifier, op:<name>.sop. With a bare name
    # the Houdini GUI recompiled the rigs on every frame, 12 times slower.
    scene_file = lop_node.addLockedGeometry(
        scene_file_name(lop_node.path()), sops.node('SCENE_FILE').geometry(), {}
    )
    scene.GetSceneFilesAttr().Set([scene_file])
    scene.GetInheritAnimationLayersAttr().Set([False])
    for packed in shapes.prims():
        character_name = packed.attribValue('__apexpath').split('/')[1][:-len('.char')]
        shape = packed.getEmbeddedGeometry()
        asset_path = Sdf.Path(asset_prim_path(set(shape.primStringAttribValues('path'))))
        asset_prim = stage.GetPrimAtPath(asset_path)
        UsdHoudini.HoudiniApexCharacterAPI.Apply(asset_prim)
        binding = UsdHoudini.HoudiniApexShapeBindingAPI.Apply(asset_prim, SHAPE)
        binding.GetBindingRel().SetTargets([asset_path.AppendChild(PROXY_NAME)])
        # No input: the rig deforms its own Base shape, from the scene file,
        # and the proxy only takes the result. With the proxy as input the
        # rig would see it converted from USD, capture weights and all, and
        # its points would differ from Classic mode's.
        binding.GetInputAttr().Set('')
        binding.GetOutputAttr().Set(f'{SHAPE}.shp')
        character = UsdHoudini.HoudiniApexCharacterBindingAPI.Apply(
            scene.GetPrim(), character_name
        )
        character.GetBindingRel().SetTargets([asset_path])
    # The SOPs name each point's part and character by the number of the
    # first prim with its path / __apexpath: ints sort far faster than paths.
    maps = point_map(
        _point_ints(parts, ATTR_PROXY),
        _point_ints(parts, ATTR_PART),
    )
    for proxy_id, (part_ids, counts, points) in maps.items():
        if proxy_id < 0:
            continue
        part_paths = [parts.prim(part_id).attribValue('path') for part_id in part_ids]
        proxy = stage.GetPrimAtPath(
            Sdf.Path(asset_prim_path(part_paths)).AppendChild(PROXY_NAME)
        )
        if not proxy:
            continue
        proxy.CreateAttribute(ATTR_PARTS, Sdf.ValueTypeNames.StringArray, custom=True).Set(part_paths)
        proxy.CreateAttribute(ATTR_COUNTS, Sdf.ValueTypeNames.IntArray, custom=True).Set(
            Vt.IntArray.FromNumpy(counts)
        )
        proxy.CreateAttribute(ATTR_POINTS, Sdf.ValueTypeNames.IntArray, custom=True).Set(
            Vt.IntArray() if points is None else Vt.IntArray.FromNumpy(points)
        )


def apply(lop_node) -> None:
    """Python LOP `fast_apply`: the baked proxies onto the parts.

    A Python Script LOP has one input, so the bake comes in through the
    cook-dependent spare input `spare_input0`, which recooks this node when
    the bake does. Authoring with Sdf into the node's layer, rather than
    through the stage, halves the cost.
    """
    baked_node = lop_node.parm('spare_input0').evalAsNode()
    if baked_node is None:
        return
    baked = baked_node.stage()
    # A spare input does not pass its errors on, so a failed bake would
    # output the rest pose without a word. It shows as no stage; asking the
    # bake for its errors on every cook instead stops playback.
    if baked is None:
        raise hou.NodeError(
            '\n'.join(baked_node.errors()) or 'Bake APEX Scene failed.'
        )
    try:
        apply_bake(lop_node.editableLayer(), baked, hou.frame())
    except BakeError as error:
        raise hou.NodeError(str(error))


def _outputs(dive_node) -> dict:
    return {
        child.parm('outputidx').eval(): child
        for child in dive_node.children()
        if child.type().name() == 'output'
    }


def ensure_scene_output(animate_node) -> bool:
    """Wire anim_dive's second output to the APEX scene, unless it has one.

    The animation template wires `output1` when it builds the dive. A dive
    from before Fast mode gets it from the APEX Scene Invoke feeding
    `output0`. False when there is no such Scene Invoke to read it from.
    """
    dive_node = animate_node.node('anim_dive')
    outputs = _outputs(dive_node)
    if SCENE_OUTPUT in outputs:
        return True
    main_output = outputs.get(0)
    invoke_node = None if main_output is None else main_output.input(0)
    if invoke_node is None or not invoke_node.type().name().startswith('apex::sceneinvoke'):
        return False
    connections = invoke_node.inputConnections()
    if len(connections) == 0:
        return False
    scene_output = dive_node.createNode('output', f'output{SCENE_OUTPUT}')
    scene_output.parm('outputidx').set(SCENE_OUTPUT)
    scene_output.setInput(0, connections[0].inputNode(), connections[0].outputIndex())
    scene_output.moveToGoodPosition()
    return True


def on_mode_changed(animate_node) -> None:
    """The Mode parm's callback."""
    if animate_node.evalParm('mode') != MODE_FAST:
        return
    if ensure_scene_output(animate_node):
        return
    raise hou.OperationFailed(
        'Fast mode reads the APEX scene from a second output in anim_dive, '
        'and found no APEX Scene Invoke feeding output0 to take it from. '
        'Add an Output SOP with Output Index 1 after the Scene Animate.'
    )
