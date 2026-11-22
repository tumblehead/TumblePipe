#!/usr/bin/env hython
"""Verify a Multi's light publish on the farm reaches the shots' renders.

Auguu (2026-11-21): a Multi lighting every shot rendered with no lights
after the animation was exported, until the light was exported again in
the session, and the Submit Manager's light publish "did not work". The
farm's publish worker took the first export node of the shot and
department, bypassed or not, where the session's publish skips bypassed
nodes (``pipe.publish_scope`` is now the one rule). A light workfile whose
template export was bypassed for a new lighting setup published the
template's unlit chain on the farm.

Drives the real nodes and the publish worker in a throwaway project with
two shots, sh010 (1001-1010) and sh020 (1001-1008), each with an animation
export, in a light Multi with a shared timeline (sh020 at offset +10):

1. The Multi's light workfile from the stock template; for sh010 the
   template's export node is bypassed and reads the bare import, and a new
   export node follows the lights.
2. The publish worker publishes each member's light as a Farm Submit
   publish job does (the Multi's workfile bundled with its context.json):
   a new version each, with the member's lights in it.
3. The staged build composes the light layer, and the stage a render
   collapses (cut at light, and at render) has the lights.
4. A new animation export and build keep them.

Needs a Houdini license and the tumbleResolver (the light template's
th::import_shot composes the staged builds). Run through preflight:

    PREFLIGHT_HYTHON=verify_multi_light_publish.py python .ci/preflight.py --only hython

``VERIFY_KEEP=1`` keeps the temp project. Exits 1 on any failure.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="verify_multi_light_publish_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")
os.environ.setdefault("TT_USER_NAME", "verify")

import hou  # noqa: E402
from pxr import Usd, UsdLux  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

from tumblepipe.config import groups, multi_timeline as mt  # noqa: E402
from tumblepipe.config.department import list_departments  # noqa: E402
from tumblepipe.config.timeline import BlockRange  # noqa: E402
from tumblepipe.farm.workers import publish_houdini  # noqa: E402
from tumblepipe.pipe.houdini.lops import export_layer  # noqa: E402
from tumblepipe.pipe.houdini.nodes import disable_layer_save_path  # noqa: E402
from tumblepipe.pipe.paths import (  # noqa: E402
    department_workspace,
    get_latest_staged_file_path,
    latest_complete_hip_file_path,
    latest_export_path,
    next_staged_file_path,
)
from tumblepipe.pipe.paths.naming import entity_stem  # noqa: E402
from tumblepipe.pipe.staged_build import build_staged  # noqa: E402
from tumblepipe.pipe.usd.collapse import (  # noqa: E402
    collapse_latest_references,
    excluded_staged_refs,
)
from tumblepipe.util.uri import Uri  # noqa: E402

SH010 = Uri.parse("entity:/shots/sq010/sh010")
SH020 = Uri.parse("entity:/shots/sq010/sh020")
LAST = {SH010: 1010, SH020: 1008}
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def save_workfile(entity_uri: Uri, department: str) -> Path:
    """Save the scene as v0001 of ``entity_uri``'s department workfile, in
    the folder the pipeline resolves (the Multi's, for a covered member)."""
    _, folder = department_workspace(entity_uri, department)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "context.json").write_text(json.dumps({
        "uri": str(entity_uri), "department": department, "version": "v0001",
    }))
    path = folder / f"{entity_stem(entity_uri, department, 'v0001')}.hip"
    hou.hipFile.save(str(path))
    return path


def anim_workfile(shot: Uri):
    hou.hipFile.clear(suppress_save_prompt=True)
    stage = hou.node("/stage")
    sop = stage.createNode("sopcreate", "char")
    disable_layer_save_path(sop)
    create = sop.node("sopnet/create")
    box = create.createNode("box")
    xform = create.createNode("xform")
    xform.setInput(0, box)
    xform.parm("tx").setExpression("$F")
    xform.setDisplayFlag(True)
    xform.setRenderFlag(True)
    node = export_layer.create(stage, "export_anim")
    node.native().setInput(0, sop)
    node.set_entity_uri(shot)
    node.set_department_name("animation")
    save_workfile(shot, "animation")
    return node


def build(shot: Uri) -> None:
    build_staged(shot, next_staged_file_path(shot, "default"),
                 BlockRange(1001, LAST[shot]), "default")


