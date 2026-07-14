"""Verify the th::cache publish-by-reference export fixes (2026-07-14).

Run this inside a live Houdini (or hython) that has the TumblePipe package
loaded — it needs ``pxr``. The intended driver is the tumbletrove-desktop
MCP::

    sessions_exec_python(session_id, open(".../scripts/verify_cache_reference_fixes.py").read())

or paste the file into a Houdini Python Shell and call ``main()``.

Background: exports aborted with the escaping-arc guard when a th::cache LOP
sat upstream, because the cache file lives next to the workfile area (under
the project:/ or proxy:/ ``lops_cache`` convention) — outside the export
folder. Versioned caches now publish by reference: their arcs are pinned
absolute and exempted from the guard, while everything else keeps the old
copy-or-abort behaviour.

Checks (all deterministic, temp dirs only, no project config, no publish):

  A. _absolutize_cache_arcs — a sublayer arc relativised against the temp
     export dir but resolving into a cache root is rewritten to an absolute
     path; a non-cache arc is left untouched.

  B. _localize_external_sidecars(skip_roots) — an external payload under a
     cache root is NOT copied into the layer dir (published by reference),
     while a plain external payload still is.

  C. _check_no_dangling_composition_paths(allowed_roots) — an absolute arc
     into a cache root passes the guard; the same layer without the
     exemption raises; a missing cache file still raises (dangling).
"""

import shutil
import tempfile
import traceback
from pathlib import Path

PRIM_PATH = "/char/test"
GEO_PATH = "/char/test/geo/mesh"


class _Report:
    def __init__(self):
        self.rows = []

    def record(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        print(f"[{status:4}] {name}" + (f" — {detail}" if detail else ""))

    def summary(self):
        print("\n=== SUMMARY ===")
        for name, status, detail in self.rows:
            print(f"  {status:4}  {name}" + (f" — {detail}" if detail else ""))
        statuses = [s for _, s, _ in self.rows]
        ok = "FAIL" not in statuses
        print(f"\nRESULT: {'ALL CHECKS PASSED (no failures)' if ok else 'FAILURES PRESENT'}")
        return {
            "passed": ok,
            "rows": [
                {"name": n, "status": s, "detail": d} for n, s, d in self.rows
            ],
        }


def _make_usd(path, define_geo=True):
    from pxr import Usd, UsdGeom

    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(path))
    if define_geo:
        UsdGeom.Cube.Define(stage, GEO_PATH)
    stage.GetRootLayer().Save()


