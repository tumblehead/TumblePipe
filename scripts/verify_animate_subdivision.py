#!/usr/bin/env hython
"""Verify th::animate leaves every mesh's subdivision scheme alone.

th::animate's inner Edit Properties ``no_default_values`` (primpattern
``*``) had its ``subdivisionScheme`` set to Block. A USD block is an opinion
that resolves to the schema fallback, and UsdGeomMesh's fallback is
catmullClark: every mesh on the stage was subdivided, the geometry made in
th::animate's SOP path and the asset's own meshes alike, and the scheme
authored on the asset in a weaker layer was overridden (tumblepipe__bugs
2026-11-13: "no_default_values blocks the subdivision scheme primvar").
The node's control is now "do nothing"; what a mesh authored stays.

Cooks th::animate on an input stage holding three asset meshes that author
catmullClark, loop and none, with a keyed TumbleRig example in its dive (its
body goes through th::animate's SOP import). Checks that each asset mesh
keeps its scheme, that the dive's mesh keeps what its SOP Import authored
(and is not catmullClark by a block), and that no mesh carries a block.

Without TumbleRig only the asset meshes are checked. Needs a Houdini
license; run as scripts/verify_animate_keyed_scene.py is run (empty package
dir, TumbleRig installed once):

    HOUDINI_PACKAGE_DIR=<empty dir> HOUDINI_PATH="<repo>;&" \
        PYTHONPATH=<repo>/python TH_PIPELINE_PATH=<repo> \
        hython scripts/verify_animate_subdivision.py

Prints one line per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_animate_keyed_scene as keyed  # noqa: E402

ASSET_MESHES = {
    '/asset/smooth': 'catmullClark',
    '/asset/loopy': 'loop',
    '/asset/flat': 'none',
}
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


ASSET_SCRIPT = f'''
from pxr import UsdGeom
stage = hou.pwd().editableStage()
for path, scheme in {ASSET_MESHES!r}.items():
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr(scheme)
'''


def asset_stage(parent):
    node = parent.createNode('pythonscript', 'asset_meshes')
    node.parm('python').set(ASSET_SCRIPT)
    return node


def schemes(stage_):
    """prim path -> (scheme, blocked) for every mesh on the stage."""
    from pxr import Sdf, UsdGeom
    found = {}
    for prim in stage_.Traverse():
        if prim.GetTypeName() != 'Mesh':
            continue
        attr = UsdGeom.Mesh(prim).GetSubdivisionSchemeAttr()
        blocked = any(
            isinstance(spec.default, Sdf.ValueBlock)
            for spec in attr.GetPropertyStack()
        )
        found[str(prim.GetPath())] = (attr.Get(), blocked)
    return found


def main():
    keyed.install()
    stage = hou.node('/stage')
    hou.playbar.setFrameRange(1, 24)
    hou.setFrame(1)
    source = asset_stage(stage)
    animate = stage.createNode('th::animate::2.0', 'animate_subdiv')
    rig_kind = next((k for k in keyed.RIGS if keyed.example_type(k)), None)
    if rig_kind is not None:
        shape = keyed.RIGS[rig_kind]
        obj = hou.node('/obj').createNode('geo', f'rig_{rig_kind}')
        pathed = obj.createNode('python', 'pathed')
        pathed.setInput(0, obj.createNode(keyed.example_type(rig_kind)))
        pathed.parm('python').set(keyed.path_shapes(shape))
        animate, _ = keyed.build(stage, pathed, shape, rig_kind, 'subdiv')
    else:
        print('SKIP  TumbleRig is not installed: only the asset meshes are checked')
    animate.setInput(0, source)
    errors = animate.errors()
    check('th::animate cooks', not errors, errors[:1])
    found = schemes(animate.stage())
    for path, scheme in ASSET_MESHES.items():
        got = found.get(path)
        check(f'asset mesh {path} keeps {scheme}', got is not None and got[0] == scheme, got)
    check('no mesh carries a blocked subdivisionScheme',
          not any(blocked for _, blocked in found.values()), found)
    if rig_kind is not None:
        built = found.get(keyed.BODY)
        check("the dive's mesh keeps what its SOP Import authored (not subdivided)",
              built is not None and built[0] == 'none', built)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED or keyed.FAILED else 0)
