#!/usr/bin/env hython
"""Verify a Multi's shared timeline in Houdini: exports, imports, bookmarks.

``tests/test_multi_timeline.py`` pins the layout and the sample shifting
without Houdini; this drives the real nodes in a throwaway project with two
shots, sh010 (1001-1010) and sh020 (1001-1008), in a shots Multi with shared
timeline and bookmarks on, so sh020 sits at 1011-1018 (offset +10):

1. th::export_layer for each shot, in the Multi's workfile, on an animated
   SOP Create (tx = $F): each published layer has samples on the shot's own
   frames, and sh020's carry the values cooked at its shared frames.
2. th::import_shot and th::export_layer read the offsets From Multi; None
   and Manual override it; a non-Multi scene reads 0.
3. A Sublayer LOP time offset, the one import_shot sets, moves sh020's
   published layer back onto 1011-1018: import and export are inverses.
4. ``multi_timeline.apply()``: the playbar spans 1001-1018 and there is a
   temporary bookmark per shot on its shared frames; bookmarks off removes
   them and leaves an artist's own bookmark alone.

Needs a Houdini license, not an existing project. Compile the HDAs first
(``hython .ci/compile_hdas.py``), then run with packages skipped and the
repo on the paths:

    HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
        TH_PIPELINE_PATH=<repo> hython scripts/verify_multi_timeline.py

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
WORK = Path(tempfile.mkdtemp(prefix="verify_multi_timeline_"))
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

from tumblepipe.config import groups, multi_timeline as mt  # noqa: E402
from tumblepipe.pipe.houdini import multi_timeline  # noqa: E402
from tumblepipe.pipe.houdini.lops import export_layer, import_shot  # noqa: E402
from tumblepipe.pipe.houdini.nodes import disable_layer_save_path  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

SH010 = Uri.parse("entity:/shots/sq010/sh010")
SH020 = Uri.parse("entity:/shots/sq010/sh020")
DEPARTMENT = "effects"
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def setup_project() -> Uri:
    api.config.set_own_properties(SH010, {"frame_start": 1001, "frame_end": 1010})
    api.config.set_own_properties(SH020, {"frame_start": 1001, "frame_end": 1008})
    group_uri = groups.add_group("shots", "seqA", [SH010, SH020], [DEPARTMENT])
    mt.set_timeline(group_uri, True, True)
    return group_uri


def save_workfile(entity_uri: Uri, folder: Path, stem: str) -> None:
    """Save the scene as a pipeline workfile of ``entity_uri``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "context.json").write_text(json.dumps({
        "uri": str(entity_uri), "department": DEPARTMENT,
    }))
    hou.hipFile.save(str(folder / f"{stem}_v0001.hip"))


def build_graph():
    stage = hou.node("/stage")
    sop = stage.createNode("sopcreate", "fx")
    # As the department templates do, or the ROP refuses the SOP layer.
    disable_layer_save_path(sop)
    create = sop.node("sopnet/create")
    box = create.createNode("box")
    xform = create.createNode("xform")
    xform.setInput(0, box)
    xform.parm("tx").setExpression("$F")
    xform.setDisplayFlag(True)
    xform.setRenderFlag(True)
    exports = {}
    for shot in (SH010, SH020):
        node = export_layer.create(stage, f"export_{shot.segments[-1]}")
        node.native().setInput(0, sop)
        node.set_entity_uri(shot)
        node.set_department_name(DEPARTMENT)
        exports[shot] = node
    return exports


def published_samples(shot: Uri) -> dict[float, float]:
    """``{frame: points[0].x}`` of the shot's newest published effects layer."""
    root = PROJECT / "export" / "shots" / "sq010" / shot.segments[-1]
    layers = sorted(
        path for path in root.rglob("*.usd")
        if DEPARTMENT in path.parts and "chunks" not in path.parts
    )
    if not layers:
        return {}
    stage = Usd.Stage.Open(str(layers[-1]))
    for prim in stage.Traverse():
        points = prim.GetAttribute("points")
        if points and points.GetTimeSamples():
            return {
                time: round(points.Get(time)[0][0], 3)
                for time in points.GetTimeSamples()
            }
    return {}


