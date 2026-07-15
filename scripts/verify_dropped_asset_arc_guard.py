"""Acceptance: the export drop-guard allows artist-added geometry.

    hython scripts/verify_dropped_asset_arc_guard.py

Must run under hython (needs pxr + the hou-importing tumblepipe.pipe
module). Read-only; exits 1 on any failed assertion.

Background: export_layer refuses to publish when a metadata-less
Scope/Xform sits beside a tracked asset, because such a prim would
silently drop out of the published layer. That guard could not tell an
asset that *lost* its customData tag from geometry the artist modelled
directly into the workfile, so adding extra prims to an asset hierarchy
aborted the export (the reported bug).

util.list_dropped_asset_prims now takes pipeline_roots (the resolved
export:/ tree) and only flags a metadata-less prim that still composes
from a pipeline export layer — a real dropped asset keeps pulling its
geometry through the import node's sublayer, while artist geometry
composes from no pipeline layer and is allowed.

This builds a stage that mixes all three cases and asserts:
- with pipeline_roots set: only the dropped asset (composed from the
  export tree) is flagged; artist geometry passes; the tagged asset is
  never flagged;
- with pipeline_roots empty: the historical fail-closed behaviour still
  flags every metadata-less sibling.
"""

import sys
import tempfile
from pathlib import Path

from pxr import Usd

from tumblepipe.pipe.houdini import util


def _write_staged_layer(export_root: Path) -> Path:
    """A pipeline export layer defining /CHAR/mom (the dropped asset's geo)."""
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


def _build_stage(staged: Path) -> Usd.Stage:
    """Category /CHAR holding a tagged asset, a dropped asset, artist geo."""
    stage = Usd.Stage.CreateInMemory()
    # /CHAR/mom composes from the pipeline export layer (a dropped asset:
    # its geometry is present but nothing tagged its customData).
    stage.GetRootLayer().subLayerPaths.append(str(staged))
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
    staged = _write_staged_layer(export_root)
    stage = _build_stage(staged)
    root = stage.GetPseudoRoot()

    failures = []

    # Arc-aware: only the pipeline-composed dropped asset is flagged.
    dropped = util.list_dropped_asset_prims(root, pipeline_roots=[export_root])
    if dropped != ['/CHAR/mom']:
        failures.append(
            f'arc-aware: expected only /CHAR/mom flagged, got {dropped}'
        )

    # Fail-closed fallback: no roots -> every metadata-less sibling flagged.
    dropped_all = util.list_dropped_asset_prims(root)
    if sorted(dropped_all) != ['/CHAR/artistProp', '/CHAR/mom']:
        failures.append(
            'fallback: expected both metadata-less siblings flagged, '
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
    print('PASS arc-aware drop-guard: artist geometry allowed, '
          'pipeline drop still blocked, fallback intact')
    return 0


if __name__ == '__main__':
    sys.exit(main())
