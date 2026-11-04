"""th::layout_rigs: what a shot's layout animates on the USD stage.

With Layout in Animate on (``config.layout_animate``) layout animates the
shot in an APEX Animate LOP after this node. It puts every asset of the shot
into the APEX scene, one row each:

- **rig**: an asset that has a rig published loads it (its newest rig
  export, or a pinned version) as a character under ``/layout_rigs/<name>``,
  its shapes only (no viz shapes), started at its layout placement on the
  rig's ``Main`` control. The asset's own prim is hidden while layout works:
  one character on screen, posed by its rig. Its keys go to animation as
  they are.
- **control**: any other asset gets one placement control (Configure APEX
  Rig, Prop) on its own prim. USD keys already on the prim (a layout done
  the old way, with an Edit LOP) become the control's channels.
- **off**: left as it is.

Then the shot camera: the camera rig a package registered
(``shot_rigs.camera_rig().layout``: TumbleRig's ``th::apex_camera``), else
SideFX's camera rig on ``CAMERA_PATH``.

The rigs never reach the published layer. The layout template publishes
from its own branch: the shot as imported, each asset placed from the keys
(:func:`apply_placement`), the camera baked from the scene and grafted to
``CAMERA_PATH``. The keys themselves go beside the layer
(``layout_keys``).

The node rebuilds its ``dive`` (an editable subnet) on **Build**, as
th::import_rigs does; **Discover** fills the rows from its input stage and
keeps the artist's choices for prims it already had.
"""

from __future__ import annotations

import hou

from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.config.channels import CHANNEL_KEY
from tumblepipe.pipe.houdini import prim_metadata, shot_rigs
from tumblepipe.pipe.houdini.sops import import_rig, import_rigs

#: Where the rig characters are loaded on the layout stage.
RIGS_ROOT = '/layout_rigs'
#: Where a registered camera rig is put on the stage.
CAMERA_RIG_PATH = RIGS_ROOT + '/camera'

MODES = ('rig', 'control', 'off')

#: The rig's shapes layout shows: every shape but the viz ones (a TumbleRig
#: rig's jointViz would land on the stage as a mesh).
SHAPE_PATTERN = '*.shp ^*Viz.shp ^*viz.shp'

_ROW_PARMS = ('prim', 'entity', 'mode', 'channel', 'version')


def character_name(prim_path: str) -> str:
    """The character an asset prim is in the APEX scene: its prim name
    (``/CHAR/hero0`` -> ``hero0``), as th::import_rig names the copies."""
    return prim_path.rstrip('/').rsplit('/', 1)[-1]


def discover(stage) -> list[dict]:
    """A row per asset prim on ``stage``: ``rig`` when the asset has a rig
    published (in its channel or another), else ``control``."""
    if stage is None:
        return []
    rows = []
    for info in prim_metadata.list_assets(stage.GetPseudoRoot()):
        metadata = info['metadata']
        uri = Uri.parse(metadata['uri'])
        channel = metadata.get(CHANNEL_KEY, 'default')
        rig_channel = import_rigs.rig_channel(uri, channel)
        rows.append({
            'prim': info['prim_path'],
            'entity': str(uri),
            'mode': 'rig' if rig_channel is not None else 'control',
            'channel': rig_channel or channel,
            'version': 'latest',
        })
    return sorted(rows, key=lambda row: row['prim'])


def keep_choices(rows: list[dict], current: list[dict]) -> list[dict]:
    """Discovered ``rows`` with the mode and version kept for every prim
    ``current`` already had (a rig mode only while the asset still has a
    rig)."""
    before = {row['prim']: row for row in current}
    kept = []
    for row in rows:
        old = before.get(row['prim'])
        if old is not None:
            row = dict(row)
            if old['mode'] != 'rig' or row['mode'] == 'rig':
                row['mode'] = old['mode']
            row['version'] = old['version']
        kept.append(row)
    return kept


