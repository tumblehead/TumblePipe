#!/usr/bin/env hython
"""Verify the animatable prim tag and Import Rigs' Discover.

Asset prims carry ``animatable`` in their pipeline customData
(prim_metadata.ANIMATABLE_KEY): the asset's Animatable box, or a rig
published in any channel. Import Rigs' Discover reads it off a LOP stage
and gives a row per animatable asset that has a rig. ``tests/`` pins the
rule without Houdini; this runs the real scripts and nodes against a
throwaway project:

1. The import nodes' metadata scripts, run in a Python Script LOP, stamp
   the tag: a rigged character (box off) and an animatable prop yes, a
   static prop no. import_layer's update overwrites a stale stamp.
2. Discover with Stage set to that LOP: one row for the rigged character,
   Instances = its tagged prims, the animatable prop with no rig named in
   the comment (checked on a stage with no rig left, where Import bypasses),
   a static prop left out; a row the stage still animates
   keeps its version. An untagged prim (imported before the tag) is judged
   by the rule.
3. Discover with Stage empty inside a th::animate reads the stage fed into
   it; outside one it raises a NodeError that says to set Stage.

Needs a Houdini license, not an existing project:

    HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
        TH_PIPELINE_PATH=<repo> hython scripts/verify_rig_discovery.py

Skipping packages also skips Houdini's own APEX scene SOPs, so the rig
import Discover runs stops at instancing; that is printed as a note.
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
WORK = Path(tempfile.mkdtemp(prefix="verify_rig_discovery_"))
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

import tumblepipe.pipe.houdini.nodes as ns  # noqa: E402
from tumblepipe.pipe.houdini import prim_metadata  # noqa: E402
from tumblepipe.pipe.houdini.lops import import_asset, import_layer  # noqa: E402
from tumblepipe.pipe.houdini.sops import import_rigs  # noqa: E402
from tumblepipe.pipe.paths import get_rig_export_path  # noqa: E402
from tumblepipe.pipe.paths.naming import entity_stem  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

HERO = Uri.parse("entity:/assets/CHAR/hero")       # rigged, box off
BALL = Uri.parse("entity:/assets/PROP/ball")       # box on, no rig
CRATE = Uri.parse("entity:/assets/PROP/crate")     # neither
OLD = Uri.parse("entity:/assets/CHAR/sidekick")    # rigged, prim untagged
FAILURES: list[str] = []
APEX_ADD = "apex::sceneaddcharacter"


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def publish_rig(asset: Uri) -> None:
    version_dir = Path(get_rig_export_path(asset, "default")) / "v0001"
    version_dir.mkdir(parents=True)
    geometry = hou.Geometry()
    geometry.createPoint()
    geometry.saveToFile(str(version_dir / f"{entity_stem(asset, 'rig', 'v0001')}.bgeo.sc"))
    (version_dir / "context.json").write_text(json.dumps({
        "inputs": [],
        "outputs": [{"uri": str(asset), "department": "rig",
                     "version": "v0001", "parameters": {}}],
    }), encoding="utf-8")


def _run(label: str, execute) -> bool:
    """Discover ends in Import; with packages skipped Houdini's APEX scene
    SOPs are missing and that stops at instancing, after the rows are set."""
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


def shot_stage():
    """/stage: prims for every asset, then the import nodes' metadata
    scripts. hero has two copies; sidekick's prim is tagged the old way."""
    stage_net = hou.node("/stage")
    prims = stage_net.createNode("primitive", "prims")
    paths = ["/CHAR/hero0", "/CHAR/hero1", "/PROP/ball", "/PROP/crate", "/CHAR/sidekick"]
    prims.parm("primpath").set(" ".join(paths))
    prims.parm("primtype").set("UsdGeomXform")
    script = stage_net.createNode("pythonscript", "metadata")
    script.setInput(0, prims)
    lines = [
        "import hou",
        "from tumblepipe.pipe.houdini import prim_metadata",
        "stage = hou.pwd().editableStage()",
        # sidekick: written before the tag existed
        "prim_metadata.set_metadata(stage.GetPrimAtPath('/CHAR/sidekick'), {",
        f"    'uri': '{OLD}', 'instance': 'sidekick', 'channel': 'default', 'inputs': []}})",
    ]
    for prim_path, asset in (("/CHAR/hero0", HERO), ("/CHAR/hero1", HERO),
                             ("/PROP/ball", BALL), ("/PROP/crate", CRATE)):
        # import_asset's script tags the asset's own prim path; point it
        # at each copy
        generated = import_asset._metadata_script(asset)
        generated = generated.replace(
            f'"{"/" + "/".join(asset.segments[1:])}"', f'"{prim_path}"')
        lines.append(generated)
    script.parm("python").set("\n".join(lines))
    return script


