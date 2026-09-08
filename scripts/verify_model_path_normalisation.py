#!/usr/bin/env hython
"""Verify that model geometry always publishes under the node's geo scope.

`path`/`name` are SOP-space object paths, not stage paths, and two common
sources make them absolute: Alembic writes `path` with a leading slash, and a
USD -> SOP round trip (lopimport/usdimport of a published asset) hands back
the asset's FULL prim path, geo scope included. SOP Import reads a leading
slash as stage-absolute, so either one walks the geometry out of the geo
scope -- and the scope, left with nothing under it, is never created at all.

`th::create_asset_model` and `th::create_model` each normalise ahead of their
SOP Import: fall back `name` -> `path`, drop the asset's own prim path when
the value is already rooted there (that prefix is re-anchoring, not authored
hierarchy), and make whatever is left relative. Hierarchy the modeller
actually authored is preserved, including a group named after the asset.

Runs headless and needs no project: both nodes are driven in direct primpath
mode, so no entity or config db is required.

    hython scripts/verify_model_path_normalisation.py
"""

from __future__ import annotations

import sys

import hou

ASSET_PRIMPATH = "/PROP/crate"
ASSET_NAME = "crate"

# (label, VEX authored on the incoming geometry, expected published mesh path)
ASSET_MODEL_CASES = [
    # A USD -> SOP round trip hands back the asset's own full prim path.
    ("roundtrip, full path", f's@path = "{ASSET_PRIMPATH}/geo/hull/body";',
     f"{ASSET_PRIMPATH}/geo/hull/body"),
    ("roundtrip, no geo", f's@path = "{ASSET_PRIMPATH}/hull/body";',
     f"{ASSET_PRIMPATH}/geo/hull/body"),
    # Nothing but the asset's own path: collapses, SOP Import names it.
    ("roundtrip, packed",
     f's@path = "{ASSET_PRIMPATH}"; s@name = "{ASSET_NAME}";',
     f"{ASSET_PRIMPATH}/geo/mesh_0"),
    # Alembic writes `path` with a leading slash.
    ("alembic absolute", 's@path = "/hull/body";',
     f"{ASSET_PRIMPATH}/geo/hull/body"),
    ("absolute, foreign root", 's@path = "/foo/body";',
     f"{ASSET_PRIMPATH}/geo/foo/body"),
    # A group the modeller named after the asset is THEIR hierarchy: kept.
    ("self-named group", f's@path = "{ASSET_NAME}/body";',
     f"{ASSET_PRIMPATH}/geo/{ASSET_NAME}/body"),
    ("name only, absolute", 's@name = "/hull/body";',
     f"{ASSET_PRIMPATH}/geo/hull/body"),
    ("relative", 's@path = "hull/body";',
     f"{ASSET_PRIMPATH}/geo/hull/body"),
    ("relative, deep", 's@path = "a/b/c/body";',
     f"{ASSET_PRIMPATH}/geo/a/b/c/body"),
    # Nothing authored: SOP Import names it, still under geo.
    ("unnamed", "// nothing authored", f"{ASSET_PRIMPATH}/geo/mesh_0"),
]

# th::create_model roots geometry under its Import Path Prefix (+ /geo). The
# normaliser must strip THAT prefix from a round-tripped path -- not the
# Load-As-Reference prim path (/$OS), which it anchored on until 2026-09-08
# and which never matched, so a round trip through a published asset doubled
# the prefix: /PROP/crate/geo/PROP/crate/geo/hull/body.
MODEL_PREFIX = "/PROP/crate"
MODEL_CASES = [
    ("relative", 's@path = "hull/body";', f"{MODEL_PREFIX}/geo/hull/body"),
    ("roundtrip, full path", f's@path = "{MODEL_PREFIX}/geo/hull/body";',
     f"{MODEL_PREFIX}/geo/hull/body"),
    ("roundtrip, no geo", f's@path = "{MODEL_PREFIX}/hull/body";',
     f"{MODEL_PREFIX}/geo/hull/body"),
    # The reference prim path is NOT the anchor: a path rooted at the node's
    # own name is authored hierarchy and must survive.
    ("node-named group", 's@path = "/{name}/body";', f"{MODEL_PREFIX}/geo/{{name}}/body"),
    ("absolute, foreign root", 's@path = "/foo/body";', f"{MODEL_PREFIX}/geo/foo/body"),
    ("name only, absolute", 's@name = "/hull/body";', f"{MODEL_PREFIX}/geo/hull/body"),
    ("unnamed", "// nothing authored", f"{MODEL_PREFIX}/geo/mesh_0"),
]


def _author_geometry(parent, snippet):
    """Box -> wrangle authoring the path attributes under test."""
    box = parent.createNode("box")
    wrangle = parent.createNode("attribwrangle")
    wrangle.setInput(0, box)
    wrangle.parm("class").set(1)  # primitives
    wrangle.parm("snippet").set(snippet)
    return wrangle


def _meshes(node):
    stage = node.stage()
    if stage is None:
        raise hou.Error(f"{node.path()} produced no stage: {node.errors()}")
    return [str(p.GetPath()) for p in stage.Traverse() if p.GetTypeName() == "Mesh"]


def check_create_asset_model(stage_ctx, failures):
    print("th::create_asset_model")
    for label, snippet, expected in ASSET_MODEL_CASES:
        node = stage_ctx.createNode("th::create_asset_model::1.0")
        node.parm("use_entity").set(0)
        node.parm("primpath").set(ASSET_PRIMPATH)

        sopnet = node.node("variant_sopnet")
        sopnet.node("OUT_default").setInput(0, _author_geometry(sopnet, snippet))

        meshes = _meshes(node)
        scope = node.stage().GetPrimAtPath(f"{ASSET_PRIMPATH}/geo")
        scoped = bool(scope) and scope.GetTypeName() == "Scope"

        ok = meshes == [expected] and scoped
        if not ok:
            failures.append((f"create_asset_model/{label}", meshes, expected, scoped))
        print(f"  {'ok ' if ok else 'FAIL'} {label:<26} {meshes}"
              + ("" if scoped else "   <<< no geo Scope"))


def check_create_model(stage_ctx, failures):
    print("th::create_model")
    for label, snippet, expected in MODEL_CASES:
        node = stage_ctx.createNode("th::create_model::1.0")
        node.parm("enable_pathprefix").set(1)
        node.parm("pathprefix").set(MODEL_PREFIX)
        snippet = snippet.format(name=node.name())
        expected = expected.format(name=node.name())

        create = node.node("sopnet/create")
        outputs = [n for n in create.children() if n.type().name() == "output"]
        outputs[0].setInput(0, _author_geometry(create, snippet))

        meshes = _meshes(node)
        ok = meshes == [expected]
        if not ok:
            failures.append((f"create_model/{label}", meshes, expected, True))
        print(f"  {'ok ' if ok else 'FAIL'} {label:<26} {meshes}")


def main() -> int:
    stage_ctx = hou.node("/stage")
    failures: list = []

    check_create_asset_model(stage_ctx, failures)
    check_create_model(stage_ctx, failures)

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for name, got, expected, scoped in failures:
            print(f"  {name}: got {got}, expected [{expected!r}]"
                  + ("" if scoped else " (geo Scope missing)"))
        return 1

    total = len(ASSET_MODEL_CASES) + len(MODEL_CASES)
    print(f"RESULT: ALL CHECKS PASSED ({total}/{total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
