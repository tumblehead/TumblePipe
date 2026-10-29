#!/usr/bin/env hython
"""Verify rig import, the dependency scan and export_rig read past a failed export.

Found 2026-10-29: an external user's rig export failed and left
``rig/v0006`` with no rig file and no ``context.json``. Every "latest"
reader took that folder: Build USD failed on every shot (``graph.scan``
read its missing context.json) and a 'latest' import_rig would have failed
with "has no rig file". ``tests/`` pins the rule without Houdini (newest
finished version, Python and the resolver alike); this drives the Houdini
nodes against a throwaway project:

1. import_rig on 'latest' loads the newest finished rig (v0001), not the
   newer empty folder, and its label says so.
2. import_rigs: a 'latest' row's label and its imported rig are v0001, and
   the asset counts as having a rig.
3. graph.scan does not raise on the empty folder, nor on an asset whose
   only rig export failed.
4. export_rig whose cook fails raises and takes its own empty version
   folder back out, leaving others' folders alone.
5. export_rig that succeeds writes the next version past every folder, and
   'latest' then loads it.

Needs a Houdini license, not an existing project. Run with packages
skipped (a stale package on one workstation points TH_PROJECT_PATH at
P:/growth) and the repo on the paths:

    HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
        TH_PIPELINE_PATH=<repo> hython scripts/verify_unfinished_versions.py

Skipping packages also skips Houdini's own APEX scene SOPs, so the rig
imports stop at instancing; that is printed as a note, and the version
they picked and the rig file they loaded are still checked.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="verify_unfinished_versions_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")
os.environ.setdefault("TT_USER_NAME", "verify")

import hou  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

from tumblepipe.pipe import graph  # noqa: E402
from tumblepipe.pipe.houdini.sops import export_rig, import_rig, import_rigs  # noqa: E402
from tumblepipe.pipe.paths import get_rig_export_path  # noqa: E402
from tumblepipe.pipe.paths.naming import entity_stem  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

CATEGORY = Uri.parse("entity:/assets/PROP")
ASSET = Uri.parse("entity:/assets/PROP/crate")
NEVER_RIGGED = Uri.parse("entity:/assets/PROP/barrel")
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def rig_dir(asset: Uri = ASSET) -> Path:
    return Path(get_rig_export_path(asset, "default"))


def rig_file(version: str) -> Path:
    return rig_dir() / version / f"{entity_stem(ASSET, 'rig', version)}.bgeo.sc"


def publish_rig(version: str) -> None:
    """A finished rig export: the rig file, then its context.json."""
    rig_file(version).parent.mkdir(parents=True)
    geometry = hou.Geometry()
    geometry.createPoint()
    geometry.saveToFile(str(rig_file(version)))
    (rig_dir() / version / "context.json").write_text(json.dumps({
        "inputs": [],
        "outputs": [{"uri": str(ASSET), "department": "rig",
                     "version": version, "parameters": {}}],
    }), encoding="utf-8")


def loaded_rig(native) -> str:
    return Path(native.node("import").parm("file").eval()).name


APEX_ADD = "apex::sceneaddcharacter"


def _run(label: str, execute) -> bool:
    """Run an import's execute. With packages skipped, Houdini's own APEX
    scene SOPs are not loaded and the import stops at instancing, after the
    version was picked and its rig file loaded: that is reported, and the
    rest is still checked. Anything else (the old "has no rig file") fails."""
    try:
        execute()
    except hou.OperationFailed:
        if hou.nodeType(hou.sopNodeTypeCategory(), APEX_ADD) is None:
            print(f"  note {label}: stopped at instancing, {APEX_ADD} is not "
                  "loaded in this session (packages skipped)")
            return True
        check(label, False, traceback.format_exc())
        return False
    except Exception:
        check(label, False, traceback.format_exc())
        return False
    return True


def check_import_rig() -> None:
    print("1. import_rig 'latest' reads past the failed v0002")
    geo = hou.node("/obj").createNode("geo", "rig_single")
    native = geo.createNode("th::import_rig::1.0", "rig")
    node = import_rig.ImportRig(native)
    node.set_entity_uri(ASSET)
    node.latest()
    if not _run("import_rig executes", node.execute):
        return
    check("loads v0001's rig file", loaded_rig(native) == rig_file("v0001").name,
          loaded_rig(native))
    check("label shows v0001", native.parm("version_label").eval() == "v0001",
          native.parm("version_label").eval())
    check("the latest helper agrees",
          import_rig.latest_rig_version(ASSET, "default") == "v0001")


def check_import_rigs() -> None:
    print("2. import_rigs 'latest' row reads past it too")
    geo = hou.node("/obj").createNode("geo", "rig_rows")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    node = import_rigs.ImportRigs(native)
    node.set_rig_imports([(ASSET, "default", "latest", 1)])
    if not _run("import_rigs executes", node.execute):
        return
    check("row label shows v0001", native.parm("version_label1").eval() == "v0001",
          native.parm("version_label1").eval())
    children = [n for n in native.allSubChildren() if n.type().name() == "th::import_rig::1.0"]
    loaded = [loaded_rig(n) for n in children]
    check("its import_rig loads v0001", loaded == [rig_file("v0001").name], loaded)
    check("the asset counts as rigged", import_rigs.has_rig_export(ASSET))


def check_scan() -> None:
    print("3. graph.scan reads past unfinished exports")
    (rig_dir(NEVER_RIGGED) / "v0001").mkdir(parents=True)
    try:
        scanned = graph.scan()
    except Exception as exc:
        check("scan does not raise", False, repr(exc))
        return
    check("scan does not raise", True)
    check("the finished rig is a node", graph.entity_key(ASSET, "rig") in scanned.nodes)
    check("the only-failed rig is not",
          graph.entity_key(NEVER_RIGGED, "rig") not in scanned.nodes)


def _export_node(name: str, source=None) -> export_rig.ExportRig:
    """An export_rig; ``source(native)`` makes a SOP inside it that its
    ``export`` cache then writes (the cache has no input of its own)."""
    geo = hou.node("/obj").createNode("geo", name)
    native = geo.createNode("th::export_rig::1.0", "export_rig")
    if source is not None:
        native.allowEditingOfContents()
        native.node("export").setInput(0, source(native))
    node = export_rig.ExportRig(native)
    node.set_entity_uri(ASSET)
    return node


def _error_sop(parent):
    error = parent.createNode("error", "fail")
    error.parm("numerror").set(1)
    error.parm("errormsg1").set("verify: forced cook failure")
    error.parm("severity1").set(2)  # error
    error.parm("enable1").set(1)
    return error


def check_failed_export() -> None:
    print("4. a failed export_rig takes its own folder back out")
    before = sorted(p.name for p in rig_dir().iterdir())
    node = _export_node("export_fails", _error_sop)
    try:
        node.execute(force_local=True)
        raised = None
    except Exception as exc:
        raised = exc
    check("the failed export raises", raised is not None, repr(raised))
    after = sorted(p.name for p in rig_dir().iterdir())
    check("no new version folder is left", after == before, f"{before} -> {after}")


def check_good_export() -> None:
    print("5. a good export_rig lands past every folder and becomes latest")
    node = _export_node("export_ok", lambda parent: parent.createNode("box"))
    version = node.execute(force_local=True)
    check("writes v0003 (past the empty v0002)", version == "v0003", version)
    check("with its rig file and context.json",
          rig_file("v0003").is_file() and (rig_dir() / "v0003" / "context.json").is_file())
    check("latest is now v0003", import_rig.latest_rig_version(ASSET, "default") == "v0003",
          import_rig.latest_rig_version(ASSET, "default"))


def main() -> int:
    print(f"project: {PROJECT}")
    project = Path(api.storage.resolve(Uri.parse("project:/")))
    if WORK not in project.parents and project != PROJECT:
        print(f"refusing to run: project resolves to {project}, not into {WORK}")
        return 1
    for uri, properties in ((CATEGORY, {}), (ASSET, {"name": "crate"}),
                            (NEVER_RIGGED, {"name": "barrel"})):
        if api.config.get_properties(uri) is None:  # the template may ship it
            api.config.add_entity(uri, properties)
    publish_rig("v0001")
    (rig_dir() / "v0002").mkdir()  # the failed export: an empty folder

    check_import_rig()
    check_import_rigs()
    check_scan()
    check_failed_export()
    check_good_export()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        print(f"project kept for inspection: {WORK}")
        return 1
    print("all checks passed")
    import logging
    logging.shutdown()  # pipeline.log holds a handle under WORK
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
