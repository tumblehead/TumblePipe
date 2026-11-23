"""Audit: a comp of the renders alone never cooks th::import_render's scene.

    hython scripts/verify_import_render_scene_cook.py

Antoine's farm comp (TumblePipe 1.122.0, 2026-11-23) segfaulted on its
first frame in ``GusdGU_USD::AppendExpandedPackedPrimsFromLopNode``: a SOP
unpacking the shot's staged stage, cooked from a COP's metadata cook under
the export. The comp read only images. Measured here (1.124.0): after an
Update, an export_comp wired to the beauty alone cooked the embedded
import_scene's LOP Import and Unpack USD on every frame, because
Copernicus cooks every output of a node together and the scene outputs
were open (``scene_ready`` / ``camera_ready``, saved with the workfile);
with only the camera open the geometry still cooked, since the embedded
import_scene cooks its own outputs together. In a throwaway project
(scripts/verify_comp.py's staging, plus a staged USD shot with a cube and
the render camera):

- **Nothing reads the scene.** Update on an import with nothing wired to
  geometry or camera imports no scene and leaves both outputs closed;
  rendering an export_comp of the beauty cooks no scene node.
- **A workfile saved with the scene open** (as 1.124.0 left it): the
  farm's ``shut_unread_scenes`` closes it, and the export's frame then
  cooks no scene node.
- **The farm reads through the export.** A Rasterize Geometry wired to
  geometry but not feeding the export: ``scene_outputs_read`` with the
  export finds nothing; one feeding it finds geometry and camera.
- **A camera alone** (wired, geometry not): Update composes the shot
  without payloads, the camera opens, and the export's frame cooks no
  LOP Import or Unpack USD; a Rasterize Geometry on both outputs still
  renders the box at the render width.

Needs Houdini with the comp HDAs compiled and on HOUDINI_OTLSCAN_PATH, and
this checkout's python/ on PYTHONPATH (preflight's hython stage sets both).
Exits 1 on any failure; nothing outside a temp dir is written.
"""

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
sys.argv = [sys.argv[0]]
import verify_comp as vc  # noqa: E402  (its throwaway project and staging)

import hou  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdRender  # noqa: E402

from tumblepipe.api import path_str  # noqa: E402
from tumblepipe.farm.workers import composite_houdini  # noqa: E402
from tumblepipe.pipe.houdini.cops import export_comp, import_render, import_scene  # noqa: E402

check = vc.check
GEOMETRY_NODES = ('scene/geometry/lopimport', 'scene/geometry/unpackusd')
SCENE_NODES = GEOMETRY_NODES + ('scene/lopnet/import_shot', 'scene/objnet/camera')


def stage_scene(shot_uri):
    from tumblepipe.pipe.paths.staged import get_staged_file_path
    staged = get_staged_file_path(shot_uri, 'v0001')
    staged.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(staged))
    UsdGeom.Xform.Define(stage, '/props')
    UsdGeom.Cube.Define(stage, '/props/box').CreateSizeAttr(2.0)
    camera = UsdGeom.Camera.Define(stage, '/cameras/render_camera')
    UsdGeom.Xformable(camera).AddTranslateOp().Set((0.0, 0.0, 10.0))
    UsdRender.Settings.Define(stage, '/Render/rendersettings').CreateResolutionAttr(
        Gf.Vec2i(1600, 900))
    stage.GetRootLayer().Save()
    (staged.parent / 'context.json').write_text(json.dumps(dict(
        entity=str(shot_uri), version_name='v0001', channel='default')))
    return staged


def cooks(native, names):
    return {name: native.node(name).cookCount() for name in names}


def render_frame(export, frame):
    """The export's ROP on one frame, as the composite worker runs it;
    whether it wrote the frame."""
    out = vc.TEMP_ROOT / 'probe' / f'{export.name()}.$F4.exr'
    export.parm('render_output').set(path_str(out))
    export.parmTuple('render_range').set((frame, frame))
    hou.setFrame(frame)
    export.node('render').parm('execute').pressButton()
    return Path(str(out).replace('$F4', str(frame))).exists()


def cooked_during(native, names, action):
    before = cooks(native, names)
    result = action()
    after = cooks(native, names)
    return result, [name for name in names if after[name] != before[name]]


def point_at(staged):
    """A real staged file holds its departments as entity:/ sublayers (the
    pipeline's resolver); this one holds its prims inline, so every import
    of the embedded import_shot ends pointing its Sublayer LOP at it, as
    verify_comp does by hand. Returns what it counts: each import."""
    imported = []
    saved = import_scene.ImportScene.execute

    def execute(self):
        imported.append(1)
        saved(self)
        sub = self.embedded().node('import')
        sub.parm('num_files').set(1)
        sub.parm('filepath1').set(path_str(staged))

    import_scene.ImportScene.execute = execute
    return imported


def flags(native):
    return (native.parm(import_render.SCENE_READY_PARM).eval(),
            native.parm(import_render.CAMERA_READY_PARM).eval())