def shot_start_placement(stage, prim_path: str, frame: float):
    """(translate, rotate, scale) of ``prim_path``'s world transform at
    ``frame``; None without the prim or at the identity."""
    from pxr import Usd, UsdGeom

    prim = stage.GetPrimAtPath(prim_path) if stage is not None else None
    if prim is None or not prim.IsValid() or not prim.IsA(UsdGeom.Xformable):
        return None
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(frame))
    matrix = hou.Matrix4([list(matrix.GetRow(row)) for row in range(4)])
    if matrix.isAlmostEqual(hou.hmath.identityTransform()):
        return None
    parts = matrix.explode(transform_order='srt', rotate_order='xyz')
    return tuple(parts['translate']), tuple(parts['rotate']), tuple(parts['scale'])


_PLACE_SNIPPET = '''from tumblepipe.pipe.houdini.sops import import_rig
# Built by th::layout_rigs: the rig starts at {prim}'s layout placement.
import_rig._place_rigs(hou.pwd().geometry(), {t!r}, {r!r}, {s!r})
'''


def _add_rig(dive, sops, previous, row, placement):
    """Load ``row``'s rig as a character under RIGS_ROOT after ``previous``;
    the new last LOP."""
    name = character_name(row['prim'])
    uri = Uri.parse(row['entity'])
    path = import_rig.rig_file_path(uri, row['channel'], row['version'])
    if path is None or not path.exists():
        version = path.parent.name if path is not None else None
        if version is not None and import_rig.is_lop_rig(uri, row['channel'], version):
            raise hou.NodeError(
                f"{row['prim']}: "
                + import_rig.lop_rig_message(uri, row['channel'], version)
            )
        raise hou.NodeError(f"{row['prim']}: no rig published for {uri} in '{row['channel']}'")
    load = sops.createNode('file', f'rig_{name}')
    load.parm('file').set(str(path).replace('\\', '/'))
    last = load
    if placement is not None:
        place = sops.createNode('python', f'place_{name}')
        t, r, s = placement
        place.parm('python').set(_PLACE_SNIPPET.format(prim=row['prim'], t=t, r=r, s=s))
        place.setInput(0, load)
        last = place
    out = sops.createNode('null', f'OUT_{name}')
    out.setInput(0, last)
    imp = dive.createNode('sopimportapexscene', f'char_{name}')
    imp.setInput(0, previous)
    imp.parm('importmode').set('char')
    imp.parm('soppath').set(out.path())
    imp.parm('pathprefix').set(f'{RIGS_ROOT}/{name}')
    imp.parm('shapepattern').set(SHAPE_PATTERN)
    return imp


def _add_control(dive, previous, row, frame_range):
    """A placement control on ``row``'s own prim; its USD keys become the
    control's channels. The new last LOP."""
    name = character_name(row['prim'])
    rig = dive.createNode('configureapexrig', f'control_{name}')
    rig.setInput(0, previous)
    rig.parm('rigtype').set('prop')
    rig.parm('primpattern').set(row['prim'])
    rig.parm('rootprim').set(row['prim'])
    rig.parm('enableanim').set(1)
    if frame_range is not None:
        rig.parmTuple('framerange').set(frame_range)
    return rig


def _hide(dive, previous, prims):
    """Hide the asset prims a rig stands in for while layout works."""
    if not prims:
        return previous
    hide = dive.createNode('configureprimitive', 'hide_rigged_assets')
    hide.setInput(0, previous)
    hide.parm('primpattern').set(' '.join(prims))
    hide.parm('setvisibility').set(1)
    hide.parm('visibility').set('invisible')
    return hide


def _add_camera(dive, previous, shot_uri):
    """The shot camera's rig: the registered package's, else SideFX's on
    CAMERA_PATH. A package hook that fails or adds nothing (its rig not
    installed) falls back to SideFX's, so layout never depends on another
    package. The new last LOP."""
    rig = shot_rigs.camera_rig()
    if rig is not None and rig.layout is not None:
        before = set(dive.children())
        try:
            last = rig.layout(previous, shot_uri, CAMERA_RIG_PATH)
        except Exception:
            import traceback
            traceback.print_exc()
            last = None
        if last is not None:
            return last
        for node in set(dive.children()) - before:
            node.destroy()
    camera = dive.createNode('configureapexrig', 'camera_rig')
    camera.setInput(0, previous)
    camera.parm('rigtype').set('camera')
    camera.parm('primpattern').set(shot_rigs.CAMERA_PATH)
    return camera


