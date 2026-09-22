"""Debug what a farm playblast will actually render, without submitting one.

A playblast that "comes back wrong" is nearly always one of four things, and
none of them are visible from the mp4:

1. husk rendered through the wrong camera. It picks the camera from the
   stage's RenderSettings prim — but it only *searches* ``/Render``, and it
   only reads ``renderSettingsPrimPath`` off the ROOT layer. A project whose
   settings live at ``/scene/Render/rendersettings`` declares that path in
   root_default_prims.usda, which reaches a collapsed stage as a sublayer, so
   husk sees neither: it logs "No camera in render settings, defaulting to
   <first camera on the stage>" and shoots the shot from the project
   template's origin camera.
2. husk rendered nothing. Storm cannot fill Karma's LPE AOVs (``beauty``,
   ``sourceName = "C.*[LO]"``), so if the playblast inherits the project's
   RenderProduct husk logs "All AOVs bypassed or missing. Nothing to write"
   and the worker reports it as a missing GPU.
3. the department cut dropped a layer the view depends on (the camera, the
   lights, the set).
4. the stage is fine and the worker environment is not (OCIO, GL context).

This script composes exactly what ``batch_submit`` would bundle -- same
staged file, same department cut, same collapse -- and reports what husk will
resolve from it. With ``--husk`` it renders one frame locally with the
worker's own flags, which settles 2 and 4.

Run under a project hython (``TH_PROJECT_PATH`` set), e.g. through
TumbleTrove Desktop's run_hython:

    hython scripts/debug_playblast.py
    hython scripts/debug_playblast.py --shot entity:/shots/HideAndReek/010
    hython scripts/debug_playblast.py --shot entity:/shots/030/060 \
        --department light --husk

Read-only: nothing is submitted, and the collapsed stage is written to a temp
directory whose path is printed so it can be opened in usdview.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


def _shot_uris(config, wanted: list[str]):
    from tumblepipe.util.uri import Uri
    from tumblepipe.config.entities import is_terminal_entity

    if wanted:
        return [Uri.parse_unsafe(raw) for raw in wanted]
    return sorted(
        (
            uri for uri in config.list_entity_uris(closure=True)
            if uri.segments and uri.segments[0] == 'shots'
            and is_terminal_entity(config, uri)
        ),
        key=str,
    )


def _departments():
    """(every department name, the renderable ones) in the shots pool.

    The whole pool, not the shot's own departments: the cut batch_submit
    applies slices `list_departments('shots')`, so a report that sliced
    anything else would not be reporting the farm's behaviour.
    """
    from tumblepipe.config.department import list_departments

    departments = list_departments('shots')
    return (
        [d.name for d in departments],
        [d.name for d in departments if d.renderable],
    )


def _world_translate(camera_prim, frame):
    from pxr import UsdGeom

    xform_cache = UsdGeom.XformCache(frame)
    matrix = xform_cache.GetLocalToWorldTransform(camera_prim)
    translation = matrix.ExtractTranslation()
    return tuple(round(value, 3) for value in translation)


def _report_stage(collapsed_path: Path, frame: int) -> bool:
    """Print what husk will resolve from *collapsed_path*. True if it looks
    renderable."""
    from pxr import Usd, UsdGeom, UsdLux, UsdRender
    from tumblepipe.pipe.usd import find_render_settings_prim_path

    stage = Usd.Stage.Open(str(collapsed_path), Usd.Stage.LoadAll)
    if stage is None:
        print('  FAIL: the collapsed stage does not compose at all')
        return False

    # Root layer only, deliberately: stage.GetMetadata() composes the value
    # out of every sublayer, and husk reads only the root. Layer metadata
    # lives on the layer's pseudo-root prim spec.
    pseudo_root = stage.GetRootLayer().pseudoRoot
    declared = (
        pseudo_root.GetInfo('renderSettingsPrimPath')
        if pseudo_root.HasInfo('renderSettingsPrimPath') else None
    )
    print(f'  root layer renderSettingsPrimPath: {declared or "(none)"}')
    if not declared:
        print(
            '    husk reads this off the ROOT layer only, and searches just '
            '/Render on its own. Without it, a settings prim outside /Render '
            'is invisible and husk falls back to the first camera it finds.'
        )

    settings_prims = [
        prim.GetPath().pathString
        for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
        if prim.IsA(UsdRender.Settings)
    ]
    print(f'  RenderSettings prims: {", ".join(settings_prims) or "(none)"}')

    cameras = [
        prim.GetPath().pathString
        for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
        if prim.IsA(UsdGeom.Camera)
    ]
    print(f'  cameras on the stage: {", ".join(cameras) or "(none)"}')

    lights = [
        prim.GetPath().pathString
        for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
        if prim.HasAPI(UsdLux.LightAPI)
    ]
    print(f'  lights on the stage: {len(lights)}')

    ok = True
    settings_path = declared
    if settings_path is None:
        try:
            settings_path = find_render_settings_prim_path(stage)
            print(f'  settings husk would find by search: {settings_path}')
        except Exception as error:
            print(f'  settings by search: none ({error})')
            settings_path = None
        if settings_path is not None and not settings_path.startswith('/Render/'):
            print(
                '    FAIL: husk will NOT find this one -- it is outside '
                '/Render and the root layer does not name it.'
            )
            ok = False

    if settings_path:
        settings_prim = stage.GetPrimAtPath(settings_path)
        if not settings_prim or not settings_prim.IsValid():
            print(f'  FAIL: {settings_path} is not a prim on this stage')
            ok = False
        else:
            settings = UsdRender.Settings(settings_prim)
            targets = settings.GetCameraRel().GetTargets()
            camera_path = targets[0].pathString if targets else None
            print(f'  render camera: {camera_path or "(no camera target)"}')
            if camera_path is None:
                ok = False
            else:
                camera_prim = stage.GetPrimAtPath(camera_path)
                if not camera_prim or not camera_prim.IsValid():
                    print('    FAIL: that camera prim is not on the stage')
                    ok = False
                else:
                    camera = UsdGeom.Camera(camera_prim)
                    focal = camera.GetFocalLengthAttr().Get(frame)
                    print(
                        f'    focalLength {focal}, world position '
                        f'{_world_translate(camera_prim, frame)} at frame {frame}'
                    )
                    if focal is not None and focal < 1.0:
                        print(
                            '    WARNING: a sub-1mm focal length is the '
                            "project template's placeholder camera, not a "
                            'shot camera.'
                        )
            products = settings.GetProductsRel().GetTargets()
            for product_path in products:
                product = UsdRender.Product(stage.GetPrimAtPath(product_path))
                if not product:
                    continue
                for var_path in product.GetOrderedVarsRel().GetTargets():
                    var = UsdRender.Var(stage.GetPrimAtPath(var_path))
                    if not var:
                        continue
                    source_type = var.GetSourceTypeAttr().Get()
                    source_name = var.GetSourceNameAttr().Get()
                    print(
                        f'    AOV {var_path.name}: sourceType={source_type} '
                        f'sourceName={source_name}'
                    )
                    if source_type == 'lpe':
                        print(
                            '      FAIL: Hydra Storm cannot fill an LPE AOV. '
                            'husk will write no image at all.'
                        )
                        ok = False
    return ok


def _run_husk(collapsed_path: Path, frame: int, resolution, out_dir: Path) -> bool:
    from tumblepipe.api import api, path_str, to_windows_path
    from tumblepipe.apps.houdini import Husk
    from tumblepipe.farm.tasks.env import get_base_env
    from tumblepipe.farm.tasks.playblast.playblast import STORM_DELEGATE

    out_path = out_dir / 'debug_playblast.$F4.jpg'
    width, height = resolution
    print(f'  husk -> {out_path}')
    exit_code = Husk().run(
        to_windows_path(collapsed_path),
        [
            '--resolver-context', path_str(to_windows_path(collapsed_path)),
            '--renderer', STORM_DELEGATE,
            '--gpu',
            '--verbose', 'a2',
            '--make-output-path',
            '--no-mplay',
            '--res', str(width), str(height),
            '--frame', str(frame),
            '--frame-count', '1',
            '--output', path_str(to_windows_path(out_path)),
        ],
        env=get_base_env(api),
    )
    rendered = sorted(out_dir.glob('debug_playblast.*.jpg'))
    print(f'  husk exit {exit_code}, {len(rendered)} frame(s) written')
    for path in rendered:
        print(f'    {path}')
    return bool(rendered)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--shot', action='append', default=[],
        help='Shot entity URI. Repeatable. Default: every shot in the project.'
    )
    parser.add_argument(
        '--department',
        help=(
            'Playblast department -- the cut, as in the dialog. Default: the '
            "shot pool's last renderable department (the whole stack)."
        )
    )
    parser.add_argument(
        '--frame', type=int,
        help='Frame to inspect and (with --husk) render. Default: first frame.'
    )
    parser.add_argument(
        '--res', type=int, nargs=2, default=[1280, 720], metavar=('W', 'H'),
        help='Resolution for --husk. Default: 1280 720, the dialog default.'
    )
    parser.add_argument(
        '--husk', action='store_true',
        help='Also render one frame locally with the playblast worker flags.'
    )
    args = parser.parse_args(argv)

    from tumblepipe.api import default_client
    from tumblepipe.config.department import department_names_up_to
    from tumblepipe.config.timeline import get_frame_range
    from tumblepipe.pipe.paths import get_latest_staged_file_path
    from tumblepipe.pipe.usd import (
        collapse_latest_references,
        excluded_staged_refs,
    )
    from tumblepipe.util.io import store_text

    config = default_client().config
    shots = _shot_uris(config, args.shot)
    if not shots:
        print('No shots to inspect.')
        return 1

    temp_dir = Path(tempfile.mkdtemp(prefix='debug_playblast_'))
    print(f'Collapsed stages: {temp_dir}\n')

    failures = 0
    for shot_uri in shots:
        print(f'== {shot_uri}')
        department_names, renderable = _departments()
        if not renderable:
            print('  SKIP: the shots pool has no renderable department')
            continue
        department = args.department or renderable[-1]
        if department not in department_names:
            print(f'  FAIL: {department} is not a shots department')
            failures += 1
            continue
        included = department_names_up_to(department_names, department)
        print(f'  cut at {department}: {", ".join(included)}')

        staged_path = get_latest_staged_file_path(shot_uri, 'default')
        if staged_path is None or not staged_path.exists():
            print("  SKIP: no staged 'default' build -- publish the shot first")
            continue
        print(f'  staged: {staged_path}')

        excluded = excluded_staged_refs(
            staged_path, included, department_names
        )
        for ref in sorted(excluded):
            print(f'    dropped by the cut: {ref}')

        collapsed_path = (
            temp_dir / f'{"_".join(shot_uri.segments)}_playblast.usda'
        )
        try:
            content = collapse_latest_references(
                staged_path, collapsed_path, {},
                excluded_refs=excluded, playblast=True
            )
        except Exception as error:
            print(f'  FAIL: collapse refused this shot: {error}')
            failures += 1
            continue
        store_text(collapsed_path, content)

        frame_range = get_frame_range(shot_uri)
        frame = args.frame
        if frame is None:
            frame = (
                frame_range.full_range().first_frame
                if frame_range is not None else 1
            )

        if not _report_stage(collapsed_path, frame):
            failures += 1

        if args.husk:
            out_dir = collapsed_path.parent / collapsed_path.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            if not _run_husk(collapsed_path, frame, args.res, out_dir):
                failures += 1
        print()

    print(
        'No problems found in the stages inspected.' if not failures
        else f'{failures} shot(s) would not playblast correctly.'
    )
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
