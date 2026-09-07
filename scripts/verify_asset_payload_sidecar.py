#!/usr/bin/env hython
"""Verify th::asset_payload composes at the asset and keeps its own sidecar.

Two defects this guards against, both found 2026-09-04:

1. *No defaultPrim.* The payload layer never declared one, so the payload
   reference's ``automaticPrim`` resolved to the layer's ROOT prim -- the
   asset's category -- and composed that under the asset prim, doubling the
   asset name (``/PROP/crate/crate/geo/...``).

2. *A shared sidecar.* ``savepath`` was the bare relative ``payload.usd``,
   which resolves against the PROCESS working directory, not ``$HIP``. Every
   asset in every project and session wrote that one file, so each publish
   clobbered the last: a previously published asset then composed the wrong
   geometry, or -- when the categories differed -- nothing at all. The
   dangling-path guard cannot catch this, because the file does exist.

Needs a Houdini license (it cooks LOPs and writes USD), but no project:
the model node is driven in direct primpath mode.

    hython scripts/verify_asset_payload_sidecar.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import hou

ASSETS = [("crate", "PROP"), ("hero", "CHAR")]


def _build(stage, name, category):
    model = stage.createNode("th::create_asset_model::1.0", "m_" + name)
    model.parm("use_entity").set(0)
    model.parm("primpath").set(f"/{category}/{name}")

    sopnet = model.node("variant_sopnet")
    box = sopnet.createNode("box")
    wrangle = sopnet.createNode("attribwrangle")
    wrangle.setInput(0, box)
    wrangle.parm("class").set(1)
    wrangle.parm("snippet").set('s@path = "hull/body";')
    sopnet.node("OUT_default").setInput(0, wrangle)

    payload = stage.createNode("th::asset_payload::1.0", "p_" + name)
    payload.setInput(0, model)
    return model, payload


def main() -> int:
    from pxr import Usd

    work = tempfile.mkdtemp(prefix="verify_payload_")
    hou.hipFile.save(os.path.join(work, "scene.hip"))
    stage = hou.node("/stage")

    failures: list[str] = []
    built = []
    for name, category in ASSETS:
        model, payload = _build(stage, name, category)
        built.append((name, category, model, payload))

    # --- 1. composes at the asset, not one level under it ------------------
    print("composition (asset name must not repeat)")
    for name, category, model, payload in built:
        expected = f"/{category}/{name}/geo/hull/body"
        meshes = [
            str(p.GetPath()) for p in payload.stage().Traverse()
            if p.GetTypeName() == "Mesh"
        ]
        ok = meshes == [expected]
        if not ok:
            failures.append(f"composition/{name}: {meshes} != [{expected!r}]")
        print(f"  {'ok ' if ok else 'FAIL'} {name:<6} {meshes}")

    # --- 2. each asset owns its sidecar ------------------------------------
    print("\nsidecar naming")
    paths = {}
    for name, category, model, payload in built:
        p = payload.node("payload_layer").parm("savepath").eval()
        paths[name] = p
        print(f"  {name:<6} -> {p}")
    if len(set(paths.values())) != len(paths):
        failures.append(f"sidecar collision: {paths}")
        print("  FAIL assets share one sidecar")
    else:
        print("  ok  every asset writes its own sidecar")

    # --- 3. publishing one asset must not empty a previously published one --
    print("\npublish A, then publish B, then re-read A")
    out = os.path.join(work, "pub")
    for name, category, model, payload in built:
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        rop = stage.createNode("usd_rop", "rop_" + name)
        rop.setInput(0, payload)
        rop.parm("lopoutput").set(os.path.join(d, "asset.usd").replace("\\", "/"))
        rop.parm("execute").pressButton()

    for name, category, _model, _payload in built:
        expected = f"/{category}/{name}/geo/hull/body"
        usd = Usd.Stage.Open(os.path.join(out, name, "asset.usd").replace("\\", "/"))
        meshes = [str(p.GetPath()) for p in usd.Traverse() if p.GetTypeName() == "Mesh"]
        ok = meshes == [expected]
        if not ok:
            failures.append(f"after-publish/{name}: {meshes or 'EMPTY'} != [{expected!r}]")
        print(f"  {'ok ' if ok else 'FAIL'} {name:<6} {meshes or '<<< EMPTY >>>'}")

    shutil.rmtree(work, ignore_errors=True)

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  {f}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
