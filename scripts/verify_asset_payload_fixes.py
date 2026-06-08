"""Verify the two asset_payload fixes made on 2026-06-05.

Run this inside a live Houdini that has the TumblePipe package loaded — it
needs both ``hou`` and ``pxr``. The intended driver is the tumbletrove-desktop
MCP::

    sessions_exec_python(session_id, open(".../scripts/verify_asset_payload_fixes.py").read())

or just paste the file into a Houdini Python Shell and call ``main()``.

It runs three independent checks, each printing PASS / FAIL / SKIP:

  A. Fix 1 — asset_payload primpath duplication.
     Builds create_asset -> create_asset_model -> (cube geo) -> asset_payload
     in a scratch /stage subnet, cooks it, and asserts the geometry composes
     at ``/char/test/geo/mesh`` and that NO duplicated ``/char/test/test``
     prim was introduced. No files written; the scratch subnet is removed on
     success. (The bug: the HDA's payload_asset reference node had
     ``primpath1 = /`@sourcename```, now ``lopinputprim('../payload_layer',0)``.)

  B. Fix 2 — export_layer._localize_external_sidecars (unit-level, USD only).
     Crafts a version-dir layer whose payload arc points at an *external*
     payload.usd sitting in a separate "workfile" dir, runs the function, and
     asserts the sidecar was copied next to the layer and the arc was
     rewritten to the bare relative ``payload.usd``. Deterministic, no project
     config, no real publish. This is the part that has no pxr in the tests/
     venv, so it can only be exercised in-app.

  C. Fix 2 — end-to-end export (OPTIONAL, has side effects).
     Off by default. When ``RUN_EXPORT = True`` and a real asset entity is
     available, builds the chain for that entity, runs a local export, and
     inspects the published version folder for a relative payload arc. This
     publishes a real version, so leave it off unless you have a throwaway
     test entity.
"""

import shutil
import tempfile
import traceback
from pathlib import Path

RUN_EXPORT = False  # flip to True only with a throwaway test entity (Check C)

PRIM_PATH = "/char/test"
DUPLICATED_PREFIX = "/char/test/test"
GEO_PATH = "/char/test/geo/mesh"


# --------------------------------------------------------------------------- #
# small reporting helpers
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Check A — asset_payload primpath duplication (in-memory composition)
# --------------------------------------------------------------------------- #
def check_a_primpath(report):
    import hou

    stage = hou.node("/stage")
    if stage is None:
        report.record("A. primpath duplication", "SKIP", "no /stage context")
        return

    subnet = None
    try:
        subnet = stage.createNode("subnet", "verify_asset_payload")

        try:
            asset = subnet.createNode("th::create_asset::1.0", "ASSET")
        except hou.OperationFailed as exc:
            report.record(
                "A. primpath duplication", "SKIP",
                f"th::create_asset::1.0 not loaded ({exc})",
            )
            return
        asset.parm("primpath").set(PRIM_PATH)

        model = subnet.createNode("th::create_asset_model::1.0", "MODEL")
        model.setInput(0, asset)
        # Direct-primpath mode so we don't need a registered entity / workfile.
        if model.parm("use_entity") is not None:
            model.parm("use_entity").set(0)
        if model.parm("primpath") is not None:
            model.parm("primpath").set(PRIM_PATH)
        # Best-effort variant sync (faithful to the template); non-fatal.
        try:
            if model.parm("variants") is not None:
                model.parm("variants").set(1)
                if model.parm("variant_name1") is not None:
                    model.parm("variant_name1").set("default")
                model.hdaModule()._sync_variants(model)
        except Exception as exc:  # noqa: BLE001
            print(f"  (variant sync skipped: {exc})")

        # Author geometry under the asset so the duplication is concrete.
        upstream = model
        geo_authored = False
        try:
            cube = subnet.createNode("cube")
            cube.setInput(0, model)
            if cube.parm("primpath") is not None:
                cube.parm("primpath").set(GEO_PATH)
                upstream = cube
                geo_authored = True
        except hou.OperationFailed:
            print("  (cube LOP unavailable; checking prim structure without geo)")

        payload = subnet.createNode("th::asset_payload::1.0", "PAYLOAD")
        payload.setInput(0, upstream)

        usd_stage = payload.stage()
        if usd_stage is None:
            report.record("A. primpath duplication", "FAIL", "payload node produced no stage")
            return

        paths = [p.GetPath().pathString for p in usd_stage.Traverse()]
        duplicated = [p for p in paths if p == DUPLICATED_PREFIX or p.startswith(DUPLICATED_PREFIX + "/")]
        asset_present = PRIM_PATH in paths
        geo_present = GEO_PATH in paths

        detail_bits = [f"asset_prim={'yes' if asset_present else 'NO'}"]
        if geo_authored:
            detail_bits.append(f"geo@{GEO_PATH}={'yes' if geo_present else 'NO'}")
        detail_bits.append(f"duplicate_prims={duplicated if duplicated else 'none'}")
        detail = ", ".join(detail_bits)

        ok = asset_present and not duplicated and (geo_present or not geo_authored)
        report.record("A. primpath duplication", "PASS" if ok else "FAIL", detail)
        if not ok:
            print("  composed prim paths:")
            for p in paths:
                print(f"    {p}")
    except Exception as exc:  # noqa: BLE001
        report.record("A. primpath duplication", "FAIL", f"exception: {exc}")
        traceback.print_exc()
    finally:
        if subnet is not None:
            try:
                subnet.destroy()
            except Exception:  # noqa: BLE001
                print("  (could not remove scratch subnet; left for inspection)")