def dive_output(dive):
    """The dive's Output LOP with Output Index 0, whatever the artist named
    it (the one Houdini reads the subnet's result from); a new ``output0``
    when the dive has none."""
    for child in dive.children():
        if child.type().name() != 'output': continue
        index = child.parm('outputidx')
        if index is None or index.eval() == 0:
            return child
    return dive.createNode('output', 'output0')


def build(dive, rows: list[dict], *, stage=None, shot_uri=None,
          start_frame=None, frame_range=None):
    """Rebuild ``dive`` (the node's editable LOP subnet) for ``rows``:
    controls, rigs, the hide, the camera, wired from the subnet's input to
    its output (:func:`dive_output`). ``stage`` (the node's input) and
    ``start_frame`` place each rig at its layout placement."""
    output = dive_output(dive)
    ns.clear_network(dive, output)
    previous = dive.indirectInputs()[0]
    sops = None
    hidden = []
    for row in rows:
        if row['mode'] == 'control':
            previous = _add_control(dive, previous, row, frame_range)
    for row in rows:
        if row['mode'] != 'rig':
            continue
        if sops is None:
            sops = dive.createNode('sopnet', 'rigs')
        placement = (
            shot_start_placement(stage, row['prim'], start_frame)
            if stage is not None and start_frame is not None else None
        )
        previous = _add_rig(dive, sops, previous, row, placement)
        hidden.append(row['prim'])
    previous = _hide(dive, previous, hidden)
    previous = _add_camera(dive, previous, shot_uri)
    output.setInput(0, previous)
    if sops is not None:
        sops.layoutChildren()
    dive.layoutChildren()


def apply_placement(stage, animate_lop, rows: list[dict], frames) -> int:
    """Author each asset's move from layout's keys on its own prim, over
    ``frames`` (the publish branch: no rigs, no controls, plain USD): a rig
    row from its ``Main`` channels, a control row from its own control's.
    Returns how many prims were placed. Prims without keys keep the
    transform they arrive with."""
    from pxr import Gf, Usd, UsdGeom
    from tumblepipe.pipe.houdini import layout_keys

    scene = layout_keys.read_scene(animate_lop)
    if scene is None:
        return 0
    entries = layout_keys.character_entries(scene)
    placed = 0
    for row in rows:
        if row['mode'] == 'off':
            continue
        name = character_name(row['prim'])
        if name not in entries:
            continue
        entry = entries[name][1]
        control = layout_keys.PLACEMENT_CONTROL if row['mode'] == 'rig' else name
        if layout_keys.placement(entry, frames[0], control=control) is None:
            continue
        prim = stage.GetPrimAtPath(row['prim'])
        if not prim.IsValid():
            continue
        xformable = UsdGeom.Xformable(prim)
        xformable.ClearXformOpOrder()
        api = UsdGeom.XformCommonAPI(prim)
        for frame in frames:
            t, r, s = layout_keys.placement(entry, frame, control=control)
            time = Usd.TimeCode(frame)
            api.SetTranslate(Gf.Vec3d(*t), time)
            api.SetRotate(Gf.Vec3f(*r), UsdGeom.XformCommonAPI.RotationOrderXYZ, time)
            api.SetScale(Gf.Vec3f(*s), time)
        placed += 1
    return placed