def lights_of(stage) -> list[str]:
    return [str(p.GetPath()) for p in stage.Traverse() if p.HasAPI(UsdLux.LightAPI)]


def light_layer(shot: Uri) -> tuple[str | None, list[str]]:
    """(newest light version, the lights in its layer)."""
    path = latest_export_path(shot, "default", "light")
    if path is None:
        return None, []
    return path.name, lights_of(Usd.Stage.Open(str(next(path.glob("*.usd")))))


def light_workfile(group_uri: Uri) -> None:
    """The Multi's light workfile from the stock template, with sh010's
    template export bypassed (on the bare import) for a new one after its
    lights: created later, so listed after the bypassed one."""
    hou.hipFile.clear(suppress_save_prompt=True)
    spec = importlib.util.spec_from_file_location(
        "light_template",
        PROJECT / "_config" / "templates" / "shots" / "light" / "template.py")
    template = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(template)
    stage = hou.node("/stage")
    template.create(stage, group_uri, "light")
    old = stage.node("export_shot_sq010_sh010")
    old.setInput(0, stage.node("import_shot_sq010_sh010"))
    old.bypass(True)
    new = export_layer.create(stage, "export_lit_sq010_sh010")
    new.native().setInput(0, stage.node("light_linker_sq010_sh010"))
    new.set_entity_uri(SH010)
    new.set_department_name("light")
    save_workfile(group_uri, "light")


def farm_publish(shot: Uri) -> int:
    """Run the publish worker as a Farm Submit light publish of ``shot``
    does: the newest finished workfile (the Multi's) and its context.json
    bundled as ``farm.pipelines.publish.bundle_workfile`` lays them out
    (that module pulls in the Deadline submitter, which hython lacks)."""
    hip = latest_complete_hip_file_path(shot, "light")
    bundle = Path("workfiles") / f"light_{hip.stem}"
    data = WORK / f"data_{shot.segments[-1]}"
    for source, target in ((hip, bundle / hip.name),
                           (hip.parent / "context.json", bundle / "context.json")):
        (data / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, data / target)
    os.environ["TH_FARM_DATA"] = str(data)
    return publish_houdini.main({
        "entity": {"uri": str(shot), "department": "light"},
        "settings": {"priority": 50, "pool_name": "verify",
                     "first_frame": 1001, "last_frame": LAST[shot]},
        "tasks": {"publish": {}},
        "workfile_path": (bundle / hip.name).as_posix(),
    })


def check_renders(shot: Uri, when: str) -> None:
    name = shot.segments[-1]
    staged = get_latest_staged_file_path(shot, "default")
    check(f"{name} staged build composes the light layer ({when})",
          "dept=light" in staged.read_text(), staged.parent.name)
    pool = [d.name for d in list_departments("shots")]
    for cut in ("light", "render"):
        excluded = excluded_staged_refs(staged, pool[:pool.index(cut) + 1], pool)
        out = WORK / f"collapsed_{name}_{cut}.usda"
        out.write_text(collapse_latest_references(staged, {}, excluded_refs=excluded))
        lights = lights_of(Usd.Stage.Open(str(out)))
        check(f"{name} render up to {cut} has its lights ({when})",
              any(name in light for light in lights), lights)


def main() -> int:
    for shot, last in LAST.items():
        api.config.set_own_properties(shot, {"frame_start": 1001, "frame_end": last})
    group_uri = groups.add_group("shots", "lit", [SH010, SH020], ["light"])
    mt.set_timeline(group_uri, True, True)
    for shot in LAST:
        anim_workfile(shot).execute(force_local=True)
        build(shot)

    print("\n1. The Multi's light workfile")
    light_workfile(group_uri)

    print("\n2. The farm publishes each member's light")
    for shot in LAST:
        name = shot.segments[-1]
        before = light_layer(shot)[0]
        check(f"{name} publish worker succeeds", farm_publish(shot) == 0)
        version, lights = light_layer(shot)
        check(f"{name} has a new light version", version not in (None, before), version)
        check(f"{name} light layer has its lights",
              any(name in light for light in lights), lights)

    print("\n3. What the renders compose")
    for shot in LAST:
        build(shot)
        check_renders(shot, "after the light publish")

    print("\n4. A new animation export keeps them")
    anim_workfile(SH010).execute(force_local=True)
    build(SH010)
    check_renders(SH010, "after the animation export")
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
