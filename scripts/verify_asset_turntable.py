"""Audit: a Farm Submit asset render builds and exports th::lookdev_studio (#53).

    hython scripts/verify_asset_turntable.py [--husk]

Farm Submit renders an asset without its lookdev workfile: the collapse job
snapshots the asset's staged build, ``pipe.houdini.turntable_workfile`` builds
a workfile of a Sublayer LOP and a th::lookdev_studio around it, and the same
job exports that studio as the export job exports a lookdev workfile's
(``farm/workers/export_houdini.py``). This stands in an asset snapshot (a
cube at /CHAR/hero/geo/body, in the channel's turntable folder as the
collapse job writes it) and checks:

- the workfile is built, reopens with the studio on the asset and its batch
  render on, and the studio's AOVs (beauty among them) are returned;
- the studio's own Farm tab evaluates to ``pipe.turntable.farm_frames()``
  and its Light Turn Framerange to ``light_turn_range`` (the pure frames
  the submit sends are the HDA's);
- the export worker's hython half exports it into the turntable folder:
  the stage has the turntable camera, the studio's lights and render
  settings with the submitted override, the asset active and turning, and
  no opdef:/oplib: path left (the HDRI and backdrop textures are files
  beside the stage);
- the exported stage sublayers the snapshot by a path that still resolves
  once the turntable folder is copied elsewhere (a render job's data dir);
- with --husk, husk renders a frame of it (needs a Karma licence).

Needs Houdini (hython). Nothing outside a temp dir is written.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
sys.path.insert(0, str(REPO / 'scripts'))
os.environ.setdefault('TH_EVENTS_DIR', tempfile.mkdtemp(prefix='verify-asset-turntable-'))

import hou  # noqa: E402

FAILURES = []


def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what)
    if not ok:
        FAILURES.append(what)


def write_asset(path: Path):
    """A stand-in for ``collapse_asset_references``' output: an asset layer."""
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.Xform.Define(stage, '/CHAR')
    UsdGeom.Xform.Define(stage, '/CHAR/hero')
    UsdGeom.Xform.Define(stage, '/CHAR/hero/geo')
    cube = UsdGeom.Cube.Define(stage, '/CHAR/hero/geo/body')
    cube.CreateSizeAttr(1.0)
    UsdGeom.Xformable(cube).AddTranslateOp().Set((0.0, 0.5, 0.0))
    stage.GetRootLayer().Save()