def check_stamps(lop) -> None:
    print("1. the import scripts stamp animatable")
    stage = lop.stage()
    if stage is None:
        check("metadata LOP cooks", False, lop.errors())
        return

    def tag(path):
        metadata = prim_metadata.get_metadata(stage.GetPrimAtPath(path))
        return None if metadata is None else metadata.get(prim_metadata.ANIMATABLE_KEY)

    check("rigged character (box off) is animatable", tag("/CHAR/hero0") is True, tag("/CHAR/hero0"))
    check("animatable prop is animatable", tag("/PROP/ball") is True, tag("/PROP/ball"))
    check("static prop is not", tag("/PROP/crate") is False, tag("/PROP/crate"))
    check("the old prim carries no tag", tag("/CHAR/sidekick") is None, tag("/CHAR/sidekick"))

    # import_layer refreshes a stale stamp from the project
    update = lop.parent().createNode("pythonscript", "stale_then_update")
    update.setInput(0, lop)
    update.parm("python").set("\n".join([
        "import hou",
        "from tumblepipe.pipe.houdini import prim_metadata",
        "prim = hou.pwd().editableStage().GetPrimAtPath('/PROP/ball')",
        "metadata = prim_metadata.get_metadata(prim)",
        "metadata['animatable'] = False",
        "prim_metadata.set_metadata(prim, metadata)",
        import_layer._metadata_update_script(
            Uri.parse("entity:/shots/SQ010/SH010"), "layout", "v0001",
            [{"asset": str(BALL)}]
        ),
    ]))
    ball = prim_metadata.get_metadata(update.stage().GetPrimAtPath("/PROP/ball"))
    check("import_layer overwrites a stale stamp",
          ball is not None and ball.get("animatable") is True, ball)


def check_discover(lop) -> None:
    print("2. Discover with Stage set")
    geo = hou.node("/obj").createNode("geo", "discover_set")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    node = import_rigs.ImportRigs(native)
    # A row the stage still animates keeps its pinned version
    node.set_rig_imports([(HERO, "default", "v0001", 1), (CRATE, "default", "latest", 1)])
    native.parm("stage_path").set(lop.path())
    if not _run("Discover runs", node.discover):
        return
    rows = [
        (native.parm(f"entity{i}").eval(), native.parm(f"channel{i}").eval(),
         native.parm(f"version{i}").eval(), native.parm(f"instances{i}").eval())
        for i in range(1, native.parm("rig_imports").eval() + 1)
    ]
    check("a row per rigged animatable asset, Instances = its prims",
          rows == [(str(HERO), "default", "v0001", 2), (str(OLD), "default", "latest", 1)],
          rows)

    # The import above stops at instancing here, before its comment; a
    # stage with no rigged asset left bypasses and comments instead
    no_rigs = lop.parent().createNode("prune", "no_characters")
    no_rigs.setInput(0, lop)
    no_rigs.parm("primpattern1").set("/CHAR")
    no_rigs.parm("method").set("deactivate")
    native.parm("stage_path").set(no_rigs.path())
    if not _run("Discover on a stage with no rigs runs", node.discover):
        return
    comment = native.comment()
    check("no rows left", native.parm("rig_imports").eval() == 0,
          native.parm("rig_imports").eval())
    check("the animatable prop with no rig is named",
          "Animatable, no rig published: ball" in comment, repr(comment))
    check("the static prop is not", "crate" not in comment, repr(comment))


def check_stage_default(lop) -> None:
    print("3. Discover with Stage empty")
    geo = hou.node("/obj").createNode("geo", "discover_outside")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    try:
        import_rigs.ImportRigs(native).discover()
        check("outside th::animate it raises", False, "no error")
    except ns.NodeError as exc:
        check("outside th::animate it raises, naming Stage", "Set Stage" in str(exc), str(exc))

    animate = lop.parent().createNode("th::animate::2.0", "animate")
    animate.setInput(0, lop)
    dive = animate.node("anim_dive")
    if dive is None:
        check("th::animate has an anim_dive", False, [c.name() for c in animate.children()])
        return
    native = dive.createNode("th::import_rigs::2.0", "rigs")
    node = import_rigs.ImportRigs(native)
    if not _run("Discover inside th::animate runs", node.discover):
        return
    entities = [native.parm(f"entity{i}").eval()
                for i in range(1, native.parm("rig_imports").eval() + 1)]
    check("it reads the stage fed into th::animate",
          entities == [str(HERO), str(OLD)], entities)


def main() -> int:
    print(f"project: {PROJECT}")
    project = Path(api.storage.resolve(Uri.parse("project:/")))
    if WORK not in project.parents and project != PROJECT:
        print(f"refusing to run: project resolves to {project}, not into {WORK}")
        return 1
    for uri, properties in (
        (Uri.parse("entity:/assets/CHAR"), {}),
        (Uri.parse("entity:/assets/PROP"), {}),
        (HERO, {"name": "hero"}),
        (BALL, {"name": "ball", "animatable": True}),
        (CRATE, {"name": "crate"}),
        (OLD, {"name": "sidekick"}),
    ):
        if api.config.get_properties(uri) is None:
            api.config.add_entity(uri, properties)
        elif properties:
            api.config.set_own_properties(uri, {**(api.config.get_own_properties(uri) or {}), **properties})
    publish_rig(HERO)
    publish_rig(OLD)

    lop = shot_stage()
    check_stamps(lop)
    check_discover(lop)
    check_stage_default(lop)

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