def check_a_absolutize(report):
    try:
        from pxr import Sdf  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report.record("A. absolutize cache arcs", "SKIP", f"pxr unavailable ({exc})")
        return
    try:
        from tumblepipe.pipe.houdini.lops.export_layer import _absolutize_cache_arcs
    except Exception as exc:  # noqa: BLE001
        report.record("A. absolutize cache arcs", "FAIL", f"import failed: {exc}")
        traceback.print_exc()
        return

    work = Path(tempfile.mkdtemp(prefix="verify_cache_ref_"))
    try:
        temp_dir = work / "temp_export"
        temp_dir.mkdir()
        cache_root = work / "lops_cache"
        cached = cache_root / "sim" / "v0001" / "v0001.usd"
        _make_usd(cached)
        other = work / "other" / "other.usd"
        _make_usd(other)

        from pxr import Sdf
        published = temp_dir / "layer.usd"
        layer = Sdf.Layer.CreateNew(str(published))
        # The ROP relativised the cache arc against the temp dir.
        layer.subLayerPaths.append("../lops_cache/sim/v0001/v0001.usd")
        layer.subLayerPaths.append("../other/other.usd")
        layer.Save()
        del layer

        _absolutize_cache_arcs(published, [cache_root.resolve()])

        reopened = Sdf.Layer.FindOrOpen(str(published))
        reopened.Reload()
        subs = [str(p) for p in reopened.subLayerPaths]
        cache_pinned = (
            len(subs) == 2
            and Path(subs[0]).is_absolute()
            and Path(subs[0]).resolve() == cached.resolve()
        )
        other_untouched = subs[1] == "../other/other.usd"

        ok = cache_pinned and other_untouched
        report.record(
            "A. absolutize cache arcs",
            "PASS" if ok else "FAIL",
            f"sublayers={subs}",
        )
    except Exception as exc:  # noqa: BLE001
        report.record("A. absolutize cache arcs", "FAIL", f"exception: {exc}")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_b_skip_roots(report):
    try:
        from pxr import Sdf, Usd, UsdGeom  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize skips cache", "SKIP", f"pxr unavailable ({exc})")
        return
    try:
        from tumblepipe.pipe.houdini.lops.export_layer import _localize_external_sidecars
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize skips cache", "FAIL", f"import failed: {exc}")
        traceback.print_exc()
        return

    work = Path(tempfile.mkdtemp(prefix="verify_cache_ref_"))
    try:
        version_dir = work / "v0001"
        version_dir.mkdir()
        cache_root = work / "lops_cache"
        cached = cache_root / "sim" / "v0001" / "v0001.usd"
        _make_usd(cached)
        loose = work / "workfiles" / "payload.usd"
        _make_usd(loose)

        from pxr import Usd, UsdGeom
        published = version_dir / "layer.usd"
        ls = Usd.Stage.CreateNew(str(published))
        prim = UsdGeom.Xform.Define(ls, PRIM_PATH).GetPrim()
        prim.GetPayloads().AddPayload(str(cached.resolve()).replace("\\", "/"))
        prim.GetPayloads().AddPayload(str(loose.resolve()).replace("\\", "/"))
        ls.GetRootLayer().Save()
        del ls

        _localize_external_sidecars(published, skip_roots=[cache_root.resolve()])

        cache_not_copied = not (version_dir / "v0001.usd").is_file()
        loose_copied = (version_dir / "payload.usd").is_file()

        from pxr import Sdf
        reopened = Sdf.Layer.FindOrOpen(str(published))
        reopened.Reload()
        arcs = []
        spec = reopened.GetPrimAtPath(PRIM_PATH)
        if spec is not None:
            for item in spec.payloadList.GetAddedOrExplicitItems():
                arcs.append(item.assetPath)
        cache_arc_kept = arcs and Path(arcs[0]).resolve() == cached.resolve()
        loose_arc_rewritten = arcs[1:] == ["payload.usd"]

        ok = cache_not_copied and loose_copied and cache_arc_kept and loose_arc_rewritten
        report.record(
            "B. localize skips cache",
            "PASS" if ok else "FAIL",
            f"cache_not_copied={cache_not_copied}, loose_copied={loose_copied}, arcs={arcs}",
        )
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize skips cache", "FAIL", f"exception: {exc}")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_c_guard(report):
    try:
        from pxr import Sdf  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report.record("C. guard exemption", "SKIP", f"pxr unavailable ({exc})")
        return
    try:
        from tumblepipe.pipe.houdini.lops.export_layer import (
            ExportLayerError,
            _check_no_dangling_composition_paths,
        )
    except Exception as exc:  # noqa: BLE001
        report.record("C. guard exemption", "FAIL", f"import failed: {exc}")
        traceback.print_exc()
        return

    work = Path(tempfile.mkdtemp(prefix="verify_cache_ref_"))
    try:
        version_dir = work / "v0001"
        version_dir.mkdir()
        cache_root = work / "lops_cache"
        cached = cache_root / "sim" / "v0001" / "v0001.usd"
        _make_usd(cached)
        missing = cache_root / "sim" / "v0002" / "v0002.usd"

        from pxr import Sdf
        published = version_dir / "layer.usd"
        layer = Sdf.Layer.CreateNew(str(published))
        layer.subLayerPaths.append(str(cached.resolve()).replace("\\", "/"))
        layer.Save()
        del layer

        allowed_passes = True
        try:
            _check_no_dangling_composition_paths(
                published, allowed_roots=[cache_root.resolve()]
            )
        except ExportLayerError:
            allowed_passes = False

        unallowed_raises = False
        try:
            _check_no_dangling_composition_paths(published)
        except ExportLayerError:
            unallowed_raises = True

        broken = version_dir / "broken.usd"
        layer = Sdf.Layer.CreateNew(str(broken))
        layer.subLayerPaths.append(str(missing.resolve()).replace("\\", "/"))
        layer.Save()
        del layer

        missing_raises = False
        try:
            _check_no_dangling_composition_paths(
                broken, allowed_roots=[cache_root.resolve()]
            )
        except ExportLayerError:
            missing_raises = True

        ok = allowed_passes and unallowed_raises and missing_raises
        report.record(
            "C. guard exemption",
            "PASS" if ok else "FAIL",
            f"allowed_passes={allowed_passes}, unallowed_raises={unallowed_raises}, "
            f"missing_raises={missing_raises}",
        )
    except Exception as exc:  # noqa: BLE001
        report.record("C. guard exemption", "FAIL", f"exception: {exc}")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    print("=== th::cache publish-by-reference verification ===\n")
    report = _Report()
    check_a_absolutize(report)
    check_b_skip_roots(report)
    check_c_guard(report)
    return report.summary()


if __name__ == "__main__":
    main()
else:
    # When fed to sessions_exec_python the trailing expression is captured.
    _result = main()
