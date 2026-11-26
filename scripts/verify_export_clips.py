#!/usr/bin/env hython
"""Verify th::export_layer's clipped publishes (#108) in Houdini.

``tests/test_usd_clips.py`` and ``tests/test_chunk_plan.py`` pin the clip
writing and the chunk sizing on usd-core layers; this drives the real node
and its USD ROP in a throwaway project. Shot src is 1001-1040 (the
template's render batch is 10); a Python LOP samples a mesh's points every
frame and a value that holds for seven frames at a time (so a chunk may
sample one value only).

1. Batch 0 at the default budget: one file, no clips/ folder, not clipped.
   This is the reference every other export is compared with.
2. Batch 0 with ``TH_EXPORT_CLIP_BUDGET_MB`` tiny: four clips of 10 frames
   in clips/ (plus the manifest), the published layer holds no time samples,
   and its points equal the reference at every frame and at a quarter and a
   half frame on (motion blur subframes, chunk edges included).
3. Batch 7, Batch Mode Stitched: one file as before, equal to the reference.
4. Batch 7, Batch Mode Clips: six clips, equal to the reference.
5. Targets: Chosen = t2 (2001-2040) with the tiny budget: t2's layer is
   clipped, named for t2 (its clips too), and equals the reference 1000
   frames on.

Run through preflight (compiles the HDAs, clean environment):

    PREFLIGHT_HYTHON=verify_export_clips.py uv run --no-project python .ci/preflight.py --only hython

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
WORK = Path(tempfile.mkdtemp(prefix="verify_export_clips_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")
os.environ.setdefault("TT_USER_NAME", "verify")
os.environ.pop("TH_EXPORT_CLIP_BUDGET_MB", None)

import hou  # noqa: E402
from pxr import Sdf, Usd  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

from tumblepipe.pipe import export_targets  # noqa: E402
from tumblepipe.pipe.houdini.lops import export_layer  # noqa: E402
from tumblepipe.pipe.usd.chunk_plan import BUDGET_ENV  # noqa: E402
from tumblepipe.pipe.usd.stitch import CLIPS_DIR, is_clipped_layer  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

SRC = Uri.parse("entity:/shots/sq010/src")
T2 = Uri.parse("entity:/shots/sq020/t2")
DEPARTMENT = "effects"
FIRST, LAST = 1001, 1040
FAILURES: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {str(detail)[:300]}")
    if not ok:
        FAILURES.append(f"{label}: {str(detail)[:300]}")


def setup_project() -> None:
    for uri, start, end in ((SRC, FIRST, LAST), (T2, 2001, 2040)):
        api.config.set_own_properties(uri, {"frame_start": start, "frame_end": end})


def save_workfile(entity_uri: Uri) -> None:
    folder = PROJECT / "shots" / Path(*entity_uri.segments[1:]) / DEPARTMENT
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "context.json").write_text(json.dumps({
        "uri": str(entity_uri), "department": DEPARTMENT,
    }))
    hou.hipFile.save(str(folder / f"{'_'.join(entity_uri.segments[1:])}_{DEPARTMENT}_v0001.hip"))


# A Python LOP's layer is implicit, so the export flattens it into the
# published file, as th::animate's layers are (a SOP Create's time-dependent
# SOP layer would be saved beside it instead).
ANIMATION = '''
import math
import hou
from pxr import Gf, UsdGeom, Vt
stage = hou.pwd().editableStage()
frame = hou.frame()
mesh = UsdGeom.Mesh.Define(stage, "/fx/geo")
mesh.CreatePointsAttr().Set(Vt.Vec3fArray([
    Gf.Vec3f(i * 0.1 + frame, math.sin(i + frame * 0.3), math.cos(i * frame))
    for i in range(60)
]), frame)
mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray([3] * 20))
mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(list(range(60))))
# Held for runs of frames: a chunk may sample one value only
held = mesh.GetPrim().CreateAttribute("held", Sdf.ValueTypeNames.Float)
held.Set(float(int(frame) // 7), frame)
'''.replace("from pxr import", "from pxr import Sdf,")


def build_graph():
    stage = hou.node("/stage")
    lop = stage.createNode("pythonscript", "fx")
    lop.parm("python").set(ANIMATION)
    node = export_layer.create(stage, "export_fx")
    node.native().setInput(0, lop)
    node.set_entity_uri(SRC)
    node.set_department_name(DEPARTMENT)
    return node


def versions(shot: Uri) -> list[Path]:
    folder = PROJECT / "export" / "shots" / Path(*shot.segments[1:]) / "default" / DEPARTMENT
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir())


def layer_of(version: Path) -> Path:
    return next(iter(sorted(version.glob("*.usd"))))


def points_by_time(layer_path: Path, shift: int = 0) -> dict:
    """Every mesh's points and held value at each frame and a quarter and
    half frame on."""
    stage = Usd.Stage.Open(str(layer_path))
    out = {}
    for prim in stage.Traverse():
        for name in ("points", "held"):
            attr = prim.GetAttribute(name)
            if not attr:
                continue
            for frame in range(FIRST - 1, LAST + 2):
                for time in (frame, frame + 0.25, frame + 0.5):
                    value = attr.Get(time + shift)
                    if value is not None and name == "points":
                        value = [tuple(round(c, 4) for c in p) for p in value]
                    elif value is not None:
                        value = round(value, 4)
                    out[(str(prim.GetPath()), name, time)] = value
    return out


def sampled_in(layer_path: Path) -> int:
    layer = Sdf.Layer.OpenAsAnonymous(str(layer_path))
    count = []
    layer.Traverse(
        Sdf.Path.absoluteRootPath,
        lambda path: count.append(path)
        if path.IsPropertyPath() and layer.GetNumTimeSamplesForPath(path) else None,
    )
    return len(count)


def export(node, *, batch=0, mode="clips", budget_mb=None) -> Path:
    node.parm("batch_size").set(batch)
    node.parm("batch_mode").set(mode)
    if budget_mb is None:
        os.environ.pop(BUDGET_ENV, None)
    else:
        os.environ[BUDGET_ENV] = str(budget_mb)
    before = versions(SRC)
    node.execute(force_local=True)
    after = versions(SRC)
    check("one new version", len(after) == len(before) + 1, after)
    return after[-1]


def clip_names(version: Path) -> list[str]:
    folder = version / CLIPS_DIR
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def main() -> int:
    setup_project()
    node = build_graph()
    save_workfile(SRC)

    print("\n1. Batch 0, default budget: one file")
    reference_version = export(node)
    reference_layer = layer_of(reference_version)
    reference = points_by_time(reference_layer)
    check("no clips folder", not (reference_version / CLIPS_DIR).exists(), clip_names(reference_version))
    check("not clipped", not is_clipped_layer(reference_layer))
    check("points sampled", sampled_in(reference_layer) > 0, sampled_in(reference_layer))
    check("points move", len({str(v) for v in reference.values()}) > LAST - FIRST, len(reference))

    print("\n2. Batch 0, tiny budget: clips sized to the render batch")
    version = export(node, budget_mb=0.0001)
    layer = layer_of(version)
    names = clip_names(version)
    print(f"  clips: {names}")
    check("clipped", is_clipped_layer(layer))
    check("four clips and the manifest", len(names) == 5, names)
    check("clips of 10 frames", all(
        f".{start:04d}-{start + 9:04d}.usd" in " ".join(names)
        for start in range(FIRST, LAST + 1, 10)
    ), names)
    check("published layer holds no samples", sampled_in(layer) == 0, sampled_in(layer))
    got = points_by_time(layer)
    diff = [key for key in reference if reference[key] != got.get(key)]
    check("equals the one-file export, subframes included", not diff, diff[:5])

    print("\n3. Batch 7, Stitched: one file")
    version = export(node, batch=7, mode="stitch")
    layer = layer_of(version)
    check("no clips folder", not (version / CLIPS_DIR).exists(), clip_names(version))
    got = points_by_time(layer)
    diff = [key for key in reference if reference[key] != got.get(key)]
    check("equals the one-file export", not diff, diff[:5])

    print("\n4. Batch 7, Clips")
    version = export(node, batch=7, mode="clips")
    layer = layer_of(version)
    names = clip_names(version)
    check("six clips and the manifest", len(names) == 7, names)
    got = points_by_time(layer)
    diff = [key for key in reference if reference[key] != got.get(key)]
    check("equals the one-file export, subframes included", not diff, diff[:5])

    print("\n5. Targets: Chosen = t2, tiny budget")
    node.parm("batch_size").set(0)
    os.environ[BUDGET_ENV] = "0.0001"
    node.parm("targets_mode").set(export_targets.CHOSEN)
    node.set_chosen_targets([T2])
    node.execute(force_local=True)
    found = versions(T2)
    check("t2 has one version", len(found) == 1, found)
    if found:
        layer = layer_of(found[0])
        names = clip_names(found[0])
        stem = layer.stem
        check("t2 clipped", is_clipped_layer(layer))
        check("t2 clips named for it", names and all(n.startswith(stem + ".") for n in names), (stem, names))
        got = points_by_time(layer, shift=1000)
        diff = [key for key in reference if reference[key] != got.get(key)]
        check("equals the one-file export 1000 frames on", not diff, diff[:5])
    os.environ.pop(BUDGET_ENV, None)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILURES.append("exception")
    print(f"\n{'FAILED: ' + str(len(FAILURES)) if FAILURES else 'ALL OK'}")
    # Again on stderr, after the ROP's own errors there (preflight shows
    # the tail of the output)
    for failure in FAILURES:
        print(f"FAIL {failure}", file=sys.stderr)
    if os.environ.get("VERIFY_KEEP"):
        print(f"kept {WORK}")
    else:
        hou.hipFile.clear(suppress_save_prompt=True)
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(1 if FAILURES else 0)
