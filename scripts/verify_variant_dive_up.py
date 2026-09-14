#!/usr/bin/env hython
"""Verify U from the model/lookdev dive targets leaves the HDA, and variants still flow.

Double-clicking `th::create_asset_model` lands in `variant_sopnet/create_variants`
(`th::create_asset_lookdev`: `lookdev_variant_subnet/lookdev_subnet`). U from
there used to stop in the wrapper, because the wrapper was in EditableNodes:
the variant sync kept a per-variant null beside the dive target, and the LOP
side fetched those nulls. Houdini's U walks up past non-editable parents only
(`nodegraphview.changeNetwork`), so an editable wrapper halts it.

Now only the dive target is editable and nothing is created beside it:

- model: `normalize_sopnet/FETCH_VARIANT` object-merges
  `variant_sopnet/create_variants/OUT_<VARIANTNAME>` directly;
- lookdev: `fetch_variant` reads `lookdev_subnet` at output `@ITERATION`.

Checks, per node: the U walk (Houdini's own loop, re-run here) lands on the
node's parent network; wrappers are not editable and hold no nulls; add /
rename / remove keep one output per row; each variant publishes what its own
output is wired to; and the edits the project lookdev template makes to
`lookdev_subnet` (position, display flag) are still allowed.

Runs headless and needs no project (direct primpath mode).

    hython scripts/verify_variant_dive_up.py
"""

from __future__ import annotations

import sys

import hou

ASSET = "/PROP/crate"


def walk_up(pwd):
    """`nodegraphview.changeNetwork(moving_up=True)`'s target, Houdini 22."""
    newnet = pwd.parent()
    childnet = pwd
    parent = childnet.parent()
    while parent and not parent.isEditable():
        childnet = parent
        parent = parent.parent()
        if parent and childnet.isLockedHDA() and childnet.type():
            hdadef = childnet.type().definition()
            if hdadef and hdadef.hasSection("DiveTarget"):
                if hdadef.sections()["DiveTarget"].contents().strip():
                    newnet = parent
    return newnet


def set_variants(node, names):
    """Drive the Variants multiparm the way its callbacks do, minus the event loop."""
    module = node.hdaModule()
    node.parm("variants").set(len(names))
    for i, name in enumerate(names, start=1):
        node.parm(f"variant_name{i}").set(name)
    module._sync_variants(node)
    for i, name in enumerate(names, start=1):
        module.on_variant_name(
            {"node": node, "script_multiparm_index": str(i), "script_value": name})


def outputs(network):
    return {n.evalParm("outputidx"): n.name()
            for n in network.children() if n.type().name() == "output"}


def per_variant(node, probe):
    """{variant name: probe(stage)}; active_variant indexes USD's sorted order."""
    names = sorted(node.parm(f"variant_name{i}").eval()
                   for i in range(1, node.parm("variants").eval() + 1))
    result = {}
    for index, name in enumerate(names):
        node.parm("active_variant").set(str(index))
        stage = node.stage()
        if stage is None:
            raise hou.Error(f"{node.path()} produced no stage: {node.errors()}")
        result[name] = probe(stage)
    node.parm("active_variant").set("0")
    return result


def mesh_points(stage):
    return sorted(len(p.GetAttribute("points").Get() or [])
                  for p in stage.Traverse() if p.GetTypeName() == "Mesh")


def markers(stage):
    return sorted(p.GetName() for p in stage.Traverse()
                  if str(p.GetPath()).startswith(f"{ASSET}/mtl/marker_"))


class Checks:
    def __init__(self):
        self.failures = []

    def __call__(self, label, ok, detail=""):
        print(f"  {'ok ' if ok else 'FAIL'} {label}" + (f"   {detail}" if not ok else ""))
        if not ok:
            self.failures.append(label)


def check_dive_structure(check, node, wrapper_name, target_name):
    wrapper = node.node(wrapper_name)
    target = wrapper.node(target_name)
    check("dive target is editable", target.isEditable())
    check("wrapper is not editable", not wrapper.isEditable())
    check("wrapper holds only the dive target",
          [c.name() for c in wrapper.children()] == [target_name],
          [c.name() for c in wrapper.children()])
    landed = walk_up(target)
    check("U from the dive target lands on the HDA's parent",
          landed == node.parent(), landed.path())
    return target


def check_model(stage_ctx, check):
    print("th::create_asset_model")
    node = stage_ctx.createNode("th::create_asset_model::1.0")
    node.parm("use_entity").set(0)
    node.parm("primpath").set(ASSET)
    set_variants(node, ["default", "alt", "extra"])
    create = check_dive_structure(check, node, "variant_sopnet", "create_variants")
    check("one output per row, in row order",
          outputs(create) == {0: "OUT_default", 1: "OUT_alt", 2: "OUT_extra"},
          outputs(create))

    box = create.createNode("box")                     # 8 points
    sphere = create.createNode("sphere")
    sphere.parm("type").set("poly")                    # 42 points
    create.node("OUT_default").setInput(0, box)
    create.node("OUT_alt").setInput(0, sphere)
    got = per_variant(node, mesh_points)
    check("each variant publishes its own output",
          got == {"alt": [42], "default": [8], "extra": []}, got)

    node.parm("variant_name2").set("hero")
    node.hdaModule().on_variant_name(
        {"node": node, "script_multiparm_index": "2", "script_value": "hero"})
    got = per_variant(node, mesh_points)
    check("rename keeps the wiring",
          outputs(create)[1] == "OUT_hero" and got.get("hero") == [42], (outputs(create), got))

    node.parm("variants").set(2)
    node.hdaModule()._sync_variants(node)
    check("remove drops the orphaned output",
          outputs(create) == {0: "OUT_default", 1: "OUT_hero"}, outputs(create))


def check_lookdev(stage_ctx, check):
    print("th::create_asset_lookdev")
    node = stage_ctx.createNode("th::create_asset_lookdev::1.0")
    node.parm("use_entity").set(0)
    node.parm("primpath").set(ASSET)
    set_variants(node, ["default", "alt"])
    subnet = check_dive_structure(check, node, "lookdev_variant_subnet", "lookdev_subnet")
    check("one output per row, in row order",
          outputs(subnet) == {0: "OUT_default", 1: "OUT_alt"}, outputs(subnet))

    for name in ("default", "alt"):
        prim = subnet.createNode("primitive", f"marker_{name}")
        prim.parm("primpath").set(f"{ASSET}/mtl/marker_{name}")
        subnet.node(f"OUT_{name}").setInput(0, prim)
    got = per_variant(node, markers)
    check("each variant publishes its own output (fetch by output index)",
          got == {"alt": ["marker_alt"], "default": ["marker_default"]}, got)

    # What templates/assets/lookdev/template.py does to the dive target.
    try:
        subnet.setPosition(hou.Vector2(8.0, 10.6))
        subnet.setDisplayFlag(True)
        check("template edits to lookdev_subnet are allowed", True)
    except hou.Error as exc:
        check("template edits to lookdev_subnet are allowed", False, exc)


def main() -> int:
    check = Checks()
    stage_ctx = hou.node("/stage")
    check_model(stage_ctx, check)
    check_lookdev(stage_ctx, check)
    if check.failures:
        print(f"\n{len(check.failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