# --------------------------------------------------------------------------- #
# Check B — _localize_external_sidecars (deterministic USD unit test)
# --------------------------------------------------------------------------- #
def check_b_localize(report):
    try:
        from pxr import Sdf, Usd, UsdGeom  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize sidecars", "SKIP", f"pxr unavailable ({exc})")
        return

    try:
        from tumblepipe.pipe.houdini.lops.export_layer import _localize_external_sidecars
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize sidecars", "FAIL", f"import failed: {exc}")
        traceback.print_exc()
        return

    work = Path(tempfile.mkdtemp(prefix="verify_payload_"))
    try:
        version_dir = work / "v0001"
        workfile_dir = work / "workfiles"  # the wrong place the sidecar starts in
        version_dir.mkdir()
        workfile_dir.mkdir()

        # External sidecar (the geometry payload), sitting OUTSIDE the version dir.
        side = Usd.Stage.CreateNew(str(workfile_dir / "payload.usd"))
        UsdGeom.Cube.Define(side, GEO_PATH)
        side.GetRootLayer().Save()

        # Published layer with a payload arc to that external absolute path.
        published = version_dir / "layer.usd"
        ls = Usd.Stage.CreateNew(str(published))
        prim = UsdGeom.Xform.Define(ls, PRIM_PATH).GetPrim()
        external_abs = str((workfile_dir / "payload.usd").resolve()).replace("\\", "/")
        prim.GetPayloads().AddPayload(external_abs)
        ls.GetRootLayer().Save()
        del ls  # drop the stage's hold on the layer before we mutate on disk

        _localize_external_sidecars(published)

        sidecar_copied = (version_dir / "payload.usd").is_file()

        reopened = Sdf.Layer.FindOrOpen(str(published))
        reopened.Reload()
        arc_paths = []
        spec = reopened.GetPrimAtPath(PRIM_PATH)
        if spec is not None:
            for item in spec.payloadList.GetAddedOrExplicitItems():
                arc_paths.append(item.assetPath)
        arc_relative = arc_paths == ["payload.usd"]

        ok = sidecar_copied and arc_relative
        report.record(
            "B. localize sidecars",
            "PASS" if ok else "FAIL",
            f"sidecar_copied={sidecar_copied}, payload_arc={arc_paths}",
        )
    except Exception as exc:  # noqa: BLE001
        report.record("B. localize sidecars", "FAIL", f"exception: {exc}")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Check C — end-to-end export (optional, side effects)
