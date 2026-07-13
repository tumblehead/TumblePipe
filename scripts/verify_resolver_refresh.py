"""Verify that refresh_context() floats composed stages to new versions.

Pins the fix for the "restart Houdini to see a new publish" bug
(02a8869): USD's Sdf layer registry hands back the originally-opened
layer for an identifier forever, so a version-less entity: URI never
re-resolves on its own — ``tumblepipe.resolver.refresh_context()`` must
close that gap by re-resolving loaded entity layers and reloading the
stale ones.

Run under any project hython (the tumbleResolver plugin and tumblepipe
must be on the package path), e.g. via TumbleTrove Desktop's run_hython
or a dev launch:

    hython scripts/verify_resolver_refresh.py

READ-ONLY with respect to project data: the script points TH_EXPORT_PATH
at a throwaway tempdir sandbox before composing anything, so no live
export is touched or even read.

Checks:
  1. tumbleResolver resolves a fake dept URI to v0001 (sandbox).
  2. A stage composing that URI shows v0001.
  3. After v0002 appears on disk, direct Ar.Resolve floats (the Rust
     core is uncached) while the composed stage stays stale — the bug.
  4. refresh_context() floats the stage to v0002 without reopening it.
"""

import os
import sys
import tempfile
from pathlib import Path

FAILURES = []


def check(label, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label)


sandbox = Path(tempfile.mkdtemp(prefix="th_refresh_verify_"))
dept_dir = sandbox / "assets" / "PROP" / "RefreshTest" / "default" / "model"

# Point the resolver at the sandbox BEFORE composing anything. Both env
# vars are read per resolve by the Rust core, so in-process assignment
# is enough.
os.environ["TH_EXPORT_PATH"] = str(sandbox)
os.environ["TH_RESOLVER_LATEST_MODE"] = "1"

from pxr import Ar, Plug, Sdf, Usd  # noqa: E402


def write_version(vname: str, marker: int) -> Path:
    vdir = dept_dir / vname
    vdir.mkdir(parents=True, exist_ok=True)
    fpath = vdir / f"assets_PROP_RefreshTest_default_model_{vname}.usd"
    layer = Sdf.Layer.CreateNew(str(fpath))
    prim = Sdf.PrimSpec(layer, "RefreshTest", Sdf.SpecifierDef, "Scope")
    attr = Sdf.AttributeSpec(prim, "marker", Sdf.ValueTypeNames.Int)
    attr.default = marker
    layer.Save()
    return fpath


plugin = Plug.Registry().GetPluginWithName("tumbleResolver")
print(f"tumbleResolver plugin: {plugin.path if plugin else 'NOT FOUND'}")
check("tumbleResolver plugin registered", plugin is not None)

URI = "entity:/assets/PROP/RefreshTest?dept=model&variant=default&version=v0001"

write_version("v0001", 1)

resolved_1 = Ar.GetResolver().Resolve(URI)
check("URI resolves pre-compose", bool(resolved_1), str(resolved_1))
check("resolves to v0001", "v0001" in str(resolved_1), str(resolved_1))

root = Sdf.Layer.CreateAnonymous(".usda")
root.subLayerPaths.append(URI)
stage = Usd.Stage.Open(root)


def stage_marker():
    prim = stage.GetPrimAtPath("/RefreshTest")
    if not prim:
        return None
    attr = prim.GetAttribute("marker")
    return attr.Get() if attr else None


check("stage composes v0001", stage_marker() == 1, f"marker={stage_marker()}")

# A new version appears on disk mid-session (a publish, from the open
# scene's point of view).
write_version("v0002", 2)

resolved_2 = Ar.GetResolver().Resolve(URI)
check(
    "direct Ar.Resolve floats to v0002 (Rust core uncached)",
    "v0002" in str(resolved_2),
    str(resolved_2),
)
check(
    "composed stage is STALE without refresh (the fixed bug)",
    stage_marker() == 1,
    f"marker={stage_marker()}",
)

from tumblepipe import resolver  # noqa: E402

resolver.refresh_context()

marker_after = stage_marker()
check(
    "stage floats to v0002 after refresh_context()",
    marker_after == 2,
    f"marker={marker_after}",
)
used = [
    layer.realPath for layer in stage.GetUsedLayers()
    if "RefreshTest" in (layer.realPath or "")
]
check(
    "used layer realPath is the v0002 file",
    any("v0002" in p for p in used),
    "; ".join(used),
)

print()
if FAILURES:
    print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