def asset_paths(layer):
    """Every asset path a layer's attributes hold, defaults and samples."""
    from pxr import Sdf
    found = []

    def visit(spec_path):
        if not spec_path.IsPropertyPath():
            return
        spec = layer.GetAttributeAtPath(spec_path)
        if spec is None:
            return
        values = [spec.default] if spec.HasDefaultValue() else []
        values += [layer.QueryTimeSample(spec_path, t)
                   for t in layer.ListTimeSamplesForPath(spec_path) or ()]
        for value in values:
            if isinstance(value, Sdf.AssetPath):
                found.append(value.path)
            elif isinstance(value, Sdf.AssetPathArray):
                found.extend(v.path for v in value)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def main():
    import verify_lookdev_studio
    verify_lookdev_studio.install_sources()

    from pxr import Usd, UsdGeom, UsdLux, UsdRender
    from tumblepipe.api import path_str
    from tumblepipe.config.timeline import BlockRange
    from tumblepipe.farm.workers import export_houdini
    from tumblepipe.pipe import turntable
    from tumblepipe.pipe.houdini import turntable_workfile
    from tumblepipe.util.io import store_json

    # resolve(): husk refuses to write under an 8.3 short temp path.
    temp = Path(tempfile.mkdtemp(prefix='verify-asset-turntable-')).resolve()
    output = temp / 'collapsed'
    asset_name = turntable.asset_input_name('default')
    (output / asset_name).parent.mkdir(parents=True)
    write_asset(output / asset_name)
    hip = output / turntable.workfile_name('default')

    # The collapse job's hython half: build the workfile...
    aovs = turntable_workfile.build(asset_name, '/CHAR/hero', hip)
    check(isinstance(aovs, list) and 'beauty' in aovs, f'the studio AOVs are returned ({aovs})')

    hou.hipFile.clear(suppress_save_prompt=True)
    hou.hipFile.load(path_str(hip))
    studio = hou.node(f'{turntable.WORKFILE_NETWORK}/{turntable.WORKFILE_NODE}')
    check(studio is not None and studio.type().name().startswith(turntable.NODE_TYPE + '::'),
          'the workfile reopens with the studio where the export looks')
    check(studio.evalParm('primpattern') == '/CHAR/hero', 'the studio frames the asset')
    check(studio.evalParm('batchrender') == 1, 'the batch render switch is on')

    frames = turntable.farm_frames()
    farm = (
        *(int(v) for v in studio.parmTuple('farm_frame_settings').eval()),
        int(studio.evalParm('farm_stepsize')),
    )
    check(farm == tuple(frames), f'the studio Farm tab is farm_frames() ({farm} == {tuple(frames)})')
    asset_turn = (studio.evalParm('asset_turn_framerangemin'), studio.evalParm('asset_turn_framerangemax'))
    light_turn = (studio.evalParm('light_turn_framerangemin'), studio.evalParm('light_turn_framerangemax'))
    check(asset_turn == turntable.ASSET_TURN_RANGE
          and light_turn == turntable.light_turn_range(*asset_turn),
          f'the studio loop is light_turn_range ({asset_turn}, {light_turn})')

    # ...and export it with the export job's own code, on a few frames
    # across the first loop's end and the second's start.
    settings_path = output / turntable.render_settings_name('default')
    store_json(settings_path, dict(
        channel_names=[turntable.render_layer('default')],
        aov_names=aovs,
        overrides={'karma:global:pathtracedsamples': 3},
    ))
    out = output / turntable.stage_name('default')
    loop = turntable.loop_length(*turntable.ASSET_TURN_RANGE)
    first = frames.first + loop - 2
    code = export_houdini.main(
        BlockRange(first, first + 3), 0, 24, settings_path,
        hip, turntable.export_node_path(), out,
    )
    check(code == 0 and out.exists(), f'the export worker exports the studio ({code})')
    if not out.exists():
        return finish()

    # The turntable folder, copied into a render job's data dir, and the
    # collapse output gone.
    moved = temp / 'render_data' / turntable.folder_name('default')
    shutil.copytree(out.parent, moved)
    shutil.rmtree(output)

    stage = Usd.Stage.Open(str(moved / turntable.STAGE_FILE))
    check(stage.GetPrimAtPath('/cameras/turntable_camera').IsValid(), 'the turntable camera is there')
    lights = [p.GetPath().name for p in stage.Traverse() if p.HasAPI(UsdLux.LightAPI)]
    check('HDRI' in lights and len(lights) > 1, f'the studio lights are there ({lights})')
    settings = [p for p in stage.Traverse() if p.IsA(UsdRender.Settings)]
    samples = settings[0].GetAttribute('karma:global:pathtracedsamples').Get(first) if settings else None
    check(len(settings) == 1 and samples == 3, f'one render settings prim, override applied ({samples})')
    body = stage.GetPrimAtPath('/CHAR/hero/geo/body')
    check(body.IsValid() and body.IsActive(),
          'the asset composes from the snapshot after the move (sublayer path travels)')
    hero = UsdGeom.Xformable(stage.GetPrimAtPath('/CHAR/hero'))
    turns = {
        frame: round(hero.ComputeLocalToWorldTransform(frame)[0][0], 4)
        for frame in range(first, first + 4)
    }
    check(len(set(turns.values())) > 1, f'the asset turns over the frames ({turns})')
    leftovers = [
        path for layer in stage.GetLayerStack() for path in asset_paths(layer)
        if path.startswith(('opdef:', 'oplib:'))
    ]
    check(not leftovers, f'no opdef:/oplib: path left ({leftovers})')
    embedded_dir = moved / 'embedded'
    embedded = sorted(p.name for p in embedded_dir.glob('*')) if embedded_dir.exists() else []
    check(any('environment' in name for name in embedded), f'the studio HDRI is a file beside the stage ({embedded})')

    if '--husk' in sys.argv:
        husk = Path(hou.text.expandString('$HFS')) / 'bin' / 'husk'
        image = temp / 'husk' / 'frame.exr'
        image.parent.mkdir()
        result = subprocess.run([
            str(husk), '--frame', str(first), '--frame-count', '1',
            '--res', '160', '160', '--engine', 'cpu', '--disable-disk-check',
            '-o', str(image), str(moved / turntable.STAGE_FILE),
        ], capture_output=True, text=True)
        print(result.stdout[-2000:], result.stderr[-2000:])
        check(result.returncode == 0 and image.exists(), f'husk renders a frame ({result.returncode})')
    return finish()


def finish():
    print('FAILED: ' + '; '.join(FAILURES) if FAILURES else 'ALL PASS')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