class LayoutRigs(ns.Node):
    """th::layout_rigs: the rows, Discover and Build."""

    def __init__(self, native):
        super().__init__(native)

    def get_rows(self) -> list[dict]:
        count = self.parm('rows').eval()
        return [
            {key: self.parm(f'{key}{i}').evalAsString() for key in _ROW_PARMS}
            for i in range(1, count + 1)
        ]

    def set_rows(self, rows: list[dict]) -> None:
        self.parm('rows').set(len(rows))
        for i, row in enumerate(rows, start=1):
            for key in _ROW_PARMS:
                self.parm(f'{key}{i}').set(row[key])

    def list_version_names(self, index: int) -> list[str]:
        """The Rig Version menu of row ``index``: 'latest' and the asset's
        published rig versions in the row's channel."""
        from tumblepipe.api import api
        from tumblepipe.pipe.paths import list_version_paths

        entity = self.parm(f'entity{index}').evalAsString()
        if not entity:
            return ['latest']
        uri = Uri.parse(entity)
        channel = self.parm(f'channel{index}').evalAsString() or 'default'
        export_uri = Uri.parse('export:/') / uri.segments / channel / 'rig'
        return ['latest'] + [p.name for p in list_version_paths(api.storage.resolve(export_uri))]

    def input_stage(self):
        inputs = self.native().inputs()
        return inputs[0].stage() if inputs and inputs[0] is not None else None

    def discover(self) -> None:
        self.set_rows(keep_choices(discover(self.input_stage()), self.get_rows()))

    def execute(self) -> None:
        """Build again: what the workfile-open refresh runs
        (``scene_imports``), so a 'latest' row loads the newest rig."""
        self.build()

    def build(self) -> None:
        from tumblepipe.pipe.houdini import shot_start
        from tumblepipe.pipe.houdini.lops.import_shot import ImportShot

        native = self.native()
        shot = shot_start._import_shot_above(native)
        shot_uri = shot.get_entity_uri() if isinstance(shot, ImportShot) else None
        start = hou.playbar.playbackRange()[0]
        if shot is not None:
            member = shot_start.member_start(shot)
            start = start if member is None else member
        frame_range = hou.playbar.playbackRange()
        build(native.node('dive'), self.get_rows(), stage=self.input_stage(),
              shot_uri=shot_uri, start_frame=start, frame_range=frame_range)


def create(scene, name):
    return ns.create_node(scene, name, LayoutRigs, 'layout_rigs')


def discover_rows(kwargs):
    LayoutRigs(kwargs['node']).discover()


def build_rigs(kwargs):
    LayoutRigs(kwargs['node']).build()


# -- the layout workfile's network ------------------------------------------------

_PLACEMENT_SNIPPET = '''from tumblepipe.pipe.houdini.lops import layout_rigs
# Built by the layout template: the shot's assets placed from layout's keys.
layout_rigs.publish_placement(hou.pwd(), {rigs!r}, {animate!r})
'''

_CAMERA_SOURCE = (
    "__import__('tumblepipe.pipe.houdini.lops.layout_rigs', "
    "fromlist=['baked_camera_path']).baked_camera_path(hou.pwd().inputs()[1])"
)


def publish_frames(node) -> list[float]:
    """The frames the publish branch places assets on: the shot's, rolls
    included, where the workfile has them (a Multi's offset); the playbar's
    without an Import Shot above ``node``."""
    from tumblepipe.config.timeline import get_frame_range
    from tumblepipe.pipe.houdini import shot_start

    shot = shot_start._import_shot_above(node)
    if shot is not None and shot.get_entity_uri() is not None:
        frame_range = get_frame_range(shot.get_entity_uri())
        if frame_range is not None:
            full = frame_range.full_range()
            offset = shot.get_time_offset()
            return [float(f + offset) for f in range(full.first_frame, full.last_frame + 1)]
    first, last = hou.playbar.frameRange()
    return [float(f) for f in range(int(first), int(last) + 1)]


def publish_placement(node, rigs_path: str, animate_path: str) -> int:
    """The publish branch's Python LOP: place every asset from layout's keys
    (:func:`apply_placement`) on ``node``'s stage."""
    rigs, animate = hou.node(rigs_path), hou.node(animate_path)
    if rigs is None or animate is None:
        return 0
    return apply_placement(
        node.editableStage(), animate, LayoutRigs(rigs).get_rows(), publish_frames(node)
    )


def baked_camera_path(bake_node) -> str:
    """The camera the publish grafts to CAMERA_PATH, on the baked stage: the
    camera rig's under CAMERA_RIG_PATH (a registered rig), else CAMERA_PATH
    itself (SideFX's rig on the shot camera)."""
    from pxr import Usd, UsdGeom

    stage = bake_node.stage() if bake_node is not None else None
    root = stage.GetPrimAtPath(CAMERA_RIG_PATH) if stage is not None else None
    if root is not None and root.IsValid():
        # Usd.PrimRange: Houdini 22's USD has no Prim.GetAllDescendants
        for prim in Usd.PrimRange(root):
            if prim.IsA(UsdGeom.Camera):
                return str(prim.GetPath())
    return shot_rigs.CAMERA_PATH


