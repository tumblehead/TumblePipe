"""Scan a hand-built USD stage with known contents: does the VRAM scan
itemise exactly what the viewport would draw?

The stage has one of everything the scanner must get right:

* a mesh of 4 quads with normals, an indexed ``st``, a primvar its material
  reads (``rest``) and one nothing reads (``junk``) — the last must keep its
  data bytes but upload nothing;
* an invisible mesh and a guide-purpose mesh, neither of which is drawn;
* a prototype shared by 3 native instances, which must be counted once;
* a point instancer drawing its prototype 5 times;
* a material whose texture is a real EXR from ``$HFS``, sized from its header,
  next to an unwired shader naming another file, which must not count;
* a Karma material whose ``kma:surface`` network reads that EXR while its
  universal preview output reads another file — only the EXR counts.

Needs ``pxr`` and OpenImageIO, so run it under hython::

    hython scripts/verify_vram_scan.py

Exit 0 = every count matched.
"""
# ruff: noqa: E402
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def report(what: str) -> int:
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print(f"All {what} checks passed.")
    return 0


from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from tumblepipe.vram import sizes
from tumblepipe.vram.images import read_image_info
from tumblepipe.vram.usd_scan import ScanOptions, StageScan

EXR = os.path.join(os.environ["HFS"], "houdini", "pic", "oceanramp.exr")


def _grid(stage, path):
    """A 2×2 grid of quads: 9 points, 4 faces, 16 face-vertices."""
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([Gf.Vec3f(x, 0, z) for z in range(3) for x in range(3)])
    mesh.CreateFaceVertexCountsAttr([4] * 4)
    mesh.CreateFaceVertexIndicesAttr([0, 1, 4, 3, 1, 2, 5, 4, 3, 4, 7, 6, 4, 5, 8, 7])
    return mesh


def build_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/world")

    mesh = _grid(stage, "/world/grid")
    mesh.CreateNormalsAttr([Gf.Vec3f(0, 1, 0)] * 16)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    api = UsdGeom.PrimvarsAPI(mesh)
    st = api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                           UsdGeom.Tokens.faceVarying)
    st.Set([Gf.Vec2f(0, 0)] * 9)
    st.SetIndices(Vt.IntArray([0] * 16))
    api.CreatePrimvar("rest", Sdf.ValueTypeNames.Float3Array,
                      UsdGeom.Tokens.vertex).Set([Gf.Vec3f(0)] * 9)
    api.CreatePrimvar("junk", Sdf.ValueTypeNames.FloatArray,
                      UsdGeom.Tokens.vertex).Set([0.0] * 9)

    _grid(stage, "/world/hidden").CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    _grid(stage, "/world/guide").CreatePurposeAttr(UsdGeom.Tokens.guide)

    # Material: a UsdUVTexture reading the EXR, a primvar reader fetching "rest".
    mat = UsdShade.Material.Define(stage, "/mat/M")
    surf = UsdShade.Shader.Define(stage, "/mat/M/surf")
    surf.CreateIdAttr("UsdPreviewSurface")
    tex = UsdShade.Shader.Define(stage, "/mat/M/tex")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(EXR))
    reader = UsdShade.Shader.Define(stage, "/mat/M/rest_reader")
    reader.CreateIdAttr("UsdPrimvarReader_float3")
    reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("rest")
    mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")
    surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.ConnectableAPI(), "rgb")
    surf.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
        reader.ConnectableAPI(), "result")
    # A shader nothing draws (like Houdini's stale preview fallback) pointing
    # at another file: never loaded by the viewport, so never counted.
    stray = UsdShade.Shader.Define(stage, "/mat/M/stale_preview_tex")
    stray.CreateIdAttr("UsdUVTexture")
    stray.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(os.path.join(os.environ["HFS"], "houdini", "pic", "Mandril.pic")))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

    # A Karma material: its kma:surface network reads one file, its
    # universal (preview) output another. The viewport draws the kma one.
    kmat = UsdShade.Material.Define(stage, "/mat/K")
    ksurf = UsdShade.Shader.Define(stage, "/mat/K/kma_surf")
    ksurf.CreateIdAttr("ND_standard_surface_surfaceshader")
    ktex = UsdShade.Shader.Define(stage, "/mat/K/kma_tex")
    ktex.CreateIdAttr("ND_image_color3")
    ktex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(EXR))
    ksurf.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        ktex.ConnectableAPI(), "out")
    kmat.CreateSurfaceOutput("kma").ConnectToSource(ksurf.ConnectableAPI(), "out")
    kprev = UsdShade.Shader.Define(stage, "/mat/K/preview")
    kprev.CreateIdAttr("UsdPreviewSurface")
    kprevtex = UsdShade.Shader.Define(stage, "/mat/K/preview_tex")
    kprevtex.CreateIdAttr("UsdUVTexture")
    kprevtex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(os.path.join(os.environ["HFS"], "houdini", "pic", "Mandril.pic")))
    kprev.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        kprevtex.ConnectableAPI(), "rgb")
    kmat.CreateSurfaceOutput().ConnectToSource(kprev.ConnectableAPI(), "surface")
    kgrid = _grid(stage, "/world/karma_grid")
    UsdShade.MaterialBindingAPI.Apply(kgrid.GetPrim()).Bind(kmat)

    # Native instancing: one prototype, three instances.
    # The source is a class: abstract, so the default traversal skips it
    # (deactivating it instead would carry active=false into every instance).
    stage.CreateClassPrim("/proto_src")
    _grid(stage, "/proto_src/geo")
    for i in range(3):
        p = stage.DefinePrim(f"/world/inst_{i}", "Xform")
        p.GetReferences().AddInternalReference("/proto_src")
        p.SetInstanceable(True)

    # Point instancing: one prototype drawn five times.
    pi = UsdGeom.PointInstancer.Define(stage, "/world/pi")
    _grid(stage, "/world/pi/protos/tree")
    pi.CreatePrototypesRel().SetTargets([Sdf.Path("/world/pi/protos/tree")])
    pi.CreateProtoIndicesAttr([0] * 5)
    pi.CreatePositionsAttr([Gf.Vec3f(i, 0, 0) for i in range(5)])
    return stage


