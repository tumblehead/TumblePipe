"""Acceptance: the export drop-guard allows non-asset geometry.

    hython scripts/verify_dropped_asset_arc_guard.py

Must run under hython (needs pxr + the hou-importing tumblepipe.pipe
module). Read-only; exits 1 on any failed assertion.

Background: export_layer refuses to publish when a metadata-less
Scope/Xform sits beside a tracked asset, because such a prim would
silently drop out of the published layer. That guard could not tell an
asset that *lost* its customData tag from geometry that never carried
per-asset metadata, so two legitimate cases aborted the export:
- geometry the artist modelled directly into the workfile (the first
  reported bug);
- department-authored shot content — an FX sim, a set-dress cache —
  imported from another department's shot export (the sh020_Clash
  'cannonball' effects geometry that blocked a light export).

util.list_dropped_asset_prims takes asset_export_roots (the resolved
export:/assets tree) and only flags a metadata-less prim that still
composes from that *asset* export tree. A real dropped asset keeps
pulling its geometry through the import node's sublayer of an
export/assets/... layer; artist geometry composes from no pipeline layer,
and department shot geometry composes only from export/shots/.../<dept>/,
so both are allowed.

This builds a stage that mixes all four cases and asserts:
- with asset_export_roots set: only the dropped asset (composed from the
  asset export tree) is flagged; artist geometry and department shot
  geometry pass; the tagged asset is never flagged;
- with asset_export_roots empty: the historical fail-closed behaviour
  still flags every metadata-less sibling.
"""

import sys
import tempfile
from pathlib import Path

from pxr import Usd

from tumblepipe.pipe.houdini import util


def _write_asset_layer(export_root: Path) -> Path:
    """An asset export layer defining /CHAR/mom (the dropped asset's geo)."""
    asset_dir = export_root / 'assets' / 'CHAR' / 'mom' / '_staged' / 'default' / 'v0001'
    asset_dir.mkdir(parents=True, exist_ok=True)
    staged = asset_dir / 'CHAR_mom_default.usda'
    staged.write_text(
        '#usda 1.0\n'
        'def Xform "CHAR"\n'
        '{\n'
        '    def Xform "mom"\n'
        '    {\n'
        '        def Sphere "geo" {}\n'
        '    }\n'
        '}\n'
    )
    return staged


def _write_shot_dept_layer(export_root: Path) -> Path:
    """A shot-department (effects) export layer defining /CHAR/cannonball.

    Department-authored shot geometry: it lives in the export tree but
    under export/shots/.../<dept>/, never carried per-asset metadata, and
    must not be mistaken for a dropped asset.
    """
    fx_dir = export_root / 'shots' / '000' / 'sh010' / 'default' / 'effects' / 'v0001' / 'stage'
    fx_dir.mkdir(parents=True, exist_ok=True)
    staged = fx_dir / 'cannonballs.usda'
    staged.write_text(
        '#usda 1.0\n'
        'def Xform "CHAR"\n'
        '{\n'
        '    def Xform "cannonball"\n'
        '    {\n'
        '        def Sphere "geo" {}\n'
        '    }\n'
        '}\n'
    )
    return staged


def _build_stage(asset_layer: Path, shot_dept_layer: Path) -> Usd.Stage:
    """Category /CHAR: tagged asset, dropped asset, artist geo, dept geo."""
    stage = Usd.Stage.CreateInMemory()
    # /CHAR/mom composes from the asset export layer (a dropped asset: its
    # geometry is present but nothing tagged its customData).
    stage.GetRootLayer().subLayerPaths.append(str(asset_layer))
    # /CHAR/cannonball composes from a shot-department export layer —
    # department-authored shot geometry, metadata-less by design.
    stage.GetRootLayer().subLayerPaths.append(str(shot_dept_layer))
    # /CHAR is the category prim; it must be typed for the walk to descend.
    stage.DefinePrim('/CHAR', 'Xform')
    # /CHAR/dad is a correctly tracked asset — proves /CHAR is a category.
    dad = stage.DefinePrim('/CHAR/dad', 'Xform')
    util.set_metadata(dad, {
        'uri': 'entity:/assets/CHAR/dad',
        'instance': 'dad',
        'variant': 'default',
        'inputs': [],
    })
    # /CHAR/artistProp is geometry the artist authored directly into the
    # workfile session layer — metadata-less, but from no pipeline layer.
    stage.DefinePrim('/CHAR/artistProp', 'Xform')
    return stage


def main() -> int:
    export_root = Path(tempfile.mkdtemp(prefix='tp-arc-guard-')) / 'export'
    export_root.mkdir(parents=True)
    asset_layer = _write_asset_layer(export_root)
    shot_dept_layer = _write_shot_dept_layer(export_root)
    stage = _build_stage(asset_layer, shot_dept_layer)
    root = stage.GetPseudoRoot()
    asset_root = export_root / 'assets'

    failures = []

    # Asset-scoped: only the asset-composed dropped asset is flagged;
    # artist geometry and department shot geometry both pass.
    dropped = util.list_dropped_asset_prims(root, asset_export_roots=[asset_root])
    if dropped != ['/CHAR/mom']:
        failures.append(
            f'asset-scoped: expected only /CHAR/mom flagged, got {dropped}'
        )

    # Fail-closed fallback: no roots -> every metadata-less sibling flagged.
    dropped_all = util.list_dropped_asset_prims(root)
    expected_all = ['/CHAR/artistProp', '/CHAR/cannonball', '/CHAR/mom']
    if sorted(dropped_all) != expected_all:
        failures.append(
            'fallback: expected every metadata-less sibling flagged, '
            f'got {sorted(dropped_all)}'
        )

    # The tagged asset is never a drop, either way.
    if '/CHAR/dad' in dropped or '/CHAR/dad' in dropped_all:
        failures.append('the tracked asset /CHAR/dad was flagged as dropped')

    for failure in failures:
        print(f'FAIL {failure}')
    if failures:
        print(f'-- {len(failures)} failure(s)')
        return 1
    print('PASS asset-scoped drop-guard: artist + department shot geometry '
          'allowed, asset drop still blocked, fallback intact')
    return 0


if __name__ == '__main__':
    sys.exit(main())