def main() -> int:
    group_uri = setup_project()
    group = groups.get_group(group_uri)
    check("layout", group.timeline_starts == {str(SH010): 1001, str(SH020): 1011},
          group.timeline_starts)

    print("\n1-2. Export from the Multi's workfile")
    exports = build_graph()
    save_workfile(group_uri, PROJECT / "groups" / "shots" / "seqA" / DEPARTMENT,
                  f"seqA_{DEPARTMENT}")
    check("export sh010 offset", exports[SH010].get_time_offset() == 0)
    check("export sh020 offset", exports[SH020].get_time_offset() == 10,
          exports[SH020].get_time_offset())
    imp = import_shot.create(hou.node("/stage"), "import_sh020")
    imp.set_entity_uri(SH020)
    check("import sh020 offset", imp.get_time_offset() == 10, imp.get_time_offset())
    imp.parm("time_offset_mode").set("none")
    check("None overrides", imp.get_time_offset() == 0)
    imp.parm("time_offset_mode").set("manual")
    imp.parm("time_offset").set(-7)
    check("Manual overrides", imp.get_time_offset() == -7)
    imp.parm("time_offset_mode").set("from_multi")

    for shot, own_last, offset in ((SH010, 1010, 0), (SH020, 1008, 10)):
        exports[shot].execute(force_local=True)
        samples = published_samples(shot)
        expected = {
            float(frame): round(frame + offset - 0.5, 3)
            for frame in range(1001, own_last + 1)
        }
        check(f"published {shot.segments[-1]} on own frames, cooked at +{offset}",
              samples == expected, samples)

    print("\n3. The import's sublayer offset puts sh020 back on 1011-1018")
    sub = hou.node("/stage").createNode("sublayer", "check_offset")
    root = PROJECT / "export" / "shots" / "sq010" / "sh020"
    layer = sorted(p for p in root.rglob("*.usd") if DEPARTMENT in p.parts)[-1]
    sub.parm("num_files").set(1)
    sub.parm("filepath1").set(str(layer).replace("\\", "/"))
    sub.parm("timeoffset1").set(10)
    stage = sub.stage()
    times = []
    for prim in stage.Traverse():
        points = prim.GetAttribute("points")
        if points and points.GetTimeSamples():
            times = list(points.GetTimeSamples())
            break
    check("sublayer timeoffset moves 1001-1008 to 1011-1018",
          times == [float(f) for f in range(1011, 1019)], times)

    print("\n4. Playbar and bookmarks")
    artist = hou.anim.newBookmark("mine", 1003, 1004)
    check("apply in a shared Multi", multi_timeline.apply() is True)
    check("playbar spans every shot",
          tuple(hou.playbar.frameRange()) == (1001.0, 1018.0),
          hou.playbar.frameRange())
    ours = [
        (b.name(), b.startFrame(), b.endFrame(), b.isTemporary())
        for b in hou.anim.bookmarks()
        if b.metadata(multi_timeline.MEMBER_KEY, None)
    ]
    check("a temporary bookmark per shot",
          sorted(ours) == [("sq010_sh010", 1001, 1010, True),
                           ("sq010_sh020", 1011, 1018, True)], ours)
    multi_timeline.apply()
    check("apply again does not duplicate",
          len([b for b in hou.anim.bookmarks()
               if b.metadata(multi_timeline.MEMBER_KEY, None)]) == 2)
    mt.set_timeline(group_uri, True, False)
    multi_timeline.apply_if_open(group_uri)
    left = [b.name() for b in hou.anim.bookmarks()]
    check("bookmarks off removes ours, keeps the artist's", left == ["mine"], left)
    artist.setName("mine")

    print("\n5. A non-Multi scene reads 0")
    save_workfile(SH020, PROJECT / "shots" / "sq010" / "sh020" / DEPARTMENT,
                  f"sq010_sh020_{DEPARTMENT}")
    check("export in the shot's own workfile", exports[SH020].get_time_offset() == 0)
    check("apply outside a Multi", multi_timeline.apply() is False)
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
