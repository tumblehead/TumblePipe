#!/usr/bin/env hython
"""Verify th::animate puts the dive's geometry with no path in /scene.

Remy 2026-11-23: a toothbrush mesh added in th::animate's dive, with no
``path`` attribute, landed at ``/Geometry/toothbrush`` (a SOP Import's own
Path Prefix), beside ``/CHAR`` and ``/cameras``, and the animation export
reported it ("Disallowed root prim 'Geometry'"). The convention for prims
a shot adds is its scene scope: the three imports' Path Prefix is now
``/scene`` (``animate_points.LOOSE_SCOPE``).

Cooks th::animate over an asset file (``/CHAR/hero/geo/body``) whose dive
outputs, unpacked: the body with its path, moving; a box named
``toothbrush`` with no path, moving; a box with neither path nor name,
still. Live on a few frames and in the layer a USD ROP saves of the range,
checks that:

- the body stays at ``/CHAR/hero/geo/body``, and follows the dive;
- the toothbrush is a Mesh at ``/scene/toothbrush``, and moves;
- the unnamed box is a Mesh under ``/scene``;
- there is no ``/Geometry``;
- the shot export's root-prim check (``validate_shot_root_prims``) finds
  nothing, and the dive's ``placed`` SOP carries no warning.

Needs a Houdini license; preflight's hython stage runs it. Prints each
failed check, and exits 1 on any failure.
"""

import sys
import tempfile
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
CHECKED = []
FIRST, LAST = 1, 4
LIVE_FRAMES = (1, 3, 2)
BODY = '/CHAR/hero/geo/body'
TOOTHBRUSH = '/scene/toothbrush'


def check(name, ok, info=''):
    CHECKED.append(name)
    if not ok:
        print(f'FAIL  {name}  {info}', flush=True)
        FAILED.append(name)


def install():
    """Houdini's own packages and the expanded HDA sources."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def write_asset(path):
    """/CHAR/hero/geo/body: a 3 x 3 grid, as the dive's at the first frame."""
    from pxr import Usd, UsdGeom, Vt
    stage = Usd.Stage.CreateNew(str(path))
    for prim_path in ('/CHAR', '/CHAR/hero', '/CHAR/hero/geo'):
        UsdGeom.Xform.Define(stage, prim_path)
    mesh = UsdGeom.Mesh.Define(stage, BODY)
    mesh.CreatePointsAttr(Vt.Vec3fArray(
        [(0.5 * (n % 3) - 0.5, 0.0, 0.5 * (n // 3) - 0.5) for n in range(9)]))
    mesh.CreateFaceVertexCountsAttr([4, 4, 4, 4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 4, 3, 1, 2, 5, 4, 3, 4, 7, 6, 4, 5, 8, 7])
    stage.GetRootLayer().Save()


def wrangle(dive, name, source, code, cls=1):
    node = dive.createNode('attribwrangle', name)
    node.setInput(0, source)
    node.parm('class').set(cls)
    node.parm('snippet').set(code)
    return node


def build(asset):
    """th::animate over the asset, its dive the body, the toothbrush and an
    unnamed box, merged unpacked."""
    stage = hou.node('/stage')
    under = stage.createNode('sublayer', 'asset')
    under.parm('filepath1').set(str(asset).replace('\\', '/'))
    animate = stage.createNode('th::animate::2.0', 'animate_scope')
    animate.setInput(0, under)
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    grid = dive.createNode('grid', 'grid')
    grid.parm('rows').set(3)
    grid.parm('cols').set(3)
    grid.parm('sizex').set(1)
    grid.parm('sizey').set(1)
    body = wrangle(dive, 'body', grid, f's@path = "{BODY}";', cls=1)
    body = wrangle(dive, 'body_moves', body, 'v@P.y += @Frame * 0.1;', cls=2)
    box = dive.createNode('box', 'box')
    brush = wrangle(dive, 'toothbrush', box, 's@name = "toothbrush";', cls=1)
    brush = wrangle(dive, 'toothbrush_moves', brush, 'v@P.x += 2 + @Frame * 0.2;', cls=2)
    other = dive.createNode('box', 'unnamed')
    other.parm('tz').set(3)
    merge = dive.createNode('merge', 'merge')
    for index, node in enumerate((body, brush, other)):
        merge.setInput(index, node)
    output.setInput(0, merge)
    return animate


def save(animate, out):
    rop = hou.node('/out').createNode('usd', f'save_{animate.name()}')
    rop.parm('loppath').set(animate.path())
    rop.parm('lopoutput').set(str(out))
    rop.parm('trange').set(1)
    rop.parmTuple('f').deleteAllKeyframes()
    rop.parmTuple('f').set((FIRST, LAST, 1))
    rop.render()
    return rop


def points(stage, path, frame):
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(path)
    if not prim or prim.GetTypeName() != 'Mesh':
        return None
    return [tuple(round(c, 4) for c in p) for p in UsdGeom.Mesh(prim).GetPointsAttr().Get(frame)]


def meshes_under(stage, root):
    return sorted(str(p.GetPath()) for p in stage.Traverse()
                  if p.GetTypeName() == 'Mesh' and str(p.GetPath()).startswith(root + '/'))


def check_stage(where, stage, frame):
    from tumblepipe.pipe.houdini.validators.shot_structure import validate_shot_root_prims
    roots = sorted(p.GetName() for p in stage.GetPseudoRoot().GetChildren())
    check(f'{where} frame {frame}: no /Geometry', 'Geometry' not in roots, roots)
    body = points(stage, BODY, frame)
    check(f'{where} frame {frame}: the body is at {BODY}, moved by the dive',
          body is not None and all(abs(p[1] - 0.1 * frame) < 1e-3 for p in body), body)
    brush = points(stage, TOOTHBRUSH, frame)
    check(f'{where} frame {frame}: the toothbrush is a Mesh at {TOOTHBRUSH}, moved',
          brush is not None and abs(min(p[0] for p in brush) - (1.5 + 0.2 * frame)) < 1e-3,
          (brush, roots, meshes_under(stage, '/scene')))
    others = [p for p in meshes_under(stage, '/scene') if p != TOOTHBRUSH]
    check(f'{where} frame {frame}: the unnamed box is a Mesh under /scene', len(others) == 1,
          (others, roots))
    result = validate_shot_root_prims(stage.GetPseudoRoot())
    check(f'{where} frame {frame}: the export root-prim check passes',
          result.passed and not result.issues,
          [issue.message for issue in result.issues])


def main():
    from pxr import Usd
    install()
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.playbar.setPlaybackRange(FIRST, LAST)
    hou.setFrame(FIRST)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        asset = work / 'asset.usd'
        write_asset(asset)
        animate = build(asset)
        for frame in LIVE_FRAMES:
            hou.setFrame(frame)
            stage = animate.stage()
            check(f'live frame {frame}: th::animate cooks', not animate.errors(), animate.errors())
            check_stage('live', stage, frame)
        placed = animate.node('points_sops/placed')
        check('placed carries no warning (the loose geometry is a note)',
              placed is not None and not placed.warnings(),
              placed.warnings() if placed is not None else 'no placed SOP')
        out = work / 'anim.usd'
        rop = save(animate, out)
        check('the ROP saves without errors', not rop.errors() and out.exists(), rop.errors())
        if out.exists():
            saved = Usd.Stage.Open(str(out))
            for frame in range(FIRST, LAST + 1):
                check_stage('saved', saved, frame)
            del saved


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f'{len(CHECKED) - len(FAILED)}/{len(CHECKED)} checks passed', flush=True)
    print(f"{'FAILED: ' + ', '.join(FAILED[:10]) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