def main():
    shot = vc.add_shot('SH010', ['default'])
    vc.stage_shot(shot)
    staged = stage_scene(shot)
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(vc.FIRST, vc.LAST)
    hou.setFrame(vc.FIRST)
    vc.use_context(shot)
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    render = import_render.create(copnet, 'import_render1')
    native = render.native()
    export = export_comp.create(copnet, 'export_comp1').native()
    export.setInput(0, native, import_render.RGBA_OUTPUT)

    print('--- nothing reads the scene')
    imported = point_at(staged)
    render.execute(scene=True)  # Update
    check('Update with nothing on geometry or camera imports no scene', not imported)
    check('both scene outputs stay closed', flags(native) == (0, 0), str(flags(native)))
    check('Scene Reads says nothing reads them',
          native.parm(import_render.SCENE_LABEL_PARM).eval().startswith(
              import_render.SCENE_UNREAD_LABEL),
          native.parm(import_render.SCENE_LABEL_PARM).eval())
    wrote, cooked = cooked_during(native, SCENE_NODES, lambda: render_frame(export, vc.FIRST))
    check('the export of the beauty writes its frame', wrote)
    check('and cooks no scene node', not cooked, str(cooked))

    print('--- a workfile saved with the scene open (1.124.0)')
    render.import_scene({'geometry', 'camera'})  # as 1.124.0's Update left it
    check('(set up: both outputs open)', flags(native) == (1, 1),
          native.parm(import_render.SCENE_LABEL_PARM).eval())
    _, cooked = cooked_during(native, GEOMETRY_NODES, lambda: render_frame(export, vc.FIRST + 1))
    check('(the cause: open, the export of the beauty unpacks the stage)',
          set(cooked) == set(GEOMETRY_NODES), str(cooked))
    closed = composite_houdini.shut_unread_scenes([export])
    check("the farm's shut_unread_scenes closes it", closed == [native.path()]
          and flags(native) == (0, 0), f'{closed} {flags(native)}')
    wrote, cooked = cooked_during(native, SCENE_NODES, lambda: render_frame(export, vc.FIRST + 2))
    check('the export then writes its frame and cooks no scene node',
          wrote and not cooked, str(cooked))

    print('--- the farm reads through the export')
    raster = copnet.createNode('rasterizegeo', 'raster')
    raster.setInput(0, native, import_render.CAMERA_OUTPUT)
    raster.setInput(1, native, import_render.GEOMETRY_OUTPUT)
    check('wired, geometry and camera are read in the session',
          render.scene_outputs_read() == {'geometry', 'camera'}, str(render.scene_outputs_read()))
    check('a Rasterize Geometry not feeding the export: the export reads neither',
          render.scene_outputs_read([export]) == set(), str(render.scene_outputs_read([export])))
    over = copnet.createNode('blend', 'over')
    over.setInput(0, raster, 0)
    over.setInput(1, native, import_render.RGBA_OUTPUT)
    export.setInput(0, over, 0)
    check('feeding the export: the export reads both',
          render.scene_outputs_read([export]) == {'geometry', 'camera'},
          str(render.scene_outputs_read([export])))
    export.setInput(0, native, import_render.RGBA_OUTPUT)
    over.destroy()
    raster.destroy()

    print('--- a camera alone')
    probe = copnet.createNode('null', 'reads_camera')
    probe.setInput(0, native, import_render.CAMERA_OUTPUT)
    render.execute(scene=True)  # Update
    shot_lop = native.node(f'{import_render.SCENE}/{import_scene.IMPORT_NODE}')
    check('Update composes the shot without payloads, camera open, geometry closed',
          shot_lop.parm('loadpayloads').eval() == 0 and flags(native) == (0, 1),
          f'{shot_lop.parm("loadpayloads").eval()} {flags(native)} '
          f'{native.parm(import_render.SCENE_LABEL_PARM).eval()}')
    _, cooked = cooked_during(native, GEOMETRY_NODES, lambda: render_frame(export, vc.FIRST))
    check('the export of the beauty cooks no LOP Import or Unpack USD', not cooked, str(cooked))
    probe.destroy()
    raster = copnet.createNode('rasterizegeo', 'raster_both')
    raster.setInput(0, native, import_render.CAMERA_OUTPUT)
    raster.setInput(1, native, import_render.GEOMETRY_OUTPUT)
    render.execute(scene=True)
    try:
        raster.cook(force=True)
        errors = list(raster.errors())
    except hou.OperationFailed as error:
        errors = [str(error)]
    layer = None if errors else raster.layer(0)
    drawn = None if layer is None else max(
        layer.computeAverage(c) for c in range(layer.channelCount()))
    check('both read: payloads loaded, a Rasterize Geometry draws the box at the render width',
          shot_lop.parm('loadpayloads').eval() == 1 and layer is not None
          and layer.bufferResolution()[0] == 1600 and drawn > 0.0,
          str(errors or (layer and (layer.bufferResolution(), drawn))))

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
