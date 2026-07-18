"""Verify the th::import_model variant dropdown against a real asset.

Covers the "Model Variant is missing a dropdown" report: the native
setvariant LOP inside the HDA lost its menu whenever set_variant's
primpattern1 degraded to a glob, and its variant set was hardcoded to
"model" regardless of the Department parm.

Run under a project's hython, e.g. via the Desktop MCP run_hython with
use_dev_overrides=true so the local otls/ source is picked up:

    hython scripts/verify_import_model_variants.py

Defaults target paleindia Clash/goblin v0015, which authors a native
`model` variantSet with four variants. Override with the env vars below
if that asset moves or is re-exported.
"""

import os
import sys

import hou

ENTITY = os.environ.get("TH_VERIFY_ENTITY", "entity:/assets/Clash/goblin")
VERSION = os.environ.get("TH_VERIFY_VERSION", "v0015")
PRIM_PATH = os.environ.get("TH_VERIFY_PRIM", "/Clash/goblin")

# The four variants Clash/goblin authors, plus setvariant's own trailing
# entries ('' renders as "Clear From Active Layer", then '<block>').
VARIANTS = ("default", "gsplat", "gsplat_proxy", "scan_mesh", "", "<block>")

# Prim counts the SOP yields per variant with Unpack To Polygons off.
COMPOSE = (("default", 8), ("scan_mesh", 3), ("gsplat_proxy", 11))

_failures = []


def check(label, got, want):
    if got == want:
        print("PASS %-42s got=%r" % (label, got))
    else:
        print("FAIL %-42s got=%r want=%r" % (label, got, want))
        _failures.append(label)


def build(parent, name):
    node = parent.createNode("th::import_model::1.0", name)
    node.parm("entity").set(ENTITY)
    node.parm("version").set(VERSION)
    node.parm("unpack").set(0)
    return node


def main():
    geo = hou.node("/obj").createNode("geo", "verify_import_model_variants")

    node = build(geo, "resolved")
    node.hdaModule().execute(node)
    set_variant = node.node("lopnet/set_variant")

    if node.node("lopnet/import_layer").isBypassed():
        print("ABORT: %s %s did not import — is the asset still on disk?"
              % (ENTITY, VERSION))
        return 1

    check("label is department-neutral",
          node.parm("model_variant").parmTemplate().label(), "Variant")
    check("explicit entity: primpattern",
          set_variant.parm("primpattern1").eval(), PRIM_PATH)
    check("explicit entity: menu",
          node.parm("model_variant").menuItems(), VARIANTS)
    check("variant set follows department",
          set_variant.parm("variantset1").eval(), "model")

    # The reported failure: the layer imported fine, but the raw
    # get_workfile_context() lookup can't resolve (unsaved/scratch hip).
    # The menu must survive that rather than collapsing to just "".
    node.parm("entity").set("from_context")
    check("degraded from_context: primpattern",
          set_variant.parm("primpattern1").eval(), PRIM_PATH)
    check("degraded from_context: menu",
          node.parm("model_variant").menuItems(), VARIANTS)

    # Selecting a variant must compose all the way through to geometry,
    # not just populate the menu. Each variant gets a fresh node: a
    # department round-trip can leave the node stuck bypassed (separate
    # pre-existing bug, reproduced on 1.37.1).
    for variant, want_prims in COMPOSE:
        target = build(geo, "compose_" + variant)
        target.parm("model_variant").set(variant)
        target.hdaModule().execute(target)
        prim = target.node("lopnet/set_variant").stage().GetPrimAtPath(PRIM_PATH)
        check("compose %s: selection" % variant,
              prim.GetVariantSet("model").GetVariantSelection(), variant)
        check("compose %s: sop prims" % variant,
              len(target.geometry().prims()), want_prims)

    # A department with no export for this entity must bypass, and switching
    # back to a valid one must recover. The bypass used to be sticky, leaving
    # the node dead with a resolved version and filepath still on it.
    trip = build(geo, "bypass_roundtrip")
    trip.parm("model_variant").set("default")
    trip.hdaModule().execute(trip)
    inner = trip.node("lopnet/import_layer")
    check("roundtrip: imports clean",
          (inner.isBypassed(), trip.isBypassed()), (False, False))

    trip.parm("department").set("blendshape")
    trip.hdaModule().execute(trip)
    check("roundtrip: bypasses on missing export",
          (inner.isBypassed(), trip.isBypassed()), (True, True))

    trip.parm("department").set("model")
    trip.hdaModule().execute(trip)
    check("roundtrip: recovers on valid department",
          (inner.isBypassed(), trip.isBypassed()), (False, False))
    check("roundtrip: geometry comes back",
          len(trip.geometry().prims()), dict(COMPOSE)["default"])

    if _failures:
        print("\n%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