# --------------------------------------------------------------------------- #
def check_c_export(report):
    if not RUN_EXPORT:
        report.record(
            "C. end-to-end export", "SKIP",
            "RUN_EXPORT is False (publishes a real version when enabled)",
        )
        return

    import hou
    from pxr import Sdf

    try:
        from tumblepipe.api import default_client
        from tumblepipe.util.uri import Uri
        from tumblepipe.pipe.houdini.util import uri_to_prim_path
        from tumblepipe.pipe.houdini.lops import export_layer
        from tumblepipe.pipe.paths import latest_export_path

        api = default_client()
        assets = api.config.list_entities(
            filter=Uri.parse_unsafe("entity:/assets"), closure=True
        )
        entity = next((e.uri for e in assets if e.uri.purpose == "entity"), None)
        if entity is None:
            report.record("C. end-to-end export", "SKIP", "no asset entity registered")
            return

        prim_path = uri_to_prim_path(entity)
        leaf = prim_path.rstrip("/").rsplit("/", 1)[-1]
        duplicated_prefix = f"{prim_path}/{leaf}"

        stage = hou.node("/stage")
        subnet = stage.createNode("subnet", "verify_export")
        try:
            asset = subnet.createNode("th::create_asset::1.0", "ASSET")
            asset.parm("primpath").set(prim_path)
            model = subnet.createNode("th::create_asset_model::1.0", "MODEL")
            model.setInput(0, asset)
            if model.parm("use_entity") is not None:
                model.parm("use_entity").set(0)
            if model.parm("primpath") is not None:
                model.parm("primpath").set(prim_path)
            cube = subnet.createNode("cube")
            cube.setInput(0, model)
            cube.parm("primpath").set(f"{prim_path}/geo/mesh")
            payload = subnet.createNode("th::asset_payload::1.0", "PAYLOAD")
            payload.setInput(0, cube)

            export_node = export_layer.create(subnet, "export_model")
            export_node.setInput(0, payload)
            export_node.set_entity_uri(entity)
            # Pick the first publishable department.
            depts = [d for d in export_node.list_department_names() if d != "from_context"]
            if depts:
                export_node.set_department_name(depts[0])

            export_node.execute(force_local=True)

            version_path = latest_export_path(
                entity, export_node.get_variant_name(), export_node.get_department_name()
            )
            if version_path is None or not Path(version_path).exists():
                report.record("C. end-to-end export", "FAIL", "no version folder produced")
                return

            layer_files = list(Path(version_path).glob("*.usd*"))
            published = next((p for p in layer_files if "payload" not in p.name.lower()), None)
            if published is None:
                report.record("C. end-to-end export", "FAIL", f"no layer in {version_path}")
                return

            sidecar = (Path(version_path) / "payload.usd").is_file()
            lyr = Sdf.Layer.FindOrOpen(str(published))
            arc_paths = []
            stack = list(lyr.rootPrims)
            while stack:
                p = stack.pop()
                for item in p.payloadList.GetAddedOrExplicitItems():
                    arc_paths.append(item.assetPath)
                stack.extend(p.nameChildren.values())
            rel_ok = all(not Path(a).is_absolute() for a in arc_paths) if arc_paths else True

            ok = sidecar and rel_ok
            report.record(
                "C. end-to-end export",
                "PASS" if ok else "FAIL",
                f"version={Path(version_path).name}, payload.usd={sidecar}, arcs={arc_paths}",
            )
        finally:
            subnet.destroy()
    except Exception as exc:  # noqa: BLE001
        report.record("C. end-to-end export", "FAIL", f"exception: {exc}")
        traceback.print_exc()


def main():
    print("=== asset_payload fix verification ===\n")
    report = _Report()
    check_a_primpath(report)
    check_b_localize(report)
    check_c_export(report)
    return report.summary()


if __name__ == "__main__":
    main()
else:
    # When fed to sessions_exec_python the trailing expression is captured.
    _result = main()
