#!/usr/bin/env hython
"""Verify th::export_layer's Targets (#91) in Houdini: one cook, a version each.

``tests/test_export_targets.py`` pins the target rules and the routing
without Houdini; this drives the real node in a throwaway project with
three shots: src (1001-1010, the test shot), t1 (1001-1006) and t2
(2001-2012). An animated SOP Create (tx = $F) is exported from src's
workfile with Targets: Chosen = t1 t2:

1. Each target gets one new version under its own layer name, and its
   context.json names src as where it was exported from, with the shift.
2. The samples move so src's first frame lands on the target's: t1 keeps
   1001-1010, t2 gets 2001-2010, each carrying the values cooked at src's
   frames.
3. Nothing is published to src itself (it is not a target).
4. Targets: All shots writes to every shot, src included.
5. An asset target is refused before anything is written.

Needs a Houdini license, not an existing project. Compile the HDAs first
(``hython .ci/compile_hdas.py``), then run with packages skipped and the
repo on the paths:

    HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
        TH_PIPELINE_PATH=<repo> hython scripts/verify_export_targets.py

``VERIFY_KEEP=1`` keeps the temp project. Exits 1 on any failure.
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
WORK = Path(tempfile.mkdtemp(prefix="verify_export_targets_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")
os.environ.setdefault("TT_USER_NAME", "verify")

import hou  # noqa: E402
from pxr import Usd  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

from tumblepipe.pipe import export_targets  # noqa: E402
from tumblepipe.pipe.houdini.export_checks import ExportLayerError  # noqa: E402
from tumblepipe.pipe.houdini.lops import export_layer  # noqa: E402
from tumblepipe.pipe.houdini.nodes import disable_layer_save_path  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

SRC = Uri.parse("entity:/shots/sq010/src")
T1 = Uri.parse("entity:/shots/sq010/t1")
T2 = Uri.parse("entity:/shots/sq020/t2")
DEPARTMENT = "effects"
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def setup_project() -> None:
    for uri, start, end in ((SRC, 1001, 1010), (T1, 1001, 1006), (T2, 2001, 2012)):
        api.config.set_own_properties(uri, {"frame_start": start, "frame_end": end})


def save_workfile(entity_uri: Uri) -> None:
    folder = PROJECT / "shots" / Path(*entity_uri.segments[1:]) / DEPARTMENT
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "context.json").write_text(json.dumps({
        "uri": str(entity_uri), "department": DEPARTMENT,
    }))
    hou.hipFile.save(str(folder / f"{'_'.join(entity_uri.segments[1:])}_{DEPARTMENT}_v0001.hip"))


def build_graph():
    stage = hou.node("/stage")
    sop = stage.createNode("sopcreate", "fx")
    disable_layer_save_path(sop)
    create = sop.node("sopnet/create")
    box = create.createNode("box")
    xform = create.createNode("xform")
    xform.setInput(0, box)
    xform.parm("tx").setExpression("$F")
    xform.setDisplayFlag(True)
    xform.setRenderFlag(True)
    node = export_layer.create(stage, "export_fx")
    node.native().setInput(0, sop)
    node.set_entity_uri(SRC)
    node.set_department_name(DEPARTMENT)
    return node


def versions(shot: Uri) -> list[Path]:
    root = PROJECT / "export" / "shots" / Path(*shot.segments[1:])
    folder = root / "default" / DEPARTMENT
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir())


def samples(version: Path) -> dict[float, float]:
    layers = [p for p in version.glob("*.usd")]
    if not layers:
        return {}
    stage = Usd.Stage.Open(str(layers[0]))
    for prim in stage.Traverse():
        points = prim.GetAttribute("points")
        if points and points.GetTimeSamples():
            return {
                time: round(points.Get(time)[0][0], 3)
                for time in points.GetTimeSamples()
            }
    return {}


def main() -> int:
    setup_project()
    node = build_graph()
    save_workfile(SRC)

    print("\n1-3. Targets: Chosen = t1 t2")
    node.parm("targets_mode").set(export_targets.CHOSEN)
    node.set_chosen_targets([T1, T2])
    check("targets resolve", node.get_target_uris() == [T1, T2], node.get_target_uris())
    result = node.execute(force_local=True)
    print(f"  export returned: {result}")
    cooked = {float(f): round(f - 0.5, 3) for f in range(1001, 1011)}
    for shot, shift in ((T1, 0), (T2, 1000)):
        found = versions(shot)
        check(f"{shot.segments[-1]} has one version", len(found) == 1, found)
        if not found:
            continue
        version = found[0]
        layer_names = [p.name for p in version.glob("*.usd")]
        check(f"{shot.segments[-1]} layer is named for it",
              any(name.startswith("_".join(shot.segments) + "_default_") for name in layer_names),
              layer_names)
        context = json.loads((version / "context.json").read_text())
        params = context.get("parameters", context)
        flat = json.dumps(context)
        check(f"{shot.segments[-1]} context names src", str(SRC) in flat, params)
        check(f"{shot.segments[-1]} context entity is the target",
              str(shot) in flat, context.get("uri") or context.get("entity"))
        got = samples(version)
        want = {frame + shift: value for frame, value in cooked.items()}
        check(f"{shot.segments[-1]} samples shifted by {shift}", got == want, got)
    check("nothing published to src", versions(SRC) == [], versions(SRC))

    print("\n4. Targets: All shots")
    node.parm("targets_mode").set(export_targets.ALL_SHOTS)
    targets = node.get_target_uris()
    check("All shots lists the three", all(u in targets for u in (SRC, T1, T2)), targets)
    node.execute(force_local=True)
    check("src now has a version", len(versions(SRC)) == 1, versions(SRC))
    check("t1 has a second version", len(versions(T1)) == 2, versions(T1))
    if versions(SRC):
        check("src's own copy is not shifted", samples(versions(SRC)[0]) == cooked)

    print("\n5. An asset target is refused before anything is written")
    asset = Uri.parse("entity:/assets/props/crate")
    api.config.set_own_properties(asset, {})
    node.parm("targets_mode").set(export_targets.CHOSEN)
    node.set_chosen_targets([T1, asset])
    before = len(versions(T1))
    try:
        node.execute(force_local=True)
        check("refused", False, "no error raised")
    except ExportLayerError as error:
        check("refused, naming the asset", str(asset) in str(error), str(error))
    check("nothing written to t1", len(versions(T1)) == before, versions(T1))
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILURES.append("exception")
    print(f"\n{'FAILED: ' + str(len(FAILURES)) if FAILURES else 'ALL OK'}")
    if os.environ.get("VERIFY_KEEP"):
        print(f"kept {WORK}")
    else:
        hou.hipFile.clear(suppress_save_prompt=True)
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(1 if FAILURES else 0)
