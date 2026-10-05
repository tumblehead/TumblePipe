"""Verify th::import_assets' per-row USD Variant dropdown.

Each row grows a "Variant" menu listing the variantSets authored on the
row's asset prim; the choice is applied by the HDA's set_metadata step,
downstream of the layerbreak, so it is part of the exported layer — the
same place a Set Variant LOP after the node used to author it.

Run under a project's hython, e.g. via the Desktop MCP run_hython with
use_dev_overrides=true so the local otls/ source is picked up (compile
the HDAs first, a stale otls/*.hda shadows the source):

    hython scripts/verify_import_assets_usd_variant.py

Defaults target paleindia Clash/goblin, which authors a native `model`
variantSet. Override with the env vars below if that asset moves.

TH_VERIFY_SYNTHETIC=1 runs against any project instead: the resolver is
patched to hand import_asset a temp staged layer that defines the asset
prim with a `model` variantSet (a, b, c; published selection a). Use the
TH_VERIFY_ENTITY/TH_VERIFY_PRIM vars to name any asset in the project.
"""

import os
import sys

import hou

ENTITY = os.environ.get("TH_VERIFY_ENTITY", "entity:/assets/Clash/goblin")
PRIM_PATH = os.environ.get("TH_VERIFY_PRIM", "/Clash/goblin")
SET_NAME = os.environ.get("TH_VERIFY_VARIANT_SET", "model")
VARIANT = os.environ.get("TH_VERIFY_VARIANT", "scan_mesh")
OTHER_VARIANT = os.environ.get("TH_VERIFY_OTHER_VARIANT", "gsplat_proxy")

SYNTHETIC = os.environ.get("TH_VERIFY_SYNTHETIC") == "1"
if SYNTHETIC:
    VARIANT = os.environ.get("TH_VERIFY_VARIANT", "b")
    OTHER_VARIANT = os.environ.get("TH_VERIFY_OTHER_VARIANT", "c")

_failures = []


def install_synthetic_fixture():
    """Patch the resolver to serve a staged layer carrying a variantSet."""
    import atexit
    import shutil
    import tempfile
    from pathlib import Path
    from pxr import Usd, UsdGeom
    from tumblepipe import resolver

    fixture_dir = tempfile.mkdtemp(prefix="th_verify_variant_")
    atexit.register(shutil.rmtree, fixture_dir, ignore_errors=True)
    staged = Path(fixture_dir) / "staged.usda"
    stage = Usd.Stage.CreateNew(str(staged))
    prim = UsdGeom.Xform.Define(stage, PRIM_PATH).GetPrim()
    variant_set = prim.GetVariantSets().AddVariantSet(SET_NAME)
    for name in ("a", "b", "c"):
        variant_set.AddVariant(name)
        variant_set.SetVariantSelection(name)
        with variant_set.GetVariantEditContext():
            UsdGeom.Cube.Define(stage, "%s/geo_%s" % (PRIM_PATH, name))
    variant_set.SetVariantSelection("a")
    stage.GetRootLayer().Save()

    resolver.try_resolve_entity_uri = lambda uri: str(staged)
    print("INFO synthetic staged layer: %s" % staged)


def check(label, got, want):
    if got == want:
        print("PASS %-46s got=%r" % (label, got))
    else:
        print("FAIL %-46s got=%r want=%r" % (label, got, want))
        _failures.append(label)


def selections(node, paths):
    stage = node.stage()
    result = []
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        result.append(
            prim.GetVariantSet(SET_NAME).GetVariantSelection()
            if prim.IsValid() else None
        )
    return result


def main():
    if SYNTHETIC:
        install_synthetic_fixture()
    stage = hou.node("/stage")

    node = stage.createNode("th::import_assets::2.0", "verify_single")
    node.parm("asset_imports").set(1)
    node.parm("entity1").set(ENTITY)
    hou.setPwd(node)
    node.hdaModule().execute()
    if node.isBypassed():
        print("ABORT: %s did not import — is it still on disk?" % ENTITY)
        return 1

    menu = node.parm("usd_variant1").menuItems()
    labels = node.parm("usd_variant1").menuLabels()
    check("menu leads with as-published", (menu[0], labels[0]), ("", "As published"))
    check("menu lists the chosen variant",
          "%s=%s" % (SET_NAME, VARIANT) in menu, True)

    published = selections(node, [PRIM_PATH])
    print("INFO published selection: %r" % published)

    node.parm("usd_variant1").set("%s=%s" % (SET_NAME, VARIANT))
    check("single: selection applied live (no re-import)",
          selections(node, [PRIM_PATH]), [VARIANT])

    spec = node.activeLayer().GetPrimAtPath(PRIM_PATH)
    check("single: selection in the exported (active) layer",
          dict(spec.variantSelections).get(SET_NAME) if spec else None,
          VARIANT)

    node.parm("usd_variant1").set("%s=%s" % (SET_NAME, OTHER_VARIANT))
    check("single: switch recooks",
          selections(node, [PRIM_PATH]), [OTHER_VARIANT])

    node.parm("usd_variant1").set("%s=no_such_variant" % SET_NAME)
    check("single: unknown variant is not authored",
          selections(node, [PRIM_PATH]), published)

    node.parm("usd_variant1").set("")
    check("single: as-published restores",
          selections(node, [PRIM_PATH]), published)

    # Survives a re-import (execute rebuilds the dive and the script).
    node.parm("usd_variant1").set("%s=%s" % (SET_NAME, VARIANT))
    node.hdaModule().execute()
    check("single: survives re-import",
          selections(node, [PRIM_PATH]), [VARIANT])

    # Multiple instances: every copy takes the selection.
    multi = stage.createNode("th::import_assets::2.0", "verify_multi")
    multi.parm("asset_imports").set(1)
    multi.parm("entity1").set(ENTITY)
    multi.parm("instances1").set(3)
    multi.parm("usd_variant1").set("%s=%s" % (SET_NAME, VARIANT))
    hou.setPwd(multi)
    multi.hdaModule().execute()
    copies = ["%s%d" % (PRIM_PATH, i) for i in range(3)]
    check("multi: every copy selected",
          selections(multi, copies), [VARIANT] * 3)

    # Inline mode bakes the asset; the choice must still apply.
    inline = stage.createNode("th::import_assets::2.0", "verify_inline")
    inline.parm("asset_imports").set(1)
    inline.parm("entity1").set(ENTITY)
    inline.parm("import_mode").set(1)
    inline.parm("usd_variant1").set("%s=%s" % (SET_NAME, VARIANT))
    hou.setPwd(inline)
    inline.hdaModule().execute()
    check("inline: selection applied",
          selections(inline, [PRIM_PATH]), [VARIANT])

    if _failures:
        print("\n%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