#: A camera's lens attributes in millimetres (Houdini's Camera LOP).
LENS_ATTRS = ('focalLength', 'horizontalAperture', 'verticalAperture',
              'horizontalApertureOffset', 'verticalApertureOffset')


def publish_lens(node) -> bool:
    """The publish branch's last Python LOP: a camera rig from SOPs reaches
    the stage with its lens in tenths of a scene unit (Houdini's SOP to USD
    camera conversion: 35 mm becomes focalLength 0.35), while every other
    department reads CAMERA_PATH in millimetres as Houdini's Camera LOP
    writes it. Scales focal length and apertures back by 100 when the
    grafted camera's focal length is under 1 (no real lens is); the field of
    view is the same either way, depth of field and the lens readouts are
    not. Returns whether it scaled."""
    from pxr import Usd, UsdGeom

    stage = node.editableStage()
    prim = stage.GetPrimAtPath(shot_rigs.CAMERA_PATH)
    if not prim or not prim.IsA(UsdGeom.Camera):
        return False
    focal = prim.GetAttribute('focalLength')
    times = focal.GetTimeSamples() if focal else []
    first = focal.Get(times[0] if times else Usd.TimeCode.Default()) if focal else None
    if first is None or first >= 1.0:
        return False
    for name in LENS_ATTRS:
        attr = prim.GetAttribute(name)
        if not attr or not attr.HasAuthoredValue():
            continue
        times = attr.GetTimeSamples()
        for time in times or [Usd.TimeCode.Default()]:
            value = attr.Get(time)
            if value is not None:
                attr.Set(value * 100.0, time)
    return True


_LENS_SNIPPET = '''from tumblepipe.pipe.houdini.lops import layout_rigs
# Built by the layout template: the shot camera's lens in millimetres.
layout_rigs.publish_lens(hou.pwd())
'''


def add_layout_animate(scene_node, camera_node, entity_uri, suffix: str = ''):
    """The Layout in Animate network after the layout template's shot camera
    ``camera_node``; returns the node the export reads.

    Layout: th::layout_rigs (discovered, built) -> APEX Animate, displayed.
    Publish: the shot as it arrives at ``camera_node`` -> each asset placed
    from the keys (a Python LOP) -> the camera baked from the scene grafted
    to CAMERA_PATH -> its lens in millimetres (:func:`publish_lens`). The
    export finds the APEX Animate up its inputs and writes the keys beside
    the layer."""
    rigs = create(scene_node, f'layout_rigs{suffix}')
    rigs.native().setInput(0, camera_node)
    rigs.discover()
    rigs.build()
    animate = scene_node.createNode('apexanimate', f'layout_animate{suffix}')
    animate.setInput(0, rigs.native())
    animate.setDisplayFlag(True)
    bake = scene_node.createNode('bakeapexscene', f'layout_bake{suffix}')
    bake.setInput(0, animate)
    bake.parm('sample_behavior').set('multiple')
    for parm, expression in (('sample_f1', '$FSTART'), ('sample_f2', '$FEND')):
        bake.parm(parm).setExpression(expression)
    placement = scene_node.createNode('pythonscript', f'layout_placement{suffix}')
    placement.setInput(0, camera_node)
    placement.parm('python').set(_PLACEMENT_SNIPPET.format(
        rigs=rigs.native().path(), animate=animate.path()))
    graft = scene_node.createNode('graftbranches', f'layout_camera{suffix}')
    graft.setInput(0, placement)
    graft.setInput(1, bake)
    graft.parm('destasparent').set(0)
    graft.parm('srcprimpath1').setExpression(_CAMERA_SOURCE, hou.exprLanguage.Python)
    graft.parm('dstprimpath1').set(shot_rigs.CAMERA_PATH)
    lens = scene_node.createNode('pythonscript', f'layout_camera_lens{suffix}')
    lens.setInput(0, graft)
    lens.parm('python').set(_LENS_SNIPPET)
    return lens