def main() -> int:
    stage = build_stage()
    scan = StageScan(stage, ScanOptions(), image_info=read_image_info).run()
    by_path = {p.path: p for p in scan.prims}
    print("prims:", sorted(by_path))

    grid = by_path.get("/world/grid")
    check("the visible mesh is scanned", grid is not None)
    if grid:
        check("points/faces/face-vertices are 9/4/16",
              (grid.points, grid.faces, grid.face_vertices) == (9, 4, 16),
              f"{grid.points}/{grid.faces}/{grid.face_vertices}")
        pv = {p.name: p for p in grid.primvars}
        check("points, normals, st, rest, junk all itemised",
              {"points", "normals", "st", "rest", "junk"} <= set(pv), str(sorted(pv)))
        check("points are 9 × float3 = 108 bytes", pv["points"].data_bytes == 108)
        check("st is indexed: 9 values + 16 indices of data; 16 faceVarying elements "
              "uploaded at 8 B + 1 B each",
              (pv["st"].values, pv["st"].indices, pv["st"].data_bytes, pv["st"].upload_bytes)
              == (9, 16, 9 * 8 + 16 * 4, 16 * 9),
              str(pv["st"]))
        check("rest is read by the material, so it uploads", pv["rest"].upload_bytes == 108)
        check("junk is read by nothing: data kept, nothing uploaded",
              pv["junk"].data_bytes == 36 and pv["junk"].upload_bytes == 0)
        check("normals keep their faceVarying interpolation",
              pv["normals"].interpolation == "faceVarying")
        check("the bound material is recorded", grid.material == "/mat/M")
        check("topology uploads 8 triangles and 16 wireframe segments",
              grid.topology_upload == 8 * 12 + 16 * sizes.WIRE_BYTES_PER_FACE_VERTEX)
        check("normals are authored, so none are generated",
              "normals (generated)" not in pv)
        check("the viewport's per-point/per-triangle buffers are counted",
              grid.overhead_upload == sizes.mesh_overhead_bytes(9, 8))

    check("the invisible mesh is not scanned", "/world/hidden" not in by_path)
    check("the guide mesh is not scanned", "/world/guide" not in by_path)

    protos = [p for p in scan.prims if p.path.startswith("/__Prototype")]
    check("the native prototype is counted once, drawn 3 times",
          len(protos) == 1 and protos[0].instances == 3,
          str([(p.path, p.instances) for p in protos]))
    check("the abstract prototype source is not scanned",
          "/proto_src/geo" not in by_path)

    proto_pv = {p.name for p in protos[0].primvars} if protos else set()
    check("a mesh with no normals gets generated ones (9 points × 12 B)",
          "normals (generated)" in proto_pv
          and next(p for p in protos[0].primvars if p.name == "normals (generated)").upload_bytes == 108)

    tree = by_path.get("/world/pi/protos/tree")
    check("the point-instanced prototype is counted once, drawn 5 times",
          tree is not None and tree.instances == 5)

    texs = scan.textures
    check("only the texture the drawn surface reads is found — not the stray one",
          len(texs) == 1 and "oceanramp" in texs[0].path, str([t.path for t in texs]))
    if texs:
        t = texs[0]
        check("the EXR header was read (256×4, 3 ch, half)",
              t.info is not None and (t.info.width, t.info.height, t.info.channels,
                                      t.info.channel_bytes) == (256, 4, 3, 2),
              str(t.info))
        check("its data size is its full-resolution pixels",
              t.data_bytes == 256 * 4 * 3 * 2)
        check("its upload follows the viewport rules",
              t.upload_bytes == sizes.texture_upload_bytes(t.info, sizes.TextureRules()))
        check("both materials using it are listed — the Karma one via kma:surface",
              sorted(t.materials) == ["/mat/K", "/mat/M"], str(t.materials))

    # Incremental: one prim per step reaches the same result.
    slow = StageScan(stage, ScanOptions(), image_info=read_image_info)
    steps = 0
    while not slow.step(1):
        steps += 1
    check("stepping one prim at a time finds the same prims",
          sorted(p.path for p in slow.prims) == sorted(by_path), f"{steps} steps")
    return report("VRAM scan")


if __name__ == "__main__":
    raise SystemExit(main())
