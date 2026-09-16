#!/usr/bin/env hython
"""Verify a reopened scene's import nodes pick up newer publishes.

Found 2026-09-16 from "the import_model in my lookdev scene didn't load the
latest version". Import nodes resolve their version when they run and save
the result; ``hou.hipFile.load`` runs nothing. Only the Asset Browser's own
open actions re-ran them, so a scene opened any other way kept the versions
it was saved with. Checks, each against real nodes:

1. Load hook: a plain ``hou.hipFile.load`` refreshes IMPORT_MODEL to the
   newest model, and leaves it alone with the setting off.
2. Pins: an import_layer pinned to v0001 still loads the v0001 file after a
   ``latest`` import_asset left the resolver's latest mode on.
3. Bypass: import_assets / import_rigs un-bypass once they have rows again.
4. Sweep: a standalone th::import_rig is refreshed; import nodes nested in
   another import node are not run a second time.
5. Farm publish: pinned import_layer, standalone import_asset and import_rig
   are all forced to their newest publish before the export.

Builds a throwaway project from ``scripts/project_template`` with fake model
publishes, so it needs no existing project, but it does need a Houdini
license. Run it through the Desktop's run_hython with the tumblepipe dev
override, or:

    hython scripts/verify_import_refresh.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="verify_import_refresh_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")

import hou  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

from tumblepipe import resolver  # noqa: E402
from tumblepipe.asset_browser import load_hook  # noqa: E402
from tumblepipe.asset_browser.prefs import PipelinePrefs, save_prefs  # noqa: E402
from tumblepipe.pipe.context import commit_next_workfile  # noqa: E402
from tumblepipe.pipe.houdini.lops import import_assets, import_layer  # noqa: E402
from tumblepipe.pipe.houdini.scene_imports import (  # noqa: E402
    find_import_nodes, refresh_scene_imports,
)
from tumblepipe.pipe.houdini.sops import import_rigs  # noqa: E402
from tumblepipe.pipe.houdini.ui.helpers import load_module  # noqa: E402
from tumblepipe.pipe.paths import get_export_uri  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

CATEGORY = Uri.parse_unsafe("entity:/assets/PROP")
ASSET = Uri.parse_unsafe("entity:/assets/PROP/crate")
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def publish_model(version: str) -> None:
    """A minimal model export: one layer plus the context.json readers need."""
    from pxr import Usd, UsdGeom

    root = api.storage.resolve(get_export_uri(ASSET, "default", "model"))
    out = Path(root) / version
    out.mkdir(parents=True)
    layer = Usd.Stage.CreateNew(
        str(out / f"assets_PROP_crate_default_model_{version}.usd"))
    UsdGeom.Xform.Define(layer, "/PROP/crate")
    layer.GetRootLayer().Save()
    (out / "context.json").write_text(json.dumps({
        "inputs": [],
        "outputs": [{
            "uri": str(ASSET), "department": "model", "variant": "default",
            "version": version, "timestamp": "", "user": "verify",
            "parameters": {"assets": [], "aov_names": []},
        }],
    }), encoding="utf-8")


def loaded_version(node) -> str:
    return Path(node.parm("import_filepath2").eval()).stem.rsplit("_", 1)[-1]


def load(path: Path) -> None:
    hou.hipFile.load(str(path), suppress_save_prompt=True,
                     ignore_load_warnings=True)


def build_lookdev_scene() -> Path:
    hou.hipFile.clear(suppress_save_prompt=True)
    hip = commit_next_workfile(ASSET, "lookdev")
    template = PROJECT / "_config/templates/assets/lookdev/template.py"
    load_module(template, "verify_lookdev_template").create(
        hou.node("/stage"), ASSET, "lookdev")
    hou.hipFile.save(str(hip))
    return hip


def check_load_hook(hip: Path) -> None:
    print("1. a plain scene load refreshes import nodes")
    # hython has no GUI, so the hook would decline to install. Pretend.
    hou.isUIAvailable = lambda: True
    load_hook.register()
    try:
        save_prefs(PipelinePrefs(auto_refresh_on_open=False))
        load(hip)
        il = hou.node("/stage/IMPORT_MODEL")
        check("setting off: keeps the saved version",
              loaded_version(il) == "v0001", loaded_version(il))

        save_prefs(PipelinePrefs(auto_refresh_on_open=True))
        load(hip)
        il = hou.node("/stage/IMPORT_MODEL")
        check("setting on: loads the newest model",
              loaded_version(il) == "v0002", loaded_version(il))
        check("  and its label agrees",
              il.parm("version_label").eval() == "v0002",
              il.parm("version_label").eval())
    finally:
        for callback in hou.hipFile.eventCallbacks():
            hou.hipFile.removeEventCallback(callback)


def check_pins(hip: Path) -> None:
    print("2. a pinned import_layer ignores a leftover latest mode")
    load(hip)
    il = import_layer.ImportLayer(hou.node("/stage/IMPORT_MODEL"))
    il.set_version_name("v0001")
    ia = hou.node("/stage").createNode("th::import_asset::1.0", "IA_latest")
    ia.parm("entity").set(str(ASSET))
    ia.parm("version").set("latest")
    resolver.set_latest_mode(True)
    il.execute()
    check("direct execute under latest mode loads v0001",
          loaded_version(il.native()) == "v0001", loaded_version(il.native()))
    resolver.set_latest_mode(False)
    # The sweep runs nodes in scene order, and IMPORT_MODEL comes first, so
    # only a second sweep sees the mode the import_asset left on.
    refresh_scene_imports()
    refresh_scene_imports()
    check("through two sweeps, after the import_asset ran",
          loaded_version(il.native()) == "v0001", loaded_version(il.native()))
    resolver.set_latest_mode(False)


def check_bypass() -> None:
    print("3. multi-import nodes un-bypass once they have rows")
    geo = hou.node("/obj").createNode("geo", "bypass_rigs")
    cases = [
        (hou.node("/stage").createNode("th::import_assets::2.0", "IA_rows"),
         import_assets.ImportAssets, "asset_imports"),
        (geo.createNode("th::import_rigs::2.0", "IR_rows"),
         import_rigs.ImportRigs, "rig_imports"),
    ]
    for native, wrapper, count_parm in cases:
        node = wrapper(native)
        native.parm(count_parm).set(0)
        node.execute()
        was = native.isBypassed()
        native.parm(count_parm).set(1)
        native.parm("entity1").set(str(ASSET))
        node.execute()
        check(f"{native.type().name()}: bypassed while empty, not after",
              was and not native.isBypassed(),
              f"empty={was} with_rows={native.isBypassed()} "
              f"comment={native.comment()!r}")


def check_sweep() -> None:
    print("4. the sweep finds import_rig and skips nested import nodes")
    hou.hipFile.clear(suppress_save_prompt=True)
    commit_next_workfile(ASSET, "rig")
    geo = hou.node("/obj").createNode("geo", "rigs")
    rig = geo.createNode("th::import_rig::1.0", "single_rig")
    ia = hou.node("/stage").createNode("th::import_assets::2.0", "IA")
    import_assets.ImportAssets(ia).execute()
    ia.parm("asset_imports").set(1)
    ia.parm("entity1").set(str(ASSET))
    import_assets.ImportAssets(ia).execute()
    nested = [n.path() for n in ia.allSubChildren()
              if n.type().name().startswith("th::import_asset::")]
    found = [n.path() for n, _spec in find_import_nodes()]
    check("standalone import_rig is refreshed", rig.path() in found, found)
    check("import_assets has a nested import_asset", bool(nested), nested)
    check("the nested one is left to its parent",
          not set(nested) & set(found), found)


def check_farm_publish(hip: Path) -> None:
    print("5. the farm publish forces every import node to its newest publish")
    from tumblepipe.farm.tasks.publish import publish_houdini

    load(hip)
    il = import_layer.ImportLayer(hou.node("/stage/IMPORT_MODEL"))
    il.set_version_name("v0001")
    il.execute()
    ia = hou.node("/stage").createNode("th::import_asset::1.0", "IA_pinned")
    ia.parm("entity").set(str(ASSET))
    ia.parm("version").set("v0001")
    geo = hou.node("/obj").createNode("geo", "farm_rigs")
    rig = geo.createNode("th::import_rig::1.0", "single_rig")
    rig.parm("entity").set(str(ASSET))
    rig.parm("version").set("v0001")

    publish_houdini._update()
    check("import_layer pinned v0001 now loads v0002",
          loaded_version(il.native()) == "v0002", loaded_version(il.native()))
    check("standalone import_asset forced to latest",
          ia.parm("version").eval() == "latest", ia.parm("version").eval())
    check("standalone import_rig forced to latest",
          rig.parm("version").eval() == "latest", rig.parm("version").eval())


def main() -> int:
    print(f"project: {PROJECT}")
    for uri, properties in ((CATEGORY, {}), (ASSET, {"name": "crate"})):
        if api.config.get_properties(uri) is None:  # the template may ship it
            api.config.add_entity(uri, properties)
    publish_model("v0001")
    hip = build_lookdev_scene()
    publish_model("v0002")

    check_load_hook(hip)
    check_pins(hip)
    check_bypass()
    check_sweep()
    check_farm_publish(hip)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        print(f"project kept for inspection: {WORK}")
        return 1
    print("all checks passed")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
